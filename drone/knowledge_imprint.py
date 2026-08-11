"""
MAX multi-source task knowledge imprint.

Boss law: drones are imprinted with the knowledge they need for *this* task —
school / curriculum / instai / AI Smarts / codex / core lessons — without
spinning school daily or loading the entire corpus into every unit.

Sources (max mode):
  learning-curriculum FTS · helper-school · instai peer lessons (222) ·
  ai_center_reference.db · AI Smarts packs · F: PACK + expand ·
  D: core lessons · in-process codex (+vram)

Latency: disk cache + process caches; budget default 200 ms warm.
false_green: 0 — only real paths / real bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_CACHE_LOCK = threading.RLock()
_JSON_CACHE: dict[str, Any] = {}
_FTS_CONN: sqlite3.Connection | None = None
_FTS_PATH: str | None = None
_REF_CONN: sqlite3.Connection | None = None
_REF_PATH: str | None = None
_INSTAI_INDEX: list[dict[str, Any]] | None = None

DEFAULTS = {
    "enabled": True,
    "mode": "max",  # max | lean
    "cold_mode": False,  # max loads capped bodies; cold = refs only
    "max_lessons": 5,
    "max_chars_per_lesson": 2400,
    "max_total_chars": 14000,
    "head_chars": 240,
    "fts_limit": 6,
    "max_instai": 4,
    "max_ref_snips": 4,
    "max_smarts_packs": 3,
    "max_expand": 2,
    "max_core_lessons": 2,
    "max_helper_school": 2,
    "budget_ms": 200,
    "use_fts": True,
    "use_disk_cache": True,
    "use_instai": True,
    "use_reference_db": True,
    "use_ai_smarts": True,
    "use_core_lessons": True,
    "use_knowledge_pack": True,
    "use_expand": True,
    "codex_extra": ["vram"],
    "registry_sync_cli": False,
    "codex_root": r"F:\GrokSelfLibrary\knowledge\codex",
    "curriculum_root": r"G:\AI-Center\learning-curriculum",
    "curriculum_json": r"G:\AI-Center\learning-curriculum\CURRICULUM.json",
    "curriculum_fts": r"G:\AI-Center\learning-curriculum\index\curriculum_fts.db",
    "school_root": r"G:\AI-Center\helper-school",
    "school_lessons_md": r"G:\AI-Center\helper-school\library\lessons",
    "school_world_kb": r"G:\AI-Center\helper-school\library\world-kb",
    "instai_lessons": r"G:\AI-Home\projects\instai\data\lessons",
    "reference_db": r"G:\AI-Center\databases\ai_center_reference.db",
    "ai_smarts_packs": r"G:\AI-Home\docs\ai-smarts\packs",
    "knowledge_pack_min": r"F:\GrokSelfLibrary\knowledge\PACK.min.json",
    "knowledge_expand": r"F:\GrokSelfLibrary\knowledge\expand",
    "core_lessons": r"D:\GrokCoreMemory\lessons",
    "cache_rel": "data/hive/knowledge_cache",
    "progress_rel": "data/hive/school_progress",
    "library_outbox_rel": "data/hive/library_outbox",
}

_DOMAIN_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("ollama", "local llm", "local model"), "ollama OR local OR agents", "ai_ops"),
    (("blender", "sculpt", "mesh", "uv "), "blender OR mesh OR sculpt", "3d"),
    (("unreal", "ue5", "nanite", "lumen"), "unreal OR ue5 OR nanite", "game"),
    (("rag", "embedding", "vector"), "rag OR embedding OR retrieval", "ai"),
    (("agent", "swarm", "multi-agent", "hive", "buzzer"), "agents OR multi-agent OR swarm", "ai"),
    (("quant", "gguf", "vram", "inference"), "quant OR vram OR inference OR ollama", "ai_ops"),
    (("python", "code", "coding", "build", "forge"), "python OR coding OR agents", "coding"),
    (("shader", "material", "pbr"), "shader OR material OR pbr", "lookdev"),
    (("animation", "rig", "retarget"), "animation OR rig OR retarget", "animation"),
    (("truth", "false green", "honest"), "truth OR compliance OR false", "compliance"),
    (("memory", "library", "recall"), "memory OR rag OR library", "memory"),
    (("teach", "lesson", "school", "curriculum"), "teaching OR agents OR foundations", "teaching"),
    (("debug", "troubleshoot", "fix"), "debug OR troubleshooting OR ops", "ops"),
]

_SMARTS_DOMAIN = {
    "ai_ops": "inference",
    "ai": "swarm",
    "coding": "coding",
    "3d": "engines",
    "game": "engines",
    "lookdev": "graphics_3d",
    "animation": "graphics_3d",
    "compliance": "compliance",
    "memory": "memory",
    "teaching": "general",
    "ops": "ops",
    "general": "general",
}


def _load_json(path: Path) -> Any | None:
    key = str(path.resolve()) if path.exists() else str(path)
    with _CACHE_LOCK:
        if key in _JSON_CACHE:
            return _JSON_CACHE[key]
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    with _CACHE_LOCK:
        _JSON_CACHE[key] = data
    return data


def _cfg_merge(root: Path, cfg: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if cfg:
        out.update({k: v for k, v in cfg.items() if v is not None})
    try:
        wo = Path(root) / "configs" / "work_order.json"
        if wo.is_file():
            data = json.loads(wo.read_text(encoding="utf-8"))
            ki = data.get("knowledge_imprint") or {}
            out.update({k: v for k, v in ki.items() if v is not None})
    except Exception:
        pass
    return out


def classify_goal(goal: str) -> dict[str, Any]:
    g = (goal or "").lower()
    tags: list[str] = []
    fts_q = "agents OR coding OR foundations"
    domain = "general"
    for keys, q, dom in _DOMAIN_HINTS:
        if any(k in g for k in keys):
            tags.append(dom)
            fts_q = q
            domain = dom
            break
    if not tags:
        tags = ["general"]
    use = "coding"
    if any(x in g for x in ("vision", "image", "vlm")):
        use = "vision"
    elif any(x in g for x in ("embed", "rag", "vector")):
        use = "embedding"
    elif any(x in g for x in ("reason", "math", "plan")):
        use = "reasoning"
    elif any(x in g for x in ("tiny", "3b", "edge")):
        use = "tiny_edge"
    elif any(x in g for x in ("tool", "agent", "function", "swarm", "hive")):
        use = "tools"
    return {
        "domain": domain,
        "tags": tags,
        "fts_query": fts_q,
        "codex_use": use,
        "goal_hash": hashlib.sha1(g.encode("utf-8")).hexdigest()[:12],
    }


def query_codex_fast(
    query: str,
    *,
    codex_root: Path | str | None = None,
    max_chars: int = 2500,
) -> dict[str, Any]:
    root = Path(codex_root or DEFAULTS["codex_root"])
    t0 = time.perf_counter()
    parts = [p for p in re.split(r"\s+", (query or "stats").strip()) if p]
    if not parts:
        parts = ["stats"]
    cmd = parts[0].lower()
    arg = parts[1].lower() if len(parts) > 1 else ""

    def _slice(obj: Any) -> Any:
        raw = json.dumps(obj, ensure_ascii=False)
        if len(raw) <= max_chars:
            return obj
        return {"_truncated": True, "preview": raw[:max_chars]}

    try:
        if cmd == "stats":
            data = _load_json(root / "CODEX.min.json") or {}
            payload = data.get("stats") if isinstance(data, dict) else data
            return {
                "ok": True,
                "mode": "in_process",
                "query": " ".join(parts),
                "data": payload,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "path": str(root / "CODEX.min.json"),
                "false_green": 0,
            }
        if cmd == "use":
            use = arg or "coding"
            bu = _load_json(root / "models" / "by_use.min.json") or {}
            payload = bu.get(use, bu) if isinstance(bu, dict) else bu
            return {
                "ok": True,
                "mode": "in_process",
                "query": f"use {use}",
                "data": _slice(payload),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "path": str(root / "models" / "by_use.min.json"),
                "false_green": 0,
            }
        if cmd == "vram":
            vg = _load_json(root / "models" / "vram_guide.min.json") or {}
            payload = (vg.get("host_rtx3060_12gb") if isinstance(vg, dict) else vg) or vg
            return {
                "ok": True,
                "mode": "in_process",
                "query": "vram",
                "data": payload,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "path": str(root / "models" / "vram_guide.min.json"),
                "false_green": 0,
            }
        if cmd == "search" and arg:
            flat = _load_json(root / "models" / "flat_index.min.json") or {}
            hits = [k for k in (flat or {}) if arg in k.lower()][:30]
            return {
                "ok": True,
                "mode": "in_process",
                "query": f"search {arg}",
                "data": hits,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "path": str(root / "models" / "flat_index.min.json"),
                "false_green": 0,
            }
        stats = (_load_json(root / "CODEX.min.json") or {}).get("stats")
        bu = _load_json(root / "models" / "by_use.min.json") or {}
        return {
            "ok": True,
            "mode": "in_process",
            "query": " ".join(parts),
            "data": {"stats": stats, "use_coding": _slice(bu.get("coding", bu))},
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "path": str(root),
            "false_green": 0,
        }
    except Exception as e:
        return {
            "ok": False,
            "mode": "in_process",
            "query": " ".join(parts),
            "error": str(e),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "false_green": 0,
        }


def _fts_conn(db_path: Path) -> sqlite3.Connection | None:
    global _FTS_CONN, _FTS_PATH
    if not db_path.is_file():
        return None
    key = str(db_path.resolve())
    with _CACHE_LOCK:
        if _FTS_CONN is not None and _FTS_PATH == key:
            return _FTS_CONN
        try:
            conn = sqlite3.connect(key, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            _FTS_CONN = conn
            _FTS_PATH = key
            return conn
        except Exception:
            return None


def search_lessons_fts(
    query: str,
    *,
    fts_path: Path,
    limit: int = 3,
) -> list[dict[str, Any]]:
    conn = _fts_conn(fts_path)
    if conn is None:
        return []
    q = re.sub(r"[^\w\s\-]", " ", (query or "").strip())
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return []
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.track_id, m.title, m.level, m.domain, m.md_path
            FROM modules_fts
            JOIN modules m ON m.id = modules_fts.module_id
            WHERE modules_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (q, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def search_lessons_catalog(
    goal: str,
    *,
    curriculum_json: Path,
    limit: int = 3,
) -> list[dict[str, Any]]:
    data = _load_json(curriculum_json)
    if not isinstance(data, dict):
        return []
    modules = data.get("modules") or []
    tokens = set(re.findall(r"[a-z0-9]{3,}", (goal or "").lower()))
    if not tokens or not modules:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for m in modules:
        if not isinstance(m, dict):
            continue
        blob = " ".join(
            str(m.get(k) or "")
            for k in ("id", "title", "domain", "track_id", "summary", "section")
        ).lower()
        score = sum(1 for t in tokens if t in blob)
        if score:
            scored.append((score, m))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("id") or "")))
    out = []
    for _, m in scored[:limit]:
        out.append(
            {
                "id": m.get("id"),
                "track_id": m.get("track_id"),
                "title": m.get("title"),
                "level": m.get("level"),
                "domain": m.get("domain"),
                "md_path": m.get("md_path") or m.get("path"),
            }
        )
    return out


def _read_lesson_body(md_path: str | Path | None, max_chars: int) -> dict[str, Any]:
    if not md_path:
        return {"ok": False, "error": "no md_path"}
    p = Path(str(md_path))
    if not p.is_file():
        return {"ok": False, "error": "missing", "path": str(p)}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        body = text[:max_chars] if max_chars > 0 else ""
        return {
            "ok": True,
            "path": str(p),
            "chars": len(body),
            "truncated": max_chars > 0 and len(text) > max_chars,
            "body": body,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(p)}


def _school_library_snippets(
    goal: str, school_lessons: Path, limit: int = 1, max_chars: int = 800
) -> list[dict[str, Any]]:
    if not school_lessons.is_dir():
        return []
    tokens = set(re.findall(r"[a-z0-9]{3,}", (goal or "").lower()))
    hits: list[tuple[int, Path]] = []
    for p in school_lessons.glob("*.md"):
        name = p.stem.lower().replace("-", " ")
        score = sum(1 for t in tokens if t in name)
        if score:
            hits.append((score, p))
    hits.sort(key=lambda x: -x[0])
    out = []
    for _, p in hits[:limit]:
        body = _read_lesson_body(p, max_chars)
        out.append({"source": "helper_school", "title": p.stem, **body})
    return out


def _instai_index(lessons_dir: Path) -> list[dict[str, Any]]:
    global _INSTAI_INDEX
    with _CACHE_LOCK:
        if _INSTAI_INDEX is not None:
            return _INSTAI_INDEX
    if not lessons_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in lessons_dir.glob("*.json"):
        stem = p.stem.lower()
        parts = stem.split("__")
        pack = parts[0] if parts else ""
        lane = parts[2] if len(parts) > 2 else ""
        tokens = set(re.findall(r"[a-z0-9]{3,}", stem.replace("__", " ").replace("-", " ")))
        rows.append({"path": str(p), "stem": stem, "pack": pack, "lane": lane, "tokens": tokens})
    with _CACHE_LOCK:
        _INSTAI_INDEX = rows
    return rows


def search_instai_lessons(
    goal: str, *, lessons_dir: Path, limit: int = 4
) -> list[dict[str, Any]]:
    tokens = set(re.findall(r"[a-z0-9]{3,}", (goal or "").lower()))
    if not tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in _instai_index(lessons_dir):
        score = len(tokens & row["tokens"])
        if row.get("lane") and row["lane"] in tokens:
            score += 3
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], x[1]["stem"]))
    out: list[dict[str, Any]] = []
    for score, row in scored[:limit]:
        data = _load_json(Path(row["path"])) or {}
        out.append(
            {
                "source": "instai",
                "score": score,
                "path": row["path"],
                "lesson_id": data.get("lesson_id") or row["stem"],
                "title": data.get("lesson_title") or row["stem"],
                "objective": data.get("objective") or "",
                "steps": (data.get("steps") or [])[:8],
                "verify": (data.get("verify") or [])[:6],
                "skills": (data.get("skills") or [])[:12],
                "author_lane": data.get("author_lane") or row.get("lane"),
                "author_pack": data.get("author_pack") or row.get("pack"),
                "difficulty": data.get("difficulty"),
                "is_teaching_tool": bool(data.get("is_teaching_tool")),
                "doable": True,
            }
        )
    return out


def _ref_conn(db_path: Path) -> sqlite3.Connection | None:
    global _REF_CONN, _REF_PATH
    if not db_path.is_file():
        return None
    key = str(db_path.resolve())
    with _CACHE_LOCK:
        if _REF_CONN is not None and _REF_PATH == key:
            return _REF_CONN
        try:
            conn = sqlite3.connect(f"file:{key}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            _REF_CONN = conn
            _REF_PATH = key
            return conn
        except Exception:
            try:
                conn = sqlite3.connect(key, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                _REF_CONN = conn
                _REF_PATH = key
                return conn
            except Exception:
                return None


def search_reference_db(query: str, *, db_path: Path, limit: int = 4) -> dict[str, Any]:
    t0 = time.perf_counter()
    conn = _ref_conn(db_path)
    if conn is None:
        return {"ok": False, "hits": [], "ms": 0, "error": "reference db missing"}
    q = re.sub(r"[^\w\s\-]", " ", (query or "").strip())
    q = re.sub(r"\s+", " ", q).strip()
    toks = [t for t in q.split() if len(t) > 2][:8]
    match = " OR ".join(toks) if toks else q
    if not match:
        return {"ok": False, "hits": [], "ms": 0, "error": "empty query"}
    try:
        rows = conn.execute(
            """
            SELECT heading, tags, snippet(fts_chunks, 0, '>>>', '<<<', '…', 28) AS snip
            FROM fts_chunks WHERE fts_chunks MATCH ? LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        hits = [
            {"heading": r["heading"], "tags": r["tags"], "snippet": (r["snip"] or "")[:500]}
            for r in rows
        ]
        return {
            "ok": True,
            "hits": hits,
            "count": len(hits),
            "query": match,
            "path": str(db_path),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "false_green": 0,
        }
    except Exception as e:
        return {
            "ok": False,
            "hits": [],
            "error": str(e),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "false_green": 0,
        }


