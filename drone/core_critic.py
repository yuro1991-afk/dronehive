"""
Third LLM — constant SENTIENT / CORE CRITIC loop.

Role:
  - Critically thinks for the CORE ENGINE (not chat with the user)
  - Reads: drone reports, observer LATEST, OS, skills, seals
  - Writes: data/core_critic/ (insights the engine can use next cycle)
  - Optional continuous loop under LEASH (slow interval)

Law:
  - user_facing: false for chat; engine_facing: true
  - false_green: 0 — must call out weak evidence when seen
  - DRONE_CRITIC=0 disables; default ON with light model under leash
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .locks import root_lock
from .observer import os_snapshot, read_latest as observer_latest


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def critic_enabled() -> bool:
    v = (os.environ.get("DRONE_CRITIC") or "1").strip().lower()
    return v not in {"0", "false", "off", "no"}


def critic_dir(root: Path) -> Path:
    d = Path(root) / "data" / "core_critic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def loop_interval_s() -> int:
    # LEASH: default 90s between constant-loop ticks
    try:
        return max(30, int(os.environ.get("DRONE_CRITIC_INTERVAL_S", "90")))
    except ValueError:
        return 90


def _gather_engine_state(root: Path) -> dict[str, Any]:
    root = Path(root)
    out = root / "out"
    state: dict[str, Any] = {"utc": _utc(), "root": str(root)}

    # latest run
    runs = sorted(out.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if runs:
        try:
            state["latest_run"] = json.loads(runs[0].read_text(encoding="utf-8"))
            state["latest_run_path"] = str(runs[0])
        except Exception as e:
            state["latest_run_error"] = str(e)

    # latest swarm
    swarms = sorted(out.glob("swarm_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if swarms:
        try:
            raw = json.loads(swarms[0].read_text(encoding="utf-8"))
            state["latest_swarm"] = {
                "path": str(swarms[0]),
                "status": raw.get("status"),
                "goals_n": raw.get("goals_n"),
                "duration_ms": raw.get("duration_ms"),
                "smarter_delta": raw.get("smarter_delta"),
            }
        except Exception as e:
            state["latest_swarm_error"] = str(e)

    # skills / smart
    skills = root / "data" / "skills" / "stats.json"
    if skills.is_file():
        try:
            state["skills"] = json.loads(skills.read_text(encoding="utf-8"))
        except Exception:
            pass

    # observer (sister silent LLM)
    try:
        state["observer"] = observer_latest(root)
    except Exception as e:
        state["observer_error"] = str(e)

    # OS
    try:
        state["os"] = os_snapshot()
    except Exception as e:
        state["os_error"] = str(e)

    # leash flag
    state["leash"] = {
        "DRONE_LEASH": os.environ.get("DRONE_LEASH", "1"),
        "DRONE_CODE_ENABLE": os.environ.get("DRONE_CODE_ENABLE", "0"),
        "DRONE_OBSERVER": os.environ.get("DRONE_OBSERVER", "1"),
        "DRONE_CRITIC": os.environ.get("DRONE_CRITIC", "1"),
    }
    return state


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    """Shrink for LLM context."""
    lr = state.get("latest_run") or {}
    return {
        "utc": state.get("utc"),
        "latest_run": {
            "task_id": lr.get("task_id"),
            "status": lr.get("status"),
            "goal": (lr.get("goal") or "")[:400],
            "nodes_run": lr.get("nodes_run"),
            "tool_calls": lr.get("tool_calls"),
            "duration_ms": lr.get("duration_ms"),
            "mode": lr.get("mode"),
            "observer": lr.get("observer"),
            "workspace": lr.get("workspace"),
        },
        "latest_swarm": state.get("latest_swarm"),
        "skills": {
            "builds": (state.get("skills") or {}).get("builds"),
            "smart_index": (state.get("skills") or {}).get("smart_index"),
            "build_level": (state.get("skills") or {}).get("build_level"),
            "mean_skill_score": (state.get("skills") or {}).get("mean_skill_score"),
        },
        "observer_summary": (
            ((state.get("observer") or {}).get("observation") or {}).get("summary")
            if isinstance((state.get("observer") or {}).get("observation"), dict)
            else None
        ),
        "os": state.get("os"),
        "leash": state.get("leash"),
    }


def critique_once(
    root: Path,
    *,
    event: str = "core_tick",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One critical-thinking pass for the core engine.
    Output is engine-facing JSON on disk — not a user chat reply.
    """
    root = Path(root)
    out: dict[str, Any] = {
        "event": event,
        "utc": _utc(),
        "core_critic": True,
        "user_facing": False,
        "engine_facing": True,
        "false_green": 0,
    }
    if not critic_enabled():
        out["status"] = "SKIP"
        out["reason"] = "DRONE_CRITIC=0"
        return out

    state = _gather_engine_state(root)
    compact = _compact_state(state)
    if extra:
        compact["extra"] = extra

    model = (os.environ.get("DRONE_CRITIC_MODEL") or "").strip()
    text = ""
    try:
        from .ollama_brain import generate, list_local_models

        if not list_local_models():
            out["status"] = "SKIP"
            out["reason"] = "ollama unreachable"
            _persist(root, out, compact, text)
            return out

        if not model:
            installed = list_local_models()
            for cand in (
                "llama3.2:3b",
                "qwen2.5:3b",
                "qwen2.5:7b",
                "llama3.1:8b",
                "mistral:7b",
            ):
                for n in installed:
                    if n == cand or n.startswith(cand.split(":")[0] + ":"):
                        model = n
                        break
                if model:
                    break
            if not model:
                model = installed[0] if installed else "llama3.2:3b"

        prompt = (
            "You are the CORE CRITIC — third LLM, constant sentient loop for the drone ENGINE.\n"
            "You do NOT chat with the human. You critically think for the core.\n"
            "Challenge weak evidence, false greens, template work, missing code, bad seals.\n"
            "Be harsh when honesty fails; be fair when measurements and real .py exist.\n"
            "Reply ONLY JSON:\n"
            "{\n"
            '  "critical": true,\n'
            '  "engine_health": "GREEN|YELLOW|RED",\n'
            '  "verdict": "one sentence",\n'
            '  "flaws": ["..."],\n'
            '  "strengths": ["..."],\n'
            '  "engine_actions": ["concrete next fix for core"],\n'
            '  "risk_to_host": "low|med|high",\n'
            '  "false_green_risk": true/false\n'
            "}\n\n"
            f"ENGINE STATE:\n{json.dumps(compact, ensure_ascii=False)[:7000]}"
        )
        text = generate(
            prompt,
            model=model,
            num_predict=int(os.environ.get("DRONE_CRITIC_NUM_PREDICT", "320")),
            temperature=0.2,
        )
        out["status"] = "GREEN" if text.strip() else "RED"
        out["model"] = model
        try:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                out["critique"] = json.loads(text[start : end + 1])
            else:
                out["critique"] = {"verdict": text[:600], "raw": True}
        except Exception:
            out["critique"] = {"verdict": text[:600], "raw": True}
    except Exception as e:
        out["status"] = "RED"
        out["error"] = str(e)
        out["model"] = model or None

    out["state_compact"] = compact
    _persist(root, out, compact, text)
    return out


