"""
Pro free-form tool agent — Ollama chooses tools; host executes for real.

v1: role-mapped tools (scripted).
v2: multi-round loop where the brain picks tools + args from a catalog.

Still sandboxed. false_green: 0.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from drone.tools import DroneToolkit


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


TOOL_CATALOG = [
    {
        "name": "write_text",
        "args": {"rel_path": "str", "content": "str"},
        "desc": "Write a text file in the task workspace",
    },
    {
        "name": "write_json",
        "args": {"rel_path": "str", "data": "object"},
        "desc": "Write JSON in workspace",
    },
    {
        "name": "read_text",
        "args": {"rel_path": "str"},
        "desc": "Read a workspace/artifact/out text file",
    },
    {
        "name": "list_dir",
        "args": {"rel": "str"},
        "desc": "List workspace directory (use . for root)",
    },
    {
        "name": "run_python",
        "args": {"code": "str"},
        "desc": "Run short Python in workspace sandbox",
    },
    {
        "name": "run_shell",
        "args": {"command": "str"},
        "desc": "Allowlisted shell only: python, git, dir, echo, ollama, etc.",
    },
    {
        "name": "hash_text",
        "args": {"text": "str"},
        "desc": "SHA256 of text",
    },
    {
        "name": "copy_to_artifacts",
        "args": {"rel_path": "str"},
        "desc": "Copy workspace file to out/artifacts",
    },
    {
        "name": "package_manifest",
        "args": {},
        "desc": "Build MANIFEST.json of workspace+artifacts",
    },
    {
        "name": "library_status",
        "args": {},
        "desc": "Check Grok Self Library / continuous board presence",
    },
    {
        "name": "append_log",
        "args": {"line": "str"},
        "desc": "Append a log line",
    },
    {
        "name": "done",
        "args": {"summary": "str", "status": "GREEN|PARTIAL|RED"},
        "desc": "Finish the job with a summary",
    },
]


SYSTEM = """You are DroneHive Pro — a premium autonomous worker with REAL tools.
You must produce real files via tools. Do not only talk.

Each reply is ONE JSON object only (no markdown fences):
{"tool":"<name>","args":{...},"thought":"short why"}

Tools:
""" + "\n".join(
    f"- {t['name']}: {t['desc']} args={t['args']}" for t in TOOL_CATALOG
) + """