def load_ai_smarts_packs(
    goal: str,
    domain: str,
    *,
    packs_dir: Path,
    pack_min: Path,
    limit: int = 3,
    max_chars: int = 1800,
) -> list[dict[str, Any]]:
    names: list[str] = []
    primary = _SMARTS_DOMAIN.get(domain) or "general"
    names.append(primary)
    pack = _load_json(pack_min) or {}
    kw = ((pack.get("spine") or {}).get("keyword_map") or {}) if isinstance(pack, dict) else {}
    g = (goal or "").lower()
    for k, dom in kw.items():
        if k in g and str(dom) not in names:
            names.append(str(dom))
        if len(names) >= limit:
            break
    if "SYSTEM_AI_SMARTS" not in names and len(names) < limit:
        names.append("SYSTEM_AI_SMARTS")
    out: list[dict[str, Any]] = []
    for name in names[:limit]:
        p = packs_dir / f"{name}.md"
        body = _read_lesson_body(p, max_chars)
        out.append(
            {
                "source": "ai_smarts",
                "name": name,
                "path": str(p),
                "body_ok": bool(body.get("ok")),
                "body": body.get("body") if body.get("ok") else "",
                "chars": int(body.get("chars") or 0),
                "truncated": bool(body.get("truncated")),
            }
        )
    return out


