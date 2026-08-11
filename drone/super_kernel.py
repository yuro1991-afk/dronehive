"""
Super Kernel Lane — barebones multi-thread model host.

Law:
  - Minimum surface (stdlib ThreadingHTTPServer only)
  - Serve ANY installed model / any instance id
  - NO artificial max-models / max-instances limit
  - ONLY refuse when free VRAM cannot hang (after headroom + optional LRU eviction)
  - Stable multi-thread request lane on dedicated port
  - STABLE PROCESS: detached + watchdog; only Boss (CLI) or Grok (token kill) may stop
  - false_green: 0

Backend: Ollama at OLLAMA_HOST (shared). Kernel is the super lane front.

CLI:
  python -m drone super-kernel start|serve|watchdog|kill|status|...
  Kill is user/Grok only — never print kill token in normal status.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


# ── Stable process / boss+Grok kill (user-only) ────────────────

STATE_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ollama" / "super-kernel"
TOKEN_FILE = STATE_DIR / "kill.token"
STOP_FLAG = STATE_DIR / "authorized_stop.flag"
KERNEL_PID_FILE = STATE_DIR / "kernel.pid"
WATCHDOG_PID_FILE = STATE_DIR / "watchdog.pid"
IDLER_PID_FILE = STATE_DIR / "idler.pid"
IDLER_HEARTBEAT = STATE_DIR / "idler_heartbeat.json"
STATE_FILE = STATE_DIR / "state.json"
WHISPER_LOG = STATE_DIR / "whisper.log"

# Win32 process flags
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def ensure_kill_token() -> str:
    """Boss/Grok kill token — user ACL, never printed in status."""
    _state_dir()
    if TOKEN_FILE.is_file():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_hex(24)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    try:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if user and sys.platform == "win32":
            subprocess.run(
                [
                    "icacls",
                    str(TOKEN_FILE),
                    "/inheritance:r",
                    "/grant:r",
                    f"{user}:F",
                ],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
    except Exception:
        pass
    return tok


def verify_kill_token(token: str | None) -> bool:
    if not TOKEN_FILE.is_file():
        return False
    expected = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not expected or not token:
        return False
    try:
        return secrets.compare_digest(expected, str(token).strip())
    except Exception:
        return False


def write_authorized_stop(reason: str = "boss_or_grok", who: str = "cli") -> Path:
    _state_dir()
    STOP_FLAG.write_text(
        json.dumps({"utc": _utc(), "reason": reason, "who": who, "false_green": 0}, indent=2),
        encoding="utf-8",
    )
    return STOP_FLAG


def clear_authorized_stop() -> None:
    try:
        if STOP_FLAG.is_file():
            STOP_FLAG.unlink()
    except Exception:
        pass


def is_authorized_stop() -> bool:
    return STOP_FLAG.is_file()


def _whisper(msg: str) -> None:
    try:
        _state_dir()
        with WHISPER_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{_utc()} {msg}\n")
    except Exception:
        pass


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
            out = (r.stdout or "") + (r.stderr or "")
            return str(pid) in out and "No tasks" not in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_pid(path: Path) -> int:
    try:
        if path.is_file():
            return int(path.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        pass
    return 0


def _port_health(host: str = "127.0.0.1", port: int = 11450, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:
        return False


def kill_auth_meta() -> dict[str, Any]:
    """Public kill surface — never includes token value."""
    return {
        "user_only": True,
        "boss_or_grok_only": True,
        "cli": 'python -m drone super-kernel kill',
        "http": "POST /kill with X-Super-Kernel-Token (from kill.token file)",
        "token_file": str(TOKEN_FILE),
        "token_printed": False,
        "unauthorized_kill": "watchdog restarts process unless authorized_stop.flag set",
        "false_green": 0,
    }


# ── VRAM ──────────────────────────────────────────────────────


def probe_vram(gpu_index: int = 0) -> dict[str, Any]:
    """Live nvidia-smi VRAM. RED fields if probe fails (never invent free)."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                f"--id={int(gpu_index)}",
                "--query-gpu=index,name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {
                "ok": False,
                "error": (r.stderr or "nvidia-smi empty").strip()[:300],
                "false_green": 0,
            }
        line = r.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        total = int(float(parts[2]))
        used = int(float(parts[3]))
        free = int(float(parts[4]))
        return {
            "ok": True,
            "index": int(float(parts[0])),
            "name": parts[1],
            "total_mib": total,
            "used_mib": used,
            "free_mib": free,
            "utc": _utc(),
            "false_green": 0,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "false_green": 0}


def probe_ram() -> dict[str, Any]:
    """
    Live system RAM (host RAM power). Windows: GlobalMemoryStatusEx via ctypes.
    Never invent free RAM — RED if probe fails.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return {"ok": False, "error": "GlobalMemoryStatusEx failed", "false_green": 0}
            total = int(stat.ullTotalPhys // (1024 * 1024))
            free = int(stat.ullAvailPhys // (1024 * 1024))
            used = max(0, total - free)
            return {
                "ok": True,
                "total_mib": total,
                "free_mib": free,
                "used_mib": used,
                "load_pct": int(stat.dwMemoryLoad),
                "pagefile_avail_mib": int(stat.ullAvailPageFile // (1024 * 1024)),
                "utc": _utc(),
                "false_green": 0,
            }
        # POSIX fallback
        if hasattr(os, "sysconf"):
            page = os.sysconf("SC_PAGE_SIZE")
            total = int(os.sysconf("SC_PHYS_PAGES") * page // (1024 * 1024))
            # free not always available
            free = total
            try:
                with open("/proc/meminfo", encoding="utf-8") as f:
                    info = f.read()
                for line in info.splitlines():
                    if line.startswith("MemAvailable:"):
                        free = int(line.split()[1]) // 1024
                        break
            except Exception:
                pass
            return {
                "ok": True,
                "total_mib": total,
                "free_mib": free,
                "used_mib": max(0, total - free),
                "utc": _utc(),
                "false_green": 0,
            }
        return {"ok": False, "error": "no ram probe", "false_green": 0}
    except Exception as e:
        return {"ok": False, "error": str(e), "false_green": 0}


# Hold small stability buffers so GC does not drop power rail
_RAM_POWER_BUFFERS: list[bytearray] = []
_RAM_POWER_STATE: dict[str, Any] = {
    "enabled": False,
    "priority": None,
    "working_set": None,
    "buffer_mib": 0,
    "applied_utc": None,
    "last_pulse_utc": None,
    "pulses": 0,
    "errors": [],
}


def _win_process_handle():
    """Open real process handle (pseudo-handle + wrong ctypes restype → ERROR_INVALID_HANDLE)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    # PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA
    access = 0x0200 | 0x0400 | 0x0100
    h = k32.OpenProcess(access, False, int(os.getpid()))
    return k32, h


def apply_ram_power(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    RAM POWER — keep Super Kernel process stable on host RAM.

    - Raise process priority (HIGH)
    - Hold working-set floor so Windows is less eager to page us out
    - Optional small stability buffer kept resident in process
    Does NOT kill the server. false_green: 0
    """
    global _RAM_POWER_BUFFERS, _RAM_POWER_STATE
    rp = dict((cfg or {}).get("ram_power") or {})
    if not rp.get("enabled", True):
        return {
            "status": "SKIP",
            "enabled": False,
            "false_green": 0,
            "utc": _utc(),
        }

    errors: list[str] = []
    priority_set = None
    ws_set = None
    buf_mib = int(rp.get("stability_buffer_mib") or 0)

    # 1) Process priority
    prio_name = str(rp.get("process_priority") or "high").lower()
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            classes = {
                "normal": 0x00000020,
                "above_normal": 0x00008000,
                "high": 0x00000080,
                "realtime": 0x00000100,
            }
            if prio_name == "realtime" and not os.environ.get("SUPER_KERNEL_REALTIME"):
                prio_name = "high"
            cls = classes.get(prio_name, 0x00000080)
            k32, handle = _win_process_handle()
            if handle:
                k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                k32.SetPriorityClass.restype = wintypes.BOOL
                ok = bool(k32.SetPriorityClass(handle, cls))
                if not ok:
                    # PowerShell fallback
                    r = subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            f"(Get-Process -Id {os.getpid()}).PriorityClass='High'; 'OK'",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    ok = "OK" in (r.stdout or "")
                priority_set = ok
                if not ok:
                    errors.append(f"SetPriorityClass err={ctypes.get_last_error()}")
                k32.CloseHandle(handle)
            else:
                priority_set = False
                errors.append(f"OpenProcess err={ctypes.get_last_error()}")
        else:
            try:
                os.nice(-5)
                priority_set = True
            except Exception as e:
                errors.append(f"nice: {e}")
                priority_set = False
    except Exception as e:
        errors.append(f"priority: {e}")
        priority_set = False

    # 2) Working set hold
    if rp.get("hold_working_set", True) and sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            k32, handle = _win_process_handle()
            min_mib = int(rp.get("min_working_set_mib") or 256)
            max_mib = int(rp.get("max_working_set_mib") or 0)
            min_bytes = max(1, min_mib) * 1024 * 1024
            if max_mib and max_mib > min_mib:
                max_bytes = max_mib * 1024 * 1024
            else:
                max_bytes = max(min_bytes * 4, 2 * 1024 * 1024 * 1024)
            min_sz = ctypes.c_size_t(min_bytes)
            max_sz = ctypes.c_size_t(max_bytes)
            if handle:
                k32.SetProcessWorkingSetSizeEx.argtypes = [
                    wintypes.HANDLE,
                    ctypes.c_size_t,
                    ctypes.c_size_t,
                    wintypes.DWORD,
                ]
                k32.SetProcessWorkingSetSizeEx.restype = wintypes.BOOL
                ok = bool(k32.SetProcessWorkingSetSizeEx(handle, min_sz, max_sz, 0))
                if not ok:
                    k32.SetProcessWorkingSetSize.argtypes = [
                        wintypes.HANDLE,
                        ctypes.c_size_t,
                        ctypes.c_size_t,
                    ]
                    k32.SetProcessWorkingSetSize.restype = wintypes.BOOL
                    ok = bool(k32.SetProcessWorkingSetSize(handle, min_sz, max_sz))
                ws_set = ok
                if not ok:
                    errors.append(f"SetProcessWorkingSetSize err={ctypes.get_last_error()}")
                k32.CloseHandle(handle)
            else:
                ws_set = False
        except Exception as e:
            errors.append(f"working_set: {e}")
            ws_set = False

    # 3) Stability buffer — small resident allocation
    if buf_mib > 0:
        try:
            # cap buffer to 512 MiB for safety
            buf_mib = min(buf_mib, 512)
            # free old
            _RAM_POWER_BUFFERS.clear()
            chunk = 16 * 1024 * 1024  # 16 MiB slabs
            need = buf_mib * 1024 * 1024
            held = 0
            while held < need:
                n = min(chunk, need - held)
                b = bytearray(n)
                # touch pages so they are committed
                for i in range(0, n, 4096):
                    b[i] = 1
                _RAM_POWER_BUFFERS.append(b)
                held += n
        except Exception as e:
            errors.append(f"buffer: {e}")
            buf_mib = 0

    ram = probe_ram()
    _RAM_POWER_STATE = {
        "enabled": True,
        "priority": prio_name if priority_set else None,
        "priority_ok": priority_set,
        "working_set_ok": ws_set,
        "buffer_mib": buf_mib,
        "applied_utc": _utc(),
        "last_pulse_utc": _utc(),
        "pulses": int(_RAM_POWER_STATE.get("pulses") or 0),
        "errors": errors[:8],
        "ram": ram,
        "false_green": 0,
    }
    _whisper(
        f"ram_power applied prio={priority_set} ws={ws_set} buf={buf_mib} "
        f"free_ram={ram.get('free_mib')}"
    )
    return {
        "status": "GREEN" if priority_set or ws_set or buf_mib else "PARTIAL",
        "false_green": 0,
        "utc": _utc(),
        "ram_power": dict(_RAM_POWER_STATE),
        "server_killed": False,
        "server_pid": os.getpid(),
    }


def ram_power_pulse(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Idler pulse: re-touch stability buffers + reassert priority if dropped."""
    global _RAM_POWER_STATE
    rp = dict((cfg or {}).get("ram_power") or {})
    if not rp.get("enabled", True):
        return {"ok": False, "skipped": True, "false_green": 0}
    # re-touch buffers
    touched = 0
    try:
        for b in _RAM_POWER_BUFFERS:
            n = len(b)
            for i in range(0, n, 4096):
                b[i] = (b[i] + 1) & 0xFF
            touched += n
    except Exception:
        pass
    # reassert priority lightly
    try:
        if sys.platform == "win32" and rp.get("process_priority", "high"):
            import ctypes

            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, 0x00000080)
    except Exception:
        pass
    ram = probe_ram()
    _RAM_POWER_STATE["last_pulse_utc"] = _utc()
    _RAM_POWER_STATE["pulses"] = int(_RAM_POWER_STATE.get("pulses") or 0) + 1
    _RAM_POWER_STATE["ram"] = ram
    return {
        "ok": True,
        "false_green": 0,
        "touched_bytes": touched,
        "pulses": _RAM_POWER_STATE["pulses"],
        "ram": ram,
        "utc": _utc(),
    }