def _persist(
    root: Path,
    out: dict[str, Any],
    compact: dict[str, Any],
    raw: str,
) -> None:
    d = critic_dir(root)
    with root_lock(root):
        led = d / "critique.jsonl"
        row = {
            "utc": out.get("utc"),
            "event": out.get("event"),
            "status": out.get("status"),
            "model": out.get("model"),
            "critique": out.get("critique"),
            "error": out.get("error"),
        }
        with led.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        latest = {**out, "raw_preview": (raw or "")[:2000]}
        (d / "LATEST.json").write_text(
            json.dumps(latest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # ENGINE_INSIGHTS — what core should read next cycle
        crit = out.get("critique") if isinstance(out.get("critique"), dict) else {}
        insights = {
            "utc": out.get("utc"),
            "engine_health": crit.get("engine_health"),
            "verdict": crit.get("verdict"),
            "engine_actions": crit.get("engine_actions") or [],
            "flaws": crit.get("flaws") or [],
            "false_green_risk": crit.get("false_green_risk"),
            "risk_to_host": crit.get("risk_to_host"),
            "user_facing": False,
            "source": "core_critic",
        }
        (d / "ENGINE_INSIGHTS.json").write_text(
            json.dumps(insights, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        md = [
            "# Core Critic (sentient loop)",
            "",
            f"UTC: {out.get('utc')}",
            f"Status: {out.get('status')}",
            f"Model: {out.get('model')}",
            f"User-facing: **false** · Engine-facing: **true**",
            "",
            "## Critique",
            "```json",
            json.dumps(crit, indent=2, ensure_ascii=False)[:4000],
            "```",
            "",
            "## Compact state",
            "```json",
            json.dumps(compact, indent=2, ensure_ascii=False)[:2500],
            "```",
        ]
        (d / "LATEST.md").write_text("\n".join(md), encoding="utf-8")


def read_latest(root: Path) -> dict[str, Any]:
    p = critic_dir(root) / "LATEST.json"
    if not p.is_file():
        return {"status": "EMPTY", "user_facing": False, "engine_facing": True}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "RED", "error": str(e), "user_facing": False}


def read_engine_insights(root: Path) -> dict[str, Any]:
    p = critic_dir(root) / "ENGINE_INSIGHTS.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_loop(root: Path, *, ticks: int | None = None) -> int:
    """
    Constant sentient loop. ticks=None → forever.
    Ctrl+C to stop. Interval from DRONE_CRITIC_INTERVAL_S (default 90).
    """
    root = Path(root)
    n = 0
    print(
        json.dumps(
            {
                "core_critic_loop": "start",
                "interval_s": loop_interval_s(),
                "user_facing": False,
                "engine_facing": True,
            }
        ),
        flush=True,
    )
    try:
        while ticks is None or n < ticks:
            n += 1
            result = critique_once(root, event=f"loop_tick_{n}")
            print(
                json.dumps(
                    {
                        "tick": n,
                        "status": result.get("status"),
                        "health": (result.get("critique") or {}).get("engine_health")
                        if isinstance(result.get("critique"), dict)
                        else None,
                        "verdict": (result.get("critique") or {}).get("verdict")
                        if isinstance(result.get("critique"), dict)
                        else None,
                        "utc": result.get("utc"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if ticks is not None and n >= ticks:
                break
            time.sleep(loop_interval_s())
    except KeyboardInterrupt:
        print(json.dumps({"core_critic_loop": "stop", "ticks": n}), flush=True)
        return 0
    return 0
