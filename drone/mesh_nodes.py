"""
M2M / flagship mesh nodes: one tool + one job each; IDLE until called.

false_green: 0
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from drone.tools import DroneToolkit


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


class MeshNodeRegistry:
    """Roster of idle workers; wake only on call_node / call_many."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.roster_path = self.root / "configs" / "m2m_node_roster.json"
        self.data = self.root / "data" / "super_mesh"
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data / "NODE_IDLE_STATE.json"
        self.last_call_path = self.root / "out" / "MESH_NODE_LAST_CALL.json"
        self.roster = self._load_roster()
        self.state = self._load_state()

    def _load_roster(self) -> dict[str, Any]:
        if self.roster_path.is_file():
            return json.loads(self.roster_path.read_text(encoding="utf-8"))
        return {"nodes": [], "flagships": [], "idle_policy": {}}

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self.roster.get("nodes") or []) + list(
            self.roster.get("flagships") or []
        )

    def by_tag(self) -> dict[str, dict[str, Any]]:
        return {str(n.get("tag")): n for n in self.all_entries() if n.get("tag")}

    def _load_state(self) -> dict[str, Any]:
        tags = list(self.by_tag().keys())
        base = {
            "schema": "drone.mesh_nodes.state.v1",
            "utc": _utc(),
            "false_green": 0,
            "nodes": {
                t: {
                    "state": "IDLE",
                    "idle": True,
                    "calls": 0,
                    "last_call_utc": None,
                    "last_ok": None,
                }
                for t in tags
            },
        }
        if self.state_path.is_file():
            try:
                saved = json.loads(self.state_path.read_text(encoding="utf-8"))
                for t, row in (saved.get("nodes") or {}).items():
                    if t in base["nodes"]:
                        # force idle on load unless mid-call (we always sleep)
                        base["nodes"][t].update(
                            {
                                "state": "IDLE",
                                "idle": True,
                                "calls": int(row.get("calls") or 0),
                                "last_call_utc": row.get("last_call_utc"),
                                "last_ok": row.get("last_ok"),
                            }
                        )
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return base

    def save_state(self) -> None:
        self.state["utc"] = _utc()
        self.state["false_green"] = 0
        idle_n = sum(
            1 for r in (self.state.get("nodes") or {}).values() if r.get("idle")
        )
        self.state["idle_count"] = idle_n
        self.state["total"] = len(self.state.get("nodes") or {})
        self._write(self.state_path, self.state)

    def hardwire_idle(self) -> dict[str, Any]:
        """All nodes → IDLE; write durable roster receipt."""
        for t, row in (self.state.get("nodes") or {}).items():
            row["state"] = "IDLE"
            row["idle"] = True
        self.save_state()
        receipt = {
            "schema": "drone.mesh_nodes.hardwire_idle.v1",
            "utc": _utc(),
            "false_green": 0,
            "status": "GREEN",
            "idle_policy": self.roster.get("idle_policy"),
            "nodes": [
                {
                    "tag": n.get("tag"),
                    "job": n.get("job"),
                    "tool": n.get("tool"),
                    "hemi": n.get("hemi"),
                    "state": "IDLE",
                    "idle": True,
                }
                for n in self.roster.get("nodes") or []
            ],
            "flagships": [
                {
                    "tag": n.get("tag"),
                    "job": n.get("job"),
                    "tool": n.get("tool"),
                    "state": "IDLE",
                    "idle": True,
                    "talks_to_user": n.get("talks_to_user"),
                }
                for n in self.roster.get("flagships") or []
            ],
            "count_m2m": len(self.roster.get("nodes") or []),
            "count_flagship": len(self.roster.get("flagships") or []),
            "state_path": str(self.state_path),
            "roster_path": str(self.roster_path),
        }
        self._write(self.data / "ROSTER_IDLE_HARDWIRE.json", receipt)
        return receipt

    def status(self) -> dict[str, Any]:
        rows = []
        by = self.by_tag()
        for t, st in (self.state.get("nodes") or {}).items():
            meta = by.get(t) or {}
            rows.append(
                {
                    "tag": t,
                    "job": meta.get("job"),
                    "tool": meta.get("tool"),
                    "hemi": meta.get("hemi"),
                    "state": st.get("state") or "IDLE",
                    "idle": bool(st.get("idle", True)),
                    "calls": st.get("calls") or 0,
                    "last_call_utc": st.get("last_call_utc"),
                    "last_ok": st.get("last_ok"),
                }
            )
        idle_n = sum(1 for r in rows if r.get("idle"))
        busy_n = len(rows) - idle_n
        return {
            "schema": "drone.mesh_nodes.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "idle_count": idle_n,
            "busy_count": busy_n,
            "total": len(rows),
            "auto_fire_all": False,
            "wake_only_when_called": True,
            "nodes": rows,
            "state_path": str(self.state_path),
        }

    def _dispatch_tool(
        self,
        toolkit: DroneToolkit,
        tool: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        name = (tool or "").strip()
        args = dict(args or {})
        # Map known tools — full belt
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
                return toolkit.write_text(
                    str(args.get("rel_path") or "out.txt"),
                    str(args.get("content") or ""),
                )
            if name == "write_json":
                return toolkit.write_json(
                    str(args.get("rel_path") or "out.json"),
                    args.get("data") if args.get("data") is not None else {},
                )
            if name == "append_log":
                return toolkit.append_log(str(args.get("line") or ""))
            if name == "hash_text":
                return toolkit.hash_text(str(args.get("text") or ""))
            if name == "run_shell":
                return toolkit.run_shell(
                    str(args.get("command") or "echo ok"),
                    timeout_s=int(args.get("timeout_s") or 45),
                )
            if name == "run_python":
                return toolkit.run_python(
                    str(args.get("code") or "print('ok')"),
                    timeout_s=int(args.get("timeout_s") or 30),
                )
            if name == "copy_to_artifacts":
                return toolkit.copy_to_artifacts(str(args.get("rel_path") or ""))
            if name == "package_manifest":
                return toolkit.package_manifest(args.get("extra") or {"mesh_node": True})
            if name == "library_status":
                return toolkit.library_status() if hasattr(toolkit, "library_status") else {
                    "tool": name, "ok": False, "error": "not_implemented"
                }
            if name == "knowledge_imprint":
                return toolkit.knowledge_imprint(**{k: args[k] for k in args}) if hasattr(toolkit, "knowledge_imprint") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "knowledge_sources":
                return toolkit.knowledge_sources() if hasattr(toolkit, "knowledge_sources") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "do_lesson":
                return toolkit.do_lesson(str(args.get("lesson") or "")) if hasattr(toolkit, "do_lesson") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "knowledge_chunk":
                return toolkit.knowledge_chunk(str(args.get("query") or "")) if hasattr(toolkit, "knowledge_chunk") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "ai_bus_status":
                return toolkit.ai_bus_status() if hasattr(toolkit, "ai_bus_status") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "ai_bus_read":
                return toolkit.ai_bus_read(**args) if hasattr(toolkit, "ai_bus_read") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "ai_bus_write":
                return toolkit.ai_bus_write(
                    channel=str(args.get("channel") or "self_library"),
                    notes=str(args.get("notes") or args.get("payload") or "mesh_node_call"),
                    status=str(args.get("status") or "PARTIAL"),
                )
            if name == "ai_bus_sync":
                return toolkit.ai_bus_sync(
                    goal=str(args.get("goal") or ""),
                    direction=str(args.get("direction") or "full"),
                )
            if name == "board_publish":
                # needs board_wave_id on toolkit; may fail closed
                return toolkit.board_publish(
                    kind=str(args.get("kind") or "note"),
                    payload=args.get("payload") if args.get("payload") is not None else {"utc": _utc(), "mesh": True},
                )
            if name == "board_get":
                return toolkit.board_get(str(args.get("chunk_id") or args.get("key") or ""))
            if name == "board_list":
                return toolkit.board_list(
                    kind=str(args.get("kind") or ""),
                    limit=int(args.get("limit") or 20),
                )
            if name == "ollama_generate":
                return toolkit.ollama_generate(**args) if hasattr(toolkit, "ollama_generate") else {"tool": name, "ok": False, "error": "not_implemented"}
            if name == "llm_list":
                return toolkit.llm_list()
            if name == "llm_route":
                return toolkit.llm_route(str(args.get("goal") or args.get("query") or "mesh"))
            if name == "llm_chat":
                return toolkit.llm_chat(**args)
            if name == "llm_generate":
                return toolkit.llm_generate(**args)
            if name == "run_host_diagnostics":
                return toolkit.run_host_diagnostics()
            if name == "write_and_smoke_python":
                return toolkit.write_and_smoke_python(**args)
            if name == "clone_app":
                return toolkit.clone_app(
                    dest=str(args.get("dest") or ""),
                    include_data=bool(args.get("include_data") or False),
                )
            return {"tool": name, "ok": False, "error": f"unknown tool {name}", "false_green": 0}
        except Exception as e:
            return {"tool": name, "ok": False, "error": str(e), "false_green": 0}

    def call_node(
        self,
        tag: str,
        *,
        goal: str = "",
        tool_args: dict[str, Any] | None = None,
        run_tool: bool = True,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Wake one node: IDLE → CALLED → run owned tool → back to IDLE.
        Does not fire Ollama unless caller separately requests LM (kept minimal).
        """
        tag = (tag or "").strip()
        meta = self.by_tag().get(tag)
        if not meta:
            return {
                "tag": tag,
                "ok": False,
                "status": "RED",
                "error": "unknown_node",
                "false_green": 0,
            }

        st = self.state.setdefault("nodes", {}).setdefault(
            tag, {"state": "IDLE", "idle": True, "calls": 0}
        )
        st["state"] = "CALLED"
        st["idle"] = False
        st["last_call_utc"] = _utc()
        self.save_state()

        tid = task_id or f"mesh_call_{tag.replace(':', '_')}"
        # Node only sees its own tool in scope
        owned = str(meta.get("tool") or "list_tools")
        toolkit = DroneToolkit(
            self.root,
            task_id=tid,
            tool_scope=[owned, "list_tools"],
            unit_id=tag,
        )
        args = dict(meta.get("tool_args_default") or {})
        if tool_args:
            args.update(tool_args)

        tool_result: dict[str, Any] = {"tool": owned, "ok": False, "skipped": True}
        if run_tool:
            tool_result = self._dispatch_tool(toolkit, owned, args)
            # normalize ok
            if "ok" not in tool_result:
                tool_result["ok"] = not bool(tool_result.get("error"))

        # sleep again
        st["state"] = "IDLE"
        st["idle"] = True
        st["calls"] = int(st.get("calls") or 0) + 1
        st["last_ok"] = bool(tool_result.get("ok"))
        self.save_state()

        out = {
            "schema": "drone.mesh_nodes.call.v1",
            "utc": _utc(),
            "false_green": 0,
            "tag": tag,
            "job": meta.get("job"),
            "tool": owned,
            "hemi": meta.get("hemi"),
            "goal": goal,
            "was_idle": True,
            "now_idle": True,
            "state_after": "IDLE",
            "ok": bool(tool_result.get("ok")),
            "status": "GREEN" if tool_result.get("ok") else "RED",
            "tool_result": {
                k: tool_result.get(k)
                for k in (
                    "tool",
                    "ok",
                    "path",
                    "error",
                    "count",
                    "entries",
                    "stdout",
                    "returncode",
                    "bytes",
                    "sha256",
                    "tools",
                    "file_count",
                    "text",
                )
                if k in tool_result
            },
            "calls_total": st["calls"],
            "task_id": tid,
        }
        self._write(self.last_call_path, out)
        return out

    def call_many(
        self,
        tags: list[str],
        *,
        goal: str = "",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Call only listed tags; all others stay IDLE untouched."""
        tid = task_id or f"mesh_multi_{int(time.time())}"
        results = []
        for tag in tags:
            results.append(
                self.call_node(tag, goal=goal, task_id=f"{tid}_{tag}", run_tool=True)
            )
        ok_n = sum(1 for r in results if r.get("ok"))
        # verify others still idle
        st = self.status()
        return {
            "schema": "drone.mesh_nodes.call_many.v1",
            "utc": _utc(),
            "false_green": 0,
            "called": list(tags),
            "ok_n": ok_n,
            "total_called": len(results),
            "results": results,
            "idle_after": st.get("idle_count"),
            "busy_after": st.get("busy_count"),
            "status": "GREEN"
            if ok_n == len(results) and results
            else ("PARTIAL" if ok_n else "RED"),
        }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone mesh-nodes")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("hardwire-idle")
    sub.add_parser("roster")
    c = sub.add_parser("call")
    c.add_argument("tag")
    c.add_argument("--goal", default="")
    cm = sub.add_parser("call-many")
    cm.add_argument("tags", nargs="+", help="tags to wake; others stay idle")
    cm.add_argument("--goal", default="")

    args = p.parse_args(argv)
    reg = MeshNodeRegistry(Path(args.root) if args.root else None)

    if args.cmd == "status":
        print(json.dumps(reg.status(), indent=2))
        return 0
    if args.cmd == "hardwire-idle":
        print(json.dumps(reg.hardwire_idle(), indent=2))
        return 0
    if args.cmd == "roster":
        print(json.dumps({"nodes": reg.roster.get("nodes"), "flagships": reg.roster.get("flagships"), "false_green": 0}, indent=2))
        return 0
    if args.cmd == "call":
        out = reg.call_node(args.tag, goal=args.goal or "")
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "call-many":
        out = reg.call_many(list(args.tags), goal=args.goal or "")
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