def ram_power_status() -> dict[str, Any]:
    ram = probe_ram()
    return {
        "schema": "drone.super_kernel.ram_power.v1",
        "false_green": 0,
        "utc": _utc(),
        "state": dict(_RAM_POWER_STATE),
        "ram": ram,
        "server_pid": os.getpid(),
        "server_killed": False,
    }


# ── CPU POWER ─────────────────────────────────────────────────

_CPU_POWER_STATE: dict[str, Any] = {
    "enabled": False,
    "priority": None,
    "affinity_mask": None,
    "thread_priority": None,
    "applied_utc": None,
    "last_pulse_utc": None,
    "pulses": 0,
    "burn_ms": 0,
    "errors": [],
}


def probe_cpu() -> dict[str, Any]:
    """Live CPU inventory + load (best effort). Never invent core counts."""
    out: dict[str, Any] = {
        "ok": False,
        "logical": None,
        "load_pct": None,
        "utc": _utc(),
        "false_green": 0,
    }
    try:
        out["logical"] = os.cpu_count()
        if sys.platform == "win32":
            # WMI via PowerShell — light, no deps
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$c=Get-CimInstance Win32_Processor; "
                        "[pscustomobject]@{"
                        "Name=($c|Select -First 1 -Expand Name); "
                        "Cores=($c|Measure-Object NumberOfCores -Sum).Sum; "
                        "Logical=($c|Measure-Object NumberOfLogicalProcessors -Sum).Sum; "
                        "Load=($c|Measure-Object LoadPercentage -Average).Average"
                        "} | ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=12,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if r.returncode == 0 and (r.stdout or "").strip():
                data = json.loads(r.stdout.strip())
                out["ok"] = True
                out["name"] = data.get("Name")
                out["cores"] = int(data.get("Cores") or 0) or None
                out["logical"] = int(data.get("Logical") or out["logical"] or 0) or out["logical"]
                try:
                    out["load_pct"] = round(float(data.get("Load") or 0), 1)
                except Exception:
                    out["load_pct"] = None
            else:
                out["ok"] = bool(out["logical"])
                out["error"] = (r.stderr or "wmi empty")[:200] if not out["ok"] else None
        else:
            out["ok"] = bool(out["logical"])
            try:
                load1, _, _ = os.getloadavg()
                logical = out["logical"] or 1
                out["load_pct"] = round(min(100.0, (load1 / logical) * 100.0), 1)
                out["loadavg_1"] = load1
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)
    return out


def _parse_affinity_mask(spec: Any, logical: int | None) -> int | None:
    """
    affinity: null/all → all CPUs
              "0-7" → mask bits 0..7
              "0,2,4" → those CPUs
              int mask → as-is
    """
    if spec is None or spec == "" or str(spec).lower() in {"all", "*"}:
        return None
    if isinstance(spec, int):
        return int(spec)
    s = str(spec).strip()
    if s.isdigit():
        return int(s)
    bits: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
                for i in range(lo, hi + 1):
                    if logical is None or 0 <= i < logical:
                        bits.add(i)
            except Exception:
                continue
        else:
            try:
                i = int(part)
                if logical is None or 0 <= i < logical:
                    bits.add(i)
            except Exception:
                continue
    if not bits:
        return None
    mask = 0
    for i in bits:
        mask |= 1 << i
    return mask if mask else None


