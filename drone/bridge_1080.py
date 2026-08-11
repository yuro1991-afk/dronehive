"""
Stable Bridge · GTX 1080 Ti (spare) ↔ RTX 3060 (main)

Law:
  - 3060 remains MAIN (display + default Ollama :11434)
  - 1080 is SPARE / opt-in only
  - false_green: 0 — hardware GREEN only when 1080 in nvidia-smi
  - Fail closed: no second compute lane while PnP Error / Code 22 / Code 31
  - Bridge never force-kills main Ollama
  - LIVE daemon: always-on probe/arm retry — NO KILL SWITCH (by design)

CLI:
  python -m drone bridge-1080 start|status|live|enable|arm|disarm|watch|seal|process
  kill is NOT supported (no kill switch)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0

LIVE_PID_FILE = "bridge.pid"
WATCHDOG_PID_FILE = "watchdog.pid"
LIVE_FLAG = "LIVE.json"
NO_KILL_LAW = (
    "NO KILL SWITCH: bridge-1080 has no kill command. "
    "Process is permanent until OS reboot or Boss Task Manager. "
    "Never kills 3060 / main Ollama."
)


def _state_dir() -> Path:
    d = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ollama" / "bridge-1080"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            code, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=8)
            return str(pid) in (out or "") and "No tasks" not in (out or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_pid(name: str) -> int:
    p = _state_dir() / name
    try:
        if p.is_file():
            return int(p.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        pass
    return 0


def _spawn_detached(args: list[str], log_name: str) -> int:
    state = _state_dir()
    log_path = state / log_name
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_root()) + os.pathsep + env.get("PYTHONPATH", "")
    env["BRIDGE_1080_NO_KILL"] = "1"
    f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    f.write(f"\n--- spawn {_utc()} ---\n{' '.join(args)}\n")
    f.flush()
    proc = subprocess.Popen(
        args,
        cwd=str(_root()),
        env=env,
        stdout=f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=False,
    )
    return int(proc.pid)


class Bridge1080:
    """Stable dual-GPU bridge: probe · enable · arm · fail-closed spare lane."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "bridge_1080.json"
        self.cfg = self._load_cfg()
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.state = _state_dir()

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {}

    # ── probes ────────────────────────────────────────────────

    def probe_nvidia_smi(self) -> dict[str, Any]:
        code, out, err = _run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,pci.bus_id",
                "--format=csv,noheader,nounits",
            ],
            timeout=15,
        )
        gpus: list[dict[str, Any]] = []
        if code != 0 or not out.strip():
            return {
                "ok": False,
                "error": (err or out or "nvidia-smi failed").strip()[:400],
                "gpus": [],
                "false_green": 0,
            }
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            try:
                gpus.append(
                    {
                        "index": int(float(parts[0])),
                        "name": parts[1],
                        "uuid": parts[2],
                        "memory_total_mib": int(float(parts[3])),
                        "memory_used_mib": int(float(parts[4])),
                        "memory_free_mib": int(float(parts[5])),
                        "pci_bus_id": parts[6],
                    }
                )
            except Exception:
                continue
        return {
            "ok": True,
            "gpus": gpus,
            "count": len(gpus),
            "utc": _utc(),
            "false_green": 0,
        }

    def probe_pnp(self) -> dict[str, Any]:
        """Windows PnP status for 1080 Ti (Code 22 disabled / 31 driver fail / OK)."""
        ps = r"""
$d = Get-PnpDevice -Class Display -ErrorAction SilentlyContinue |
  Where-Object { $_.FriendlyName -match '1080' }
if (-not $d) { @{ found=$false } | ConvertTo-Json -Compress; exit 0 }
$d = $d | Select-Object -First 1
$props = @{}
try {
  Get-PnpDeviceProperty -InstanceId $d.InstanceId -ErrorAction SilentlyContinue |
    Where-Object { $_.KeyName -match 'ProblemCode|DriverVersion|LocationInfo' } |
    ForEach-Object { $props[$_.KeyName] = $_.Data }
} catch {}
[pscustomobject]@{
  found = $true
  status = $d.Status
  friendly = $d.FriendlyName
  instance_id = $d.InstanceId
  problem = $d.Problem
  problem_description = $d.ProblemDescription
  problem_code = $props['DEVPKEY_Device_ProblemCode']
  driver_version = $props['DEVPKEY_Device_DriverVersion']
  location = $props['DEVPKEY_Device_LocationInfo']
} | ConvertTo-Json -Compress
"""
        code, out, err = _run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=25,
        )
        if code != 0 or not (out or "").strip():
            return {
                "ok": False,
                "found": False,
                "error": (err or out or "pnp probe failed")[:300],
                "false_green": 0,
            }
        try:
            data = json.loads(out.strip())
        except Exception as e:
            return {"ok": False, "found": False, "error": f"json: {e}", "raw": out[:200], "false_green": 0}
        data["ok"] = True
        data["false_green"] = 0
        data["utc"] = _utc()
        # normalize problem code
        pc = data.get("problem_code")
        if pc is None and data.get("problem"):
            # CM_PROB_DISABLED etc.
            data["problem_hint"] = str(data.get("problem"))
        return data

    def classify(self) -> dict[str, Any]:
        """
        Full bridge classification — honest LIVE / DISABLED / DRIVER_FAIL / ABSENT / MAIN_ONLY.
        """
        smi = self.probe_nvidia_smi()
        pnp = self.probe_pnp()
        gpus = smi.get("gpus") or []
        main = next((g for g in gpus if "3060" in str(g.get("name") or "")), None)
        spare_smi = next((g for g in gpus if "1080" in str(g.get("name") or "")), None)

        pnp_status = str(pnp.get("status") or "").strip()
        problem = str(pnp.get("problem") or pnp.get("problem_description") or "")
        code22 = "22" in problem or "CM_PROB_DISABLED" in problem or pnp.get("problem_code") == 22
        code31 = "31" in problem or "CM_PROB_FAILED_ADD" in problem or pnp.get("problem_code") == 31

        if spare_smi and main:
            bridge_state = "LIVE_DUAL"
            ready = True
        elif spare_smi and not main:
            bridge_state = "LIVE_1080_ONLY"
            ready = True
        elif pnp.get("found") and pnp_status.lower() == "error" and code22:
            bridge_state = "PNP_DISABLED_CODE22"
            ready = False
        elif pnp.get("found") and pnp_status.lower() == "error" and code31:
            bridge_state = "PNP_DRIVER_FAIL_CODE31"
            ready = False
        elif pnp.get("found") and pnp_status.lower() == "error":
            bridge_state = "PNP_ERROR"
            ready = False
        elif pnp.get("found") and pnp_status.lower() == "ok" and not spare_smi:
            bridge_state = "PNP_OK_NOT_IN_SMI"
            ready = False
        elif not pnp.get("found") and main:
            bridge_state = "MAIN_ONLY_NO_PNP_1080"
            ready = False
        else:
            bridge_state = "UNKNOWN"
            ready = False

        # CUDA map recommendation (only when ready)
        cuda = {
            "device_order": "PCI_BUS_ID",
            "main_lane_default": "do_not_change_user_CUDA_for_main_ollama",
            "session_dual_when_live": "0,1",
            "session_only_1080": "0",
            "note": "Verify indices with nvidia-smi after enable — bus order may vary",
        }

        payload = {
            "schema": "drone.bridge_1080.classify.v1",
            "utc": _utc(),
            "false_green": 0,
            "bridge_state": bridge_state,
            "ready": ready,
            "status": "GREEN" if ready else "RED",
            "main_gpu": main,
            "spare_gpu_smi": spare_smi,
            "pnp": pnp,
            "nvidia_smi": {"ok": smi.get("ok"), "count": smi.get("count"), "gpus": gpus},
            "cuda": cuda,
            "next_steps": self._next_steps(bridge_state),
            "law": (self.cfg.get("law") or []),
            "honesty": {
                "3060_is_main": True,
                "1080_is_spare_opt_in": True,
                "not_auto_hijack_main_ollama": True,
                "driver": (self.cfg.get("honesty") or {}),
            },
        }
        _write(self.state / "LAST_CLASSIFY.json", payload)
        _write(self.out / "BRIDGE_1080_STATUS.json", payload)
        return payload

    def _next_steps(self, state: str) -> list[str]:
        if state == "LIVE_DUAL":
            return [
                "Bridge ready: arm session with `python -m drone bridge-1080 arm`",
                "Main Ollama stays on 3060 :11434",
                "Spare work: set CUDA_VISIBLE_DEVICES per session (do not flip User env casually)",
            ]
        if state == "PNP_DISABLED_CODE22":
            return [
                "1080 Ti is DISABLED (Code 22) — hardware present, software disabled",
                "Admin: `python -m drone bridge-1080 enable` (Enable-PnpDevice)",
                "If enable fails: check Device Manager; may need dual-GPU driver (Pascal+Ampere)",
                "See configs/bridge_1080.json honesty + ~/.ollama/fix-dual-gpu-admin.ps1",
            ]
        if state == "PNP_DRIVER_FAIL_CODE31":
            return [
                "1080 Ti driver fail (Code 31) — need driver that lists DEV_1B06 + 3060",
                "Typically: DDU clean + NVIDIA 580.x (or last branch listing Pascal)",
                "Do NOT keep Ampere-only 610 packs if you need both cards",
            ]
        if state == "PNP_OK_NOT_IN_SMI":
            return [
                "PnP OK but not in nvidia-smi — reboot or reinstall dual-capable driver",
            ]
        return ["Run probe; follow RED reason"]

    # ── enable / disable PnP ──────────────────────────────────

    def enable_pnp(self, *, force: bool = False) -> dict[str, Any]:
        """
        Try Enable-PnpDevice on 1080 Ti. Often requires Administrator.
        Does not kill Ollama. Re-classify after.
        """
        pnp = self.probe_pnp()
        if not pnp.get("found"):
            return {
                "status": "RED",
                "false_green": 0,
                "error": "1080 Ti not found in PnP",
                "pnp": pnp,
            }
        iid = pnp.get("instance_id")
        if not iid:
            return {"status": "RED", "false_green": 0, "error": "no instance_id", "pnp": pnp}

        # PowerShell enable
        ps = f"""
$ErrorActionPreference = 'Stop'
try {{
  Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false
  Start-Sleep -Seconds 2
  $d = Get-PnpDevice -InstanceId '{iid}'
  [pscustomobject]@{{ ok=$true; status=$d.Status; problem=$d.Problem; problem_description=$d.ProblemDescription }} | ConvertTo-Json -Compress
}} catch {{
  [pscustomobject]@{{ ok=$false; error=$_.Exception.Message }} | ConvertTo-Json -Compress
}}
"""
        code, out, err = _run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=60,
        )
        try:
            result = json.loads((out or "").strip() or "{}")
        except Exception:
            result = {"ok": False, "error": (err or out or "enable failed")[:400]}

        # wait and reclassify
        time.sleep(2)
        cls = self.classify()
        report = {
            "schema": "drone.bridge_1080.enable.v1",
            "utc": _utc(),
            "false_green": 0,
            "enable_result": result,
            "classify": {
                "bridge_state": cls.get("bridge_state"),
                "ready": cls.get("ready"),
                "status": cls.get("status"),
                "spare_gpu_smi": cls.get("spare_gpu_smi"),
                "pnp": cls.get("pnp"),
            },
            "status": "GREEN" if cls.get("ready") else "PARTIAL" if result.get("ok") else "RED",
            "server_killed": False,
            "main_ollama_untouched": True,
        }
        _write(self.state / "LAST_ENABLE.json", report)
        _write(self.out / "BRIDGE_1080_ENABLE.json", report)
        return report

    def disable_pnp(self) -> dict[str, Any]:
        """Disable 1080 Ti (return to spare offline). Admin may be required."""
        pnp = self.probe_pnp()
        iid = pnp.get("instance_id")
        if not iid:
            return {"status": "RED", "false_green": 0, "error": "no 1080 instance"}
        ps = f"""
try {{
  Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false
  $d = Get-PnpDevice -InstanceId '{iid}'
  [pscustomobject]@{{ ok=$true; status=$d.Status }} | ConvertTo-Json -Compress
}} catch {{
  [pscustomobject]@{{ ok=$false; error=$_.Exception.Message }} | ConvertTo-Json -Compress
}}
"""
        code, out, err = _run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=45,
        )
        try:
            result = json.loads((out or "").strip() or "{}")
        except Exception:
            result = {"ok": False, "error": err or out}
        cls = self.classify()
        return {
            "status": "GREEN" if result.get("ok") else "RED",
            "false_green": 0,
            "disable_result": result,
            "bridge_state": cls.get("bridge_state"),
            "utc": _utc(),
        }

    # ── arm / disarm session map ──────────────────────────────

    def arm(self, *, only_1080: bool = False, persist_user: bool = False) -> dict[str, Any]:
        """
        Arm CUDA session map for spare work.
        Fail closed if 1080 not LIVE in nvidia-smi.
        Does not kill main Ollama. Persist user env only if explicitly requested.
        """
        cls = self.classify()
        if not cls.get("ready"):
            return {
                "status": "RED",
                "false_green": 0,
                "error": "bridge_not_ready",
                "bridge_state": cls.get("bridge_state"),
                "next_steps": cls.get("next_steps"),
                "utc": _utc(),
            }

        order = "PCI_BUS_ID"
        visible = "0" if only_1080 else "0,1"
        # Write session arm file — processes that source this get the map
        arm = {
            "schema": "drone.bridge_1080.arm.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": "GREEN",
            "CUDA_DEVICE_ORDER": order,
            "CUDA_VISIBLE_DEVICES": visible,
            "only_1080": only_1080,
            "persist_user": persist_user,
            "main_ollama_port": 11434,
            "main_gpu": "RTX 3060",
            "spare_gpu": "GTX 1080 Ti",
            "how_to_use": {
                "powershell": [
                    f'$env:CUDA_DEVICE_ORDER="{order}"',
                    f'$env:CUDA_VISIBLE_DEVICES="{visible}"',
                    "nvidia-smi -L",
                ],
                "cmd": [
                    f"set CUDA_DEVICE_ORDER={order}",
                    f"set CUDA_VISIBLE_DEVICES={visible}",
                ],
            },
            "law": "Do not point default Ollama :11434 at 1080 without explicit boss order",
        }
        _write(self.state / "ARMED.json", arm)
        _write(self.out / "BRIDGE_1080_ARMED.json", arm)

        # Optional session env for *this* process
        os.environ["CUDA_DEVICE_ORDER"] = order
        os.environ["CUDA_VISIBLE_DEVICES"] = visible

        if persist_user:
            # careful — user asked explicitly
            try:
                _run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"[Environment]::SetEnvironmentVariable('CUDA_DEVICE_ORDER','{order}','User'); "
                        f"[Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES','{visible}','User'); "
                        "'OK'",
                    ],
                    timeout=15,
                )
                arm["user_env_written"] = True
            except Exception as e:
                arm["user_env_error"] = str(e)
                arm["user_env_written"] = False

        # generate apply scripts
        ps1 = self.state / "Apply-Arm-Session.ps1"
        ps1.write_text(
            f"""# Stable Bridge 1080 — session arm (sourced)
$env:CUDA_DEVICE_ORDER = '{order}'
$env:CUDA_VISIBLE_DEVICES = '{visible}'
Write-Host "Armed: CUDA_VISIBLE_DEVICES=$env:CUDA_VISIBLE_DEVICES (1080 bridge)"
nvidia-smi -L
""",
            encoding="utf-8",
        )
        arm["apply_script"] = str(ps1)
        _write(self.state / "ARMED.json", arm)
        return arm

    def disarm(self, *, restore_main_only: bool = True) -> dict[str, Any]:
        """
        Disarm spare map. Default restore: hide 1080 for CUDA again if dual layout
        used CUDA_VISIBLE_DEVICES=1 for 3060-only when dual cards live.
        When only 3060 is present, CUDA_VISIBLE_DEVICES=0 is correct.
        """
        cls = self.classify()
        gpus = (cls.get("nvidia_smi") or {}).get("gpus") or []
        # If only one GPU (3060), use 0. If dual would be live, main-only pin is 1.
        if len(gpus) >= 2:
            visible = "1"  # dual present: index 1 = 3060 under PCI order when 1080 is 0
        else:
            visible = "0"

        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        if restore_main_only:
            os.environ["CUDA_VISIBLE_DEVICES"] = visible

        report = {
            "schema": "drone.bridge_1080.disarm.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": "GREEN",
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "bridge_state": cls.get("bridge_state"),
            "note": "Session disarmed; main Ollama untouched",
        }
        armed = self.state / "ARMED.json"
        if armed.is_file():
            try:
                armed.rename(self.state / f"ARMED_prev_{int(time.time())}.json")
            except Exception:
                pass
        _write(self.state / "DISARMED.json", report)
        _write(self.out / "BRIDGE_1080_DISARMED.json", report)
        return report

    # ── LIVE daemon (no kill switch) ──────────────────────────

    def live_tick(self, *, try_enable: bool = True, auto_arm: bool = True) -> dict[str, Any]:
        """
        One live cycle: classify → optional enable retry → arm if ready.
        Bridge process stays up. Never kills Ollama / 3060.
        """
        enable_report = None
        arm_report = None
        cls = self.classify()

        # Retry enable while Code 22 (best-effort; needs admin often)
        if try_enable and not cls.get("ready"):
            st = cls.get("bridge_state") or ""
            if "CODE22" in st or "DISABLED" in st or st == "PNP_ERROR":
                enable_report = self.enable_pnp()
                cls = self.classify()

        # Auto-arm when hardware ready
        if auto_arm and cls.get("ready"):
            arm_path = self.state / "ARMED.json"
            already = False
            if arm_path.is_file():
                try:
                    already = json.loads(arm_path.read_text(encoding="utf-8")).get("status") == "GREEN"
                except Exception:
                    already = False
            if not already:
                arm_report = self.arm(only_1080=False, persist_user=False)
            else:
                arm_report = {"status": "GREEN", "already_armed": True}

        # LIVE = daemon is running (software live), hardware may still be RED
        live = {
            "schema": "drone.bridge_1080.live.v1",
            "utc": _utc(),
            "false_green": 0,
            "daemon": "LIVE",
            "daemon_status": "GREEN",
            "no_kill_switch": True,
            "no_kill_law": NO_KILL_LAW,
            "pid": os.getpid(),
            "hardware_ready": bool(cls.get("ready")),
            "hardware_status": cls.get("status"),
            "bridge_state": cls.get("bridge_state"),
            "main_gpu": cls.get("main_gpu"),
            "spare_gpu_smi": cls.get("spare_gpu_smi"),
            "pnp": {
                "status": (cls.get("pnp") or {}).get("status"),
                "problem": (cls.get("pnp") or {}).get("problem"),
                "problem_description": (cls.get("pnp") or {}).get("problem_description"),
            },
            "enable_last": {
                "status": (enable_report or {}).get("status"),
            }
            if enable_report
            else None,
            "arm_last": {
                "status": (arm_report or {}).get("status"),
                "already_armed": (arm_report or {}).get("already_armed"),
            }
            if arm_report
            else None,
            "main_ollama_untouched": True,
            "never_kills_3060": True,
        }
        # Combined status: daemon always GREEN when running; overall PARTIAL until hw ready
        live["status"] = "GREEN" if cls.get("ready") else "PARTIAL"
        _write(self.state / LIVE_FLAG, live)
        _write(self.out / "BRIDGE_1080_LIVE.json", live)
        return live

    def run_live(
        self,
        *,
        interval_s: float = 20.0,
        try_enable: bool = True,
        auto_arm: bool = True,
    ) -> int:
        """
        Permanent live loop. NO KILL SWITCH.
        Ignores SIGINT/SIGTERM/SIGBREAK — only OS/Boss Task Manager can stop.
        """
        interval_s = max(5.0, float(interval_s))
        pid = os.getpid()
        (self.state / LIVE_PID_FILE).write_text(str(pid), encoding="utf-8")

        def _ignore(signum: int, frame: Any) -> None:  # noqa: ARG001
            # no kill switch — log and continue
            try:
                with (self.state / "whisper.log").open("a", encoding="utf-8") as f:
                    f.write(f"{_utc()} signal {signum} IGNORED (no kill switch)\n")
            except Exception:
                pass

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _ignore)
                except Exception:
                    pass

        print(f"[bridge-1080] LIVE daemon pid={pid} interval={interval_s}s", flush=True)
        print(f"[bridge-1080] {NO_KILL_LAW}", flush=True)
        n = 0
        while True:
            try:
                live = self.live_tick(try_enable=try_enable, auto_arm=auto_arm)
                beat = {
                    "schema": "drone.bridge_1080.heartbeat.v1",
                    "utc": _utc(),
                    "tick": n,
                    "daemon": "LIVE",
                    "status": live.get("status"),
                    "bridge_state": live.get("bridge_state"),
                    "hardware_ready": live.get("hardware_ready"),
                    "no_kill_switch": True,
                    "pid": pid,
                    "false_green": 0,
                }
                _write(self.state / "HEARTBEAT.json", beat)
                _write(self.out / "BRIDGE_1080_HEARTBEAT.json", beat)
                print(
                    f"[{_utc()}] LIVE tick={n} hw={live.get('bridge_state')} "
                    f"ready={live.get('hardware_ready')} status={live.get('status')}",
                    flush=True,
                )
            except Exception as e:
                try:
                    with (self.state / "whisper.log").open("a", encoding="utf-8") as f:
                        f.write(f"{_utc()} live_tick error: {e}\n")
                except Exception:
                    pass
            n += 1
            # sleep in slices (still no exit on signal)
            slept = 0.0
            while slept < interval_s:
                time.sleep(min(1.0, interval_s - slept))
                slept += 1.0
        # unreachable
        return 0

    def start_live(
        self,
        *,
        interval_s: float = 20.0,
        with_watchdog: bool = True,
    ) -> dict[str, Any]:
        """Spawn detached LIVE daemon + optional watchdog (restarts daemon, never kills 3060)."""
        existing = _read_pid(LIVE_PID_FILE)
        if existing and _pid_alive(existing):
            # already live
            live = {}
            lf = self.state / LIVE_FLAG
            if lf.is_file():
                try:
                    live = json.loads(lf.read_text(encoding="utf-8"))
                except Exception:
                    live = {}
            return {
                "schema": "drone.bridge_1080.start.v1",
                "status": "GREEN",
                "already_running": True,
                "daemon": "LIVE",
                "pid": existing,
                "no_kill_switch": True,
                "no_kill_law": NO_KILL_LAW,
                "live": live,
                "false_green": 0,
                "utc": _utc(),
            }

        py = sys.executable
        bpid = _spawn_detached(
            [
                py,
                "-m",
                "drone",
                "bridge-1080",
                "live",
                "--interval",
                str(interval_s),
            ],
            "bridge.log",
        )
        # wait for LIVE.json
        ok = False
        for _ in range(30):
            time.sleep(0.3)
            if (self.state / LIVE_FLAG).is_file() or _pid_alive(bpid):
                # give one tick
                if (self.state / LIVE_FLAG).is_file() or (self.state / "HEARTBEAT.json").is_file():
                    ok = True
                    break
                if _pid_alive(bpid):
                    ok = True

        wpid = 0
        if with_watchdog:
            w_exist = _read_pid(WATCHDOG_PID_FILE)
            if not (w_exist and _pid_alive(w_exist)):
                wpid = _spawn_detached(
                    [
                        py,
                        "-m",
                        "drone",
                        "bridge-1080",
                        "watchdog",
                        "--interval",
                        str(interval_s),
                    ],
                    "watchdog.log",
                )
                (self.state / WATCHDOG_PID_FILE).write_text(str(wpid), encoding="utf-8")
            else:
                wpid = w_exist

        report = {
            "schema": "drone.bridge_1080.start.v1",
            "status": "GREEN" if ok or _pid_alive(bpid) else "PARTIAL",
            "false_green": 0,
            "utc": _utc(),
            "daemon": "LIVE",
            "spawned_pid": bpid,
            "watchdog_pid": wpid or None,
            "no_kill_switch": True,
            "no_kill_law": NO_KILL_LAW,
            "kill_command": None,
            "kill_refused": "bridge-1080 has NO kill switch by boss design",
            "main_ollama_untouched": True,
            "state_dir": str(self.state),
            "live_path": str(self.out / "BRIDGE_1080_LIVE.json"),
        }
        _write(self.state / "start_last.json", report)
        _write(self.out / "BRIDGE_1080_START.json", report)
        return report

    def run_watchdog(self, *, interval_s: float = 20.0) -> int:
        """Restart LIVE daemon if it dies. Has no kill path for main GPU."""
        interval_s = max(5.0, float(interval_s))
        (self.state / WATCHDOG_PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
        print(f"[bridge-1080] watchdog pid={os.getpid()} (no kill switch on bridge)", flush=True)

        def _ignore(signum: int, frame: Any) -> None:  # noqa: ARG001
            pass

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _ignore)
                except Exception:
                    pass

        while True:
            pid = _read_pid(LIVE_PID_FILE)
            if not pid or not _pid_alive(pid):
                # respawn live daemon
                _spawn_detached(
                    [
                        sys.executable,
                        "-m",
                        "drone",
                        "bridge-1080",
                        "live",
                        "--interval",
                        str(interval_s),
                    ],
                    "bridge.log",
                )
            time.sleep(interval_s)
        return 0

    def process_status(self) -> dict[str, Any]:
        bpid = _read_pid(LIVE_PID_FILE)
        wpid = _read_pid(WATCHDOG_PID_FILE)
        live = {}
        if (self.state / LIVE_FLAG).is_file():
            try:
                live = json.loads((self.state / LIVE_FLAG).read_text(encoding="utf-8"))
            except Exception:
                live = {}
        return {
            "schema": "drone.bridge_1080.process.v1",
            "false_green": 0,
            "utc": _utc(),
            "daemon_live": bool(bpid and _pid_alive(bpid)),
            "bridge_pid": bpid or None,
            "watchdog_pid": wpid or None,
            "watchdog_alive": bool(wpid and _pid_alive(wpid)),
            "no_kill_switch": True,
            "no_kill_law": NO_KILL_LAW,
            "live": {
                "status": live.get("status"),
                "bridge_state": live.get("bridge_state"),
                "hardware_ready": live.get("hardware_ready"),
                "daemon": live.get("daemon"),
            },
            "main_ollama_untouched": True,
        }

    def refuse_kill(self) -> dict[str, Any]:
        """Explicit no-kill-switch response."""
        return {
            "schema": "drone.bridge_1080.kill.v1",
            "status": "REFUSED",
            "false_green": 0,
            "utc": _utc(),
            "killed": False,
            "no_kill_switch": True,
            "error": "NO_KILL_SWITCH",
            "message": NO_KILL_LAW,
            "process": self.process_status(),
        }

    # ── watch / seal ──────────────────────────────────────────

    def watch(self, interval_s: float = 20.0, ticks: int = 0) -> int:
        """Foreground heartbeat (dev). Prefer `start` / `live` for no-kill daemon."""
        return self.run_live(interval_s=interval_s, try_enable=True, auto_arm=True)

    def seal(self) -> dict[str, Any]:
        cls = self.classify()
        proc = self.process_status()
        seal = {
            "schema": "drone.bridge_1080.seal.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": "GREEN"
            if proc.get("daemon_live") and cls.get("ready")
            else ("PARTIAL" if proc.get("daemon_live") else cls.get("status")),
            "daemon_live": proc.get("daemon_live"),
            "no_kill_switch": True,
            "no_kill_law": NO_KILL_LAW,
            "bridge_state": cls.get("bridge_state"),
            "ready": cls.get("ready"),
            "main_gpu": cls.get("main_gpu"),
            "spare_gpu_smi": cls.get("spare_gpu_smi"),
            "pnp": {
                "found": (cls.get("pnp") or {}).get("found"),
                "status": (cls.get("pnp") or {}).get("status"),
                "problem": (cls.get("pnp") or {}).get("problem"),
                "problem_description": (cls.get("pnp") or {}).get("problem_description"),
                "driver_version": (cls.get("pnp") or {}).get("driver_version"),
            },
            "process": proc,
            "next_steps": cls.get("next_steps"),
            "law": cls.get("law"),
            "evidence": [
                str(self.out / "BRIDGE_1080_STATUS.json"),
                str(self.out / "BRIDGE_1080_SEAL.json"),
                str(self.out / "BRIDGE_1080_LIVE.json"),
                str(self.state / "LAST_CLASSIFY.json"),
            ],
            "honesty": {
                "live_daemon_even_if_hw_red": True,
                "hardware_green_only_when_in_nvidia_smi": True,
                "code22_is_not_hardware_green": True,
                "3060_main_protected": True,
                "no_kill_switch": True,
            },
        }
        path = self.out / "BRIDGE_1080_SEAL.json"
        _write(path, seal)
        seal["path"] = str(path)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone bridge-1080")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start", help="start LIVE daemon + watchdog (NO kill switch)")
    st.add_argument("--interval", type=float, default=20.0)
    st.add_argument("--no-watchdog", action="store_true")
    lv = sub.add_parser("live", help="foreground/detached LIVE loop (no kill)")
    lv.add_argument("--interval", type=float, default=20.0)
    wd = sub.add_parser("watchdog", help="restart LIVE daemon if it dies")
    wd.add_argument("--interval", type=float, default=20.0)
    sub.add_parser("process", help="daemon pids + live flag")
    sub.add_parser("status", help="classify bridge state (honest)")
    sub.add_parser("probe", help="alias status")
    sub.add_parser("enable", help="Enable-PnpDevice 1080 Ti (may need admin)")
    sub.add_parser("disable", help="Disable-PnpDevice 1080 Ti")
    ar = sub.add_parser("arm", help="arm CUDA session map (fail closed if not LIVE)")
    ar.add_argument("--only-1080", action="store_true")
    ar.add_argument("--persist-user", action="store_true", help="write User env (careful)")
    sub.add_parser("disarm", help="disarm session map")
    w = sub.add_parser("watch", help="alias of live (no kill)")
    w.add_argument("--interval", type=float, default=20.0)
    w.add_argument("--ticks", type=int, default=0)
    sub.add_parser("seal", help="write BRIDGE_1080_SEAL.json")
    sub.add_parser("kill", help="REFUSED — no kill switch")

    args = p.parse_args(argv)
    b = Bridge1080(Path(args.root) if args.root else None)

    if args.cmd == "start":
        out = b.start_live(
            interval_s=float(args.interval),
            with_watchdog=not bool(args.no_watchdog),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "live":
        return b.run_live(interval_s=float(args.interval))
    if args.cmd == "watchdog":
        return b.run_watchdog(interval_s=float(args.interval))
    if args.cmd == "process":
        print(json.dumps(b.process_status(), indent=2))
        return 0
    if args.cmd == "kill":
        print(json.dumps(b.refuse_kill(), indent=2))
        return 2  # refused
    if args.cmd in {"status", "probe"}:
        print(json.dumps(b.classify(), indent=2))
        return 0
    if args.cmd == "enable":
        out = b.enable_pnp()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "disable":
        out = b.disable_pnp()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "arm":
        out = b.arm(only_1080=bool(args.only_1080), persist_user=bool(args.persist_user))
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "disarm":
        print(json.dumps(b.disarm(), indent=2))
        return 0
    if args.cmd == "watch":
        return b.run_live(interval_s=float(args.interval))
    if args.cmd == "seal":
        out = b.seal()
        print(json.dumps(out, indent=2))
        return 0 if out.get("path") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