def load_expand_digests(
    domain: str, *, expand_dir: Path, limit: int = 2, max_chars: int = 1200
) -> list[dict[str, Any]]:
    if not expand_dir.is_dir():
        return []
    prefer = {
        "ai": ["swarm", "memory", "agent_env"],
        "ai_ops": ["llm_frameworks_codex", "coding_stack"],
        "coding": ["coding_stack", "agent_env"],
        "memory": ["memory"],
        "general": ["swarm", "coding_stack"],
    }.get(domain, ["swarm", "coding_stack", "llm_frameworks_codex"])
    out: list[dict[str, Any]] = []
    for key in prefer:
        if len(out) >= limit:
            break
        candidates = [
            expand_dir / f"{key}.v1.min.json",
            expand_dir / f"{key}.v1.json",
            expand_dir / f"{key}.min.json",
        ]
        hit = next((c for c in candidates if c.is_file()), None)
        if not hit:
            for p in expand_dir.glob(f"*{key}*"):
                if p.suffix == ".json":
                    hit = p
                    break
        if not hit:
            continue
        data = _load_json(hit)
        raw = json.dumps(data, ensure_ascii=False) if data is not None else ""
        out.append(
            {
                "source": "knowledge_expand",
                "key": key,
                "path": str(hit),
                "body": raw[:max_chars],
                "chars": min(len(raw), max_chars),
                "truncated": len(raw) > max_chars,
                "ok": data is not None,
            }
        )
    return out


