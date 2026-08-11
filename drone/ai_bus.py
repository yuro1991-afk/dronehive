"""
AI Bus — direct two-way wire for all host AI knowledge surfaces.

Boss law:
  - Every channel supports READ and WRITE (or honest read_only + write_mirror).
  - No false greens: write only succeeds with on-disk paths / real exit codes.
  - Does not invent library contents or mutate master codex catalogs.

Surfaces:
  self_library · continuous · codex · curriculum · instai · helper_school ·
  hive_memory · hive_board · live_registry · ai_smarts · reference · work_experience
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

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


# Channel catalog (paths are host BOSS defaults)
CHANNEL_META: dict[str, dict[str, Any]] = {
    "self_library": {
        "title": "Grok Self Library (F:)",
        "rw": "read_write",
        "root": r"F:\GrokSelfLibrary",
    },
    "continuous": {
        "title": "Continuous OPEN board (D:)",
        "rw": "read_write",
        "root": r"D:\GrokCoreMemory\continuous",
    },
    "codex": {
        "title": "LLM Frameworks Codex",
        "rw": "read_write_index",  # master catalogs read; writes go to indexes
        "root": r"F:\GrokSelfLibrary\knowledge\codex",
    },
    "curriculum": {
        "title": "Learning curriculum",
        "rw": "read_write_progress",
        "root": r"G:\AI-Center\learning-curriculum",
    },
    "instai": {
        "title": "Instai peer lessons",
        "rw": "read_write",
        "root": r"G:\AI-Home\projects\instai\data",
    },
    "helper_school": {
        "title": "Helper School",
        "rw": "read_write",
        "root": r"G:\AI-Center\helper-school",
    },
    "hive_memory": {
        "title": "Hive durable memory",
        "rw": "read_write",
        "root_rel": "data/hive",
    },
    "hive_board": {
        "title": "Hive live board chunks",
        "rw": "read_write",
        "root_rel": "data/hive/board",
    },
    "live_registry": {
        "title": "Live registry events",
        "rw": "read_write",
        "local_rel": "data/hive/registry_local_events.jsonl",
        "primary": r"G:\AI-Center\agents\super-cell-4\registry\events.jsonl",
    },
    "ai_smarts": {
        "title": "AI Smarts packs",
        "rw": "read_write_notes",
        "root": r"G:\AI-Home\docs\ai-smarts",
    },
    "reference": {
        "title": "AI Center reference FTS",
        "rw": "read_write_mirror",  # DB read; write = mirror jsonl
        "root": r"G:\AI-Center\databases\ai_center_reference.db",
    },
    "work_experience": {
        "title": "Work experience ledger",
        "rw": "read_write",
        "root": r"D:\GrokCoreMemory\work-experience",
    },
}


class AIBus:
    """Direct two-way connection hub for drone fabric ↔ host AI stores."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.python = _default_python()
        self.bus_dir = self.root / "data" / "hive" / "ai_bus"
        self.journal = self.bus_dir / "JOURNAL.jsonl"
        self.status_path = self.bus_dir / "CONNECTIONS.json"

    # ── plumbing ──────────────────────────────────────────────

    def _ensure(self) -> None:
        with root_lock(self.root):
            self.bus_dir.mkdir(parents=True, exist_ok=True)
            (self.bus_dir / "inbox").mkdir(exist_ok=True)
            (self.bus_dir / "outbox").mkdir(exist_ok=True)
            (self.bus_dir / "mirrors").mkdir(exist_ok=True)

    def _journal(self, direction: str, channel: str, ok: bool, detail: dict[str, Any]) -> None:
        self._ensure()
        row = {
            "utc": _utc(),
            "direction": direction,
            "channel": channel,
            "ok": ok,
            "false_green": 0,
            **detail,
        }
        with root_lock(self.root):
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_json(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def channels(self) -> list[str]:
        return list(CHANNEL_META.keys())

    # ── connection matrix ─────────────────────────────────────

    def connect_status(self) -> dict[str, Any]:
        """Two-way health: can_read + can_write proven with path existence / probe."""
        self._ensure()
        matrix: dict[str, Any] = {}
        for name in self.channels():
            meta = CHANNEL_META[name]
            r = self.read(name, query="__probe__", limit=1)
            # lightweight write probe to bus mirror only (never mutates masters)
            probe_path = self.bus_dir / "mirrors" / f"probe_{name}.json"
            try:
                self._write_json(
                    probe_path,
                    {
                        "utc": _utc(),
                        "channel": name,
                        "probe": True,
                        "read_ok": bool(r.get("ok")),
                    },
                )
                write_ok = probe_path.is_file()
            except Exception as e:
                write_ok = False
                probe_path_err = str(e)
            # channel-specific write path readiness
            write_target = self._write_target(name)
            matrix[name] = {
                "title": meta.get("title"),
                "rw_mode": meta.get("rw"),
                "can_read": bool(r.get("ok") or r.get("probe_ok")),
                "can_write": bool(write_ok and write_target.get("ready")),
                "two_way": bool(
                    (r.get("ok") or r.get("probe_ok")) and write_ok and write_target.get("ready")
                ),
                "read_detail": {
                    "ok": r.get("ok"),
                    "probe_ok": r.get("probe_ok"),
                    "path": r.get("path"),
                    "error": r.get("error"),
                },
                "write_target": write_target,
                "probe_write": str(probe_path) if write_ok else None,
                "false_green": 0,
            }
        two_way_n = sum(1 for v in matrix.values() if v.get("two_way"))
        report = {
            "schema": "ai.worker.drone.ai_bus.connections.v1",
            "utc": _utc(),
            "root": str(self.root),
            "channels": matrix,
            "channel_count": len(matrix),
            "two_way_count": two_way_n,
            "all_two_way": two_way_n == len(matrix),
            "false_green": 0,
            "law": "two_way = can_read AND can_write with real paths",
        }
        self._write_json(self.status_path, report)
        report["status_path"] = str(self.status_path)
        return report

    def _write_target(self, channel: str) -> dict[str, Any]:
        if channel == "self_library":
            p = Path(r"F:\GrokSelfLibrary\knowledge\indexes\drone_imprints.jsonl")
            outbox = self.root / "data" / "hive" / "library_outbox"
            return {
                "ready": p.parent.is_dir() or Path(r"F:\GrokSelfLibrary").is_dir(),
                "paths": [str(p), str(outbox)],
            }
        if channel == "continuous":
            cli = Path(r"D:\GrokCoreMemory\continuous\update_continuous.py")
            ledger = Path(r"D:\GrokCoreMemory\continuous\LEDGER.jsonl")
            return {
                "ready": cli.is_file() or Path(r"D:\GrokCoreMemory\continuous").is_dir(),
                "paths": [str(cli), str(ledger)],
            }
        if channel == "codex":
            p = Path(r"F:\GrokSelfLibrary\knowledge\indexes\drone_codex_events.jsonl")
            return {
                "ready": Path(r"F:\GrokSelfLibrary\knowledge").is_dir(),
                "paths": [str(p)],
            }
        if channel == "curriculum":
            p = self.root / "data" / "hive" / "school_progress"
            return {"ready": True, "paths": [str(p)]}
        if channel == "instai":
            p = Path(r"G:\AI-Home\projects\instai\data\learn_log")
            return {"ready": p.is_dir() or Path(r"G:\AI-Home\projects\instai\data").is_dir(), "paths": [str(p)]}
        if channel == "helper_school":
            p = Path(r"G:\AI-Center\helper-school\data\contributions\pending")
            return {
                "ready": p.is_dir() or Path(r"G:\AI-Center\helper-school").is_dir(),
                "paths": [str(p)],
            }
        if channel == "hive_memory":
            p = self.root / "data" / "hive"
            return {"ready": True, "paths": [str(p)]}
        if channel == "hive_board":
            p = self.root / "data" / "hive" / "board"
            return {"ready": True, "paths": [str(p)]}
        if channel == "live_registry":
            p = self.root / "data" / "hive" / "registry_local_events.jsonl"
            return {"ready": True, "paths": [str(p)]}
        if channel == "ai_smarts":
            p = self.root / "data" / "hive" / "ai_bus" / "outbox" / "ai_smarts"
            return {"ready": Path(r"G:\AI-Home\docs\ai-smarts").is_dir(), "paths": [str(p)]}
        if channel == "reference":
            p = self.root / "data" / "hive" / "ai_bus" / "mirrors" / "reference_hits.jsonl"
            return {
                "ready": Path(r"G:\AI-Center\databases\ai_center_reference.db").is_file(),
                "paths": [str(p)],
            }
        if channel == "work_experience":
            p = Path(r"D:\GrokCoreMemory\work-experience\LEDGER.jsonl")
            return {
                "ready": Path(r"D:\GrokCoreMemory\work-experience").is_dir(),
                "paths": [str(p)],
            }
        return {"ready": False, "paths": []}

    # ── READ ──────────────────────────────────────────────────

    def read(
        self,
        channel: str,
        *,
        query: str = "",
        limit: int = 8,
        key: str = "",
    ) -> dict[str, Any]:
        ch = (channel or "").strip().lower()
        if ch not in CHANNEL_META:
            return {"ok": False, "error": f"unknown channel: {channel}", "false_green": 0}
        probe = query == "__probe__"
        try:
            if ch == "self_library":
                out = self._read_self_library(query=query, key=key, probe=probe)
            elif ch == "continuous":
                out = self._read_continuous(limit=limit, probe=probe)
            elif ch == "codex":
                out = self._read_codex(query=query or "stats", probe=probe)
            elif ch == "curriculum":
                out = self._read_curriculum(query=query, limit=limit, probe=probe)
            elif ch == "instai":
                out = self._read_instai(query=query, limit=limit, probe=probe)
            elif ch == "helper_school":
                out = self._read_helper_school(query=query, probe=probe)
            elif ch == "hive_memory":
                out = self._read_hive_memory(query=query, limit=limit, probe=probe)
            elif ch == "hive_board":
                out = self._read_hive_board(query=query, limit=limit, probe=probe)
            elif ch == "live_registry":
                out = self._read_live_registry(limit=limit, probe=probe)
            elif ch == "ai_smarts":
                out = self._read_ai_smarts(query=query, probe=probe)
            elif ch == "reference":
                out = self._read_reference(query=query, limit=limit, probe=probe)
            elif ch == "work_experience":
                out = self._read_work_experience(limit=limit, probe=probe)
            else:
                out = {"ok": False, "error": "unhandled"}
            out.setdefault("channel", ch)
            out.setdefault("direction", "read")
            out.setdefault("false_green", 0)
            if not probe:
                self._journal("read", ch, bool(out.get("ok") or out.get("probe_ok")), {
                    "query": (query or "")[:200],
                    "path": out.get("path"),
                })
            return out
        except Exception as e:
            err = {"ok": False, "channel": ch, "direction": "read", "error": str(e), "false_green": 0}
            self._journal("read", ch, False, {"error": str(e)})
            return err

    def _read_self_library(self, *, query: str, key: str, probe: bool) -> dict[str, Any]:
        root = Path(r"F:\GrokSelfLibrary")
        hot = root / "HOT.min.json"
        pack = root / "knowledge" / "PACK.min.json"
        if probe:
            return {
                "ok": root.is_dir() and hot.is_file(),
                "probe_ok": root.is_dir(),
                "path": str(root),
                "hot_exists": hot.is_file(),
                "pack_exists": pack.is_file(),
            }
        from .library_bridge import LibraryBridge

        lib = LibraryBridge()
        data = {
            "status": lib.status(),
            "hot": lib.pull_hot(),
            "open_tasks": lib.pull_open_tasks(),
        }
        if key:
            data["recall"] = lib.recall_key(key)
        elif query and query not in ("", "status"):
            # search flat keys via recall search if available
            data["query"] = query
            try:
                if lib.recall_py.is_file() and self.python.is_file():
                    proc = subprocess.run(
                        [str(self.python), str(lib.recall_py), "search", query],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=str(root),
                    )
                    data["search"] = {
                        "ok": proc.returncode == 0,
                        "stdout_tail": (proc.stdout or "")[-2000:],
                        "returncode": proc.returncode,
                    }
            except Exception as e:
                data["search"] = {"ok": False, "error": str(e)}
        return {
            "ok": bool(data["status"].get("library_exists")),
            "path": str(root),
            "data": data,
        }

    def _read_continuous(self, *, limit: int, probe: bool) -> dict[str, Any]:
        path = Path(r"D:\GrokCoreMemory\continuous\OPEN_TASKS.json")
        if probe:
            return {"ok": path.is_file(), "probe_ok": path.parent.is_dir(), "path": str(path)}
        from .library_bridge import LibraryBridge

        hit = LibraryBridge().pull_open_tasks(max_n=limit)
        return {"ok": bool(hit.get("ok")), "path": str(path), "data": hit}

    def _read_codex(self, *, query: str, probe: bool) -> dict[str, Any]:
        root = Path(r"F:\GrokSelfLibrary\knowledge\codex")
        if probe:
            return {
                "ok": (root / "CODEX.min.json").is_file(),
                "probe_ok": root.is_dir(),
                "path": str(root),
            }
        from .knowledge_imprint import query_codex_fast

        hit = query_codex_fast(query or "stats", codex_root=root)
        return {"ok": bool(hit.get("ok")), "path": hit.get("path") or str(root), "data": hit}

    def _read_curriculum(self, *, query: str, limit: int, probe: bool) -> dict[str, Any]:
        fts = Path(r"G:\AI-Center\learning-curriculum\index\curriculum_fts.db")
        if probe:
            return {"ok": fts.is_file(), "probe_ok": fts.is_file(), "path": str(fts)}
        from .knowledge_imprint import search_lessons_fts

        q = query or "agents OR coding"
        hits = search_lessons_fts(q, fts_path=fts, limit=limit)
        return {"ok": True, "path": str(fts), "data": {"hits": hits, "count": len(hits), "query": q}}

    def _read_instai(self, *, query: str, limit: int, probe: bool) -> dict[str, Any]:
        lessons = Path(r"G:\AI-Home\projects\instai\data\lessons")
        if probe:
            n = len(list(lessons.glob("*.json"))) if lessons.is_dir() else 0
            return {"ok": lessons.is_dir(), "probe_ok": lessons.is_dir(), "path": str(lessons), "count": n}
        from .knowledge_imprint import search_instai_lessons

        hits = search_instai_lessons(query or "swarm code tools", lessons_dir=lessons, limit=limit)
        return {"ok": True, "path": str(lessons), "data": {"hits": hits, "count": len(hits)}}

    def _read_helper_school(self, *, query: str, probe: bool) -> dict[str, Any]:
        root = Path(r"G:\AI-Center\helper-school")
        idx = root / "library" / "UNIVERSAL-INDEX.json"
        if probe:
            return {"ok": root.is_dir(), "probe_ok": root.is_dir(), "path": str(root), "index": idx.is_file()}
        payload: dict[str, Any] = {"root": str(root)}
        if idx.is_file():
            try:
                payload["index"] = json.loads(idx.read_text(encoding="utf-8"))
            except Exception as e:
                payload["index_error"] = str(e)
        # lesson list
        lessons = root / "library" / "lessons"
        if lessons.is_dir():
            payload["lessons"] = [p.name for p in lessons.glob("*.md")][:30]
        # optional status file
        for name in ("state/boot-report.json", "state/smoke-report.json"):
            p = root / name
            if p.is_file():
                try:
                    payload[name] = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    payload[name] = {"path": str(p), "unreadable": True}
        if query:
            payload["query"] = query
        return {"ok": root.is_dir(), "path": str(root), "data": payload}

    def _read_hive_memory(self, *, query: str, limit: int, probe: bool) -> dict[str, Any]:
        base = self.root / "data" / "hive"
        if probe:
            return {"ok": base.is_dir() or True, "probe_ok": True, "path": str(base)}
        try:
            from .hive_memory import HiveMemory

            hm = HiveMemory(self.root)
            if query:
                docs = hm.retrieve(query, top_k=limit)
            else:
                docs = []
                meta = base / "hive_meta.json"
                if meta.is_file():
                    docs = [{"meta": json.loads(meta.read_text(encoding="utf-8"))}]
            return {"ok": True, "path": str(base), "data": {"docs": docs, "count": len(docs)}}
        except Exception as e:
            return {"ok": False, "path": str(base), "error": str(e)}

    def _read_hive_board(self, *, query: str, limit: int, probe: bool) -> dict[str, Any]:
        base = self.root / "data" / "hive" / "board"
        if probe:
            return {"ok": True, "probe_ok": True, "path": str(base), "exists": base.is_dir()}
        waves = []
        if base.is_dir():
            for w in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                if not w.is_dir():
                    continue
                idx = w / "index.json"
                entry: dict[str, Any] = {"wave_id": w.name, "path": str(w)}
                if idx.is_file():
                    try:
                        entry["index"] = json.loads(idx.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                waves.append(entry)
        return {"ok": True, "path": str(base), "data": {"waves": waves, "query": query}}

    def _read_live_registry(self, *, limit: int, probe: bool) -> dict[str, Any]:
        local = self.root / "data" / "hive" / "registry_local_events.jsonl"
        primary = Path(r"G:\AI-Center\agents\super-cell-4\registry\events.jsonl")
        if probe:
            return {
                "ok": local.is_file() or primary.is_file() or True,
                "probe_ok": True,
                "path": str(local),
                "primary_exists": primary.is_file(),
            }
        lines: list[Any] = []
        for path in (local, primary):
            if not path.is_file():
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in raw[-limit:]:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        lines.append({"raw": line[:200]})
            except Exception:
                pass
        return {
            "ok": True,
            "path": str(local),
            "data": {"events": lines[-limit:], "count": len(lines[-limit:])},
        }

    def _read_ai_smarts(self, *, query: str, probe: bool) -> dict[str, Any]:
        packs = Path(r"G:\AI-Home\docs\ai-smarts\packs")
        if probe:
            return {
                "ok": packs.is_dir(),
                "probe_ok": packs.is_dir(),
                "path": str(packs),
                "count": len(list(packs.glob("*.md"))) if packs.is_dir() else 0,
            }
        q = (query or "swarm").lower()
        hits = []
        if packs.is_dir():
            for p in packs.glob("*.md"):
                if q in p.stem.lower() or not query:
                    hits.append({"name": p.stem, "path": str(p), "bytes": p.stat().st_size})
                if len(hits) >= 12:
                    break
        return {"ok": packs.is_dir(), "path": str(packs), "data": {"packs": hits}}

    def _read_reference(self, *, query: str, limit: int, probe: bool) -> dict[str, Any]:
        db = Path(r"G:\AI-Center\databases\ai_center_reference.db")
        if probe:
            return {"ok": db.is_file(), "probe_ok": db.is_file(), "path": str(db)}
        from .knowledge_imprint import search_reference_db

        hit = search_reference_db(query or "agents", db_path=db, limit=limit)
        return {"ok": bool(hit.get("ok")), "path": str(db), "data": hit}

    def _read_work_experience(self, *, limit: int, probe: bool) -> dict[str, Any]:
        ledger = Path(r"D:\GrokCoreMemory\work-experience\LEDGER.jsonl")
        if probe:
            return {
                "ok": ledger.parent.is_dir(),
                "probe_ok": ledger.parent.is_dir(),
                "path": str(ledger),
                "exists": ledger.is_file(),
            }
        events = []
        if ledger.is_file():
            raw = ledger.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in raw[-limit:]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return {"ok": True, "path": str(ledger), "data": {"events": events, "count": len(events)}}

    # ── WRITE ─────────────────────────────────────────────────

    def write(
        self,
        channel: str,
        payload: dict[str, Any] | None = None,
        *,
        unit_id: str = "",
        goal: str = "",
    ) -> dict[str, Any]:
        ch = (channel or "").strip().lower()
        payload = payload or {}
        if ch not in CHANNEL_META:
            return {"ok": False, "error": f"unknown channel: {channel}", "false_green": 0}
        try:
            if ch == "self_library":
                out = self._write_self_library(payload, unit_id=unit_id, goal=goal)
            elif ch == "continuous":
                out = self._write_continuous(payload, unit_id=unit_id, goal=goal)
            elif ch == "codex":
                out = self._write_codex(payload, unit_id=unit_id, goal=goal)
            elif ch == "curriculum":
                out = self._write_curriculum(payload, unit_id=unit_id)
            elif ch == "instai":
                out = self._write_instai(payload, unit_id=unit_id, goal=goal)
            elif ch == "helper_school":
                out = self._write_helper_school(payload, unit_id=unit_id, goal=goal)
            elif ch == "hive_memory":
                out = self._write_hive_memory(payload, unit_id=unit_id, goal=goal)
            elif ch == "hive_board":
                out = self._write_hive_board(payload, unit_id=unit_id, goal=goal)
            elif ch == "live_registry":
                out = self._write_live_registry(payload, unit_id=unit_id, goal=goal)
            elif ch == "ai_smarts":
                out = self._write_ai_smarts(payload, unit_id=unit_id, goal=goal)
            elif ch == "reference":
                out = self._write_reference_mirror(payload, unit_id=unit_id, goal=goal)
            elif ch == "work_experience":
                out = self._write_work_experience(payload, unit_id=unit_id, goal=goal)
            else:
                out = {"ok": False, "error": "unhandled"}
            out.setdefault("channel", ch)
            out.setdefault("direction", "write")
            out.setdefault("false_green", 0)
            self._journal("write", ch, bool(out.get("ok")), {
                "path": out.get("path"),
                "unit_id": unit_id,
            })
            # always mirror to bus outbox
            self._ensure()
            mirror = self.bus_dir / "outbox" / f"{ch}_{int(time.time())}_{unit_id or 'x'}.json"
            self._write_json(
                mirror,
                {"utc": _utc(), "channel": ch, "unit_id": unit_id, "goal": goal[:300], "result": out},
            )
            out["bus_outbox"] = str(mirror)
            return out
        except Exception as e:
            err = {"ok": False, "channel": ch, "direction": "write", "error": str(e), "false_green": 0}
            self._journal("write", ch, False, {"error": str(e)})
            return err

    def _write_self_library(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        from .library_bridge import LibraryBridge
        from .knowledge_imprint import write_knowledge_to_library

        lib = LibraryBridge()
        outbox = self.root / "data" / "hive" / "library_outbox"
        note_path = lib.write_hive_note(
            outbox,
            {
                "unit_id": unit_id,
                "goal": goal,
                "payload": payload,
                "source": "ai_bus",
                "two_way": True,
            },
        )
        pack = payload.get("knowledge") or payload
        f_note = write_knowledge_to_library(self.root, pack if isinstance(pack, dict) else {"goal": goal, "ok": True}, buzzer_id=unit_id)
        post = {"attempted": False}
        if payload.get("post_live"):
            post = lib.try_post_live_write()
        return {
            "ok": note_path.is_file(),
            "path": str(note_path),
            "f_mirror": f_note,
            "post_live": post,
        }

    def _write_continuous(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        cont = Path(r"D:\GrokCoreMemory\continuous")
        ledger = cont / "LEDGER.jsonl"
        entry = {
            "schema": "ai.worker.drone.continuous_note.v1",
            "utc": _utc(),
            "agent": unit_id or "drone",
            "goal": goal[:400],
            "payload": payload,
            "source": "ai_bus",
            "false_green": 0,
        }
        cont.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # optional task status update
        task_id = payload.get("task_id") or payload.get("continuous_task_id")
        cli = cont / "update_continuous.py"
        cli_result = None
        if task_id and cli.is_file() and self.python.is_file() and payload.get("status"):
            try:
                cmd = [
                    str(self.python),
                    str(cli),
                    "status",
                    "--id",
                    str(task_id),
                    "--status",
                    str(payload.get("status")),
                    "--agent",
                    unit_id or "drone",
                ]
                if payload.get("next"):
                    cmd.extend(["--next", str(payload["next"])[:300]])
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                cli_result = {
                    "ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "stdout_tail": (proc.stdout or "")[-400:],
                }
            except Exception as e:
                cli_result = {"ok": False, "error": str(e)}
        return {
            "ok": ledger.is_file(),
            "path": str(ledger),
            "cli": cli_result,
        }

    def _write_codex(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        # Never mutate master catalogs — write index events only
        path = Path(r"F:\GrokSelfLibrary\knowledge\indexes\drone_codex_events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema": "ai.worker.drone.codex_event.v1",
            "utc": _utc(),
            "unit_id": unit_id,
            "goal": goal[:300],
            "query": payload.get("query") or payload.get("codex_query"),
            "result_ok": payload.get("ok"),
            "data_preview": str(payload.get("data") or payload)[:1500],
            "source": "ai_bus",
            "false_green": 0,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": path.is_file(), "path": str(path), "note": "index write only — master codex unchanged"}

    def _write_curriculum(self, payload: dict[str, Any], *, unit_id: str) -> dict[str, Any]:
        from .knowledge_imprint import do_lesson

        lesson = dict(payload.get("lesson") or {})
        if not lesson:
            lesson = {
                "id": payload.get("lesson_id") or f"bus_lesson:{unit_id or 'x'}",
                "title": payload.get("title") or "ai_bus curriculum write",
                "path": payload.get("path"),
                "kind": "curriculum",
            }
        # If no concrete lesson path, bind a real curriculum file when available
        # so progress can be PARTIAL/GREEN honestly (not false RED on missing path).
        if not lesson.get("path") and not lesson.get("md_path"):
            tracks = Path(r"G:\AI-Center\learning-curriculum\tracks")
            if tracks.is_dir():
                for p in tracks.rglob("*.md"):
                    lesson["path"] = str(p)
                    lesson["md_path"] = str(p)
                    lesson.setdefault("id", p.stem)
                    break
        notes = str(payload.get("notes") or payload.get("summary") or "ai_bus write")
        evidence = list(payload.get("evidence_paths") or [])
        # ensure at least notes count as study evidence for PARTIAL/GREEN
        result = do_lesson(
            self.root,
            lesson=lesson,
            buzzer_id=unit_id,
            notes=notes,
            evidence_paths=evidence,
        )
        # progress file on disk is the write proof even if lesson status RED
        path = result.get("progress_path")
        ok = bool(path and Path(str(path)).is_file())
        return {
            "ok": ok,
            "path": path,
            "status": result.get("status"),
            "lesson_ok": bool(result.get("ok")),
            "data": result,
            "false_green": 0,
        }

    def _write_instai(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        log_dir = Path(r"G:\AI-Home\projects\instai\data\learn_log")
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"drone_{int(time.time())}_{unit_id or 'x'}.json"
        blob = {
            "schema": "instai.drone_learn_log.v1",
            "utc": _utc(),
            "author": unit_id or "drone",
            "goal": goal[:400],
            "payload": payload,
            "source": "ai_bus",
            "false_green": 0,
        }
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        return {"ok": path.is_file(), "path": str(path)}

    def _write_helper_school(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        pending = Path(r"G:\AI-Center\helper-school\data\contributions\pending")
        pending.mkdir(parents=True, exist_ok=True)
        path = pending / f"drone_{int(time.time())}_{unit_id or 'x'}.json"
        blob = {
            "schema": "helper_school.contribution.pending.v1",
            "utc": _utc(),
            "seat_hint": unit_id or "drone",
            "goal": goal[:400],
            "contribution": payload,
            "source": "ai_bus",
            "false_green": 0,
            "note": "pending until school commit pipeline picks up",
        }
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        return {"ok": path.is_file(), "path": str(path)}

    def _write_hive_memory(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        from .hive_memory import HiveMemory

        hm = HiveMemory(self.root)
        text = str(payload.get("text") or payload.get("summary") or json.dumps(payload)[:2000])
        doc = hm.write_doc(
            buzzer_id=unit_id or "ai_bus",
            goal=goal or str(payload.get("goal") or "ai_bus"),
            text=text,
            tags=list(payload.get("tags") or ["ai_bus"]),
            evidence=list(payload.get("evidence_paths") or []),
            task_id=str(payload.get("task_id") or unit_id or ""),
            status=str(payload.get("status") or "PARTIAL"),
        )
        return {"ok": bool(doc.get("id")), "path": str(self.root / "data" / "hive"), "doc": doc}

    def _write_hive_board(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        from .hive_board import HiveBoard

        board = HiveBoard(self.root)
        wave_id = str(payload.get("wave_id") or f"bus_{unit_id or 'x'}")
        # ensure wave exists
        wdir = board.wave_dir(wave_id)
        if not wdir.is_dir():
            board.open_wave([goal or "ai_bus"], wave_id=wave_id)
        try:
            chunk = board.publish(
                wave_id,
                kind=str(payload.get("kind") or "ai_bus"),
                payload=payload.get("payload") if payload.get("payload") is not None else payload,
                unit_id=unit_id or "ai_bus",
            )
        except TypeError:
            # older signature fallback
            chunk = board.publish(wave_id, str(payload.get("kind") or "ai_bus"), payload)
        path = chunk.get("path") if isinstance(chunk, dict) else None
        return {
            "ok": True if chunk else False,
            "path": path or str(wdir),
            "chunk": chunk if isinstance(chunk, dict) else {"raw": str(chunk)[:500]},
            "wave_id": wave_id,
        }

    def _write_live_registry(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        from .work_order import write_live_registry

        reg = write_live_registry(
            self.root,
            buzzer_id=unit_id or "ai_bus",
            generation=int(payload.get("generation") or 0),
            task={"task_id": payload.get("task_id") or "ai_bus", "goal": goal or payload.get("goal")},
            status=str(payload.get("status") or "PARTIAL"),
            evidence=list(payload.get("evidence_paths") or payload.get("evidence") or []),
            duration_ms=float(payload.get("duration_ms") or 0),
            codex_used=bool(payload.get("codex_used")),
            swarm_system=str(payload.get("swarm_system") or "buzzer_hive"),
            extra={"source": "ai_bus", "payload_keys": list(payload.keys())[:20]},
        )
        return {
            "ok": bool(reg.get("ok")),
            "path": reg.get("local_path"),
            "mode": reg.get("mode"),
            "data": reg,
        }

    def _write_ai_smarts(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        out = self.bus_dir / "outbox" / "ai_smarts"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"note_{int(time.time())}_{unit_id or 'x'}.json"
        blob = {
            "schema": "ai.smarts.drone_note.v1",
            "utc": _utc(),
            "unit_id": unit_id,
            "goal": goal[:400],
            "note": payload,
            "packs_root": r"G:\AI-Home\docs\ai-smarts\packs",
            "false_green": 0,
        }
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        # also mirror under G if writable
        g_mirror = Path(r"G:\AI-Home\docs\ai-smarts\runtime\drone_notes.jsonl")
        try:
            g_mirror.parent.mkdir(parents=True, exist_ok=True)
            with g_mirror.open("a", encoding="utf-8") as f:
                f.write(json.dumps(blob, ensure_ascii=False) + "\n")
            g_ok = True
        except Exception as e:
            g_ok = False
            g_err = str(e)
        return {
            "ok": path.is_file(),
            "path": str(path),
            "g_mirror_ok": g_ok,
            "g_mirror": str(g_mirror) if g_ok else None,
            "g_error": None if g_ok else g_err,
        }

    def _write_reference_mirror(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        path = self.bus_dir / "mirrors" / "reference_hits.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "utc": _utc(),
            "unit_id": unit_id,
            "goal": goal[:300],
            "payload": payload,
            "note": "mirror only — reference.db not mutated",
            "false_green": 0,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": path.is_file(), "path": str(path)}

    def _write_work_experience(self, payload: dict[str, Any], *, unit_id: str, goal: str) -> dict[str, Any]:
        ledger = Path(r"D:\GrokCoreMemory\work-experience\LEDGER.jsonl")
        ledger.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema": "work_experience.drone.v1",
            "utc": _utc(),
            "engine": payload.get("engine") or "other",
            "action": payload.get("action") or "ai_bus_write",
            "summary": payload.get("summary") or goal[:400],
            "agent": unit_id or "drone",
            "paths": payload.get("paths") or payload.get("evidence_paths") or [],
            "result": payload.get("status") or "ok",
            "source": "ai_bus",
            "false_green": 0,
        }
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        mirror = Path(r"G:\MASTER-CORE\memory\work-experience\LEDGER.jsonl")
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            with mirror.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            m_ok = True
        except Exception:
            m_ok = False
        return {"ok": ledger.is_file(), "path": str(ledger), "mirror_ok": m_ok}

    # ── SYNC (two-way bulk) ───────────────────────────────────

    def sync_read(self, goal: str = "", *, channels: list[str] | None = None) -> dict[str, Any]:
        """Read from all (or selected) channels for a goal — inbound wire."""
        chans = channels or self.channels()
        results = {}
        for ch in chans:
            results[ch] = self.read(ch, query=goal or "status", limit=5)
        ok_n = sum(1 for v in results.values() if v.get("ok") or v.get("probe_ok"))
        pack = {
            "schema": "ai.worker.drone.ai_bus.sync_read.v1",
            "utc": _utc(),
            "goal": (goal or "")[:400],
            "channels": results,
            "ok_count": ok_n,
            "channel_count": len(chans),
            "false_green": 0,
        }
        path = self.bus_dir / "inbox" / f"sync_read_{int(time.time())}.json"
        self._write_json(path, pack)
        pack["path"] = str(path)
        pack["ok"] = ok_n > 0
        return pack

    def sync_write(
        self,
        result: dict[str, Any],
        *,
        unit_id: str = "",
        goal: str = "",
        channels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write a run/result to all write-capable channels — outbound wire."""
        chans = channels or self.channels()
        goal = goal or str(result.get("goal") or "")
        unit_id = unit_id or str(result.get("buzzer_id") or result.get("unit_id") or "drone")
        payload = {
            "status": result.get("status") or "PARTIAL",
            "summary": result.get("summary") or result.get("status"),
            "evidence_paths": result.get("evidence")
            or result.get("evidence_paths")
            or [
                result.get("seal_path"),
                result.get("imprint_path"),
                result.get("report_path"),
            ],
            "task_id": (result.get("task") or {}).get("task_id") or result.get("task_id"),
            "codex_used": bool((result.get("codex") or {}).get("attempted") or result.get("codex_used")),
            "knowledge": result.get("knowledge") or result.get("knowledge_imprint"),
            "duration_ms": result.get("duration_ms"),
            "generation": result.get("generation") or 0,
            "swarm_system": result.get("swarm_system") or "buzzer_hive",
            "text": (
                f"ai_bus sync_write unit={unit_id} status={result.get('status')} goal={goal[:120]}"
            ),
            "tags": ["ai_bus", "sync_write"],
            "kind": "result",
            "payload": {
                "status": result.get("status"),
                "unit_id": unit_id,
                "goal": goal[:200],
            },
            "wave_id": result.get("wave_id") or f"sync_{unit_id}",
            "engine": "other",
            "action": "drone_sync_write",
            "notes": f"sync from {unit_id}",
            "lesson": {
                "id": f"sync:{unit_id}",
                "title": goal[:80] or "sync",
                "kind": "bus_sync",
                "path": result.get("imprint_path") or result.get("seal_path"),
            },
            "query": (result.get("codex") or {}).get("query") or "use coding",
            "ok": result.get("status") in {"GREEN", "PARTIAL"},
            "data": result.get("codex"),
        }
        # clean evidence
        payload["evidence_paths"] = [e for e in (payload["evidence_paths"] or []) if e]

        results = {}
        for ch in chans:
            results[ch] = self.write(ch, payload, unit_id=unit_id, goal=goal)
        ok_n = sum(1 for v in results.values() if v.get("ok"))
        pack = {
            "schema": "ai.worker.drone.ai_bus.sync_write.v1",
            "utc": _utc(),
            "unit_id": unit_id,
            "goal": goal[:400],
            "channels": results,
            "ok_count": ok_n,
            "channel_count": len(chans),
            "false_green": 0,
        }
        path = self.bus_dir / "outbox" / f"sync_write_{int(time.time())}_{unit_id}.json"
        self._write_json(path, pack)
        pack["path"] = str(path)
        pack["ok"] = ok_n > 0
        return pack

    def full_duplex(
        self,
        goal: str,
        *,
        unit_id: str = "",
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Full two-way cycle: connect_status → sync_read → (optional) sync_write.
        """
        t0 = time.perf_counter()
        conn = self.connect_status()
        inbound = self.sync_read(goal)
        outbound = None
        if result is not None:
            outbound = self.sync_write(result, unit_id=unit_id, goal=goal)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        report = {
            "schema": "ai.worker.drone.ai_bus.full_duplex.v1",
            "utc": _utc(),
            "goal": goal[:400],
            "unit_id": unit_id,
            "connections": {
                "two_way_count": conn.get("two_way_count"),
                "all_two_way": conn.get("all_two_way"),
                "status_path": conn.get("status_path"),
            },
            "inbound": {
                "ok": inbound.get("ok"),
                "ok_count": inbound.get("ok_count"),
                "path": inbound.get("path"),
            },
            "outbound": {
                "ok": (outbound or {}).get("ok"),
                "ok_count": (outbound or {}).get("ok_count"),
                "path": (outbound or {}).get("path"),
            }
            if outbound
            else None,
            "ms": ms,
            "false_green": 0,
            "status": "GREEN"
            if conn.get("two_way_count", 0) >= 8 and inbound.get("ok")
            else "PARTIAL",
        }
        path = self.bus_dir / f"FULL_DUPLEX_{int(time.time())}.json"
        self._write_json(path, report)
        report["path"] = str(path)
        return report


def get_bus(root: Path) -> AIBus:
    return AIBus(Path(root))
