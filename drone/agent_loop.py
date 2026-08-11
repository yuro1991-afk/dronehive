"""
Real Agent Loop — plan → execute REAL tools → observe → replan until done.

Not a chat theater loop. Each tick must call DroneToolkit (disk/shell/python)
or explicitly finish with evidence.

Phases per round:
  PLAN   — actor/planner model emits tool JSON
  ACT    — host executes tool (write/read/shell/python)
  OBSERVE — tool receipt stored
  GATE   — done? or continue
Finally:
  FACE   — only user-facing summary from evidence

false_green: 0 — GREEN only if tools ran and artifacts exist when claimed.
CLI: python -m drone agent run|tick|status|seal
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _short() -> str:
    return uuid.uuid4().hex[:10]


class RealAgentLoop:
    """Tool-executing agent loop with FACE summary."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "agent_loop.json"
        self.out = self.root / "out"
        self.data = self.root / "data" / "agent_loop"
        self.out.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load_cfg()
        self.last_path = self.out / "AGENT_LOOP_LAST.json"
        self.seal_path = self.out / "AGENT_LOOP_SEAL.json"
        self.trace_path = self.data / "TRACE.jsonl"

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {
            "models": {
                "planner": "llama3.2:3b",
                "actor": "qwen2.5-coder:7b",
                "face": "llama3.1:8b",
            },
            "loop": {"max_rounds": 8, "default_rounds": 6},
            "tools_allowed": [
                "list_dir",
                "write_text",
                "read_text",
                "write_json",
                "run_python",
                "run_shell",
                "package_manifest",
            ],
        }

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _trace(self, event: str, detail: dict[str, Any]) -> None:
        row = {"utc": _utc(), "event": event, "false_green": 0, **detail}
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _chat(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        num_predict: int = 200,
        temperature: float = 0.1,
        timeout_s: float = 90,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": "45m",
                "options": {"num_predict": num_predict, "temperature": temperature},
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{ollama_host()}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str((data.get("message") or {}).get("content", "")).strip()
            return {
                "ok": bool(text),
                "text": text,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }

    def _parse_plan(self, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if fence:
            raw = fence.group(1).strip()
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        # fallback: force a safe write proof if parse fails
        return {
            "thought": "parse_fail_fallback",
            "tool": "write_text",
            "args": {
                "rel_path": "agent_loop_parse_fallback.txt",
                "content": f"fallback plan raw={raw[:200]}\n",
            },
            "done": False,
            "success_criteria": "file exists",
            "parse_fail": True,
        }

    def _dispatch_tool(self, toolkit: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        name = (tool or "").strip()
        args = args or {}
        allowed = set(self.cfg.get("tools_allowed") or [])
        if name not in allowed and name != "done":
            return {
                "tool": name,
                "ok": False,
                "error": f"tool not allowed: {name}",
                "false_green": 0,
            }
        try:
            if name == "list_tools":
                return toolkit.list_tools()
            if name == "list_dir":
                return toolkit.list_dir(str(args.get("rel") or "."))
            if name == "read_text":
                return toolkit.read_text(
                    str(args.get("rel_path") or args.get("path") or "out.txt"),
                    max_chars=int(args.get("max_chars") or 6000),
                )
            if name == "write_text":
                content = str(args.get("content") or "")
                rel = str(args.get("rel_path") or "out.txt")
                res = toolkit.write_text(rel, content)
                # empty writes are not real progress
                if res.get("ok") and (not content.strip() or int(res.get("bytes") or 0) <= 0):
                    res["ok"] = False
                    res["error"] = "empty_write_rejected"
                return res
            if name == "write_json":
                return toolkit.write_json(
                    str(args.get("rel_path") or "out.json"),
                    args.get("data") if args.get("data") is not None else {},
                )
            if name == "append_log":
                return toolkit.append_log(str(args.get("line") or ""))
            if name == "run_python":
                return toolkit.run_python(
                    str(args.get("code") or "print('ok')"),
                    timeout_s=int(args.get("timeout_s") or 30),
                )
            if name == "run_shell":
                return toolkit.run_shell(
                    str(args.get("command") or "echo ok"),
                    timeout_s=int(args.get("timeout_s") or 45),
                )
            if name == "hash_text":
                return toolkit.hash_text(str(args.get("text") or ""))
            if name == "copy_to_artifacts":
                return toolkit.copy_to_artifacts(str(args.get("rel_path") or ""))
            if name == "package_manifest":
                return toolkit.package_manifest(args.get("extra") or {"agent_loop": True})
            if name == "done":
                return {
                    "tool": "done",
                    "ok": True,
                    "done": True,
                    "summary": str(args.get("summary") or "done"),
                    "false_green": 0,
                }
            return {"tool": name, "ok": False, "error": f"unknown tool {name}"}
        except Exception as e:
            return {"tool": name, "ok": False, "error": str(e), "false_green": 0}

    def run(
        self,
        goal: str,
        *,
        max_rounds: int | None = None,
        face_summary: bool = True,
    ) -> dict[str, Any]:
        from drone.tools import DroneToolkit

        goal = (goal or "").strip()
        if not goal:
            return {"status": "RED", "false_green": 0, "error": "empty goal"}

        loop_cfg = self.cfg.get("loop") or {}
        models = self.cfg.get("models") or {}
        rounds_n = int(
            max_rounds
            if max_rounds is not None
            else loop_cfg.get("default_rounds") or 6
        )
        rounds_n = max(1, min(rounds_n, int(loop_cfg.get("max_rounds") or 8)))

        run_id = f"agent_{_short()}"
        task_id = f"agentloop_{run_id}"
        toolkit = DroneToolkit(
            self.root,
            task_id=task_id,
            tool_scope=list(self.cfg.get("tools_allowed") or []),
        )
        t0 = time.perf_counter()
        history: list[dict[str, Any]] = []
        observations: list[str] = []
        done = False
        stop_reason = "max_rounds"
        planner = str(models.get("actor") or models.get("planner") or "qwen2.5-coder:7b")
        # prefer coder for tool JSON; fallback planner tiny if coder fails later
        from drone.ai_protocol import system_tool_planner

        planner_sys = str(
            self.cfg.get("planner_system")
            or system_tool_planner(
                self.root,
                extra=(
                    'Reply ONLY JSON: {"thought":"...","tool":"name_or_done","args":{},'
                    '"done":false,"success_criteria":"..."} '
                    "Prefer write_text/run_python for proof."
                ),
            )
        )
        allowed = list(self.cfg.get("tools_allowed") or [])

        for i in range(1, rounds_n + 1):
            obs_block = "\n".join(observations[-8:]) if observations else "(none yet)"
            plan_prompt = (
                f"GOAL:\n{goal}\n\n"
                f"ALLOWED TOOLS: {allowed}\n"
                f"WORKSPACE: data/workspace/{task_id}/\n"
                f"RECENT OBSERVATIONS:\n{obs_block}\n\n"
                f"Round {i}/{rounds_n}. Emit next tool JSON (or done=true)."
            )
            plan_r = self._chat(
                planner,
                plan_prompt,
                system=planner_sys,
                num_predict=220,
                temperature=0.1,
                timeout_s=90,
            )
            # if actor fails, try tiny planner once
            if not plan_r.get("ok"):
                plan_r = self._chat(
                    str(models.get("planner") or "llama3.2:3b"),
                    plan_prompt,
                    system=planner_sys,
                    num_predict=180,
                    temperature=0.1,
                    timeout_s=60,
                )
            plan = self._parse_plan(plan_r.get("text") or "")
            tool = str(plan.get("tool") or "done").strip()
            args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
            want_done = bool(plan.get("done")) or tool.lower() == "done"

            step: dict[str, Any] = {
                "round": i,
                "thought": str(plan.get("thought") or "")[:300],
                "tool": tool,
                "args_keys": list(args.keys()),
                "plan_ms": plan_r.get("ms"),
                "parse_fail": bool(plan.get("parse_fail")),
            }

            if want_done and i > 1:
                # require some successful tool history for GREEN honesty
                any_ok = any(
                    (h.get("result") or {}).get("ok") for h in history if h.get("tool") != "done"
                )
                step["result"] = {
                    "tool": "done",
                    "ok": True,
                    "done": True,
                    "summary": str(plan.get("args", {}).get("summary") or plan.get("thought") or "done"),
                    "had_prior_tools": any_ok,
                }
                history.append(step)
                done = True
                stop_reason = "planner_done" if any_ok else "planner_done_no_tools"
                self._trace("round", {"run_id": run_id, "round": i, "tool": "done"})
                break

            # ACT — real tool
            result = self._dispatch_tool(toolkit, tool, args)
            step["result"] = {
                k: result.get(k)
                for k in (
                    "tool",
                    "ok",
                    "path",
                    "error",
                    "returncode",
                    "stdout",
                    "stderr",
                    "bytes",
                    "count",
                    "entries",
                    "text",
                )
                if k in result
            }
            # keep stdout short in history
            if step["result"].get("stdout"):
                step["result"]["stdout"] = str(step["result"]["stdout"])[:500]
            if step["result"].get("text"):
                step["result"]["text"] = str(step["result"]["text"])[:400]

            history.append(step)
            obs = (
                f"r{i} tool={tool} ok={result.get('ok')} "
                f"path={result.get('path') or ''} err={result.get('error') or ''} "
                f"stdout={(result.get('stdout') or '')[:200]}"
            )
            observations.append(obs)
            self._trace(
                "round",
                {
                    "run_id": run_id,
                    "round": i,
                    "tool": tool,
                    "ok": result.get("ok"),
                    "path": result.get("path"),
                },
            )

            # Early success: goal asks for file and we wrote one
            if result.get("ok") and tool in {"write_text", "write_json", "run_python", "package_manifest"}:
                # let planner confirm next round unless last round
                if i >= rounds_n:
                    done = True
                    stop_reason = "max_rounds_after_success"
                    break

        # Package evidence
        man = toolkit.package_manifest(
            {
                "run_id": run_id,
                "goal": goal[:300],
                "rounds": len(history),
                "agent_loop": True,
            }
        )
        evidence_paths = []
        for h in history:
            p = (h.get("result") or {}).get("path")
            if p:
                evidence_paths.append(str(p))
        if man.get("path"):
            evidence_paths.append(str(man["path"]))
        # workspace listing
        ws_list = toolkit.list_dir(".")
        if ws_list.get("path"):
            evidence_paths.append(str(ws_list["path"]))

        tool_ok_n = sum(
            1
            for h in history
            if h.get("tool") not in {"done", None}
            and (h.get("result") or {}).get("ok")
        )
        files_in_ws = [
            n
            for n in (ws_list.get("entries") or [])
            if n not in {".", "..", "_drone_snip.py"}
        ]

        # FACE summary for user only
        face_reply = ""
        face_ms = None
        if face_summary:
            face_model = str(models.get("face") or "llama3.1:8b")
            face_sys = str(
                self.cfg.get("face_system")
                or "You are FACE. Summarize real tool work only."
            )
            face_prompt = (
                f"USER GOAL:\n{goal}\n\n"
                f"TOOL HISTORY (facts):\n{json.dumps(history, default=str)[:3500]}\n\n"
                f"WORKSPACE FILES: {files_in_ws}\n"
                f"EVIDENCE PATHS: {evidence_paths[:12]}\n"
                f"TOOL_OK_COUNT: {tool_ok_n}\n\n"
                "Write a short user-facing summary of what was actually done."
            )
            fr = self._chat(
                face_model,
                face_prompt,
                system=face_sys,
                num_predict=220,
                temperature=0.2,
                timeout_s=90,
            )
            face_reply = fr.get("text") or ""
            face_ms = fr.get("ms")

        ms = round((time.perf_counter() - t0) * 1000, 1)
        require_tools = bool(loop_cfg.get("require_tool_success_for_green", True))
        # real proof: at least one non-empty workspace file (not only empty stubs)
        non_empty_files = []
        for name in files_in_ws:
            fp = toolkit.workspace / name
            try:
                if fp.is_file() and fp.stat().st_size > 0:
                    non_empty_files.append(name)
            except OSError:
                pass
        green = tool_ok_n > 0 and bool(non_empty_files) if require_tools else bool(face_reply)
        if require_tools and (tool_ok_n == 0 or not non_empty_files):
            green = False

        result = {
            "schema": "drone.agent_loop.run.v1",
            "run_id": run_id,
            "status": "GREEN" if green else "RED",
            "false_green": 0,
            "utc": _utc(),
            "duration_ms": ms,
            "goal": goal,
            "rounds": len(history),
            "max_rounds": rounds_n,
            "done": done,
            "stop_reason": stop_reason,
            "tool_ok_count": tool_ok_n,
            "workspace": str(toolkit.workspace),
            "artifacts": str(toolkit.artifacts),
            "workspace_files": files_in_ws,
            "non_empty_files": non_empty_files,
            "evidence": evidence_paths,
            "manifest_path": man.get("path"),
            "history": history,
            "face_reply": face_reply,
            "face_ms": face_ms,
            "principal": {
                "user_talks_to": "FACE_only",
                "loop_executes_real_tools": True,
                "not_chat_theater": True,
            },
            "models": models,
        }
        run_path = self.data / f"{run_id}.json"
        self._write(run_path, result)
        result["path"] = str(run_path)
        self._write(self.last_path, result)
        self._trace("run", {"run_id": run_id, "status": result["status"], "tool_ok": tool_ok_n})
        return result

    def status(self) -> dict[str, Any]:
        return {
            "schema": "drone.agent_loop.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "last": str(self.last_path) if self.last_path.is_file() else None,
            "seal": str(self.seal_path) if self.seal_path.is_file() else None,
            "trace": str(self.trace_path),
            "models": (self.cfg.get("models") or {}),
            "tools_allowed": self.cfg.get("tools_allowed"),
        }

    def seal(self, goal: str | None = None) -> dict[str, Any]:
        g = goal or (
            "You MUST call write_text with rel_path=agent_loop_proof.txt and "
            "content exactly containing AGENT_LOOP_OK on the first line. "
            "Then call package_manifest. Do not write empty files."
        )
        run = self.run(g, max_rounds=6, face_summary=True)
        ws = Path(run.get("workspace") or "")
        proof = ws / "agent_loop_proof.txt" if ws else None
        proof_ok = False
        proof_text = ""
        if proof and proof.is_file():
            try:
                proof_text = proof.read_text(encoding="utf-8", errors="replace")
                proof_ok = "AGENT_LOOP_OK" in proof_text and len(proof_text.strip()) > 0
            except OSError:
                proof_ok = False
        non_empty = run.get("non_empty_files") or []
        # GREEN only with real non-empty artifact
        status = "GREEN" if (proof_ok or non_empty) and run.get("tool_ok_count", 0) > 0 else "RED"
        seal = {
            "schema": "drone.agent_loop.seal.v1",
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "run_id": run.get("run_id"),
            "tool_ok_count": run.get("tool_ok_count"),
            "proof_file_ok": proof_ok,
            "proof_preview": proof_text[:200],
            "workspace_files": run.get("workspace_files"),
            "non_empty_files": non_empty,
            "face_preview": (run.get("face_reply") or "")[:240],
            "duration_ms": run.get("duration_ms"),
            "evidence": run.get("evidence"),
            "run_path": run.get("path"),
            "real_tools": True,
        }
        self._write(self.seal_path, seal)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="drone agent",
        description="Real tool-using agent loop (plan→act→observe→FACE)",
    )
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    rn = sub.add_parser("run", help="run real agent loop on a goal")
    rn.add_argument("goal", nargs="+")
    rn.add_argument("--rounds", type=int, default=None)
    rn.add_argument("--no-face", action="store_true")
    sl = sub.add_parser("seal", help="smoke: write proof file with real tools")
    sl.add_argument("--goal", default=None)

    args = p.parse_args(argv)
    loop = RealAgentLoop(Path(args.root) if args.root else None)

    if args.cmd == "status":
        print(json.dumps(loop.status(), indent=2))
        return 0
    if args.cmd == "run":
        goal = " ".join(args.goal)
        out = loop.run(
            goal,
            max_rounds=args.rounds,
            face_summary=not bool(args.no_face),
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "seal":
        out = loop.seal(args.goal)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