def load_core_lessons(
    goal: str, *, lessons_dir: Path, limit: int = 2, max_chars: int = 900
) -> list[dict[str, Any]]:
    if not lessons_dir.is_dir():
        return []
    always = ["ABSOLUTE_TRUTH_NO_FALSE_GREEN.md"]
    g = (goal or "").lower()
    extras: list[str] = []
    if any(x in g for x in ("gaslight", "memory", "session", "amnesia")):
        extras.append("session_amnesia_gaslight_2026-08-09.md")
    if "fail" in g or "lesson" in g:
        extras.append("failures/This_Session_Lessons.md")
    names = always + [e for e in extras if e not in always]
    out: list[dict[str, Any]] = []
    for name in names[:limit]:
        p = lessons_dir / name
        body = _read_lesson_body(p, max_chars)
        out.append(
            {
                "source": "core_lessons",
                "title": name,
                "path": str(p),
                "body_ok": bool(body.get("ok")),
                "body": body.get("body") if body.get("ok") else "",
                "chars": int(body.get("chars") or 0),
            }
        )
    return out


def knowledge_sources_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    conf = dict(DEFAULTS)
    if cfg:
        conf.update(cfg)
    paths = {
        "curriculum_fts": conf["curriculum_fts"],
        "curriculum_json": conf["curriculum_json"],
        "school_lessons": conf["school_lessons_md"],
        "instai_lessons": conf["instai_lessons"],
        "reference_db": conf["reference_db"],
        "ai_smarts_packs": conf["ai_smarts_packs"],
        "knowledge_pack_min": conf["knowledge_pack_min"],
        "knowledge_expand": conf["knowledge_expand"],
        "core_lessons": conf["core_lessons"],
        "codex_root": conf["codex_root"],
    }
    report: dict[str, Any] = {"utc": _utc(), "false_green": 0, "mode": conf.get("mode"), "sources": {}}
    for k, p in paths.items():
        path = Path(str(p))
        if path.is_dir():
            n = sum(1 for x in path.iterdir() if x.is_file())
            report["sources"][k] = {"exists": True, "kind": "dir", "path": str(path), "files": n}
        elif path.is_file():
            report["sources"][k] = {
                "exists": True,
                "kind": "file",
                "path": str(path),
                "bytes": path.stat().st_size,
            }
        else:
            report["sources"][k] = {"exists": False, "path": str(path)}
    instai = Path(str(conf["instai_lessons"]))
    if instai.is_dir():
        report["instai_lesson_count"] = len(list(instai.glob("*.json")))
    return report


