"""
Drone Work Order — imprint, codex assist, live registry, memory recycle, fresh next.

Lifecycle law:
  imprint → execute → live registry → recycle dump → discard → fresh drone + next task

Honesty: false_green 0. No fabricated registry/codex success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_python() -> Path:
    return Path(
        os.environ.get(
            "DRONE_PYTHON",
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs",
                "Python",
                "Python312",
                "python.exe",
            ),
        )
    )


def load_work_order_config(root: Path) -> dict[str, Any]:
    path = Path(root) / "configs" / "work_order.json"
    if not path.is_file():
        return {
            "schema": "ai.worker.drone.work_order.v1",
            "missing": True,
            "path": str(path),
            "false_green": 0,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["path"] = str(path)
    data["missing"] = False
    return data


def _goal_needs_codex(goal: str, cfg: dict[str, Any], explicit: bool | None) -> bool:
    if explicit is True:
        return True
    if explicit is False:
        return False
    g = (goal or "").lower()
    keys = (cfg.get("codex") or {}).get("required_when") or []
    return any(k.lower() in g for k in keys)


def build_task_slot(
    goal: str,
    *,
    source: str = "explicit",
    domain: str = "build",
    skill_tags: list[str] | None = None,
    codex_required: bool | None = None,
    codex_query: str = "",
    continuous_task_id: str | None = None,
    priority: int = 99,
    next_action: str = "",
    task_id: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    tags = list(skill_tags or [])
    if "work_order" not in tags:
        tags.insert(0, "work_order")
    need = _goal_needs_codex(goal, cfg, codex_required)
    q = (codex_query or "").strip()
    if need and not q:
        # sensible default query from goal keywords
        gl = goal.lower()
        if "vision" in gl:
            q = "use vision"
        elif "embed" in gl:
            q = "use embedding"
        elif "reason" in gl:
            q = "use reasoning"
        elif any(x in gl for x in ("code", "coder", "build", "forge", "draft")):
            q = "use coding"
        elif any(x in gl for x in ("tiny", "3b", "edge")):
            q = "use tiny_edge"
        else:
            q = "stats"
    tid = task_id or f"{source}:{uuid.uuid4().hex[:10]}"
    return {
        "task_id": tid,
        "goal": (goal or "").strip(),
        "next_action": (next_action or goal or "").strip(),
        "source": source,
        "domain": domain,
        "skill_tags": tags,
        "codex_required": need,
        "codex_query": q,
        "evidence_paths_expected": [],
        "priority": priority,
        "continuous_task_id": continuous_task_id,
    }


def imprint_drone(
    root: Path,
    *,
    buzzer_id: str,
    generation: int,
    task: dict[str, Any],
    controller_kind: str = "local",
    controller_name: str = "hive",
    swarm_system: str = "buzzer_hive",
) -> dict[str, Any]:
    """
    Write the work-order imprint for a fresh drone (active imprint file).

    swarm_system:
      - buzzer_hive  → BuzzerHive / clean-slate buzzers (hive / swarm CLI)
      - fabric_swarm → DroneSwarm multi-goal fabric fan-out (24 drones L||R)
    """
    root = Path(root)
    cfg = load_work_order_config(root)
    systems = (cfg.get("swarm_systems") or {})
    sys_meta = systems.get(swarm_system) or {"id": swarm_system}
    imprint = {
        "schema": "ai.worker.drone.imprint.v1",
        "utc": _utc(),
        "buzzer_id": buzzer_id,
        "unit_id": buzzer_id,
        "generation": generation,
        "swarm_system": swarm_system,
        "swarm_system_meta": sys_meta,
        "controller": {"kind": controller_kind, "name": controller_name},
        "laws": cfg.get("ai_laws") or {},
        "codex": {
            "root": (cfg.get("codex") or {}).get("root"),
            "query_cli": (cfg.get("codex") or {}).get("query_cli"),
            "master_min": (cfg.get("codex") or {}).get("master_min"),
            "required_for_this_task": bool(task.get("codex_required")),
            "query": task.get("codex_query") or "",
            "commands": (cfg.get("codex") or {}).get("commands"),
            "decision_tree": (cfg.get("codex") or {}).get("decision_tree"),
            "host_muscle_defaults": (cfg.get("codex") or {}).get("host_muscle_defaults"),
        },
        "task": task,
        "lifecycle": cfg.get("lifecycle") or [],
        "live_registry": cfg.get("live_registry") or {},
        "memory_recycle": cfg.get("memory_recycle") or {},
        "agreement": cfg.get("agreement") or "",
        "false_green": 0,
        "docs": cfg.get("docs") or "docs/WORK_ORDER.md",
        "work_order_config": str(root / "configs" / "work_order.json"),
        "ready_to_swarm": True,
    }

    active_rel = (cfg.get("imprints") or {}).get("active_rel") or "data/hive/imprints/active"
    active_dir = root / active_rel
    with root_lock(root):
        active_dir.mkdir(parents=True, exist_ok=True)
        path = active_dir / f"{buzzer_id}.json"
        path.write_text(json.dumps(imprint, indent=2), encoding="utf-8")
        imprint["imprint_path"] = str(path)
    return imprint


def query_codex(
    root: Path,
    query: str,
    *,
    timeout_s: int = 45,
) -> dict[str, Any]:
    """
    Run LLM Frameworks Codex CLI. Returns real exit code; never fakes success.
    """
    root = Path(root)
    cfg = load_work_order_config(root)
    codex = cfg.get("codex") or {}
    cli = Path(codex.get("query_cli") or r"F:\GrokSelfLibrary\bin\query_llm_codex.py")
    py = _default_python()
    if not cli.is_file():
        return {
            "ok": False,
            "attempted": False,
            "error": "query_llm_codex.py missing",
            "cli": str(cli),
            "false_green": 0,
        }
    if not py.is_file():
        return {
            "ok": False,
            "attempted": False,
            "error": "python missing",
            "python": str(py),
            "false_green": 0,
        }
    parts = [p for p in re.split(r"\s+", (query or "stats").strip()) if p]
    if not parts:
        parts = ["stats"]
    cmd = [str(py), str(cli), *parts]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(cli.parent.parent) if cli.parent.name == "bin" else str(root),
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        return {
            "ok": proc.returncode == 0,
            "attempted": True,
            "returncode": proc.returncode,
            "cmd": cmd,
            "query": " ".join(parts),
            "stdout_tail": out[-2000:],
            "stderr_tail": err[-600:],
            "false_green": 0,
        }
    except Exception as e:
        return {
            "ok": False,
            "attempted": True,
            "error": str(e),
            "cmd": cmd,
            "false_green": 0,
        }


def write_live_registry(
    root: Path,
    *,
    buzzer_id: str,
    generation: int,
    task: dict[str, Any],
    status: str,
    evidence: list[str],
    duration_ms: float = 0.0,
    hive_doc_id: str = "",
    seal_path: str = "",
    recycle_path: str = "",
    codex_used: bool = False,
    model: str = "local",
    swarm_system: str = "buzzer_hive",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Append a drone event to Super Cell live registry (mirrors to F:).
    Used by BOTH buzzer_hive and fabric_swarm.
    """
    root = Path(root)
    cfg = load_work_order_config(root)
    reg = cfg.get("live_registry") or {}
    cli = Path(reg.get("cli") or r"G:\AI-Center\agents\super-cell-4\bridges\live_registry.py")
    py = _default_python()
    gate = (status or "RED").upper()
    if gate not in {"GREEN", "PARTIAL", "RED"}:
        gate = "RED"
    ok = gate == "GREEN" and bool([e for e in evidence if e])
    role = "buzzer" if swarm_system == "buzzer_hive" else "fabric_swarm_unit"
    event: dict[str, Any] = {
        "schema": "jane.live_registry.v1",
        "kind": reg.get("kind") or "drone",
        "expert": reg.get("expert") or "hive",
        "role": role,
        "lane": reg.get("lane") or "drone",
        "swarm_system": swarm_system,
        "buzzer_id": buzzer_id,
        "unit_id": buzzer_id,
        "generation": generation,
        "task_id": task.get("task_id"),
        "goal": task.get("goal"),
        "source": task.get("source"),
        "gate": gate,
        "status": gate,
        "ok": ok,
        "false_green": 0,
        "evidence": [e for e in evidence if e][:16],
        "hive_doc_id": hive_doc_id,
        "seal_path": seal_path,
        "recycle_path": recycle_path,
        "codex_used": codex_used,
        "model": model or "local",
        "sec": round(float(duration_ms) / 1000.0, 4),
        "ms": round(float(duration_ms), 2),
        "run_id": buzzer_id,
    }
    if extra:
        event.update(extra)

    if not cli.is_file() or not py.is_file():
        # Fallback: write local mirror under hive if registry CLI missing
        local = root / "data" / "hive" / "registry_local_events.jsonl"
        with root_lock(root):
            local.parent.mkdir(parents=True, exist_ok=True)
            with local.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return {
            "ok": False,
            "attempted": True,
            "fallback": str(local),
            "error": "live_registry.py or python missing — wrote local hive events only",
            "event": event,
            "false_green": 0,
        }

    try:
        proc = subprocess.run(
            [str(py), str(cli), "append", "--event", json.dumps(event, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(cli.parent),
        )
        out = (proc.stdout or "").strip()
        payload: Any = out
        try:
            payload = json.loads(out) if out else None
        except json.JSONDecodeError:
            pass
        return {
            "ok": proc.returncode == 0,
            "attempted": True,
            "returncode": proc.returncode,
            "stdout": payload,
            "stderr_tail": ((proc.stderr or "")[-400:]),
            "event": event,
            "false_green": 0,
        }
    except Exception as e:
        return {
            "ok": False,
            "attempted": True,
            "error": str(e),
            "event": event,
            "false_green": 0,
        }


def recycle_memory(
    root: Path,
    *,
    buzzer_id: str,
    imprint: dict[str, Any],
    scratch: dict[str, Any],
    result: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    """
    Dump ephemeral files into memory_recycle, then caller clears scratch.
    """
    root = Path(root)
    cfg = load_work_order_config(root)
    rel = (cfg.get("memory_recycle") or {}).get("root_rel") or "data/hive/memory_recycle"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dump_dir = root / rel / f"{stamp}_{buzzer_id}"
    files: dict[str, str] = {}
    with root_lock(root):
        dump_dir.mkdir(parents=True, exist_ok=True)

        def _w(name: str, obj: Any) -> str:
            p = dump_dir / name
            text = json.dumps(obj, indent=2, ensure_ascii=False)
            p.write_text(text, encoding="utf-8")
            files[name] = str(p)
            return text

        _w("scratch.json", scratch or {})
        _w("imprint.json", imprint or {})
        _w("result.json", result or {})
        _w(
            "evidence_index.json",
            {
                "utc": _utc(),
                "buzzer_id": buzzer_id,
                "paths": [e for e in evidence if e],
            },
        )
        manifest = {
            "schema": "ai.worker.drone.memory_recycle.v1",
            "utc": _utc(),
            "buzzer_id": buzzer_id,
            "dump_dir": str(dump_dir),
            "files": files,
            "sha256": {},
            "false_green": 0,
        }
        for name, path in files.items():
            raw = Path(path).read_bytes()
            manifest["sha256"][name] = hashlib.sha256(raw).hexdigest()
        man_path = dump_dir / ((cfg.get("memory_recycle") or {}).get("manifest_name") or "MANIFEST.json")
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        files["MANIFEST.json"] = str(man_path)

        # archive imprint to history
        hist_rel = (cfg.get("imprints") or {}).get("history_rel") or "data/hive/imprints/history"
        hist_dir = root / hist_rel
        hist_dir.mkdir(parents=True, exist_ok=True)
        hist_path = hist_dir / f"{buzzer_id}.json"
        hist_path.write_text(
            json.dumps(
                {
                    "utc": _utc(),
                    "buzzer_id": buzzer_id,
                    "imprint": imprint,
                    "result_status": result.get("status"),
                    "recycle_dir": str(dump_dir),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # remove active imprint if present
        active_rel = (cfg.get("imprints") or {}).get("active_rel") or "data/hive/imprints/active"
        active = root / active_rel / f"{buzzer_id}.json"
        if active.is_file():
            try:
                active.unlink()
            except OSError:
                pass

    return {
        "ok": True,
        "dump_dir": str(dump_dir),
        "files": files,
        "history_path": str(hist_path),
        "false_green": 0,
    }


def write_next_imprint(
    root: Path,
    *,
    next_task: dict[str, Any] | None = None,
    last_buzzer_id: str = "",
    last_status: str = "",
    swarm_system: str = "buzzer_hive",
) -> dict[str, Any]:
    """
    Point NEXT.json at the next task so a fresh drone can be written and swarm again.
    Applies to both swarm systems.
    """
    root = Path(root)
    cfg = load_work_order_config(root)
    next_rel = (cfg.get("imprints") or {}).get("next_rel") or "data/hive/imprints/NEXT.json"
    path = root / next_rel
    task = next_task or {
        "task_id": f"await:{uuid.uuid4().hex[:8]}",
        "goal": "await next swarm unit from library or queue",
        "next_action": "pull library open tasks or hive queue",
        "source": "await",
        "domain": "ops",
        "skill_tags": ["work_order", "hive", "await"],
        "codex_required": False,
        "codex_query": "",
        "priority": 99,
    }
    blob = {
        "schema": "ai.worker.drone.next_imprint.v1",
        "ready": True,
        "utc": _utc(),
        "swarm_system": swarm_system,
        "task": task,
        "laws_ref": cfg.get("docs") or "docs/WORK_ORDER.md",
        "codex_ref": (cfg.get("codex") or {}).get("master_min"),
        "last_buzzer_id": last_buzzer_id,
        "last_status": last_status,
        "false_green": 0,
        "note": "Fresh drone will be imprinted with this task when a free swarm slot opens (hive or fabric)",
    }
    with root_lock(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "next": blob, "false_green": 0}


def close_drone_lifecycle(
    root: Path,
    *,
    buzzer_id: str,
    generation: int,
    imprint: dict[str, Any],
    scratch: dict[str, Any],
    result: dict[str, Any],
    next_task: dict[str, Any] | None = None,
    model: str = "local",
    swarm_system: str = "buzzer_hive",
) -> dict[str, Any]:
    """
    Post-execute: live registry → memory recycle → NEXT imprint for fresh drone.
    Shared by BOTH buzzer_hive and fabric_swarm.
    """
    evidence = []
    fabric = result.get("fabric") or {}
    for k in ("report_path", "build_path", "seal_path"):
        v = result.get(k) or fabric.get(k)
        if v:
            evidence.append(str(v))
    if result.get("hive_doc_id"):
        evidence.append(f"hive_doc:{result.get('hive_doc_id')}")
    if result.get("library_note_path"):
        evidence.append(str(result["library_note_path"]))
    if result.get("imprint_path"):
        evidence.append(str(result["imprint_path"]))

    recycle = recycle_memory(
        root,
        buzzer_id=buzzer_id,
        imprint=imprint,
        scratch=scratch,
        result=result,
        evidence=evidence,
    )
    if recycle.get("dump_dir"):
        evidence.append(str(recycle["dump_dir"]))

    reg = write_live_registry(
        root,
        buzzer_id=buzzer_id,
        generation=generation,
        task=imprint.get("task") or {},
        status=str(result.get("status") or "RED"),
        evidence=evidence,
        duration_ms=float(result.get("duration_ms") or 0),
        hive_doc_id=str(result.get("hive_doc_id") or ""),
        seal_path=str(result.get("seal_path") or ""),
        recycle_path=str(recycle.get("dump_dir") or ""),
        codex_used=bool((result.get("codex") or {}).get("attempted")),
        model=model,
        swarm_system=swarm_system,
        extra={
            "codex": result.get("codex"),
            "goal_source": result.get("goal_source"),
            "swarm_system": swarm_system,
        },
    )

    nxt = write_next_imprint(
        root,
        next_task=next_task,
        last_buzzer_id=buzzer_id,
        last_status=str(result.get("status") or ""),
        swarm_system=swarm_system,
    )

    return {
        "ok": True,
        "false_green": 0,
        "swarm_system": swarm_system,
        "live_registry": reg,
        "recycle": recycle,
        "next_imprint": nxt,
        "lifecycle_complete": [
            "write_live_registry",
            "memory_recycle_dump",
            "write_fresh_drone_next_task",
        ],
    }


def show_work_order(root: Path) -> dict[str, Any]:
    """Status view for CLI."""
    root = Path(root)
    cfg = load_work_order_config(root)
    md = root / "docs" / "WORK_ORDER.md"
    next_rel = (cfg.get("imprints") or {}).get("next_rel") or "data/hive/imprints/NEXT.json"
    next_path = root / next_rel
    recycle_rel = (cfg.get("memory_recycle") or {}).get("root_rel") or "data/hive/memory_recycle"
    recycle_dir = root / recycle_rel
    active_rel = (cfg.get("imprints") or {}).get("active_rel") or "data/hive/imprints/active"
    active_dir = root / active_rel
    n_recycle = 0
    n_active = 0
    if recycle_dir.is_dir():
        n_recycle = sum(1 for p in recycle_dir.iterdir() if p.is_dir())
    if active_dir.is_dir():
        n_active = sum(1 for p in active_dir.glob("*.json"))
    next_blob = None
    if next_path.is_file():
        try:
            next_blob = json.loads(next_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            next_blob = {"error": "NEXT.json unreadable"}
    return {
        "schema": "ai.worker.drone.work_order_status.v1",
        "utc": _utc(),
        "false_green": 0,
        "config_path": cfg.get("path"),
        "config_ok": not cfg.get("missing"),
        "docs_path": str(md),
        "docs_ok": md.is_file(),
        "agreement": cfg.get("agreement"),
        "lifecycle": cfg.get("lifecycle"),
        "swarm_systems": cfg.get("swarm_systems") or {
            "buzzer_hive": {"module": "drone.hive.BuzzerHive"},
            "fabric_swarm": {"module": "drone.swarm.DroneSwarm"},
        },
        "codex_cli": (cfg.get("codex") or {}).get("query_cli"),
        "live_registry_cli": (cfg.get("live_registry") or {}).get("cli"),
        "active_imprints": n_active,
        "recycle_dumps": n_recycle,
        "history_imprints": (
            sum(1 for _ in (root / ((cfg.get("imprints") or {}).get("history_rel") or "data/hive/imprints/history")).glob("*.json"))
            if (root / ((cfg.get("imprints") or {}).get("history_rel") or "data/hive/imprints/history")).is_dir()
            else 0
        ),
        "next": next_blob,
        "paths": {
            "active": str(active_dir),
            "recycle": str(recycle_dir),
            "next": str(next_path),
        },
    }
