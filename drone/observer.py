"""
Silent Observer LLM — stays in the loop, never answers the user.

Law:
  - Informed by drone work (run reports, seals, diagnostics, modules)
  - Full internal comms with OS snapshot (nvidia-smi, disk, ollama)
  - NEVER handles user chat / MAIN input / chat tab
  - Writes informed state to disk only
  - false_green: 0

Enable: default ON if Ollama reachable, disable with DRONE_OBSERVER=0
Model: DRONE_OBSERVER_MODEL (default llama3.2:3b light under leash; 8b if set)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def observer_enabled() -> bool:
    v = (os.environ.get("DRONE_OBSERVER") or "1").strip().lower()
    return v not in {"0", "false", "off", "no"}


def observer_dir(root: Path) -> Path:
    d = Path(root) / "data" / "observer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def os_snapshot() -> dict[str, Any]:
    """Lightweight OS / host facts for the silent observer."""
    snap: dict[str, Any] = {"utc": _utc()}
    # GPU
    try:
        p = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        snap["gpu"] = {
            "ok": p.returncode == 0,
            "csv": (p.stdout or "").strip()[:400],
        }
    except Exception as e:
        snap["gpu"] = {"ok": False, "error": str(e)}

    # Disk free (quick)
    try:
        p = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-PSDrive C,G -ErrorAction SilentlyContinue | "
                "Select-Object Name,@{N='FreeGB';E={[math]::Round($_.Free/1GB,1)}} | "
                "ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        snap["disk"] = {
            "ok": p.returncode == 0,
            "json": (p.stdout or "").strip()[:500],
        }
    except Exception as e:
        snap["disk"] = {"ok": False, "error": str(e)}

    # Ollama
    try:
        import urllib.request

        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        snap["ollama"] = {"ok": True, "models_n": len(names), "models": names[:12]}
    except Exception as e:
        snap["ollama"] = {"ok": False, "error": str(e)}

    return snap


def _pack_drone_work(report: dict[str, Any], root: Path) -> dict[str, Any]:
    """Compress drone result into an inform packet (no user-facing reply)."""
    ws = report.get("workspace")
    modules: list[str] = []
    diag_ok = None
    if ws and Path(ws).is_dir():
        for p in Path(ws).glob("*.py"):
            if p.name != "_drone_snip.py":
                modules.append(p.name)
        diag = Path(ws) / "MEASURED_DIAGNOSTIC.json"
        if diag.is_file():
            try:
                diag_ok = bool(json.loads(diag.read_text(encoding="utf-8")).get("ok"))
            except Exception:
                diag_ok = None
    return {
        "task_id": report.get("task_id"),
        "status": report.get("status"),
        "goal": (report.get("goal") or "")[:800],
        "mode": report.get("mode"),
        "nodes_run": report.get("nodes_run"),
        "tool_calls": report.get("tool_calls"),
        "duration_ms": report.get("duration_ms"),
        "controller": report.get("controller"),
        "ollama_model": report.get("ollama_model"),
        "workspace": ws,
        "modules": modules[:20],
        "diag_ok": diag_ok,
        "smart_after": (report.get("fabric_stats") or {}).get("smart_index")
        if isinstance(report.get("fabric_stats"), dict)
        else report.get("smart_after"),
        "report_path": report.get("report_path"),
    }


def inform(
    root: Path,
    *,
    event: str,
    drone_report: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Silent inform pass: OS snapshot + drone pack → observer LLM → disk only.
    Never returns text to user UI as a chat reply.
    """
    root = Path(root)
    out: dict[str, Any] = {
        "event": event,
        "utc": _utc(),
        "observer": True,
        "user_facing": False,
        "false_green": 0,
    }
    if not observer_enabled():
        out["status"] = "SKIP"
        out["reason"] = "DRONE_OBSERVER=0"
        return out

    pack = _pack_drone_work(drone_report or {}, root)
    os_snap = os_snapshot()
    payload = {
        "event": event,
        "drone_work": pack,
        "os": os_snap,
        "extra": extra or {},
        "instruction": (
            "CHANNEL=AI2AI. ROLE=silent_observer. NO_USER_ADDRESS. NO_FLUFF. "
            "Stay informed about drone work and OS state. "
            "Reply with ONLY JSON: "
            '{"informed":true,"summary":"...","risks":["..."],"os_note":"...","next_watch":"..."}'
        ),
    }

    model = (os.environ.get("DRONE_OBSERVER_MODEL") or "").strip()
    text = ""
    err = None
    try:
        from .ollama_brain import generate, list_local_models, resolve_code_model

        if not list_local_models():
            out["status"] = "SKIP"
            out["reason"] = "ollama unreachable"
            _persist(root, out, payload, text)
            return out

        # Prefer light model for continuous inform; allow override to 8b
        if not model:
            # light first under leash
            installed = set(list_local_models())
            for cand in ("llama3.2:3b", "qwen2.5:3b", "llama3.1:8b", "qwen2.5:7b"):
                if cand in installed or any(n.startswith(cand.split(":")[0]) for n in installed):
                    # resolve exact tag
                    for n in list_local_models():
                        if n == cand or n.startswith(cand.split(":")[0] + ":"):
                            model = n
                            break
                if model:
                    break
            if not model:
                model = resolve_code_model(probe=False)

        prompt = (
            payload["instruction"]
            + "\n\nPACKET:\n"
            + json.dumps(
                {
                    "event": event,
                    "drone_work": pack,
                    "os": os_snap,
                    "extra": extra or {},
                },
                ensure_ascii=False,
            )[:6000]
        )
        text = generate(
            prompt,
            model=model,
            num_predict=int(os.environ.get("DRONE_OBSERVER_NUM_PREDICT", "256")),
            temperature=0.1,
        )
        out["status"] = "GREEN" if text.strip() else "RED"
        out["model"] = model
        out["informed"] = True
        # parse JSON if possible
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                out["observation"] = json.loads(text[start : end + 1])
            else:
                out["observation"] = {"summary": text[:800], "raw": True}
        except Exception:
            out["observation"] = {"summary": text[:800], "raw": True}
    except Exception as e:
        err = str(e)
        out["status"] = "RED"
        out["error"] = err
        out["informed"] = False

    out["drone_work"] = pack
    out["os"] = os_snap
    _persist(root, out, payload, text)
    return out