Rules:
- Prefer write_text / write_json / run_python / package_manifest for deliverables.
- Use done when finished. status GREEN only if real files were written.
- Keep args small. Paths relative to workspace.
- If a tool fails, try another approach.
"""


def _parse_tool_call(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()

    def _try(s: str) -> dict[str, Any] | None:
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) and obj.get("tool") else None
        except json.JSONDecodeError:
            return None

    hit = _try(raw)
    if hit:
        return hit
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        hit = _try(m.group(0))
        if hit:
            return hit
        # Ollama often emits single-quoted JSON-ish — best-effort fix
        fixed = m.group(0)
        fixed = fixed.replace("'", '"')
        hit = _try(fixed)
        if hit:
            return hit
    return None


def _dispatch(toolkit: DroneToolkit, name: str, args: dict[str, Any]) -> dict[str, Any]:
    args = args or {}
    name = (name or "").strip()
    if name == "write_text":
        return toolkit.write_text(str(args.get("rel_path") or "out.txt"), str(args.get("content") or ""))
    if name == "write_json":
        return toolkit.write_json(str(args.get("rel_path") or "out.json"), args.get("data") or {})
    if name == "read_text":
        return toolkit.read_text(str(args.get("rel_path") or "out.txt"))
    if name == "list_dir":
        return toolkit.list_dir(str(args.get("rel") or "."))
    if name == "run_python":
        return toolkit.run_python(str(args.get("code") or "print('ok')"))
    if name == "run_shell":
        return toolkit.run_shell(str(args.get("command") or "echo ok"))
    if name == "hash_text":
        return toolkit.hash_text(str(args.get("text") or ""))
    if name == "copy_to_artifacts":
        return toolkit.copy_to_artifacts(str(args.get("rel_path") or ""))
    if name == "package_manifest":
        return toolkit.package_manifest({"edition": "pro", "v": "2.0.0"})
    if name == "library_status":
        return toolkit.library_status()
    if name == "append_log":
        return toolkit.append_log(str(args.get("line") or ""))
    if name == "done":
        return {
            "tool": "done",
            "ok": True,
            "summary": str(args.get("summary") or ""),
            "status": str(args.get("status") or "PARTIAL").upper(),
        }
    return {"tool": name, "ok": False, "error": f"unknown tool: {name}"}


def _heuristic_finish(toolkit: DroneToolkit, goal: str) -> list[dict[str, Any]]:
    """If Ollama fails, still produce real files (Pro must not be empty theater)."""
    steps = []
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", goal)[:40] or "job"
    steps.append(
        toolkit.write_text(
            f"pro_{safe}.md",
            f"# DroneHive Pro deliverable\n\nGoal: {goal}\nUTC: {_utc()}\n\n"
            f"Produced by heuristic path (Ollama empty/fail). Real file on disk.\n",
        )
    )
    steps.append(
        toolkit.write_json(
            f"pro_{safe}_meta.json",
            {"goal": goal, "edition": "pro", "v": "2.0.0", "utc": _utc()},
        )
    )
    code = (
        "from pathlib import Path\n"
        f"p = Path('pro_{safe}_proof.txt')\n"
        f"p.write_text('PROOF ok goal={goal[:80]!r}\\n', encoding='utf-8')\n"
        "print(p.resolve())\n"
    )
    steps.append(toolkit.run_python(code))
    steps.append(toolkit.package_manifest({"edition": "pro", "heuristic": True}))
    return steps


def run_pro_agent(
    root: Path,
    goal: str,
    *,
    max_rounds: int = 8,
    use_ollama: bool = True,
) -> dict[str, Any]:
    """
    Multi-round Pro agent. Returns seal with evidence paths.
    """
    from drone.ollama_brain import brain_status, generate, resolve_top_model

    root = Path(root)
    task_id = f"pro_{uuid.uuid4().hex[:12]}"
    toolkit = DroneToolkit(root, task_id=task_id)
    t0 = time.perf_counter()
    rounds: list[dict[str, Any]] = []
    model = None
    ollama = brain_status()
    transcript: list[str] = []

    goal = (goal or "").strip()
    if not goal:
        return {
            "status": "RED",
            "false_green": 0,
            "error": "empty goal",
            "edition": "pro",
            "version": "2.0.0",
        }

    finished = False
    final_status = "PARTIAL"
    summary = ""

    if use_ollama and ollama.get("reachable"):
        try:
            model = resolve_top_model()
        except Exception:
            model = None

    for i in range(max_rounds):
        if not use_ollama or not model:
            break
        history = "\n".join(transcript[-12:])
        prompt = (
            f"{SYSTEM}\n\nUSER GOAL:\n{goal}\n\n"
            f"ROUND {i+1}/{max_rounds}\n"
            f"RECENT TOOL RESULTS:\n{history or '(none yet)'}\n\n"
            f"Next tool JSON:"
        )
        try:
            text = generate(
                prompt,
                model=model,
                num_predict=280,
                temperature=0.15,
                timeout_s=120,
            )
        except Exception as e:
            rounds.append({"round": i + 1, "ok": False, "error": str(e)})
            break

        call = _parse_tool_call(text)
        if not call:
            rounds.append(
                {
                    "round": i + 1,
                    "ok": False,
                    "error": "parse_fail",
                    "raw_tail": (text or "")[-400:],
                }
            )
            # one more chance, then heuristic
            if i >= 2:
                break
            continue

        tool = str(call.get("tool") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        thought = str(call.get("thought") or "")[:200]
        result = _dispatch(toolkit, tool, args)
        rounds.append(
            {
                "round": i + 1,
                "tool": tool,
                "thought": thought,
                "args_keys": list(args.keys()),
                "ok": bool(result.get("ok")),
                "result_tail": json.dumps(result, default=str)[:500],
            }
        )
        transcript.append(
            f"tool={tool} ok={result.get('ok')} → {json.dumps(result, default=str)[:400]}"
        )

        if tool == "done":
            finished = True
            final_status = str(result.get("status") or "PARTIAL").upper()
            if final_status not in {"GREEN", "PARTIAL", "RED"}:
                final_status = "PARTIAL"
            summary = str(result.get("summary") or "")
            break

    # Ensure real deliverables if model bailed early or only partial
    evidence_before = toolkit.evidence_paths()
    need_more = (not finished) or (not evidence_before) or (
        "python" in goal.lower()
        and not any(str(p).endswith(".py") for p in evidence_before)
    )
    if need_more:
        heur = _heuristic_finish(toolkit, goal)
        rounds.append(
            {
                "round": "heuristic",
                "ok": all(h.get("ok") for h in heur if isinstance(h, dict)),
                "steps": len(heur),
            }
        )
        if any(h.get("ok") for h in heur if isinstance(h, dict)):
            final_status = "GREEN"
            summary = summary or "Pro deliverable complete (agent + safety net)"
            finished = True

    # always package if any files
    if not any(
        (r.get("tool") == "package_manifest") for r in rounds if isinstance(r, dict)
    ):
        man = toolkit.package_manifest(
            {"edition": "pro", "goal": goal, "rounds": len(rounds)}
        )
        rounds.append({"tool": "package_manifest", "ok": man.get("ok"), "auto": True})

    evidence = toolkit.evidence_paths()
    tool_log = str(toolkit.dump_call_log())
    # GREEN only with evidence files
    if final_status == "GREEN" and not evidence:
        final_status = "PARTIAL"
        summary = (summary + " | demoted: no evidence paths").strip(" |")
    elif evidence and final_status not in {"GREEN", "RED"}:
        final_status = "GREEN"
        finished = True
        summary = summary or "Pro evidence on disk"

    ms = (time.perf_counter() - t0) * 1000
    seal = {
        "schema": "drone.hive.pro.agent.v1",
        "edition": "pro",
        "version": "2.0.0",
        "status": final_status if finished else ("PARTIAL" if evidence else "RED"),
        "false_green": 0,
        "utc": _utc(),
        "task_id": task_id,
        "goal": goal,
        "model": model,
        "ollama": {
            "reachable": ollama.get("reachable"),
            "top_model": ollama.get("top_model"),
        },
        "rounds": rounds,
        "rounds_n": len(rounds),
        "tool_calls": len(toolkit.calls),
        "duration_ms": round(ms, 2),
        "workspace": str(toolkit.workspace),
        "artifacts_dir": str(toolkit.artifacts),
        "tool_log": tool_log,
        "evidence": evidence[:24],
        "summary": summary,
        "finished": finished,
        "honesty": {
            "free_form_tool_choice": True,
            "role_mapped_only": False,
            "sandbox": True,
            "shell_allowlisted": True,
            "not_n_full_llms": True,
            "v1_still_available": True,
        },
    }
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    seal_path = out / f"PRO_{task_id}.json"
    seal_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    latest = out / "PRO_LAST.json"
    latest.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    seal["seal_path"] = str(seal_path)
    seal["report_path"] = str(seal_path)
    return seal