def build_knowledge_imprint(
    root: Path,
    goal: str,
    *,
    codex_query: str = "",
    cfg: dict[str, Any] | None = None,
    force_refresh: bool = False,
    cold_mode: bool | None = None,
    max_body_chars: int | None = None,
) -> dict[str, Any]:
    """
    Multi-source knowledge pack for one task imprint (MAX default).
    cold_mode=True → refs/heads only (ultra lean).
    """
    root = Path(root)
    t0 = time.perf_counter()
    conf = _cfg_merge(root, cfg)
    if not conf.get("enabled", True):
        return {
            "ok": False,
            "skipped": True,
            "reason": "knowledge_imprint disabled",
            "ms": 0,
            "false_green": 0,
        }

    mode = str(conf.get("mode") or "max").lower()
    if mode == "lean":
        conf["max_lessons"] = min(int(conf.get("max_lessons") or 2), 2)
        conf["max_instai"] = min(int(conf.get("max_instai") or 1), 1)
        conf["max_ref_snips"] = min(int(conf.get("max_ref_snips") or 1), 1)
        conf["max_smarts_packs"] = min(int(conf.get("max_smarts_packs") or 1), 1)
        conf["max_expand"] = min(int(conf.get("max_expand") or 1), 1)
        conf["use_reference_db"] = conf.get("use_reference_db", False)

    if cold_mode is None:
        cold_mode = bool(conf.get("cold_mode", False))
    if max_body_chars is None:
        max_body_chars = 0 if cold_mode else int(conf.get("max_chars_per_lesson") or 2400)
    head_chars = int(conf.get("head_chars") or 240)

    cls = classify_goal(goal)
    cq = (codex_query or "").strip() or f"use {cls['codex_use']}"

    cache_key = f"v2max_{mode}_c{int(bool(cold_mode))}_{cls['goal_hash']}_{cls['domain']}_{cls['codex_use']}"
    cache_dir = root / str(conf.get("cache_rel") or DEFAULTS["cache_rel"])
    cache_path = cache_dir / f"{cache_key}.json"
    if conf.get("use_disk_cache") and not force_refresh and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            cached["ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return cached
        except Exception:
            pass

    timing: dict[str, float] = {}

    # curriculum
    lessons_meta: list[dict[str, Any]] = []
    fts_path = Path(str(conf["curriculum_fts"]))
    curr_json = Path(str(conf["curriculum_json"]))
    if conf.get("use_fts", True):
        s = time.perf_counter()
        lessons_meta = search_lessons_fts(
            cls["fts_query"], fts_path=fts_path, limit=int(conf.get("fts_limit") or 6)
        )
        timing["curriculum_fts_ms"] = round((time.perf_counter() - s) * 1000, 2)
    if not lessons_meta:
        s = time.perf_counter()
        lessons_meta = search_lessons_catalog(
            goal, curriculum_json=curr_json, limit=int(conf.get("max_lessons") or 5)
        )
        timing["curriculum_catalog_ms"] = round((time.perf_counter() - s) * 1000, 2)

    max_lessons = int(conf.get("max_lessons") or 5)
    max_chars = int(max_body_chars or 0)
    max_total = int(conf.get("max_total_chars") or 14000)
    lessons: list[dict[str, Any]] = []
    total = 0
    for meta in lessons_meta[:max_lessons]:
        if total >= max_total:
            break
        if cold_mode or max_chars <= 0:
            head = ""
            if head_chars > 0 and meta.get("md_path"):
                hb = _read_lesson_body(meta.get("md_path"), head_chars)
                head = (hb.get("body") or "") if hb.get("ok") else ""
            entry = {
                "id": meta.get("id"),
                "track_id": meta.get("track_id"),
                "title": meta.get("title"),
                "domain": meta.get("domain"),
                "level": meta.get("level"),
                "md_path": meta.get("md_path"),
                "source": "learning_curriculum",
                "body_ok": False,
                "body": "",
                "head": head,
                "body_loaded": False,
                "cold_ref": True,
                "truncated": False,
                "chars": len(head),
                "doable": True,
            }
        else:
            room = min(max_chars, max_total - total)
            body = _read_lesson_body(meta.get("md_path"), room)
            entry = {
                "id": meta.get("id"),
                "track_id": meta.get("track_id"),
                "title": meta.get("title"),
                "domain": meta.get("domain"),
                "level": meta.get("level"),
                "md_path": meta.get("md_path"),
                "source": "learning_curriculum",
                "body_ok": bool(body.get("ok")),
                "body": body.get("body") if body.get("ok") else "",
                "head": (body.get("body") or "")[:head_chars] if body.get("ok") else "",
                "body_loaded": bool(body.get("ok")),
                "cold_ref": False,
                "truncated": bool(body.get("truncated")),
                "chars": int(body.get("chars") or 0),
                "doable": True,
            }
        lessons.append(entry)
        total += entry["chars"]

    s = time.perf_counter()
    school_snips = _school_library_snippets(
        goal,
        Path(str(conf["school_lessons_md"])),
        limit=int(conf.get("max_helper_school") or 2),
        max_chars=min(1000, max_chars or 1000) if not cold_mode else head_chars,
    )
    timing["helper_school_ms"] = round((time.perf_counter() - s) * 1000, 2)

    instai: list[dict[str, Any]] = []
    if conf.get("use_instai", True):
        s = time.perf_counter()
        instai = search_instai_lessons(
            goal,
            lessons_dir=Path(str(conf["instai_lessons"])),
            limit=int(conf.get("max_instai") or 4),
        )
        timing["instai_ms"] = round((time.perf_counter() - s) * 1000, 2)

    ref: dict[str, Any] = {"ok": False, "hits": [], "skipped": True}
    if conf.get("use_reference_db", True) and not cold_mode:
        s = time.perf_counter()
        ref = search_reference_db(
            cls["fts_query"],
            db_path=Path(str(conf["reference_db"])),
            limit=int(conf.get("max_ref_snips") or 4),
        )
        ref["skipped"] = False
        timing["reference_db_ms"] = round((time.perf_counter() - s) * 1000, 2)

    smarts: list[dict[str, Any]] = []
    if conf.get("use_ai_smarts", True):
        s = time.perf_counter()
        smarts = load_ai_smarts_packs(
            goal,
            cls["domain"],
            packs_dir=Path(str(conf["ai_smarts_packs"])),
            pack_min=Path(str(conf["knowledge_pack_min"])),
            limit=int(conf.get("max_smarts_packs") or 3),
            max_chars=min(1800, max_chars or 1800) if not cold_mode else head_chars,
        )
        timing["ai_smarts_ms"] = round((time.perf_counter() - s) * 1000, 2)

    expand: list[dict[str, Any]] = []
    if conf.get("use_expand", True) and not cold_mode:
        s = time.perf_counter()
        expand = load_expand_digests(
            cls["domain"],
            expand_dir=Path(str(conf["knowledge_expand"])),
            limit=int(conf.get("max_expand") or 2),
        )
        timing["expand_ms"] = round((time.perf_counter() - s) * 1000, 2)

    core: list[dict[str, Any]] = []
    if conf.get("use_core_lessons", True):
        s = time.perf_counter()
        core = load_core_lessons(
            goal,
            lessons_dir=Path(str(conf["core_lessons"])),
            limit=int(conf.get("max_core_lessons") or 2),
            max_chars=900 if not cold_mode else head_chars,
        )
        timing["core_lessons_ms"] = round((time.perf_counter() - s) * 1000, 2)

    pack_meta: dict[str, Any] = {}
    if conf.get("use_knowledge_pack", True):
        s = time.perf_counter()
        kp = _load_json(Path(str(conf["knowledge_pack_min"]))) or {}
        if isinstance(kp, dict):
            pack_meta = {
                "ok": True,
                "id": kp.get("id"),
                "title": kp.get("title"),
                "path": conf["knowledge_pack_min"],
                "domains": list(((kp.get("spine") or {}).get("domain_depth") or {}).keys())[:24],
                "files": ((kp.get("spine") or {}).get("files")),
                "tools": ((kp.get("spine") or {}).get("unique_tools")),
                "expansions": [e.get("domain") for e in (kp.get("expansions") or [])][:12],
                "law": kp.get("law"),
            }
        else:
            pack_meta = {"ok": False, "error": "PACK.min unreadable"}
        timing["pack_meta_ms"] = round((time.perf_counter() - s) * 1000, 2)

    s = time.perf_counter()
    codex = query_codex_fast(cq, codex_root=Path(str(conf["codex_root"])))
    codex_extra: dict[str, Any] = {}
    if not cold_mode:
        for extra in conf.get("codex_extra") or []:
            ex = str(extra).strip().lower()
            if ex:
                codex_extra[ex] = query_codex_fast(ex, codex_root=Path(str(conf["codex_root"])))
    timing["codex_ms"] = round((time.perf_counter() - s) * 1000, 2)

    muscle = {
        "lightning": "llama3.2:3b",
        "code": "qwen2.5-coder:7b",
        "ops": "qwen2.5:7b",
        "avoid": "qwen3.6:latest",
        "endpoint": "http://127.0.0.1:11434",
        "note": "12GB 3060 defaults — do not invent larger stacks",
    }

    do_queue: list[dict[str, Any]] = []
    for les in lessons:
        do_queue.append(
            {
                "kind": "curriculum",
                "id": les.get("id"),
                "title": les.get("title"),
                "path": les.get("md_path"),
            }
        )
    for il in instai:
        do_queue.append(
            {
                "kind": "instai",
                "id": il.get("lesson_id"),
                "title": il.get("title"),
                "path": il.get("path"),
                "steps": il.get("steps"),
                "verify": il.get("verify"),
            }
        )

    ms = round((time.perf_counter() - t0) * 1000, 2)
    budget = float(conf.get("budget_ms") or 200)
    sources_hit = {
        "curriculum": len(lessons),
        "helper_school": len(school_snips),
        "instai": len(instai),
        "reference_db": len(ref.get("hits") or []),
        "ai_smarts": sum(1 for x in smarts if x.get("body_ok")),
        "expand": len(expand),
        "core_lessons": sum(1 for x in core if x.get("body_ok")),
        "knowledge_pack": bool(pack_meta.get("ok")),
        "codex": bool(codex.get("ok")),
    }

    pack_out: dict[str, Any] = {
        "schema": "ai.worker.drone.knowledge_imprint.v2",
        "utc": _utc(),
        "ok": True,
        "mode": mode,
        "cold_mode": bool(cold_mode),
        "cache_hit": False,
        "goal": (goal or "")[:400],
        "classify": cls,
        "codex_query": cq,
        "codex": {
            "ok": bool(codex.get("ok")),
            "mode": codex.get("mode"),
            "query": codex.get("query"),
            "ms": codex.get("ms"),
            "data": (
                codex.get("data")
                if not cold_mode
                else {"_cold": True, "query": codex.get("query"), "ok": codex.get("ok")}
            ),
            "path": codex.get("path"),
            "error": codex.get("error"),
            "extra": {
                k: {"ok": v.get("ok"), "data": v.get("data"), "ms": v.get("ms")}
                for k, v in codex_extra.items()
            },
        },
        "school": {
            "mode": "multi_source_max" if not cold_mode else "cold_refs",
            "curriculum_fts": str(fts_path),
            "lessons_matched": len(lessons_meta),
            "lessons_imprinted": len(lessons),
            "lessons": lessons,
            "helper_school_snips": school_snips,
            "do_not": "per-buzzer school daily / full seat online / full corpus RAM",
        },
        "instai": {"root": conf["instai_lessons"], "matched": len(instai), "lessons": instai},
        "reference": ref,
        "ai_smarts": smarts,
        "expand": expand,
        "core_lessons": core,
        "knowledge_pack": pack_meta,
        "do_queue": do_queue[:12],
        "sources_hit": sources_hit,
        "host_muscle": muscle,
        "timing_ms": timing,
        "latency": {
            "ms": ms,
            "budget_ms": budget,
            "within_budget": ms <= budget,
            "strategy": "multi_source_max_capped + in_process_codex + disk_cache",
        },
        "false_green": 0,
        "honesty": {
            "imprint_not_full_corpus": True,
            "capability_max": mode == "max" and not cold_mode,
            "bodies_in_pack": any(bool(x.get("body")) for x in lessons),
            "not_full_llm_per_drone": True,
            "full_llm_roster_access": True,
            "instai_peer_lessons_wired": conf.get("use_instai", True),
        },
    }

    # Full LLM resource imprint (slim) — agents know every callable model + tools
    try:
        s = time.perf_counter()
        from .llm_resources import LLMResources

        pack_out["llm_resources"] = LLMResources(root).imprint_pack()
        timing["llm_resources_ms"] = round((time.perf_counter() - s) * 1000, 2)
        pack_out["timing_ms"] = timing
        pack_out["sources_hit"]["llm_resources"] = bool(
            (pack_out.get("llm_resources") or {}).get("reachable")
        )
    except Exception as e:
        pack_out["llm_resources"] = {
            "ok": False,
            "error": str(e),
            "false_green": 0,
        }

    if conf.get("use_disk_cache"):
        try:
            with root_lock(root):
                cache_dir.mkdir(parents=True, exist_ok=True)
                to_store = dict(pack_out)
                to_store["cache_written"] = True
                cache_path.write_text(json.dumps(to_store, ensure_ascii=False), encoding="utf-8")
            pack_out["cache_path"] = str(cache_path)
        except Exception as e:
            pack_out["cache_error"] = str(e)

    pack_out["ms"] = ms
    return pack_out


def do_lesson(
    root: Path,
    *,
    lesson: dict[str, Any],
    buzzer_id: str = "",
    notes: str = "",
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Mark a lesson as studied/done with evidence. No false GREEN without paths/notes."""
    root = Path(root)
    conf = _cfg_merge(root, None)
    progress_dir = root / str(conf.get("progress_rel") or DEFAULTS["progress_rel"])
    lid = str(lesson.get("id") or lesson.get("lesson_id") or "unknown")
    evidence = [e for e in (evidence_paths or []) if e]
    source_path = lesson.get("path") or lesson.get("md_path")
    source_ok = bool(source_path and Path(str(source_path)).is_file())
    status = (
        "GREEN"
        if source_ok and (evidence or notes.strip())
        else ("PARTIAL" if source_ok else "RED")
    )
    blob = {
        "schema": "ai.worker.drone.lesson_done.v1",
        "utc": _utc(),
        "buzzer_id": buzzer_id,
        "lesson_id": lid,
        "title": lesson.get("title"),
        "kind": lesson.get("kind") or lesson.get("source") or "unknown",
        "source_path": source_path,
        "source_ok": source_ok,
        "steps": lesson.get("steps") or [],
        "verify": lesson.get("verify") or [],
        "notes": (notes or "")[:2000],
        "evidence_paths": evidence[:16],
        "status": status,
        "false_green": 0,
    }
    with root_lock(root):
        progress_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", lid)[:80]
        path = progress_dir / f"{int(time.time())}_{safe}.json"
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        ledger = progress_dir / "LEDGER.jsonl"
        with ledger.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "utc": blob["utc"],
                        "lesson_id": lid,
                        "status": status,
                        "path": str(path),
                        "buzzer_id": buzzer_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    blob["progress_path"] = str(path)
    blob["ok"] = status in {"GREEN", "PARTIAL"}
    return blob


def write_knowledge_to_library(
    root: Path,
    pack: dict[str, Any],
    *,
    buzzer_id: str = "",
) -> dict[str, Any]:
    """Durable hive library_outbox note for ontology/codex AI-db wiring."""
    root = Path(root)
    conf = _cfg_merge(root, None)
    outbox = root / str(conf.get("library_outbox_rel") or DEFAULTS["library_outbox_rel"])
    slim = {
        "schema": "ai.worker.drone.knowledge_library_note.v1",
        "utc": _utc(),
        "buzzer_id": buzzer_id,
        "goal": pack.get("goal"),
        "mode": pack.get("mode"),
        "classify": pack.get("classify"),
        "sources_hit": pack.get("sources_hit"),
        "do_queue": pack.get("do_queue"),
        "codex_query": pack.get("codex_query"),
        "codex_ok": (pack.get("codex") or {}).get("ok"),
        "knowledge_pack_id": (pack.get("knowledge_pack") or {}).get("id"),
        "latency_ms": (pack.get("latency") or {}).get("ms"),
        "false_green": 0,
    }
    with root_lock(root):
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"knowledge_{int(time.time())}_{buzzer_id or 'x'}.json"
        path.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    # also try F: mirror under GrokSelfLibrary if present
    f_mirror = Path(r"F:\GrokSelfLibrary\knowledge\indexes\drone_imprints.jsonl")
    try:
        f_mirror.parent.mkdir(parents=True, exist_ok=True)
        with f_mirror.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        f_ok = True
    except Exception as e:
        f_ok = False
        f_err = str(e)
    return {
        "ok": True,
        "path": str(path),
        "f_mirror": str(f_mirror) if f_ok else None,
        "f_mirror_ok": f_ok,
        "f_error": None if f_ok else f_err,
        "false_green": 0,
    }


def knowledge_prompt_block(pack: dict[str, Any], max_chars: int = 7000) -> str:
    if not pack or not pack.get("ok"):
        return ""
    cold = bool(pack.get("cold_mode"))
    parts: list[str] = [
        "# Task knowledge imprint MAX — study then produce real evidence"
        if not cold
        else "# Task knowledge COLD refs — load bodies only if needed"
    ]
    cls = pack.get("classify") or {}
    parts.append(
        f"mode={pack.get('mode')} domain={cls.get('domain')} "
        f"codex_use={cls.get('codex_use')} cold={cold}"
    )
    codex = pack.get("codex") or {}
    if codex.get("ok") and codex.get("data") is not None and not cold:
        parts.append("## Codex")
        parts.append(json.dumps(codex.get("data"), ensure_ascii=False)[:1000])
        for k, v in (codex.get("extra") or {}).items():
            if v.get("ok"):
                parts.append(f"## Codex extra:{k}")
                parts.append(json.dumps(v.get("data"), ensure_ascii=False)[:600])
    for les in ((pack.get("school") or {}).get("lessons") or [])[:4]:
        parts.append(f"## Curriculum: {les.get('title') or les.get('id')}")
        parts.append(f"path: {les.get('md_path')}")
        if les.get("body"):
            parts.append(str(les["body"])[:1400])
        elif les.get("head"):
            parts.append(f"head: {str(les['head'])[:240]}")
    for il in ((pack.get("instai") or {}).get("lessons") or [])[:3]:
        parts.append(f"## Instai: {il.get('title')}")
        parts.append(f"objective: {il.get('objective')}")
        if il.get("steps"):
            parts.append("steps: " + " | ".join(str(s) for s in il["steps"][:6]))
        if il.get("verify"):
            parts.append("verify: " + " | ".join(str(s) for s in il["verify"][:4]))
    for sm in (pack.get("ai_smarts") or [])[:2]:
        if sm.get("body"):
            parts.append(f"## AI Smarts: {sm.get('name')}")
            parts.append(str(sm["body"])[:1200])
    for sn in ((pack.get("reference") or {}).get("hits") or [])[:3]:
        parts.append(f"## Ref: {sn.get('heading')}")
        parts.append(str(sn.get("snippet") or "")[:400])
    for cl in (pack.get("core_lessons") or [])[:1]:
        if cl.get("body"):
            parts.append(f"## Core law: {cl.get('title')}")
            parts.append(str(cl["body"])[:800])
    muscle = pack.get("host_muscle") or {}
    if muscle:
        parts.append(f"## Host muscle: {json.dumps(muscle, ensure_ascii=False)}")
    for d in (pack.get("do_queue") or [])[:8]:
        parts.append(f"- DO [{d.get('kind')}] {d.get('title')} :: {d.get('path')}")
    return "\n".join(parts)[:max_chars]
