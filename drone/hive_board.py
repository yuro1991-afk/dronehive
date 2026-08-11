"""
Hive Board — cold knowledge + live chunk share.

Boss law (latency + honesty):
  - Full curriculum / codex stays COLD on disk (paths + ids only in imprint).
  - Each drone gets ONLY the lesson refs + tool pack for its delegated task.
  - Live work is published as small CHUNKS on a shared board — hive-think.
  - Drones do NOT each load the whole knowledge library into RAM/prompts.

false_green: 0
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Minimal tool packs — exact tools per task class (not the full belt)
TOOL_PACKS: dict[str, list[str]] = {
    "write": [
        "list_tools",
        "write_text",
        "write_json",
        "read_text",
        "list_dir",
        "append_log",
        "package_manifest",
        "board_publish",
        "board_get",
        "board_list",
        "knowledge_chunk",
    ],
    "code": [
        "list_tools",
        "write_text",
        "write_json",
        "read_text",
        "list_dir",
        "run_python",
        "write_and_smoke_python",
        "package_manifest",
        "board_publish",
        "board_get",
        "board_list",
        "knowledge_chunk",
        "append_log",
    ],
    "diag": [
        "list_tools",
        "run_host_diagnostics",
        "run_shell",
        "write_text",
        "write_json",
        "package_manifest",
        "board_publish",
        "board_get",
        "board_list",
        "append_log",
    ],
    "knowledge": [
        "list_tools",
        "knowledge_imprint",
        "knowledge_sources",
        "do_lesson",
        "knowledge_chunk",
        "ai_bus_status",
        "ai_bus_read",
        "ai_bus_write",
        "ai_bus_sync",
        "board_get",
        "board_list",
        "board_publish",
        "write_json",
        "write_text",
        "read_text",
        "package_manifest",
    ],
    "ops": [
        "list_tools",
        "run_shell",
        "library_status",
        "ai_bus_status",
        "ai_bus_read",
        "ai_bus_write",
        "ai_bus_sync",
        "write_text",
        "write_json",
        "read_text",
        "package_manifest",
        "board_publish",
        "board_get",
        "board_list",
        "append_log",
    ],
    "default": [
        "list_tools",
        "write_text",
        "write_json",
        "read_text",
        "list_dir",
        "run_python",
        "package_manifest",
        "board_publish",
        "board_get",
        "board_list",
        "knowledge_chunk",
        "ai_bus_status",
        "ai_bus_read",
        "ai_bus_write",
        "append_log",
    ],
}

# Order matters: first match wins — code before write (goals often say "write python…")
_GOAL_PACK_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("python", "code", "module", "smoke", "script", "forge", ".py", "function def"), "code"),
    (("write", "note", "report", "md ", ".md", "json", "proof", "document", "readme"), "write"),
    (("lesson", "school", "curriculum", "codex", "learn", "teach"), "knowledge"),
    (("diagnostic", "nvidia-smi", "gpu", "disk free", "ping", "sensor", "systems test", "health check", "host diag"), "diag"),
    (("library", "links", "commission", "ops status"), "ops"),
]


def classify_tool_pack(goal: str) -> str:
    g = (goal or "").lower()
    for keys, pack in _GOAL_PACK_HINTS:
        if any(k in g for k in keys):
            return pack
    return "default"


def tools_for_goal(goal: str) -> dict[str, Any]:
    pack_id = classify_tool_pack(goal)
    tools = list(TOOL_PACKS.get(pack_id) or TOOL_PACKS["default"])
    return {
        "pack_id": pack_id,
        "tools": tools,
        "count": len(tools),
        "law": "drone receives only this pack — not full toolkit",
    }


class HiveBoard:
    """
    Shared live board under data/hive/board/<wave_id>/.

    Cold knowledge: refs only in unit packs; bodies fetched once into board chunks.
    Live think: publish tiny chunks; all units in the wave can read them.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.base = self.root / "data" / "hive" / "board"
        self._lock = threading.RLock()

    def wave_dir(self, wave_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", wave_id or "wave")[:64]
        return self.base / safe

    def open_wave(
        self,
        goals: list[str],
        *,
        wave_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        wid = wave_id or f"w_{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        wdir = self.wave_dir(wid)
        with root_lock(self.root):
            wdir.mkdir(parents=True, exist_ok=True)
            (wdir / "chunks").mkdir(exist_ok=True)
            index = {
                "schema": "ai.worker.drone.hive_board.v1",
                "wave_id": wid,
                "utc": _utc(),
                "goals": [(g or "")[:300] for g in goals],
                "goals_n": len(goals),
                "units": {},
                "chunk_count": 0,
                "cold_packs": {},
                "meta": meta or {},
                "law": {
                    "cold_knowledge": True,
                    "live_chunks": True,
                    "no_full_curriculum_per_drone": True,
                    "scoped_tools": True,
                },
                "false_green": 0,
            }
            (wdir / "INDEX.json").write_text(
                json.dumps(index, indent=2), encoding="utf-8"
            )
            (wdir / "chunks.jsonl").write_text("", encoding="utf-8")

        # Publish goal list once (hive-think shared)
        self.publish(
            wid,
            kind="wave_goals",
            payload={"goals": goals, "n": len(goals)},
            unit_id="board",
            tags=["shared", "plan"],
        )
        # Pre-build cold packs once per unique goal classify (not per drone node)
        cold_by_goal: dict[str, Any] = {}
        for g in goals:
            cold_by_goal[g] = self.ensure_cold_pack(wid, g)

        ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "ok": True,
            "wave_id": wid,
            "path": str(wdir),
            "goals_n": len(goals),
            "cold_packs_n": len(cold_by_goal),
            "ms": ms,
            "false_green": 0,
        }

    def _read_index(self, wave_id: str) -> dict[str, Any]:
        p = self.wave_dir(wave_id) / "INDEX.json"
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_index(self, wave_id: str, index: dict[str, Any]) -> None:
        p = self.wave_dir(wave_id) / "INDEX.json"
        with root_lock(self.root):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def publish(
        self,
        wave_id: str,
        *,
        kind: str,
        payload: Any,
        unit_id: str = "",
        tags: list[str] | None = None,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        """Publish a live chunk to the shared board (small, hive-visible)."""
        cid = f"c_{uuid.uuid4().hex[:12]}"
        # cap payload size
        raw = payload
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload or "")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
            if isinstance(payload, (dict, list)):
                try:
                    raw = json.loads(text) if text.startswith("{") or text.startswith("[") else {"_truncated": True, "preview": text}
                except Exception:
                    raw = {"_truncated": True, "preview": text}
            else:
                raw = text

        chunk = {
            "chunk_id": cid,
            "wave_id": wave_id,
            "kind": kind,
            "unit_id": unit_id or "",
            "tags": list(tags or []),
            "utc": _utc(),
            "chars": len(text),
            "truncated": truncated,
            "payload": raw,
            "false_green": 0,
        }
        wdir = self.wave_dir(wave_id)
        with self._lock:
            with root_lock(self.root):
                wdir.mkdir(parents=True, exist_ok=True)
                (wdir / "chunks").mkdir(exist_ok=True)
                (wdir / "chunks" / f"{cid}.json").write_text(
                    json.dumps(chunk, ensure_ascii=False), encoding="utf-8"
                )
                with open(wdir / "chunks.jsonl", "a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "chunk_id": cid,
                                "kind": kind,
                                "unit_id": unit_id,
                                "tags": tags or [],
                                "utc": chunk["utc"],
                                "chars": chunk["chars"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                idx = self._read_index(wave_id) or {
                    "wave_id": wave_id,
                    "chunk_count": 0,
                    "units": {},
                }
                idx["chunk_count"] = int(idx.get("chunk_count") or 0) + 1
                idx["last_chunk"] = {
                    "chunk_id": cid,
                    "kind": kind,
                    "unit_id": unit_id,
                    "utc": chunk["utc"],
                }
                idx["updated_utc"] = _utc()
                self._write_index(wave_id, idx)
        return {"ok": True, "chunk_id": cid, "kind": kind, "chars": chunk["chars"]}

    def get_chunk(self, wave_id: str, chunk_id: str) -> dict[str, Any]:
        p = self.wave_dir(wave_id) / "chunks" / f"{chunk_id}.json"
        if not p.is_file():
            return {"ok": False, "error": "chunk not found", "chunk_id": chunk_id}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["ok"] = True
            return data
        except Exception as e:
            return {"ok": False, "error": str(e), "chunk_id": chunk_id}

    def list_chunks(
        self,
        wave_id: str,
        *,
        kind: str | None = None,
        unit_id: str | None = None,
        limit: int = 40,
    ) -> dict[str, Any]:
        jl = self.wave_dir(wave_id) / "chunks.jsonl"
        rows: list[dict[str, Any]] = []
        if jl.is_file():
            for line in jl.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if kind and r.get("kind") != kind:
                    continue
                if unit_id and r.get("unit_id") != unit_id:
                    continue
                rows.append(r)
        rows = rows[-limit:]
        return {
            "ok": True,
            "wave_id": wave_id,
            "count": len(rows),
            "chunks": rows,
            "false_green": 0,
        }

    def ensure_cold_pack(self, wave_id: str, goal: str) -> dict[str, Any]:
        """
        Build once per goal-class hash for this wave: lesson REFS only (no full body).
        Shared by all units with the same classify hash — cold hive knowledge.
        """
        from .knowledge_imprint import build_knowledge_imprint, classify_goal

        cls = classify_goal(goal)
        key = f"{cls.get('domain')}_{cls.get('codex_use')}_{cls.get('goal_hash')}"
        idx = self._read_index(wave_id)
        cold = (idx.get("cold_packs") or {}) if idx else {}
        if key in cold and cold[key].get("ok"):
            return {**cold[key], "cache_hit": True, "wave_id": wave_id}

        # cold mode: refs + short heads only
        pack = build_knowledge_imprint(
            self.root,
            goal,
            cold_mode=True,
            max_body_chars=0,  # no bodies in imprint path
        )
        school = pack.get("school") or {}
        lessons = school.get("lessons") or []
        refs = []
        for les in lessons:
            refs.append(
                {
                    "id": les.get("id"),
                    "title": les.get("title"),
                    "md_path": les.get("md_path"),
                    "domain": les.get("domain"),
                    "head": (les.get("body") or les.get("head") or "")[:200],
                    "body_loaded": False,
                }
            )
        # publish refs as shared board chunk (once)
        pub = self.publish(
            wave_id,
            kind="cold_lesson_refs",
            payload={
                "goal": (goal or "")[:200],
                "classify": cls,
                "refs": refs,
                "codex_query": pack.get("codex_query"),
                "codex_ok": (pack.get("codex") or {}).get("ok"),
            },
            unit_id="board",
            tags=["cold", "knowledge", "shared"],
        )
        entry = {
            "ok": bool(pack.get("ok")),
            "key": key,
            "classify": cls,
            "lesson_refs": refs,
            "lessons_n": len(refs),
            "codex_query": pack.get("codex_query"),
            "codex_ok": (pack.get("codex") or {}).get("ok"),
            "codex_ms": (pack.get("codex") or {}).get("ms"),
            "knowledge_ms": pack.get("ms"),
            "chunk_id": pub.get("chunk_id"),
            "cache_hit": False,
            "false_green": 0,
            "law": "bodies stay on disk until knowledge_chunk; not copied per drone",
        }
        idx = self._read_index(wave_id) or {"wave_id": wave_id, "cold_packs": {}}
        packs = dict(idx.get("cold_packs") or {})
        packs[key] = entry
        idx["cold_packs"] = packs
        idx["updated_utc"] = _utc()
        self._write_index(wave_id, idx)
        return {**entry, "wave_id": wave_id}

    def load_lesson_body(
        self,
        wave_id: str,
        *,
        md_path: str,
        max_chars: int = 1600,
        unit_id: str = "",
    ) -> dict[str, Any]:
        """
        On-demand body load → publish as live chunk so the whole hive can reuse
        without each drone re-reading disk.
        """
        # reuse existing body chunk if same path already published this wave
        listed = self.list_chunks(wave_id, kind="lesson_body", limit=80)
        for row in listed.get("chunks") or []:
            full = self.get_chunk(wave_id, row.get("chunk_id") or "")
            pl = full.get("payload") or {}
            if isinstance(pl, dict) and pl.get("md_path") == md_path and pl.get("body"):
                return {
                    "ok": True,
                    "cache_hit": True,
                    "chunk_id": row.get("chunk_id"),
                    "md_path": md_path,
                    "body": pl.get("body"),
                    "chars": pl.get("chars") or len(str(pl.get("body") or "")),
                    "false_green": 0,
                }

        p = Path(md_path)
        if not p.is_file():
            return {"ok": False, "error": "missing lesson file", "md_path": md_path}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            body = text[:max_chars]
            pub = self.publish(
                wave_id,
                kind="lesson_body",
                payload={
                    "md_path": md_path,
                    "body": body,
                    "chars": len(body),
                    "truncated": len(text) > max_chars,
                },
                unit_id=unit_id or "board",
                tags=["cold", "body", "shared"],
                max_chars=max_chars + 400,
            )
            return {
                "ok": True,
                "cache_hit": False,
                "chunk_id": pub.get("chunk_id"),
                "md_path": md_path,
                "body": body,
                "chars": len(body),
                "truncated": len(text) > max_chars,
                "false_green": 0,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "md_path": md_path}

    def assign_unit(
        self,
        wave_id: str,
        *,
        unit_id: str,
        goal: str,
    ) -> dict[str, Any]:
        """
        Exact pack for one delegated unit: tool pack + cold lesson refs + board pointers.
        No full curriculum embedded.
        """
        t0 = time.perf_counter()
        tools = tools_for_goal(goal)
        cold = self.ensure_cold_pack(wave_id, goal)
        # publish assignment (live hive visibility)
        pub = self.publish(
            wave_id,
            kind="unit_assignment",
            payload={
                "unit_id": unit_id,
                "goal": (goal or "")[:300],
                "tool_pack": tools,
                "lesson_ref_ids": [r.get("id") for r in (cold.get("lesson_refs") or [])],
                "cold_chunk_id": cold.get("chunk_id"),
            },
            unit_id=unit_id,
            tags=["assign", "scoped"],
        )
        assignment = {
            "unit_id": unit_id,
            "goal": (goal or "")[:300],
            "tool_pack": tools,
            "cold": {
                "key": cold.get("key"),
                "lesson_refs": cold.get("lesson_refs") or [],
                "lessons_n": cold.get("lessons_n") or 0,
                "chunk_id": cold.get("chunk_id"),
                "codex_query": cold.get("codex_query"),
                "codex_ok": cold.get("codex_ok"),
                "knowledge_ms": cold.get("knowledge_ms"),
                "cache_hit": cold.get("cache_hit"),
            },
            "assignment_chunk_id": pub.get("chunk_id"),
            "wave_id": wave_id,
            "board_path": str(self.wave_dir(wave_id)),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "false_green": 0,
            "honesty": {
                "full_curriculum_loaded": False,
                "bodies_in_assignment": False,
                "tools_scoped": True,
                "shared_board": True,
            },
        }
        # Lock unit registry write so parallel goals don't drop each other
        with self._lock:
            idx = self._read_index(wave_id) or {"units": {}}
            units = dict(idx.get("units") or {})
            units[unit_id] = {
                "goal": assignment["goal"],
                "pack_id": tools["pack_id"],
                "tools_n": tools["count"],
                "lessons_n": assignment["cold"]["lessons_n"],
                "assignment_chunk_id": pub.get("chunk_id"),
                "utc": _utc(),
            }
            idx["units"] = units
            idx["updated_utc"] = _utc()
            self._write_index(wave_id, idx)
        return assignment

    def seal_wave(self, wave_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        idx = self._read_index(wave_id) or {}
        seal = {
            "schema": "ai.worker.drone.hive_board.seal.v1",
            "wave_id": wave_id,
            "utc": _utc(),
            "chunk_count": idx.get("chunk_count"),
            "units_n": len(idx.get("units") or {}),
            "cold_packs_n": len(idx.get("cold_packs") or {}),
            "path": str(self.wave_dir(wave_id)),
            "false_green": 0,
            **(extra or {}),
        }
        out = self.root / "out" / "HIVE_BOARD_LAST.json"
        with root_lock(self.root):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
            (self.wave_dir(wave_id) / "SEAL.json").write_text(
                json.dumps(seal, indent=2), encoding="utf-8"
            )
        seal["seal_path"] = str(out)
        return seal