def _persist(
    root: Path,
    out: dict[str, Any],
    payload: dict[str, Any],
    raw_text: str,
) -> None:
    d = observer_dir(root)
    with root_lock(root):
        # append ledger
        led = d / "informed.jsonl"
        row = {
            "utc": out.get("utc"),
            "event": out.get("event"),
            "status": out.get("status"),
            "model": out.get("model"),
            "task_id": (out.get("drone_work") or {}).get("task_id"),
            "observation": out.get("observation"),
            "error": out.get("error"),
        }
        with led.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # latest full
        latest = {
            **out,
            "payload_meta": {
                "event": payload.get("event"),
                "user_facing": False,
            },
            "raw_text_preview": (raw_text or "")[:1500],
        }
        (d / "LATEST.json").write_text(
            json.dumps(latest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # human readable
        obs = out.get("observation") or {}
        md = [
            f"# Silent Observer — informed",
            f"",
            f"UTC: {out.get('utc')}",
            f"Event: {out.get('event')}",
            f"Status: {out.get('status')}",
            f"Model: {out.get('model')}",
            f"User-facing: **false** (never answers user)",
            f"",
            f"## Observation",
            f"```json",
            json.dumps(obs, indent=2, ensure_ascii=False)[:3000],
            f"```",
            f"",
            f"## Drone work (received)",
            f"```json",
            json.dumps(out.get("drone_work"), indent=2, ensure_ascii=False)[:2000],
            f"```",
            f"",
            f"## OS",
            f"```json",
            json.dumps(out.get("os"), indent=2, ensure_ascii=False)[:1500],
            f"```",
        ]
        (d / "LATEST.md").write_text("\n".join(md), encoding="utf-8")


def read_latest(root: Path) -> dict[str, Any]:
    p = observer_dir(root) / "LATEST.json"
    if not p.is_file():
        return {"status": "EMPTY", "user_facing": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "RED", "error": str(e), "user_facing": False}
