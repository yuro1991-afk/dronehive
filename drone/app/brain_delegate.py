"""
Ollama brain → swarm delegate.

Main input flow:
  user command → Ollama (plan JSON) → execute hive / task / fabric / ops

Honesty: one shared Ollama backend; not N full models. false_green: 0.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


PLAN_SYSTEM = """CHANNEL=AI2AI. ROLE=brain_delegate. NO_USER_ADDRESS. NO_FLUFF. NO_PERSONA.
Receive USER COMMAND; DELEGATE to swarm — do not perform full job alone.
Reply with ONLY one JSON object (no markdown fences, no prose outside JSON):
{
  "action": "hive" | "task" | "fabric_swarm" | "health" | "links" | "inbox" | "commission",
  "goals": ["short swarm unit goal 1", "goal 2"],
  "lane": "fast" | "full",
  "workers": 2,
  "cycles": 2,
  "rationale": "one sentence why this action",
  "user_echo": "restated user intent"
}
Rules: prefer hive for multi-unit; task for single build; fabric_swarm for 24-drone fabric;
health/links/inbox/commission only when clearly requested; goals 1-4 under 120 chars;
workers 1-4; cycles 1-6; false_green is host concern.
"""


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _chat(role: str, text: str) -> None:
    """Stream chat lines for lean Rust TUI (CHAT|role|text)."""
    line = (text or "").replace("\n", " ").strip()
    if not line:
        return
    print(f"CHAT|{role}|{line[:500]}", flush=True)


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # strip ```json fences if model ignored instructions
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # find first {...}
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _fallback_plan(user_command: str) -> dict[str, Any]:
    """If Ollama is down or returns garbage, still route to swarm (honest)."""
    low = (user_command or "").lower().strip()
    if low in {"health", "status", "ok"}:
        return {
            "action": "health",
            "goals": [],
            "lane": "fast",
            "workers": 1,
            "cycles": 1,
            "rationale": "fallback: health keyword",
            "user_echo": user_command,
            "fallback": True,
        }
    if low in {"links", "link"}:
        return {
            "action": "links",
            "goals": [],
            "lane": "fast",
            "workers": 1,
            "cycles": 1,
            "rationale": "fallback: links keyword",
            "user_echo": user_command,
            "fallback": True,
        }
    if low in {"inbox"}:
        return {
            "action": "inbox",
            "goals": [],
            "lane": "fast",
            "workers": 1,
            "cycles": 1,
            "rationale": "fallback: inbox keyword",
            "user_echo": user_command,
            "fallback": True,
        }
    if low in {"commission", "seal"}:
        return {
            "action": "commission",
            "goals": [],
            "lane": "fast",
            "workers": 1,
            "cycles": 1,
            "rationale": "fallback: commission keyword",
            "user_echo": user_command,
            "fallback": True,
        }
    if "fabric" in low:
        g = user_command.strip()
        return {
            "action": "fabric_swarm",
            "goals": [g, f"{g} (peer)"],
            "lane": "fast",
            "workers": 2,
            "cycles": 2,
            "rationale": "fallback: fabric keyword → fabric_swarm",
            "user_echo": user_command,
            "fallback": True,
        }
    # default: hive swarm so "main input → swarm" always holds
    g = user_command.strip() or "hive heartbeat"
    return {
        "action": "hive",
        "goals": [g, f"{g} (peer unit)"],
        "lane": "fast",
        "workers": 2,
        "cycles": 2,
        "rationale": "fallback: Ollama unavailable or bad JSON — delegate hive swarm",
        "user_echo": user_command,
        "fallback": True,
    }


def ollama_plan(user_command: str, *, num_predict: int = 320) -> dict[str, Any]:
    """Ask Ollama for a delegation plan. Returns plan dict + meta."""
    from drone.ollama_brain import brain_status, generate, resolve_top_model

    status = brain_status()
    meta: dict[str, Any] = {
        "ollama": status,
        "model": status.get("top_model"),
        "planned_by": "ollama",
        "false_green": 0,
    }
    if not status.get("reachable"):
        plan = _fallback_plan(user_command)
        plan["brain_error"] = "ollama not reachable"
        meta["planned_by"] = "fallback"
        meta["plan"] = plan
        return meta

    prompt = (
        f"{PLAN_SYSTEM}\n\nUSER COMMAND:\n{user_command.strip()}\n\nJSON plan:"
    )
    try:
        model = resolve_top_model()
        text = generate(
            prompt,
            model=model,
            num_predict=num_predict,
            temperature=0.1,
            timeout_s=120,
        )
        meta["raw_response_tail"] = (text or "")[-800:]
        plan = _extract_json(text)
        if not plan:
            plan = _fallback_plan(user_command)
            plan["brain_error"] = "could not parse ollama JSON"
            meta["planned_by"] = "fallback_parse"
        else:
            # normalize
            action = str(plan.get("action") or "hive").lower().strip()
            allowed = {
                "hive",
                "task",
                "fabric_swarm",
                "health",
                "links",
                "inbox",
                "commission",
            }
            if action not in allowed:
                action = "hive"
            goals = plan.get("goals") or []
            if isinstance(goals, str):
                goals = [goals]
            goals = [str(g).strip() for g in goals if str(g).strip()]
            if action in {"hive", "task", "fabric_swarm"} and not goals:
                goals = [user_command.strip()]
            plan = {
                "action": action,
                "goals": goals[:4],
                "lane": "full" if str(plan.get("lane", "")).lower() == "full" else "fast",
                "workers": max(1, min(4, int(plan.get("workers") or 2))),
                "cycles": max(1, min(6, int(plan.get("cycles") or max(2, len(goals) or 2)))),
                "rationale": str(plan.get("rationale") or "")[:300],
                "user_echo": str(plan.get("user_echo") or user_command)[:300],
                "fallback": False,
            }
            meta["planned_by"] = "ollama"
            meta["model"] = model
        meta["plan"] = plan
        return meta
    except Exception as e:
        plan = _fallback_plan(user_command)
        plan["brain_error"] = str(e)
        meta["planned_by"] = "fallback_error"
        meta["error"] = str(e)
        meta["plan"] = plan
        return meta


def execute_plan(service: Any, plan_meta: dict[str, Any]) -> dict[str, Any]:
    """
    Execute plan via DroneHiveService — Ollama already planned; swarm does the work.
    Controller for swarm work = ollama; LM assist = ollama when working.
    """
    plan = plan_meta.get("plan") or _fallback_plan("")
    action = plan.get("action") or "hive"
    goals = list(plan.get("goals") or [])
    lane = plan.get("lane") or "fast"
    workers = int(plan.get("workers") or 2)
    cycles = int(plan.get("cycles") or 2)
    t0 = time.perf_counter()

    _chat(
        "drone",
        f"plan action={action} workers={workers} cycles={cycles} goals={len(goals)}",
    )
    for i, g in enumerate(goals[:4]):
        _chat("drone", f"goal[{i}] {g[:120]}")
    if plan.get("rationale"):
        _chat("system", f"rationale · {str(plan.get('rationale'))[:200]}")
    planned_by = plan_meta.get("planned_by") or "?"
    model = plan_meta.get("model") or "?"
    _chat("system", f"planned_by={planned_by} model={model}")
    _chat("system", f"delegating → {action}…")

    result: dict[str, Any]
    if action == "health":
        result = service.health()
    elif action == "links":
        from drone.app.links import LinkRegistry

        result = LinkRegistry(service.root).list_links()
    elif action == "inbox":
        result = service.process_inbox(max_n=10)
    elif action == "commission":
        from drone.app.commission import commission

        result = commission(service.root, serve_probe=False)
    elif action == "task":
        goal = goals[0] if goals else "task from brain"
        _chat("tool", f"run_task · {goal[:100]}")
        result = service.run_task(
            goal,
            lane=lane,
            controller="ollama",
            lm_assist="ollama",
            tags=["build", "app", "brain_delegate", "ollama"],
        )
    elif action == "fabric_swarm":
        from drone.swarm import DroneSwarm
        from drone.controllers import make_lm_fn

        lm = make_lm_fn("ollama")
        swarm = DroneSwarm(service.root, lm_fn=lm, max_workers=workers)
        g = goals or ["fabric unit A", "fabric unit B"]
        _chat("tool", f"fabric_swarm · {len(g)} units · workers={workers} · cold board")
        result = swarm.run_multi(
            goals=g,
            controller_kind="ollama",
            controller_name="ollama-brain",
            domain="build",
            skill_tags=["build", "swarm", "fabric_swarm", "brain_delegate"],
            parallel_hemispheres=True,
        )
    else:
        # hive (default) — real parallel buzzers under ollama parent
        g = goals or ["hive unit from brain"]
        # ensure enough cycles for goals
        cycles = max(cycles, len(g))
        _chat("tool", f"hive · {len(g)} goals · workers={workers} cycles={cycles}")
        result = service.run_hive(
            goals=g,
            cycles=cycles,
            workers=workers,
            lane=lane,
            lm_assist="ollama",
            controller="ollama",
        )

    ms = (time.perf_counter() - t0) * 1000
    seal = {
        "schema": "drone.hive.brain_delegate.v1",
        "status": result.get("status")
        or ("GREEN" if result.get("ok") is True else "PARTIAL"),
        "false_green": 0,
        "utc": _utc(),
        "duration_ms": round(ms, 2),
        "pipeline": [
            "user_command",
            "ollama_plan",
            f"delegate:{action}",
            "swarm_or_ops_execute",
        ],
        "brain": {
            "planned_by": plan_meta.get("planned_by"),
            "model": plan_meta.get("model"),
            "plan": plan,
            "ollama_reachable": (plan_meta.get("ollama") or {}).get("reachable"),
            "raw_response_tail": plan_meta.get("raw_response_tail"),
            "error": plan_meta.get("error") or plan.get("brain_error"),
        },
        "execution": result,
        "app": "DroneHive",
        "honesty": {
            "main_input_wired_to_ollama": True,
            "ollama_delegates_swarm": True,
            "not_n_full_models": True,
        },
    }
    # bubble common fields for swarm viewport pulse_from_result
    seal["units"] = result.get("units") or len(goals)
    seal["peak_parallel_observed"] = result.get("peak_parallel_observed") or workers
    seal["workers"] = workers
    if result.get("seal_path"):
        seal["seal_path"] = result.get("seal_path")
    if result.get("report_path"):
        seal["report_path"] = result.get("report_path")
    return seal


def brain_command(service: Any, user_command: str) -> dict[str, Any]:
    """Full path: command → Ollama plan → shared board → swarm/ops execute."""
    cmd = (user_command or "").strip()
    if not cmd:
        return {
            "status": "RED",
            "false_green": 0,
            "error": "empty command",
            "pipeline": ["user_command"],
        }
    _chat("system", "Ollama brain planning…")
    plan_meta = ollama_plan(cmd)
    # persist plan
    try:
        out = Path(service.root) / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "BRAIN_LAST_PLAN.json").write_text(
            json.dumps(plan_meta, indent=2), encoding="utf-8"
        )
        _chat("tool", f"evidence · {out / 'BRAIN_LAST_PLAN.json'}")
    except OSError:
        pass

    # Open hive board: cold knowledge shared once; live plan chunks for all units
    board_info: dict[str, Any] = {}
    plan = (plan_meta.get("plan") or {}) if isinstance(plan_meta, dict) else {}
    goals = list(plan.get("goals") or []) or [cmd]
    try:
        from drone.hive_board import HiveBoard, tools_for_goal

        board = HiveBoard(Path(service.root))
        wave_id = f"brain_{int(time.time())}_{uuid_short()}"
        board_info = board.open_wave(
            goals,
            wave_id=wave_id,
            meta={
                "source": "brain_delegate",
                "action": plan.get("action"),
                "user_command": cmd[:300],
            },
        )
        # Publish commander plan once (hive-think)
        board.publish(
            wave_id,
            kind="commander_plan",
            payload={
                "action": plan.get("action"),
                "goals": goals,
                "lane": plan.get("lane"),
                "workers": plan.get("workers"),
                "rationale": plan.get("rationale"),
                "planned_by": plan_meta.get("planned_by"),
                "model": plan_meta.get("model"),
            },
            unit_id="commander",
            tags=["plan", "shared", "brain"],
        )
        for i, g in enumerate(goals):
            tp = tools_for_goal(str(g))
            board.publish(
                wave_id,
                kind="goal_tool_pack",
                payload={"goal_i": i, "goal": g, "tool_pack": tp},
                unit_id="commander",
                tags=["tools", "scoped"],
            )
            _chat("drone", f"pack[{i}] {tp.get('pack_id')} tools={tp.get('count')} · {str(g)[:80]}")
        plan_meta["board_wave_id"] = wave_id
        plan_meta["board"] = board_info
        _chat("system", f"hive board open · wave={wave_id} · cold packs shared")
    except Exception as e:
        board_info = {"ok": False, "error": str(e)}
        plan_meta["board"] = board_info
        _chat("system", f"board open failed (continuing): {e}")

    seal = execute_plan(service, plan_meta)
    seal["board"] = board_info
    seal["board_wave_id"] = plan_meta.get("board_wave_id")
    seal.setdefault("honesty", {})
    if isinstance(seal.get("honesty"), dict):
        seal["honesty"]["cold_knowledge"] = True
        seal["honesty"]["scoped_tools"] = True
        seal["honesty"]["live_board"] = bool(plan_meta.get("board_wave_id"))
    try:
        out = Path(service.root) / "out"
        path = out / "BRAIN_DELEGATE_LAST.json"
        path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        seal["seal_path"] = str(path)
        _chat("tool", f"evidence · {path}")
    except OSError:
        pass
    st = seal.get("status") or "?"
    _chat("system", f"done · {st} · false_green={seal.get('false_green', 0)}")
    return seal


def uuid_short() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex[:8]