def apply_cpu_power(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    CPU POWER — keep Super Kernel process snappy and sticky on CPU.

    - Process priority class (HIGH default; not REALTIME unless forced)
    - Optional CPU affinity mask (pin cores)
    - Main-thread priority boost
    - Does NOT kill server. false_green: 0
    """
    global _CPU_POWER_STATE
    cp = dict((cfg or {}).get("cpu_power") or {})
    if not cp.get("enabled", True):
        return {
            "status": "SKIP",
            "enabled": False,
            "false_green": 0,
            "utc": _utc(),
        }

    errors: list[str] = []
    prio_name = str(cp.get("process_priority") or "high").lower()
    priority_ok = False
    affinity_ok = None
    affinity_mask: int | None = None
    thread_ok = None
    cpu = probe_cpu()
    logical = cpu.get("logical") or os.cpu_count()

    # 1) Process priority
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            classes = {
                "idle": 0x00000040,
                "below_normal": 0x00004000,
                "normal": 0x00000020,
                "above_normal": 0x00008000,
                "high": 0x00000080,
                "realtime": 0x00000100,
            }
            if prio_name == "realtime" and not os.environ.get("SUPER_KERNEL_REALTIME"):
                prio_name = "high"
                errors.append("realtime demoted to high (set SUPER_KERNEL_REALTIME=1 to force)")
            cls = classes.get(prio_name, 0x00000080)
            k32, handle = _win_process_handle()
            if not handle:
                errors.append(f"OpenProcess err={ctypes.get_last_error()}")
            else:
                k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                k32.SetPriorityClass.restype = wintypes.BOOL
                priority_ok = bool(k32.SetPriorityClass(handle, cls))
                if not priority_ok:
                    errors.append(f"SetPriorityClass err={ctypes.get_last_error()}")
                # also try psutil-free: PowerShell fallback
                if not priority_ok:
                    try:
                        r = subprocess.run(
                            [
                                "powershell",
                                "-NoProfile",
                                "-Command",
                                f"(Get-Process -Id {os.getpid()}).PriorityClass='High'; 'OK'",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            creationflags=CREATE_NO_WINDOW,
                        )
                        if "OK" in (r.stdout or ""):
                            priority_ok = True
                            if errors:
                                errors.append("SetPriorityClass recovered via PowerShell")
                    except Exception as e:
                        errors.append(f"ps_priority: {e}")
                k32.CloseHandle(handle)
        else:
            try:
                os.nice(int(cp.get("nice") or -5))
                priority_ok = True
            except Exception as e:
                errors.append(f"nice: {e}")
    except Exception as e:
        errors.append(f"priority: {e}")

    # 2) Affinity
    try:
        affinity_mask = _parse_affinity_mask(cp.get("affinity"), int(logical) if logical else None)
        # default: leave all CPUs (None) — full power
        if affinity_mask is not None and sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k32, handle = _win_process_handle()
            if handle:
                k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
                k32.SetProcessAffinityMask.restype = wintypes.BOOL
                affinity_ok = bool(
                    k32.SetProcessAffinityMask(handle, ctypes.c_size_t(affinity_mask))
                )
                if not affinity_ok:
                    errors.append(f"SetProcessAffinityMask err={ctypes.get_last_error()}")
                k32.CloseHandle(handle)
            else:
                affinity_ok = False
                errors.append("OpenProcess failed for affinity")
        elif affinity_mask is not None and hasattr(os, "sched_setaffinity"):
            cpus = [i for i in range(64) if affinity_mask & (1 << i)]
            os.sched_setaffinity(0, cpus)  # type: ignore[attr-defined]
            affinity_ok = True
        else:
            affinity_ok = None  # all CPUs = full power
    except Exception as e:
        errors.append(f"affinity: {e}")
        affinity_ok = False

    # 3) Thread priority (main thread)
    try:
        if sys.platform == "win32" and cp.get("boost_thread", True):
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentThread.restype = wintypes.HANDLE
            k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
            k32.SetThreadPriority.restype = wintypes.BOOL
            thr = k32.GetCurrentThread()
            tp = int(cp.get("thread_priority") or 2)
            tp = max(-15, min(15, tp))
            thread_ok = bool(k32.SetThreadPriority(thr, tp))
            if not thread_ok:
                # thread pseudo-handle often works when restype is set; fallback ignore
                errors.append(f"SetThreadPriority err={ctypes.get_last_error()}")
    except Exception as e:
        errors.append(f"thread: {e}")
        thread_ok = False

    _CPU_POWER_STATE = {
        "enabled": True,
        "priority": prio_name if priority_ok else None,
        "priority_ok": priority_ok,
        "affinity_mask": affinity_mask,
        "affinity_ok": affinity_ok,
        "thread_priority_ok": thread_ok,
        "logical_cpus": logical,
        "applied_utc": _utc(),
        "last_pulse_utc": _utc(),
        "pulses": int(_CPU_POWER_STATE.get("pulses") or 0),
        "burn_ms": int(_CPU_POWER_STATE.get("burn_ms") or 0),
        "errors": errors[:8],
        "cpu": cpu,
        "false_green": 0,
    }
    _whisper(
        f"cpu_power applied prio={priority_ok}/{prio_name} "
        f"affinity={affinity_mask} thread={thread_ok} logical={logical}"
    )
    return {
        "status": "GREEN" if priority_ok else "PARTIAL",
        "false_green": 0,
        "utc": _utc(),
        "cpu_power": dict(_CPU_POWER_STATE),
        "server_killed": False,
        "server_pid": os.getpid(),
    }


def cpu_power_pulse(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Idler / serve pulse: reassert CPU priority + optional micro burn to keep core awake.
    Burn is capped (default 2ms) — keeps scheduler aware without thrashing.
    """
    global _CPU_POWER_STATE
    cp = dict((cfg or {}).get("cpu_power") or {})
    if not cp.get("enabled", True):
        return {"ok": False, "skipped": True, "false_green": 0}

    # reassert HIGH priority
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k32, handle = _win_process_handle()
            if handle:
                prio_name = str(cp.get("process_priority") or "high").lower()
                classes = {
                    "normal": 0x00000020,
                    "above_normal": 0x00008000,
                    "high": 0x00000080,
                }
                cls = classes.get(prio_name, 0x00000080)
                k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                k32.SetPriorityClass.restype = wintypes.BOOL
                k32.SetPriorityClass(handle, cls)
                mask = _CPU_POWER_STATE.get("affinity_mask")
                if mask:
                    k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
                    k32.SetProcessAffinityMask.restype = wintypes.BOOL
                    k32.SetProcessAffinityMask(handle, ctypes.c_size_t(int(mask)))
                k32.CloseHandle(handle)
            if cp.get("boost_thread", True):
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.GetCurrentThread.restype = wintypes.HANDLE
                k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
                k32.SetThreadPriority.restype = wintypes.BOOL
                thr = k32.GetCurrentThread()
                k32.SetThreadPriority(thr, int(cp.get("thread_priority") or 2))
    except Exception:
        pass

    burn_ms = int(cp.get("pulse_burn_ms") or 2)
    burn_ms = max(0, min(burn_ms, 20))  # hard cap 20ms
    t0 = time.perf_counter()
    if burn_ms > 0:
        # tiny compute spin — keeps a core out of deep idle if configured
        end = t0 + (burn_ms / 1000.0)
        x = 0
        while time.perf_counter() < end:
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        _ = x
    elapsed = round((time.perf_counter() - t0) * 1000, 2)

    cpu = probe_cpu()
    _CPU_POWER_STATE["last_pulse_utc"] = _utc()
    _CPU_POWER_STATE["pulses"] = int(_CPU_POWER_STATE.get("pulses") or 0) + 1
    _CPU_POWER_STATE["burn_ms"] = int(_CPU_POWER_STATE.get("burn_ms") or 0) + burn_ms
    _CPU_POWER_STATE["cpu"] = cpu
    return {
        "ok": True,
        "false_green": 0,
        "pulses": _CPU_POWER_STATE["pulses"],
        "burn_ms_this": elapsed,
        "cpu": cpu,
        "utc": _utc(),
    }


def cpu_power_status() -> dict[str, Any]:
    cpu = probe_cpu()
    return {
        "schema": "drone.super_kernel.cpu_power.v1",
        "false_green": 0,
        "utc": _utc(),
        "state": dict(_CPU_POWER_STATE),
        "cpu": cpu,
        "server_pid": os.getpid(),
        "server_killed": False,
    }


def apply_host_power(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply RAM + CPU power rails together (serve/start). No kill."""
    ram = apply_ram_power(cfg)
    cpu = apply_cpu_power(cfg)
    ok = ram.get("status") in {"GREEN", "PARTIAL", "SKIP"} and cpu.get("status") in {
        "GREEN",
        "PARTIAL",
        "SKIP",
    }
    return {
        "status": "GREEN" if ok and cpu.get("status") == "GREEN" else "PARTIAL",
        "false_green": 0,
        "utc": _utc(),
        "ram_power": ram,
        "cpu_power": cpu,
        "server_killed": False,
        "server_pid": os.getpid(),
    }


# ── Kernel state ──────────────────────────────────────────────


class SuperKernel:
    """
    In-process super lane: instance registry + VRAM gate + Ollama proxy.
    Thread-safe. Unlimited instances until VRAM refuses.
    """

    def __init__(self, root: Path | None = None, cfg: dict[str, Any] | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "super_kernel.json"
        self.cfg = cfg or self._load_cfg()
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._req_lock = threading.Semaphore(
            int(os.environ.get("SUPER_KERNEL_PARALLEL", "32") or "32")
        )
        # instance_id -> meta
        self.instances: dict[str, dict[str, Any]] = {}
        # live connection_id -> meta (survives model hot-swap; server stays up)
        self.connections: dict[str, dict[str, Any]] = {}
        # default active model for requests that omit model/conn
        self.active_model: str | None = None
        self._started_utc = _utc()
        self._req_count = 0
        self._refuse_count = 0
        self._serve_count = 0
        self._swap_count = 0
        self._connect_count = 0
        self._http: ThreadingHTTPServer | None = None
        self._cfg_mtime: float = 0.0
        try:
            if self.cfg_path.is_file():
                self._cfg_mtime = self.cfg_path.stat().st_mtime
        except Exception:
            pass

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {
            "host": "127.0.0.1",
            "port": 11450,
            "ollama_host": "http://127.0.0.1:11434",
            "limits": {"headroom_mib": 384, "max_models": None, "max_instances": None},
            "defaults": {"num_predict": 256, "temperature": 0.2, "timeout_s": 180},
            "est_vram_mib": {},
            "gpu": {"index": 0, "vram_mib": 12288},
        }

    def ollama_host(self) -> str:
        return (
            os.environ.get("OLLAMA_HOST")
            or self.cfg.get("ollama_host")
            or "http://127.0.0.1:11434"
        ).rstrip("/")

    def headroom_mib(self) -> int:
        lim = self.cfg.get("limits") or {}
        return int(lim.get("headroom_mib") or 384)

    # ── Ollama HTTP ───────────────────────────────────────────

    def _ollama_json(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        timeout: float = 180,
    ) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.ollama_host()}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def list_models(self) -> list[dict[str, Any]]:
        try:
            data = self._ollama_json("GET", "/api/tags", timeout=10)
        except Exception as e:
            return [{"error": str(e), "ok": False}]
        out = []
        for m in data.get("models") or []:
            name = m.get("name") or ""
            size = int(m.get("size") or 0)
            out.append(
                {
                    "name": name,
                    "size_bytes": size,
                    "size_mib": round(size / (1024 * 1024), 1) if size else 0,
                    "parameter_size": (m.get("details") or {}).get("parameter_size"),
                    "family": (m.get("details") or {}).get("family"),
                    "quantization": (m.get("details") or {}).get("quantization_level"),
                    "est_vram_mib": self.estimate_vram_mib(name, size_bytes=size),
                    "cloud": str(name).endswith(":cloud") or size < 1024,
                }
            )
        return out

    def list_loaded(self) -> list[dict[str, Any]]:
        try:
            data = self._ollama_json("GET", "/api/ps", timeout=5)
            rows = []
            for m in data.get("models") or []:
                rows.append(
                    {
                        "name": m.get("name"),
                        "size_vram": m.get("size_vram") or m.get("size"),
                        "expires_at": m.get("expires_at"),
                    }
                )
            return rows
        except Exception:
            return []

    def estimate_vram_mib(self, model: str, size_bytes: int = 0) -> int:
        """Heuristic VRAM need. Cloud/zero-size → 0 local VRAM."""
        table = dict(self.cfg.get("est_vram_mib") or {})
        if model in table:
            return int(table[model])
        base = model.split(":")[0]
        for k, v in table.items():
            if k.split(":")[0] == base:
                return int(v)
        if str(model).endswith(":cloud"):
            return 0
        if size_bytes and size_bytes < 1024:
            return 0
        # weights on disk ~ Q4; VRAM roughly size_mib * 1.15 + 400 overhead
        if size_bytes > 0:
            size_mib = size_bytes / (1024 * 1024)
            return int(size_mib * 1.15 + 400)
        # param parse e.g. 7B / 8b / 12B
        m = re.search(r"(\d+(?:\.\d+)?)\s*[bB]", model)
        if m:
            b = float(m.group(1))
            # rough Q4: ~0.65 GiB per B + 0.5
            return int(b * 700 + 500)
        return 4500  # unknown full model — conservative default

    # ── VRAM admission (only limit) ───────────────────────────

    def can_hang(self, model: str, *, force: bool = False) -> dict[str, Any]:
        """
        True when free VRAM (after headroom) can hang this model,
        or model is cloud/0-VRAM, or force=True.
        No max-instance count check.
        """
        models = {m.get("name"): m for m in self.list_models() if m.get("name")}
        meta = models.get(model) or {}
        need = self.estimate_vram_mib(model, size_bytes=int(meta.get("size_bytes") or 0))
        if need <= 0:
            return {
                "ok": True,
                "model": model,
                "need_mib": 0,
                "reason": "cloud_or_zero_vram",
                "false_green": 0,
            }

        # already loaded → hang free
        loaded_names = {x.get("name") for x in self.list_loaded()}
        if model in loaded_names or any(
            str(n).startswith(model.split(":")[0] + ":") for n in loaded_names if n
        ):
            return {
                "ok": True,
                "model": model,
                "need_mib": need,
                "reason": "already_loaded",
                "loaded": sorted(x for x in loaded_names if x),
                "false_green": 0,
            }

        vram = probe_vram(int((self.cfg.get("gpu") or {}).get("index") or 0))
        if not vram.get("ok"):
            # Cannot verify VRAM — refuse unless force (honesty)
            if force:
                return {
                    "ok": True,
                    "model": model,
                    "need_mib": need,
                    "reason": "force_no_vram_probe",
                    "vram": vram,
                    "false_green": 0,
                }
            return {
                "ok": False,
                "model": model,
                "need_mib": need,
                "reason": "vram_probe_failed",
                "vram": vram,
                "false_green": 0,
            }

        free = int(vram.get("free_mib") or 0)
        head = self.headroom_mib()
        usable = free - head
        if usable >= need:
            return {
                "ok": True,
                "model": model,
                "need_mib": need,
                "free_mib": free,
                "usable_mib": usable,
                "headroom_mib": head,
                "reason": "vram_ok",
                "false_green": 0,
            }

        # try LRU eviction of kernel-tracked instances (unload via keep_alive=0)
        lim = self.cfg.get("limits") or {}
        if lim.get("evict_lru_on_pressure", True):
            freed = self._evict_until(need + head)
            vram2 = probe_vram(int((self.cfg.get("gpu") or {}).get("index") or 0))
            free2 = int(vram2.get("free_mib") or 0) if vram2.get("ok") else free
            usable2 = free2 - head
            if usable2 >= need:
                return {
                    "ok": True,
                    "model": model,
                    "need_mib": need,
                    "free_mib": free2,
                    "usable_mib": usable2,
                    "evicted": freed,
                    "reason": "vram_ok_after_evict",
                    "false_green": 0,
                }

        if force:
            return {
                "ok": True,
                "model": model,
                "need_mib": need,
                "free_mib": free,
                "usable_mib": usable,
                "reason": "force_override",
                "false_green": 0,
            }

        return {
            "ok": False,
            "model": model,
            "need_mib": need,
            "free_mib": free,
            "usable_mib": usable,
            "headroom_mib": head,
            "reason": "vram_cannot_hang",
            "false_green": 0,
            "hint": f"need ~{need} MiB usable; have {usable} MiB (free {free} - headroom {head})",
        }

    def _evict_until(self, need_free_usable: int) -> list[str]:
        """Unload LRU instances until usable free may cover need. Best-effort."""
        freed: list[str] = []
        with self._lock:
            ordered = sorted(
                self.instances.values(),
                key=lambda x: float(x.get("last_used_ts") or 0),
            )
        for inst in ordered:
            model = inst.get("model")
            if not model:
                continue
            # don't evict 0-vram cloud
            if self.estimate_vram_mib(str(model)) <= 0:
                continue
            try:
                self._unload_model(str(model))
                freed.append(str(model))
            except Exception:
                pass
            vram = probe_vram(int((self.cfg.get("gpu") or {}).get("index") or 0))
            if vram.get("ok"):
                free = int(vram["free_mib"])
                if free - self.headroom_mib() >= need_free_usable - self.headroom_mib():
                    # approximate stop
                    if free >= need_free_usable:
                        break
        return freed

    def _unload_model(self, model: str) -> None:
        """Ask Ollama to drop weights (keep_alive=0)."""
        try:
            body = {
                "model": model,
                "prompt": "",
                "keep_alive": 0,
                "stream": False,
                "options": {"num_predict": 1},
            }
            self._ollama_json("POST", "/api/generate", body, timeout=30)
        except Exception:
            pass

    # ── Instances (unlimited count) ───────────────────────────

    def open_instance(
        self,
        model: str,
        *,
        force: bool = False,
        label: str = "",
        keep_alive: str | None = None,
    ) -> dict[str, Any]:
        model = (model or "").strip()
        if not model:
            return {"status": "RED", "false_green": 0, "error": "model required"}

        # optional artificial limits must stay null — enforce honesty
        lim = self.cfg.get("limits") or {}
        max_i = lim.get("max_instances")
        max_m = lim.get("max_models")
        if max_i is not None or max_m is not None:
            # config should be null; if someone sets a number, still only VRAM is law
            # we ignore artificial caps per product law
            pass

        gate = self.can_hang(model, force=force)
        if not gate.get("ok"):
            with self._lock:
                self._refuse_count += 1
            return {
                "status": "RED",
                "false_green": 0,
                "error": "vram_cannot_hang",
                "gate": gate,
                "utc": _utc(),
            }

        iid = f"inst-{uuid.uuid4().hex[:12]}"
        ka = keep_alive or (self.cfg.get("defaults") or {}).get("keep_alive") or "30m"
        row = {
            "id": iid,
            "model": model,
            "label": label or model,
            "opened_utc": _utc(),
            "last_used_ts": time.time(),
            "last_used_utc": _utc(),
            "keep_alive": ka,
            "requests": 0,
            "est_vram_mib": gate.get("need_mib"),
            "gate_reason": gate.get("reason"),
            "status": "OPEN",
        }
        with self._lock:
            self.instances[iid] = row
        # warm touch (non-fatal)
        try:
            self._ollama_json(
                "POST",
                "/api/chat",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "keep_alive": ka,
                    "options": {"num_predict": 4, "temperature": 0},
                },
                timeout=120,
            )
            row["warm"] = True
        except Exception as e:
            row["warm"] = False
            row["warm_error"] = str(e)[:200]

        return {
            "status": "GREEN",
            "false_green": 0,
            "instance": row,
            "gate": gate,
            "utc": _utc(),
        }

    def close_instance(self, instance_id: str, *, unload: bool = False) -> dict[str, Any]:
        with self._lock:
            row = self.instances.pop(instance_id, None)
        if not row:
            return {
                "status": "RED",
                "false_green": 0,
                "error": f"unknown instance: {instance_id}",
            }
        if unload and row.get("model"):
            try:
                self._unload_model(str(row["model"]))
            except Exception:
                pass
        return {
            "status": "GREEN",
            "false_green": 0,
            "closed": row,
            "utc": _utc(),
            "server_killed": False,
        }

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self.instances[instance_id]) if instance_id in self.instances else None

    def list_instances(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self.instances.values()]

    # ── Live connections (no server kill) ─────────────────────

    def connect(
        self,
        model: str | None = None,
        *,
        client: str = "",
        label: str = "",
        force: bool = False,
        keep_alive: str | None = None,
        open_instance: bool = True,
    ) -> dict[str, Any]:
        """
        Open a live connection handle. Server process stays up.
        Connection survives model hot-swap under the same connection_id.
        """
        model = (model or self.active_model or "").strip()
        if not model:
            # pick first safe installed if none
            models = [m.get("name") for m in self.list_models() if m.get("name") and not m.get("cloud")]
            prefer = ["llama3.2:3b", "qwen2.5:3b", "llama3.1:8b"]
            for p in prefer:
                if p in models:
                    model = p
                    break
            if not model and models:
                model = str(models[0])
        if not model:
            return {"status": "RED", "false_green": 0, "error": "no model available"}

        gate = self.can_hang(model, force=force)
        if not gate.get("ok"):
            with self._lock:
                self._refuse_count += 1
            return {
                "status": "RED",
                "false_green": 0,
                "error": "vram_cannot_hang",
                "gate": gate,
                "server_killed": False,
            }

        cid = f"conn-{uuid.uuid4().hex[:12]}"
        inst_id = None
        if open_instance:
            opened = self.open_instance(
                model, force=force, label=label or client or model, keep_alive=keep_alive
            )
            if opened.get("status") == "GREEN":
                inst_id = (opened.get("instance") or {}).get("id")
            elif not force:
                return {
                    **opened,
                    "server_killed": False,
                    "note": "connection not created — instance open failed",
                }

        ka = keep_alive or (self.cfg.get("defaults") or {}).get("keep_alive") or "30m"
        row = {
            "id": cid,
            "model": model,
            "client": client or "anon",
            "label": label or client or model,
            "instance_id": inst_id,
            "connected_utc": _utc(),
            "last_used_ts": time.time(),
            "last_used_utc": _utc(),
            "keep_alive": ka,
            "requests": 0,
            "swaps": 0,
            "status": "LIVE",
            "history_turns": 0,
        }
        with self._lock:
            self.connections[cid] = row
            self._connect_count += 1
            if not self.active_model:
                self.active_model = model
        _whisper(f"connect {cid} model={model} client={client}")
        return {
            "status": "GREEN",
            "false_green": 0,
            "connection": dict(row),
            "gate": gate,
            "server_killed": False,
            "server_pid": os.getpid(),
            "utc": _utc(),
        }

    def disconnect(
        self,
        connection_id: str,
        *,
        unload: bool = False,
        close_instance: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            row = self.connections.pop(connection_id, None)
        if not row:
            return {
                "status": "RED",
                "false_green": 0,
                "error": f"unknown connection: {connection_id}",
                "server_killed": False,
            }
        inst_id = row.get("instance_id")
        if close_instance and inst_id:
            self.close_instance(str(inst_id), unload=unload)
        elif unload and row.get("model"):
            try:
                self._unload_model(str(row["model"]))
            except Exception:
                pass
        return {
            "status": "GREEN",
            "false_green": 0,
            "disconnected": row,
            "server_killed": False,
            "utc": _utc(),
        }

    def list_connections(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self.connections.values()]

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self.connections[connection_id]) if connection_id in self.connections else None

    def touch_connection(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connections.get(connection_id)
            if not row:
                return {"status": "RED", "false_green": 0, "error": "unknown connection"}
            row["last_used_ts"] = time.time()
            row["last_used_utc"] = _utc()
            return {
                "status": "GREEN",
                "false_green": 0,
                "connection": dict(row),
                "server_killed": False,
            }

    # ── Hot swap (never kills server) ─────────────────────────

    def hot_swap(
        self,
        to_model: str,
        *,
        connection_id: str | None = None,
        instance_id: str | None = None,
        unload_from: bool = True,
        force: bool = False,
        warm: bool = True,
        set_active: bool = False,
    ) -> dict[str, Any]:
        """
        Swap model on a live connection and/or instance WITHOUT killing the server.
        Same connection_id / instance_id retained. Only VRAM may refuse.
        """
        t0 = time.perf_counter()
        to_model = (to_model or "").strip()
        if not to_model:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "to_model required",
                "server_killed": False,
            }

        from_model: str | None = None
        conn: dict[str, Any] | None = None
        inst: dict[str, Any] | None = None

        if connection_id:
            conn = self.get_connection(connection_id)
            if not conn:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"unknown connection: {connection_id}",
                    "server_killed": False,
                }
            from_model = str(conn.get("model") or "")
            instance_id = instance_id or conn.get("instance_id")

        if instance_id:
            inst = self.get_instance(str(instance_id))
            if not inst and not conn:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"unknown instance: {instance_id}",
                    "server_killed": False,
                }
            if inst:
                from_model = from_model or str(inst.get("model") or "")

        if not connection_id and not instance_id:
            # global active-model hot swap
            from_model = self.active_model
            set_active = True

        if from_model == to_model:
            return {
                "status": "GREEN",
                "false_green": 0,
                "noop": True,
                "model": to_model,
                "connection_id": connection_id,
                "instance_id": instance_id,
                "server_killed": False,
                "server_pid": os.getpid(),
                "utc": _utc(),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        # VRAM admit new model (may evict LRU including old)
        gate = self.can_hang(to_model, force=force)
        if not gate.get("ok") and unload_from and from_model:
            # free the from model first, then re-check
            try:
                self._unload_model(str(from_model))
            except Exception:
                pass
            gate = self.can_hang(to_model, force=force)

        if not gate.get("ok"):
            with self._lock:
                self._refuse_count += 1
            return {
                "status": "RED",
                "false_green": 0,
                "error": "vram_cannot_hang",
                "from_model": from_model,
                "to_model": to_model,
                "gate": gate,
                "server_killed": False,
                "server_pid": os.getpid(),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        if unload_from and from_model and from_model != to_model:
            try:
                self._unload_model(str(from_model))
            except Exception:
                pass

        warm_ok = None
        if warm:
            try:
                self._ollama_json(
                    "POST",
                    "/api/chat",
                    {
                        "model": to_model,
                        "messages": [{"role": "user", "content": "hot-swap ping"}],
                        "stream": False,
                        "keep_alive": (self.cfg.get("defaults") or {}).get("keep_alive") or "30m",
                        "options": {"num_predict": 4, "temperature": 0},
                    },
                    timeout=120,
                )
                warm_ok = True
            except Exception as e:
                warm_ok = False
                if not force:
                    return {
                        "status": "RED",
                        "false_green": 0,
                        "error": f"warm_failed: {e}",
                        "from_model": from_model,
                        "to_model": to_model,
                        "server_killed": False,
                        "ms": round((time.perf_counter() - t0) * 1000, 2),
                    }

        with self._lock:
            if connection_id and connection_id in self.connections:
                self.connections[connection_id]["model"] = to_model
                self.connections[connection_id]["last_used_ts"] = time.time()
                self.connections[connection_id]["last_used_utc"] = _utc()
                self.connections[connection_id]["swaps"] = int(
                    self.connections[connection_id].get("swaps") or 0
                ) + 1
                self.connections[connection_id]["prev_model"] = from_model
            if instance_id and str(instance_id) in self.instances:
                self.instances[str(instance_id)]["model"] = to_model
                self.instances[str(instance_id)]["last_used_ts"] = time.time()
                self.instances[str(instance_id)]["last_used_utc"] = _utc()
                self.instances[str(instance_id)]["label"] = to_model
                self.instances[str(instance_id)]["est_vram_mib"] = gate.get("need_mib")
                self.instances[str(instance_id)]["gate_reason"] = gate.get("reason")
            if set_active or (not connection_id and not instance_id):
                self.active_model = to_model
            self._swap_count += 1
            swap_n = self._swap_count

        out = {
            "status": "GREEN",
            "false_green": 0,
            "from_model": from_model,
            "to_model": to_model,
            "connection_id": connection_id,
            "instance_id": instance_id,
            "active_model": self.active_model,
            "warm_ok": warm_ok,
            "gate": gate,
            "swap_index": swap_n,
            "server_killed": False,
            "server_pid": os.getpid(),
            "utc": _utc(),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "honesty": {
                "hot_swap_no_server_restart": True,
                "same_process": True,
                "live_connection_retained": bool(connection_id),
            },
        }
        _whisper(f"hot_swap {from_model} -> {to_model} conn={connection_id} inst={instance_id}")
        try:
            path = self.out / "SUPER_KERNEL_HOT_SWAP_LAST.json"
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            out["path"] = str(path)
        except Exception:
            pass
        return out

    def reload_config(self) -> dict[str, Any]:
        """Reload super_kernel.json without killing the server process."""
        try:
            new_cfg = self._load_cfg()
            mtime = self.cfg_path.stat().st_mtime if self.cfg_path.is_file() else 0.0
            with self._lock:
                old_port = self.cfg.get("port")
                self.cfg = new_cfg
                self._cfg_mtime = mtime
            return {
                "status": "GREEN",
                "false_green": 0,
                "reloaded": True,
                "cfg_path": str(self.cfg_path),
                "mtime": mtime,
                "port_unchanged": old_port == new_cfg.get("port"),
                "note": "bind host/port require process restart to change; VRAM/defaults/est tables live",
                "server_killed": False,
                "server_pid": os.getpid(),
                "utc": _utc(),
            }
        except Exception as e:
            return {
                "status": "RED",
                "false_green": 0,
                "error": str(e),
                "server_killed": False,
            }

    def live_surface(self) -> dict[str, Any]:
        """Snapshot of live connections + instances + active model (no kill)."""
        return {
            "schema": "drone.super_kernel.live_surface.v1",
            "false_green": 0,
            "utc": _utc(),
            "server_pid": os.getpid(),
            "server_killed": False,
            "active_model": self.active_model,
            "connections": self.list_connections(),
            "connections_count": len(self.connections),
            "instances": self.list_instances(),
            "instances_count": len(self.instances),
            "loaded": self.list_loaded(),
            "counters": {
                "requests": self._req_count,
                "served": self._serve_count,
                "swaps": self._swap_count,
                "connects": self._connect_count,
                "vram_refused": self._refuse_count,
            },
            "endpoints": [
                "POST /connect",
                "POST /disconnect",
                "GET  /connections",
                "POST /hot-swap",
                "POST /reload-config",
                "GET  /live",
                "POST /chat  (connection_id|instance_id|model)",
            ],
        }

    # ── Generate / chat ───────────────────────────────────────

    def chat(
        self,
        *,
        model: str | None = None,
        instance_id: str | None = None,
        connection_id: str | None = None,
        messages: list[dict[str, str]] | None = None,
        prompt: str | None = None,
        num_predict: int | None = None,
        temperature: float | None = None,
        timeout_s: float | None = None,
        force: bool = False,
        allow_any: bool = True,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self._lock:
            self._req_count += 1

        conn = None
        if connection_id:
            conn = self.get_connection(connection_id)
            if not conn:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"unknown connection: {connection_id}",
                    "server_killed": False,
                }
            model = str(conn.get("model") or model)
            instance_id = instance_id or conn.get("instance_id")

        inst = None
        if instance_id:
            inst = self.get_instance(str(instance_id))
            if not inst and not conn:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"unknown instance: {instance_id}",
                    "server_killed": False,
                }
            if inst:
                model = str(inst.get("model") or model)

        model = (model or self.active_model or "").strip()
        if not model:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "model, connection_id, or instance_id required",
                "server_killed": False,
            }

        if not allow_any:
            names = {m.get("name") for m in self.list_models()}
            if model not in names:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"model not installed: {model}",
                    "installed": sorted(n for n in names if n),
                }

        gate = self.can_hang(model, force=force)
        if not gate.get("ok"):
            with self._lock:
                self._refuse_count += 1
            return {
                "status": "RED",
                "false_green": 0,
                "error": "vram_cannot_hang",
                "gate": gate,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        defs = self.cfg.get("defaults") or {}
        np = int(num_predict if num_predict is not None else defs.get("num_predict") or 256)
        temp = float(temperature if temperature is not None else defs.get("temperature") or 0.2)
        to = float(timeout_s if timeout_s is not None else defs.get("timeout_s") or 180)
        ka = defs.get("keep_alive") or "30m"

        if messages is None:
            messages = [{"role": "user", "content": prompt or ""}]

        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": ka,
            "options": {"num_predict": np, "temperature": temp},
        }

        acquired = self._req_lock.acquire(timeout=to + 5)
        if not acquired:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "lane_busy_timeout",
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }
        try:
            data = self._ollama_json("POST", "/api/chat", body, timeout=to)
            text = str((data.get("message") or {}).get("content", "")).strip()
            with self._lock:
                if instance_id and str(instance_id) in self.instances:
                    self.instances[str(instance_id)]["last_used_ts"] = time.time()
                    self.instances[str(instance_id)]["last_used_utc"] = _utc()
                    self.instances[str(instance_id)]["requests"] = (
                        int(self.instances[str(instance_id)].get("requests") or 0) + 1
                    )
                if connection_id and connection_id in self.connections:
                    self.connections[connection_id]["last_used_ts"] = time.time()
                    self.connections[connection_id]["last_used_utc"] = _utc()
                    self.connections[connection_id]["requests"] = (
                        int(self.connections[connection_id].get("requests") or 0) + 1
                    )
                    self.connections[connection_id]["history_turns"] = (
                        int(self.connections[connection_id].get("history_turns") or 0) + 1
                    )
                self._serve_count += 1
            return {
                "status": "GREEN" if text else "RED",
                "false_green": 0,
                "model": model,
                "connection_id": connection_id,
                "instance_id": instance_id,
                "server_killed": False,
                "text": text,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "eval_count": data.get("eval_count"),
                "gate_reason": gate.get("reason"),
                "utc": _utc(),
                "lane": "super_kernel",
            }
        except Exception as e:
            return {
                "status": "RED",
                "false_green": 0,
                "model": model,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }
        finally:
            self._req_lock.release()

    def generate(self, prompt: str, **kw: Any) -> dict[str, Any]:
        return self.chat(prompt=prompt, **kw)

    # ── Status / seal ─────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        vram = probe_vram(int((self.cfg.get("gpu") or {}).get("index") or 0))
        models = self.list_models()
        err_models = [m for m in models if m.get("error")]
        return {
            "schema": "drone.super_kernel.status.v1",
            "name": self.cfg.get("name") or "Super Kernel Lane",
            "false_green": 0,
            "utc": _utc(),
            "started_utc": self._started_utc,
            "bind": f"{self.cfg.get('bind') or self.cfg.get('host')}:{self.cfg.get('port')}",
            "ollama_host": self.ollama_host(),
            "lane": "super_kernel",
            "barebones": True,
            "multi_thread": True,
            "limits": {
                "max_models": None,
                "max_instances": None,
                "only_limit": "vram",
                "headroom_mib": self.headroom_mib(),
            },
            "vram": vram,
            "models_count": 0 if err_models else len(models),
            "models": models if not err_models else [],
            "models_error": err_models[0] if err_models else None,
            "loaded": self.list_loaded(),
            "active_model": self.active_model,
            "instances": self.list_instances(),
            "instances_count": len(self.instances),
            "connections": self.list_connections(),
            "connections_count": len(self.connections),
            "counters": {
                "requests": self._req_count,
                "served": self._serve_count,
                "swaps": self._swap_count,
                "connects": self._connect_count,
                "vram_refused": self._refuse_count,
            },
            "hot_swap": True,
            "live_connections": True,
            "honesty": {
                "no_artificial_model_cap": True,
                "vram_only_limit": True,
                "shared_ollama_backend": True,
                "kernel_is_front_lane": True,
                "hot_swap_no_server_kill": True,
                "live_connections_no_server_kill": True,
            },
        }

    def smoke(self) -> dict[str, Any]:
        """Minimal smoke: health models + one generate on smallest safe model."""
        t0 = time.perf_counter()
        models = self.list_models()
        names = [m.get("name") for m in models if m.get("name") and not m.get("cloud")]
        # prefer tiny
        prefer = ["llama3.2:3b", "qwen2.5:3b", "ai-smarts:latest", "llama3.1:8b"]
        pick = None
        for p in prefer:
            if p in names:
                pick = p
                break
        if not pick and names:
            pick = names[0]
        gen = None
        if pick:
            gen = self.chat(
                model=pick,
                prompt="Reply with exactly: KERNEL_OK",
                num_predict=12,
                temperature=0,
                timeout_s=90,
            )
        vram = probe_vram(int((self.cfg.get("gpu") or {}).get("index") or 0))
        ok = bool(names) and bool(gen and gen.get("status") == "GREEN" and gen.get("text"))
        return {
            "schema": "drone.super_kernel.smoke.v1",
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "utc": _utc(),
            "models_count": len(names),
            "pick": pick,
            "generate": {
                "ok": bool(gen and gen.get("status") == "GREEN"),
                "preview": str((gen or {}).get("text") or "")[:80],
                "ms": (gen or {}).get("ms"),
                "error": (gen or {}).get("error"),
            },
            "vram_ok": bool(vram.get("ok")),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def seal(self) -> dict[str, Any]:
        sm = self.smoke()
        st = self.status()
        seal = {
            "schema": "drone.super_kernel.seal.v1",
            "status": sm.get("status"),
            "false_green": 0,
            "utc": _utc(),
            "bind": st.get("bind"),
            "ollama_host": st.get("ollama_host"),
            "limits": st.get("limits"),
            "models_count": st.get("models_count"),
            "vram": st.get("vram"),
            "smoke": sm,
            "honesty": st.get("honesty"),
            "endpoints": [
                "GET  /health",
                "GET  /status",
                "GET  /vram",
                "GET  /models",
                "GET  /instances",
                "POST /instance/open",
                "POST /instance/close",
                "POST /chat",
                "POST /generate",
                "POST /v1/chat/completions",
            ],
            "evidence": [],
        }
        path = self.out / "SUPER_KERNEL_SEAL.json"
        path.write_text(json.dumps(seal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        seal["path"] = str(path)
        seal["evidence"] = [str(path)]
        path.write_text(json.dumps(seal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return seal


# ── HTTP server ───────────────────────────────────────────────

_KERNEL: SuperKernel | None = None
_KERNEL_LOCK = threading.Lock()


def get_kernel(root: Path | None = None) -> SuperKernel:
    global _KERNEL
    with _KERNEL_LOCK:
        if _KERNEL is None:
            _KERNEL = SuperKernel(root)
        return _KERNEL


class KernelHandler(BaseHTTPRequestHandler):
    """Barebones multi-thread request handler."""

    server_version = "SuperKernel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # whisper-friendly: only errors to stderr path via default suppressed
        if os.environ.get("SUPER_KERNEL_VERBOSE"):
            super().log_message(fmt, *args)

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send(self, code: int, data: Any, *, extra_headers: dict | None = None) -> None:
        body = _json_bytes(data)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Super-Kernel", "1")
        self.send_header("Connection", "close")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _kernel(self) -> SuperKernel:
        return getattr(self.server, "kernel", None) or get_kernel()  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        try:
            k = self._kernel()
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in {"/", "/health"}:
                self._send(
                    200,
                    {
                        "ok": True,
                        "lane": "super_kernel",
                        "barebones": True,
                        "multi_thread": True,
                        "stable": True,
                        "boss_or_grok_kill_only": True,
                        "pid": os.getpid(),
                        "utc": _utc(),
                        "false_green": 0,
                    },
                )
                return
            if path == "/status":
                self._send(200, k.status())
                return
            if path == "/vram":
                self._send(200, probe_vram(int((k.cfg.get("gpu") or {}).get("index") or 0)))
                return
            if path == "/ram":
                self._send(200, ram_power_status())
                return
            if path == "/cpu":
                self._send(200, cpu_power_status())
                return
            if path == "/power":
                self._send(
                    200,
                    {
                        "schema": "drone.super_kernel.host_power.v1",
                        "false_green": 0,
                        "utc": _utc(),
                        "cpu": cpu_power_status(),
                        "ram": ram_power_status(),
                        "server_pid": os.getpid(),
                        "server_killed": False,
                    },
                )
                return
            if path == "/models":
                self._send(
                    200,
                    {
                        "models": k.list_models(),
                        "loaded": k.list_loaded(),
                        "max_models": None,
                        "only_limit": "vram",
                        "false_green": 0,
                    },
                )
                return
            if path == "/instances":
                self._send(
                    200,
                    {
                        "instances": k.list_instances(),
                        "count": len(k.instances),
                        "max_instances": None,
                        "false_green": 0,
                    },
                )
                return
            if path == "/connections":
                self._send(
                    200,
                    {
                        "connections": k.list_connections(),
                        "count": len(k.connections),
                        "active_model": k.active_model,
                        "server_pid": os.getpid(),
                        "server_killed": False,
                        "false_green": 0,
                    },
                )
                return
            if path == "/live":
                self._send(200, k.live_surface())
                return
            if path == "/seal":
                self._send(200, k.seal())
                return
            if path == "/kill":
                # GET only describes auth — never kills, never leaks token
                self._send(
                    405,
                    {
                        "error": "use POST /kill with token",
                        "auth": kill_auth_meta(),
                        "false_green": 0,
                    },
                )
                return
            if path == "/stable":
                live = {}
                try:
                    lp = k.out / "SUPER_KERNEL_LIVE.json"
                    if lp.is_file():
                        live = json.loads(lp.read_text(encoding="utf-8"))
                except Exception:
                    pass
                self._send(
                    200,
                    {
                        "stable": True,
                        "pid": os.getpid(),
                        "authorized_stop": is_authorized_stop(),
                        "kill": kill_auth_meta(),
                        "live": {
                            "status": live.get("status"),
                            "stable_mode": live.get("stable_mode"),
                            "pid": live.get("pid"),
                        },
                        "false_green": 0,
                        "utc": _utc(),
                    },
                )
                return
            self._send(404, {"error": "not_found", "path": path, "false_green": 0})
        except Exception as e:
            self._send(
                500,
                {"status": "RED", "error": str(e), "trace": traceback.format_exc()[-500:], "false_green": 0},
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            k = self._kernel()
            path = urlparse(self.path).path.rstrip("/") or "/"
            body = self._read_json()

            if path == "/kill":
                # Boss/Grok only — token required (HTTP). CLI kill uses local file.
                hdr = self.headers.get("X-Super-Kernel-Token") or self.headers.get(
                    "X-OCell-Token"
                )
                tok = (body.get("token") if isinstance(body, dict) else None) or hdr
                if not verify_kill_token(str(tok) if tok else None):
                    _whisper("kill_denied unauthorized")
                    self._send(
                        403,
                        {
                            "status": "RED",
                            "error": "unauthorized_kill",
                            "auth": kill_auth_meta(),
                            "false_green": 0,
                        },
                    )
                    return
                who = str((body or {}).get("who") or "http_token")
                reason = str((body or {}).get("reason") or "authorized_kill")
                write_authorized_stop(reason=reason, who=who)
                _whisper(f"kill_authorized who={who} reason={reason}")
                # schedule shutdown after response
                httpd = self.server

                def _shutdown() -> None:
                    time.sleep(0.15)
                    try:
                        httpd.shutdown()
                    except Exception:
                        pass
                    # hard exit so detached child dies cleanly
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
                self._send(
                    200,
                    {
                        "status": "GREEN",
                        "killed": True,
                        "who": who,
                        "reason": reason,
                        "false_green": 0,
                        "utc": _utc(),
                    },
                )
                return

            if path == "/instance/open":
                out = k.open_instance(
                    str(body.get("model") or ""),
                    force=bool(body.get("force")),
                    label=str(body.get("label") or ""),
                    keep_alive=body.get("keep_alive"),
                )
                self._send(200 if out.get("status") == "GREEN" else 409, out)
                return

            if path == "/instance/close":
                out = k.close_instance(
                    str(body.get("id") or body.get("instance_id") or ""),
                    unload=bool(body.get("unload")),
                )
                self._send(200 if out.get("status") == "GREEN" else 404, out)
                return

            if path == "/connect":
                out = k.connect(
                    body.get("model"),
                    client=str(body.get("client") or ""),
                    label=str(body.get("label") or ""),
                    force=bool(body.get("force")),
                    keep_alive=body.get("keep_alive"),
                    open_instance=bool(body.get("open_instance", True)),
                )
                self._send(200 if out.get("status") == "GREEN" else 409, out)
                return

            if path == "/disconnect":
                out = k.disconnect(
                    str(body.get("id") or body.get("connection_id") or ""),
                    unload=bool(body.get("unload")),
                    close_instance=bool(body.get("close_instance", True)),
                )
                self._send(200 if out.get("status") == "GREEN" else 404, out)
                return

            if path in {"/hot-swap", "/hot_swap", "/swap"}:
                out = k.hot_swap(
                    str(body.get("to_model") or body.get("model") or ""),
                    connection_id=body.get("connection_id") or body.get("conn"),
                    instance_id=body.get("instance_id") or body.get("instance"),
                    unload_from=bool(body.get("unload_from", True)),
                    force=bool(body.get("force")),
                    warm=bool(body.get("warm", True)),
                    set_active=bool(body.get("set_active", False)),
                )
                code = 200 if out.get("status") == "GREEN" else (
                    507 if out.get("error") == "vram_cannot_hang" else 400
                )
                self._send(code, out)
                return

            if path in {"/reload-config", "/reload_config"}:
                self._send(200, k.reload_config())
                return

            if path in {"/cpu-power", "/cpu_power"}:
                # re-apply CPU power live — no server kill
                out = apply_cpu_power(k.cfg)
                self._send(200, out)
                return

            if path in {"/ram-power", "/ram_power"}:
                out = apply_ram_power(k.cfg)
                self._send(200, out)
                return

            if path in {"/host-power", "/power-apply"}:
                out = apply_host_power(k.cfg)
                self._send(200, out)
                return

            if path == "/touch":
                out = k.touch_connection(str(body.get("connection_id") or body.get("id") or ""))
                self._send(200 if out.get("status") == "GREEN" else 404, out)
                return

            if path in {"/chat", "/generate"}:
                out = k.chat(
                    model=body.get("model"),
                    instance_id=body.get("instance_id") or body.get("id"),
                    connection_id=body.get("connection_id") or body.get("conn"),
                    messages=body.get("messages"),
                    prompt=body.get("prompt") or body.get("input"),
                    num_predict=body.get("num_predict") or body.get("max_tokens"),
                    temperature=body.get("temperature"),
                    timeout_s=body.get("timeout_s"),
                    force=bool(body.get("force")),
                )
                code = 200 if out.get("status") == "GREEN" else (
                    507 if out.get("error") == "vram_cannot_hang" else 400
                )
                self._send(code, out)
                return

            if path == "/v1/chat/completions":
                # OpenAI-compatible thin shim — supports connection_id in extra field
                msgs = body.get("messages") or []
                model = body.get("model")
                out = k.chat(
                    model=model,
                    connection_id=body.get("connection_id"),
                    messages=msgs,
                    num_predict=body.get("max_tokens") or body.get("num_predict"),
                    temperature=body.get("temperature"),
                    force=bool(body.get("force")),
                )
                if out.get("status") != "GREEN":
                    code = 507 if out.get("error") == "vram_cannot_hang" else 400
                    self._send(code, out)
                    return
                openai_shape = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": out.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": out.get("text") or ""},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"completion_tokens": out.get("eval_count")},
                    "super_kernel": {
                        "ms": out.get("ms"),
                        "lane": "super_kernel",
                        "connection_id": out.get("connection_id"),
                        "server_killed": False,
                        "false_green": 0,
                    },
                }
                self._send(200, openai_shape)
                return

            if path == "/can_hang":
                out = k.can_hang(str(body.get("model") or ""), force=bool(body.get("force")))
                self._send(200, out)
                return

            self._send(404, {"error": "not_found", "path": path, "false_green": 0})
        except Exception as e:
            self._send(
                500,
                {"status": "RED", "error": str(e), "trace": traceback.format_exc()[-500:], "false_green": 0},
            )


class SuperKernelServer(ThreadingHTTPServer):
    """Threading HTTP — one thread per request; daemon threads."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, addr: tuple[str, int], kernel: SuperKernel) -> None:
        self.kernel = kernel
        super().__init__(addr, KernelHandler)


def _install_stable_signal_handlers(httpd: ThreadingHTTPServer) -> None:
    """Ignore casual Ctrl+C / console close; only authorized_stop or /kill stops."""

    def _ignore(signum: int, frame: Any) -> None:  # noqa: ARG001
        if is_authorized_stop():
            _whisper(f"signal {signum} with authorized_stop — shutdown")
            try:
                httpd.shutdown()
            except Exception:
                pass
            return
        _whisper(f"signal {signum} IGNORED — boss/Grok kill only")
        # do not exit

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _ignore)
        except Exception:
            pass


def serve(
    root: Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    stable: bool = True,
) -> int:
    """
    Run kernel HTTP lane.
    stable=True (default): ignore casual kill signals; only boss/Grok authorized stop.
    """
    ensure_kill_token()
    if stable:
        clear_authorized_stop()

    k = SuperKernel(root)
    # RAM + CPU power rails — keep process sticky/stable (no kill)
    power = apply_host_power(k.cfg)
    host = host or os.environ.get("SUPER_KERNEL_HOST") or k.cfg.get("bind") or k.cfg.get("host") or "127.0.0.1"
    port = int(port or os.environ.get("SUPER_KERNEL_PORT") or k.cfg.get("port") or 11450)
    httpd = SuperKernelServer((host, port), k)
    k._http = httpd

    if stable:
        _install_stable_signal_handlers(httpd)

    pid = os.getpid()
    _state_dir()
    KERNEL_PID_FILE.write_text(str(pid), encoding="utf-8")

    live = {
        "schema": "drone.super_kernel.live.v1",
        "status": "RUNNING",
        "false_green": 0,
        "utc": _utc(),
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "pid": pid,
        "stable_mode": bool(stable),
        "kill": kill_auth_meta(),
        "ollama_host": k.ollama_host(),
        "limits": {"max_models": None, "max_instances": None, "only_limit": "vram"},
        "boss_or_grok_kill_only": True,
        "cpu_power": (power.get("cpu_power") or {}).get("cpu_power")
        or (power.get("cpu_power") or {}),
        "ram_power": (power.get("ram_power") or {}).get("ram_power")
        or (power.get("ram_power") or {}),
        "host_power_status": power.get("status"),
    }
    live_path = k.out / "SUPER_KERNEL_LIVE.json"
    live_path.write_text(json.dumps(live, indent=2), encoding="utf-8")
    _write_json(STATE_FILE, {**live, "live_path": str(live_path)})

    print(f"[super-kernel] barebones multi-thread lane  http://{host}:{port}", flush=True)
    print(f"[super-kernel] ollama backend {k.ollama_host()}", flush=True)
    print(f"[super-kernel] only limit = VRAM (headroom {k.headroom_mib()} MiB)", flush=True)
    print(f"[super-kernel] stable={stable} boss/Grok kill only", flush=True)
    print(f"[super-kernel] live → {live_path}", flush=True)
    _whisper(f"serve start pid={pid} stable={stable} port={port}")

    stop_ev = threading.Event()

    def _poll_authorized_stop() -> None:
        while not stop_ev.is_set():
            if is_authorized_stop():
                _whisper("authorized_stop flag seen — shutdown serve")
                try:
                    httpd.shutdown()
                except Exception:
                    pass
                break
            time.sleep(0.5)

    threading.Thread(target=_poll_authorized_stop, daemon=True).start()

    try:
        while True:
            try:
                httpd.serve_forever(poll_interval=0.5)
                break  # clean shutdown()
            except KeyboardInterrupt:
                if stable and not is_authorized_stop():
                    _whisper("KeyboardInterrupt ignored (stable) — boss/Grok kill only")
                    continue
                print("\n[super-kernel] stop", flush=True)
                break
    finally:
        stop_ev.set()
        stopped_auth = is_authorized_stop()
        live["status"] = "STOPPED" if stopped_auth else "EXITED"
        live["stopped_utc"] = _utc()
        live["stopped_authorized"] = stopped_auth
        live_path.write_text(json.dumps(live, indent=2), encoding="utf-8")
        _write_json(STATE_FILE, live)
        try:
            httpd.server_close()
        except Exception:
            pass
        _whisper(f"serve end pid={pid} authorized={stopped_auth}")
    return 0


def _spawn_detached(args: list[str], log_name: str) -> int:
    """Spawn child process hidden (survives parent console death)."""
    _state_dir()
    log_path = STATE_DIR / log_name
    flags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW + new process group. Avoid DETACHED_PROCESS — breaks
        # some Python stdio/socket paths on Windows and killed the lane silently.
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    env = os.environ.copy()
    root_s = str(_root())
    env["PYTHONPATH"] = root_s + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("SUPER_KERNEL_HOST", "127.0.0.1")
    env.setdefault("SUPER_KERNEL_PORT", "11450")
    stdout = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    stdout.write(f"\n--- spawn {_utc()} ---\n")
    stdout.write(" ".join(args) + "\n")
    stdout.flush()
    proc = subprocess.Popen(
        args,
        cwd=root_s,
        env=env,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=False,
    )
    return int(proc.pid)


def run_idler(
    *,
    host: str = "127.0.0.1",
    port: int = 11450,
    interval_s: float = 15.0,
    warm_model: str | None = None,
    warm_every_n: int = 12,
) -> int:
    """
    Super Kernel IDLER — minimum keep-alive process.

    - Heartbeats /health on a loop (proves lane alive)
    - Writes idler_heartbeat.json for Boss/Grok
    - Optional rare warm touch (tiny generate) so models do not fully cold-sleep
    - Exits only on authorized_stop (Boss/Grok kill)
    - If lane down: one restart attempt of serve (does not clear authorized_stop)
    """
    ensure_kill_token()
    _state_dir()
    IDLER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    interval_s = max(3.0, float(interval_s))
    warm_every_n = max(0, int(warm_every_n))
    warm_model = (warm_model or os.environ.get("SUPER_KERNEL_IDLER_WARM") or "llama3.2:3b").strip()
    tick = 0
    ok_streak = 0
    fail_streak = 0
    # Idler gets its own CPU/RAM power (separate process)
    idler_cfg: dict[str, Any] = {}
    try:
        cfg_path = _root() / "configs" / "super_kernel.json"
        if cfg_path.is_file():
            idler_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        idler_cfg = {}
    apply_host_power(idler_cfg)
    _whisper(f"idler start pid={os.getpid()} interval={interval_s}s warm={warm_model} cpu_power=on")

    while True:
        if is_authorized_stop():
            _whisper("idler exit — authorized_stop")
            hb = {
                "schema": "drone.super_kernel.idler.v1",
                "status": "STOPPED",
                "false_green": 0,
                "utc": _utc(),
                "reason": "authorized_stop",
                "pid": os.getpid(),
            }
            _write_json(IDLER_HEARTBEAT, hb)
            return 0

        t0 = time.perf_counter()
        healthy = _port_health(host, port, timeout=3.0)
        action = "ping"
        warm: dict[str, Any] | None = None
        power_pulse: dict[str, Any] | None = None

        # CPU + RAM power pulses (keep stable without killing server)
        cp_cfg = dict(idler_cfg.get("cpu_power") or {})
        rp_cfg = dict(idler_cfg.get("ram_power") or {})
        cpu_every = int(cp_cfg.get("pulse_every_ticks") or 2)
        ram_every = int(rp_cfg.get("pulse_every_ticks") or 4)
        if cp_cfg.get("idler_power_pulse", True) and cpu_every > 0 and (tick % cpu_every) == 0:
            power_pulse = {"cpu": cpu_power_pulse(idler_cfg)}
            action = "cpu_power_pulse"
        if rp_cfg.get("idler_power_pulse", True) and ram_every > 0 and (tick % ram_every) == 0:
            rp = ram_power_pulse(idler_cfg)
            power_pulse = {**(power_pulse or {}), "ram": rp}
            if action == "ping":
                action = "ram_power_pulse"

        if not healthy:
            fail_streak += 1
            ok_streak = 0
            action = "restart_serve"
            if not is_authorized_stop():
                try:
                    _spawn_detached(
                        [
                            sys.executable,
                            "-m",
                            "drone",
                            "super-kernel",
                            "serve",
                            "--stable",
                            "--host",
                            host,
                            "--port",
                            str(port),
                        ],
                        "kernel.log",
                    )
                    _whisper(f"idler: lane down — restarted serve (fail_streak={fail_streak})")
                except Exception as e:
                    _whisper(f"idler restart error: {e}")
            # brief wait for come-up
            time.sleep(2.0)
            healthy = _port_health(host, port, timeout=3.0)
        else:
            fail_streak = 0
            ok_streak += 1
            # rare warm touch — keeps a tiny model path hot; VRAM-gated by kernel
            if warm_every_n > 0 and tick > 0 and (tick % warm_every_n) == 0:
                action = "warm"
                try:
                    body = json.dumps(
                        {
                            "model": warm_model,
                            "prompt": "idle",
                            "num_predict": 2,
                            "temperature": 0,
                        }
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        f"http://{host}:{port}/chat",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    warm = {
                        "ok": data.get("status") == "GREEN",
                        "model": warm_model,
                        "ms": data.get("ms"),
                        "status": data.get("status"),
                    }
                except Exception as e:
                    warm = {"ok": False, "error": str(e)[:200], "model": warm_model}

        ms = round((time.perf_counter() - t0) * 1000, 2)
        tick += 1
        hb = {
            "schema": "drone.super_kernel.idler.v1",
            "status": "GREEN" if healthy else "RED",
            "false_green": 0,
            "utc": _utc(),
            "pid": os.getpid(),
            "url": f"http://{host}:{port}",
            "healthy": healthy,
            "tick": tick,
            "ok_streak": ok_streak,
            "fail_streak": fail_streak,
            "action": action,
            "warm": warm,
            "power_pulse": power_pulse,
            "cpu_power": {
                "enabled": bool(_CPU_POWER_STATE.get("enabled")),
                "priority": _CPU_POWER_STATE.get("priority"),
                "pulses": _CPU_POWER_STATE.get("pulses"),
            },
            "interval_s": interval_s,
            "ms": ms,
            "boss_or_grok_kill_only": True,
        }
        try:
            _write_json(IDLER_HEARTBEAT, hb)
            root_out = _root() / "out" / "SUPER_KERNEL_IDLER.json"
            _write_json(root_out, hb)
        except Exception:
            pass

        # sleep in slices so kill is responsive
        slept = 0.0
        while slept < interval_s:
            if is_authorized_stop():
                break
            step = min(1.0, interval_s - slept)
            time.sleep(step)
            slept += step


def start_stable(
    root: Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    with_watchdog: bool = True,
    with_idler: bool = True,
) -> dict[str, Any]:
    """
    Launch super-kernel as stable hidden process + watchdog + idler.
    Only boss CLI / Grok token kill may stop it permanently.
    """
    ensure_kill_token()
    clear_authorized_stop()
    root = Path(root or _root())
    host = host or os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
    port = int(port or os.environ.get("SUPER_KERNEL_PORT") or 11450)
    py = sys.executable

    # already healthy?
    already = _port_health(host, port)
    kpid = _read_pid(KERNEL_PID_FILE) if already else 0

    if not already:
        # clear stale pid if dead
        old = _read_pid(KERNEL_PID_FILE)
        if old and not _pid_alive(old):
            try:
                KERNEL_PID_FILE.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass

        serve_args = [
            py,
            "-m",
            "drone",
            "super-kernel",
            "serve",
            "--stable",
            "--host",
            host,
            "--port",
            str(port),
        ]
        kpid = _spawn_detached(serve_args, "kernel.log")
        # wait for health
        ok = False
        for _ in range(50):
            time.sleep(0.25)
            if _port_health(host, port):
                ok = True
                break
    else:
        ok = True

    wpid = _read_pid(WATCHDOG_PID_FILE)
    if with_watchdog and (not wpid or not _pid_alive(wpid)):
        wd_args = [
            py,
            "-m",
            "drone",
            "super-kernel",
            "watchdog",
            "--host",
            host,
            "--port",
            str(port),
        ]
        wpid = _spawn_detached(wd_args, "watchdog.log")
        WATCHDOG_PID_FILE.write_text(str(wpid), encoding="utf-8")

    ipid = _read_pid(IDLER_PID_FILE)
    if with_idler and (not ipid or not _pid_alive(ipid)):
        id_args = [
            py,
            "-m",
            "drone",
            "super-kernel",
            "idler",
            "--host",
            host,
            "--port",
            str(port),
            "--interval",
            os.environ.get("SUPER_KERNEL_IDLER_INTERVAL", "15"),
        ]
        ipid = _spawn_detached(id_args, "idler.log")
        IDLER_PID_FILE.write_text(str(ipid), encoding="utf-8")

    # refresh live from file if ready
    live_path = root / "out" / "SUPER_KERNEL_LIVE.json"
    report = {
        "schema": "drone.super_kernel.start.v1",
        "status": "GREEN" if ok else "PARTIAL",
        "false_green": 0,
        "utc": _utc(),
        "url": f"http://{host}:{port}",
        "spawned_pid": kpid or None,
        "watchdog_pid": wpid or None,
        "idler_pid": ipid or None,
        "already_running": already,
        "healthy": ok,
        "stable": True,
        "idler": True,
        "boss_or_grok_kill_only": True,
        "kill": kill_auth_meta(),
        "live_path": str(live_path),
        "state_dir": str(STATE_DIR),
        "heartbeat": str(IDLER_HEARTBEAT),
    }
    _write_json(STATE_DIR / "start_last.json", report)
    _whisper(f"start_stable kpid={kpid} wpid={wpid} ipid={ipid} healthy={ok}")
    try:
        (root / "out").mkdir(parents=True, exist_ok=True)
        _write_json(root / "out" / "SUPER_KERNEL_STABLE.json", report)
    except Exception:
        pass
    return report


def run_watchdog(
    *,
    host: str = "127.0.0.1",
    port: int = 11450,
    interval_s: float = 8.0,
) -> int:
    """
    Keep super-kernel alive. Restarts unless authorized_stop.flag is set.
    Boss/Grok kill writes the flag so watchdog exits without restart.
    """
    ensure_kill_token()
    _state_dir()
    WATCHDOG_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _whisper(f"watchdog start pid={os.getpid()} port={port}")
    py = sys.executable
    while True:
        if is_authorized_stop():
            _whisper("watchdog exit — authorized_stop")
            return 0
        healthy = _port_health(host, port)
        if not healthy:
            if is_authorized_stop():
                return 0
            _whisper("watchdog: lane down — restarting serve")
            try:
                _spawn_detached(
                    [
                        py,
                        "-m",
                        "drone",
                        "super-kernel",
                        "serve",
                        "--stable",
                        "--host",
                        host,
                        "--port",
                        str(port),
                    ],
                    "kernel.log",
                )
            except Exception as e:
                _whisper(f"watchdog restart error: {e}")
            # give it time
            time.sleep(3)
            continue
        time.sleep(interval_s)


def hard_kill_kernel(*, who: str = "cli", reason: str = "boss_or_grok") -> dict[str, Any]:
    """
    Authorized kill path for Boss (CLI) or Grok (same user session + token file).
    Sets authorized_stop so watchdog will NOT restart.
    """
    ensure_kill_token()
    write_authorized_stop(reason=reason, who=who)
    host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
    port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
    tok = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.is_file() else ""

    http_result: dict[str, Any] = {}
    try:
        data = json.dumps({"token": tok, "who": who, "reason": reason}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}:{port}/kill",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Super-Kernel-Token": tok,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            http_result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        http_result = {"http_kill": False, "error": str(e)}

    # force terminate known pids if still up (kernel, idler, watchdog)
    killed_pids: list[int] = []
    for path in (KERNEL_PID_FILE, IDLER_PID_FILE, WATCHDOG_PID_FILE):
        pid = _read_pid(path)
        if pid and _pid_alive(pid):
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True,
                        timeout=10,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    os.kill(pid, signal.SIGTERM)
                killed_pids.append(pid)
            except Exception:
                pass

    # also kill any python -m drone super-kernel {serve,watchdog,idler}
    try:
        if sys.platform == "win32":
            ps = (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'super-kernel (serve|watchdog|idler)' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
            )
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=CREATE_NO_WINDOW,
            )
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    killed_pids.append(int(line))
    except Exception:
        pass

    out = {
        "schema": "drone.super_kernel.kill.v1",
        "status": "GREEN",
        "false_green": 0,
        "utc": _utc(),
        "who": who,
        "reason": reason,
        "authorized_stop": True,
        "http_result": http_result,
        "killed_pids": sorted(set(killed_pids)),
        "kill": kill_auth_meta(),
        "note": "watchdog will not restart while authorized_stop.flag exists",
    }
    _write_json(STATE_DIR / "kill_last.json", out)
    try:
        root = _root()
        _write_json(root / "out" / "SUPER_KERNEL_KILL.json", out)
        # update live
        live_path = root / "out" / "SUPER_KERNEL_LIVE.json"
        live = {
            "schema": "drone.super_kernel.live.v1",
            "status": "STOPPED",
            "false_green": 0,
            "utc": _utc(),
            "stopped_authorized": True,
            "who": who,
            "reason": reason,
        }
        _write_json(live_path, live)
    except Exception:
        pass
    _whisper(f"hard_kill who={who} pids={killed_pids}")
    return out


def process_status() -> dict[str, Any]:
    host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
    port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
    kpid = _read_pid(KERNEL_PID_FILE)
    wpid = _read_pid(WATCHDOG_PID_FILE)
    ipid = _read_pid(IDLER_PID_FILE)
    hb: dict[str, Any] = {}
    if IDLER_HEARTBEAT.is_file():
        try:
            hb = json.loads(IDLER_HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            hb = {}
    return {
        "schema": "drone.super_kernel.process.v1",
        "false_green": 0,
        "utc": _utc(),
        "url": f"http://{host}:{port}",
        "healthy": _port_health(host, port),
        "kernel_pid": kpid or None,
        "kernel_alive": _pid_alive(kpid) if kpid else False,
        "watchdog_pid": wpid or None,
        "watchdog_alive": _pid_alive(wpid) if wpid else False,
        "idler_pid": ipid or None,
        "idler_alive": _pid_alive(ipid) if ipid else False,
        "idler_heartbeat": {
            "status": hb.get("status"),
            "tick": hb.get("tick"),
            "healthy": hb.get("healthy"),
            "action": hb.get("action"),
            "utc": hb.get("utc"),
            "ok_streak": hb.get("ok_streak"),
        }
        if hb
        else None,
        "authorized_stop": is_authorized_stop(),
        "stable_dir": str(STATE_DIR),
        "kill": kill_auth_meta(),
        "boss_or_grok_kill_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone super-kernel")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser(
        "start",
        help="hidden stable process + watchdog + idler (boss/Grok kill only)",
    )
    st.add_argument("--host", default=None)
    st.add_argument("--port", type=int, default=None)
    st.add_argument("--no-watchdog", action="store_true")
    st.add_argument("--no-idler", action="store_true")

    sv = sub.add_parser("serve", help="run barebones multi-thread kernel lane")
    sv.add_argument("--host", default=None)
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument(
        "--stable",
        action="store_true",
        default=True,
        help="stable mode (default): ignore casual signals; boss/Grok kill only",
    )
    sv.add_argument(
        "--foreground",
        action="store_true",
        help="allow Ctrl+C to stop (dev only — not stable)",
    )

    wd = sub.add_parser("watchdog", help="restart lane unless authorized stop")
    wd.add_argument("--host", default="127.0.0.1")
    wd.add_argument("--port", type=int, default=11450)
    wd.add_argument("--interval", type=float, default=8.0)

    idl = sub.add_parser("idler", help="keep-alive idler heartbeats + optional warm")
    idl.add_argument("--host", default="127.0.0.1")
    idl.add_argument("--port", type=int, default=11450)
    idl.add_argument("--interval", type=float, default=15.0)
    idl.add_argument("--warm-model", default=None)
    idl.add_argument("--warm-every", type=int, default=12, help="ticks between warm; 0=off")

    sub.add_parser("kill", help="HARD KILL (Boss CLI or Grok only — sets authorized stop)")
    sub.add_parser("process", help="stable process + idler + kill auth status")
    sub.add_parser("status")
    sub.add_parser("vram")
    sub.add_parser("ram", help="RAM power status")
    sub.add_parser("cpu", help="CPU power status")
    sub.add_parser("power", help="CPU+RAM power status")
    sub.add_parser("cpu-power", help="apply/reassert CPU power (no kill)")
    sub.add_parser("ram-power", help="apply/reassert RAM power (no kill)")
    sub.add_parser("host-power", help="apply CPU+RAM power (no kill)")
    sub.add_parser("models")
    sub.add_parser("instances")
    sub.add_parser("connections", help="list live connections")
    sub.add_parser("live", help="live surface snapshot (no kill)")
    sub.add_parser("reload-config", help="reload config without killing server")
    sub.add_parser("smoke")
    sub.add_parser("seal")
    op = sub.add_parser("open")
    op.add_argument("--model", "-m", required=True)
    op.add_argument("--force", action="store_true")
    cl = sub.add_parser("close")
    cl.add_argument("--id", required=True)
    cl.add_argument("--unload", action="store_true")
    cn = sub.add_parser("connect", help="open live connection (no server kill)")
    cn.add_argument("--model", "-m", default=None)
    cn.add_argument("--client", default="")
    cn.add_argument("--force", action="store_true")
    dc = sub.add_parser("disconnect")
    dc.add_argument("--id", required=True)
    dc.add_argument("--unload", action="store_true")
    hs = sub.add_parser("hot-swap", help="swap model on live conn/instance — no server kill")
    hs.add_argument("--to", "--model", dest="to_model", required=True)
    hs.add_argument("--connection", default=None)
    hs.add_argument("--instance", default=None)
    hs.add_argument("--force", action="store_true")
    hs.add_argument("--no-unload", action="store_true")
    hs.add_argument("--set-active", action="store_true")
    ch = sub.add_parser("chat")
    ch.add_argument("--model", "-m", default=None)
    ch.add_argument("--instance", default=None)
    ch.add_argument("--connection", default=None)
    ch.add_argument("prompt", nargs="+")
    ch.add_argument("--force", action="store_true")
    cg = sub.add_parser("can-hang")
    cg.add_argument("--model", "-m", required=True)

    args = p.parse_args(argv)
    root = Path(args.root) if args.root else None
    k = SuperKernel(root)

    if args.cmd == "start":
        out = start_stable(
            root,
            host=args.host,
            port=args.port,
            with_watchdog=not bool(args.no_watchdog),
            with_idler=not bool(getattr(args, "no_idler", False)),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "serve":
        stable = True
        if getattr(args, "foreground", False):
            stable = False
        elif getattr(args, "stable", True):
            stable = True
        return serve(root, host=args.host, port=args.port, stable=stable)
    if args.cmd == "watchdog":
        return run_watchdog(host=args.host, port=int(args.port), interval_s=float(args.interval))
    if args.cmd == "idler":
        return run_idler(
            host=args.host,
            port=int(args.port),
            interval_s=float(args.interval),
            warm_model=getattr(args, "warm_model", None),
            warm_every_n=int(getattr(args, "warm_every", 12) or 0),
        )
    if args.cmd == "kill":
        # CLI = boss session (or Grok on boss host) — authorized
        out = hard_kill_kernel(who="cli_boss_or_grok", reason="cli_kill")
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "process":
        print(json.dumps(process_status(), indent=2))
        return 0
    if args.cmd == "status":
        st_body = k.status()
        st_body["process"] = process_status()
        print(json.dumps(st_body, indent=2))
        return 0
    if args.cmd == "vram":
        print(json.dumps(probe_vram(int((k.cfg.get("gpu") or {}).get("index") or 0)), indent=2))
        return 0
    if args.cmd == "ram":
        # prefer live server status if up
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/ram", timeout=10) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception:
                pass
        print(json.dumps(ram_power_status(), indent=2))
        return 0
    if args.cmd == "cpu":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/cpu", timeout=15) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception:
                pass
        print(json.dumps(cpu_power_status(), indent=2))
        return 0
    if args.cmd == "power":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/power", timeout=15) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception:
                pass
        print(
            json.dumps(
                {
                    "cpu": cpu_power_status(),
                    "ram": ram_power_status(),
                    "false_green": 0,
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "cpu-power":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                req = urllib.request.Request(
                    f"http://{host}:{port}/cpu-power",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        print(json.dumps(apply_cpu_power(k.cfg), indent=2))
        return 0
    if args.cmd == "ram-power":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                req = urllib.request.Request(
                    f"http://{host}:{port}/ram-power",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        print(json.dumps(apply_ram_power(k.cfg), indent=2))
        return 0
    if args.cmd == "host-power":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                req = urllib.request.Request(
                    f"http://{host}:{port}/host-power",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        print(json.dumps(apply_host_power(k.cfg), indent=2))
        return 0
    if args.cmd == "models":
        print(json.dumps({"models": k.list_models(), "max_models": None}, indent=2))
        return 0
    if args.cmd == "instances":
        print(json.dumps({"instances": k.list_instances(), "max_instances": None}, indent=2))
        return 0
    if args.cmd == "smoke":
        out = k.smoke()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "seal":
        out = k.seal()
        out["process"] = process_status()
        out["kill"] = kill_auth_meta()
        path = k.out / "SUPER_KERNEL_SEAL.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        out["path"] = str(path)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "open":
        print(json.dumps(k.open_instance(args.model, force=bool(args.force)), indent=2))
        return 0
    if args.cmd == "close":
        print(json.dumps(k.close_instance(args.id, unload=bool(args.unload)), indent=2))
        return 0
    if args.cmd == "connect":
        # Prefer live HTTP if server up — so CLI uses same process state
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                body = json.dumps(
                    {
                        "model": args.model,
                        "client": args.client,
                        "force": bool(args.force),
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/connect",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                print(json.dumps(out, indent=2))
                return 0 if out.get("status") == "GREEN" else 1
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        out = k.connect(args.model, client=args.client or "", force=bool(args.force))
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "disconnect":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                body = json.dumps(
                    {"id": args.id, "unload": bool(args.unload)}
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/disconnect",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                print(json.dumps(out, indent=2))
                return 0 if out.get("status") == "GREEN" else 1
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        print(json.dumps(k.disconnect(args.id, unload=bool(args.unload)), indent=2))
        return 0
    if args.cmd == "hot-swap":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        payload = {
            "to_model": args.to_model,
            "connection_id": args.connection,
            "instance_id": args.instance,
            "force": bool(args.force),
            "unload_from": not bool(args.no_unload),
            "set_active": bool(args.set_active),
        }
        if _port_health(host, port):
            try:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/hot-swap",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                print(json.dumps(out, indent=2))
                return 0 if out.get("status") == "GREEN" else 1
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        out = k.hot_swap(
            args.to_model,
            connection_id=args.connection,
            instance_id=args.instance,
            force=bool(args.force),
            unload_from=not bool(args.no_unload),
            set_active=bool(args.set_active),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "connections":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/connections", timeout=10) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"error": str(e)}, indent=2))
                return 1
        print(json.dumps({"connections": k.list_connections()}, indent=2))
        return 0
    if args.cmd == "live":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/live", timeout=10) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"error": str(e)}, indent=2))
                return 1
        print(json.dumps(k.live_surface(), indent=2))
        return 0
    if args.cmd == "reload-config":
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                req = urllib.request.Request(
                    f"http://{host}:{port}/reload-config",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    print(resp.read().decode("utf-8"))
                return 0
            except Exception as e:
                print(json.dumps({"error": str(e)}, indent=2))
                return 1
        print(json.dumps(k.reload_config(), indent=2))
        return 0
    if args.cmd == "chat":
        prompt = " ".join(args.prompt)
        host = os.environ.get("SUPER_KERNEL_HOST") or "127.0.0.1"
        port = int(os.environ.get("SUPER_KERNEL_PORT") or 11450)
        if _port_health(host, port):
            try:
                body = json.dumps(
                    {
                        "model": args.model,
                        "instance_id": args.instance,
                        "connection_id": getattr(args, "connection", None),
                        "prompt": prompt,
                        "force": bool(args.force),
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/chat",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                print(json.dumps(out, indent=2))
                return 0 if out.get("status") == "GREEN" else 1
            except Exception as e:
                print(json.dumps({"status": "RED", "error": str(e), "false_green": 0}, indent=2))
                return 1
        out = k.chat(
            model=args.model,
            instance_id=args.instance,
            connection_id=getattr(args, "connection", None),
            prompt=prompt,
            force=bool(args.force),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "can-hang":
        print(json.dumps(k.can_hang(args.model), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
