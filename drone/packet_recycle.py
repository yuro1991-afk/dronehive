"""
Packet recycle — cut latency by reusing slim work packets.

Boss law:
  - Full 24-node fabric is expensive; light packs (write/code) use FAST lane.
  - After a GREEN unit, deposit a slim PACKET (tools + cold refs + lane).
  - Next unit with same pack_id / goal-class CLAIMS the packet — no re-FTS,
    no re-build of tool catalog, no full imprint knowledge rebuild.
  - Bodies stay cold on disk/board; packet never embeds full curriculum.

false_green: 0
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Packs that auto-route to fast lane (5 nodes) unless force_full
FAST_PACKS = frozenset({"write", "code", "default"})
# Always full fabric (24-node) unless force_fast
FULL_PACKS = frozenset({"diag", "knowledge", "ops"})

_LOCK = threading.RLock()


def goal_class_key(goal: str, pack_id: str = "") -> str:
    g = re.sub(r"\s+", " ", (goal or "").lower().strip())
    # tokenize: keep first 6 significant words for soft match
    toks = re.findall(r"[a-z0-9]{3,}", g)[:8]
    h = hashlib.sha1((" ".join(toks)).encode("utf-8")).hexdigest()[:10]
    return f"{pack_id or 'default'}_{h}"


def packet_dir(root: Path) -> Path:
    return Path(root) / "data" / "hive" / "packet_recycle"


def choose_lane(
    pack_id: str,
    *,
    force_full: bool = False,
    force_fast: bool = False,
    recycled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Decide fast vs full fabric for this unit.
    Recycled packet lane preference wins when pack still matches.
    """
    if force_full:
        return {"lane": "full", "reason": "force_full", "nodes": 24}
    if force_fast:
        return {"lane": "fast", "reason": "force_fast", "nodes": 5}
    pid = (pack_id or "default").lower()
    if recycled and recycled.get("ok") and recycled.get("lane") in {"fast", "full"}:
        # Prefer recycled lane if last run GREEN on this pack
        if recycled.get("status") == "GREEN":
            return {
                "lane": recycled.get("lane"),
                "reason": "packet_recycle_prefer",
                "nodes": 5 if recycled.get("lane") == "fast" else 24,
                "packet_id": recycled.get("packet_id"),
            }
    if pid in FAST_PACKS:
        return {"lane": "fast", "reason": f"pack:{pid}", "nodes": 5}
    if pid in FULL_PACKS:
        return {"lane": "full", "reason": f"pack:{pid}", "nodes": 24}
    return {"lane": "fast", "reason": "default_fast", "nodes": 5}


def deposit_packet(
    root: Path,
    *,
    goal: str,
    pack_id: str,
    tool_pack: dict[str, Any] | None,
    cold: dict[str, Any] | None,
    lane: str,
    status: str,
    unit_id: str = "",
    duration_ms: float = 0.0,
    nodes_run: int = 0,
    board_wave_id: str = "",
    evidence: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write slim recycle packet after unit completes (GREEN preferred)."""
    root = Path(root)
    pid = (pack_id or "default").lower()
    key = goal_class_key(goal, pid)
    packet_id = f"pkt_{key}"
    cold = cold or {}
    refs = cold.get("lesson_refs") or []
    # slim refs only
    slim_refs = [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "md_path": r.get("md_path"),
            "domain": r.get("domain"),
        }
        for r in refs
        if isinstance(r, dict)
    ][:4]
    packet = {
        "schema": "ai.worker.drone.packet_recycle.v1",
        "packet_id": packet_id,
        "key": key,
        "pack_id": pid,
        "utc": _utc(),
        "goal_sample": (goal or "")[:200],
        "tool_pack": {
            "pack_id": (tool_pack or {}).get("pack_id") or pid,
            "tools": list((tool_pack or {}).get("tools") or [])[:24],
            "count": (tool_pack or {}).get("count")
            or len((tool_pack or {}).get("tools") or []),
        },
        "cold": {
            "key": cold.get("key"),
            "lesson_refs": slim_refs,
            "lessons_n": len(slim_refs),
            "codex_query": cold.get("codex_query"),
            "codex_ok": cold.get("codex_ok"),
            "chunk_id": cold.get("chunk_id"),
        },
        "lane": "fast" if lane == "fast" else "full",
        "status": (status or "RED").upper(),
        "unit_id": unit_id,
        "duration_ms": round(float(duration_ms or 0), 2),
        "nodes_run": int(nodes_run or 0),
        "board_wave_id": board_wave_id or "",
        "evidence": [e for e in (evidence or []) if e][:8],
        "recycle_hits": 0,
        "false_green": 0,
        "honesty": {
            "no_full_curriculum": True,
            "bodies_not_embedded": True,
            "reuse_tools_and_refs_only": True,
        },
    }
    if extra:
        packet["extra"] = extra

    d = packet_dir(root)
    path = d / f"{packet_id}.json"
    latest = d / f"LATEST_{pid}.json"
    with _LOCK:
        with root_lock(root):
            d.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
            # only promote LATEST on GREEN
            if packet["status"] == "GREEN":
                latest.write_text(json.dumps(packet, indent=2), encoding="utf-8")
            # index
            idx_path = d / "INDEX.json"
            idx: dict[str, Any] = {}
            if idx_path.is_file():
                try:
                    idx = json.loads(idx_path.read_text(encoding="utf-8"))
                except Exception:
                    idx = {}
            by_pack = dict(idx.get("by_pack") or {})
            by_pack[pid] = {
                "packet_id": packet_id,
                "path": str(path),
                "status": packet["status"],
                "lane": packet["lane"],
                "utc": packet["utc"],
            }
            idx["by_pack"] = by_pack
            idx["updated_utc"] = _utc()
            idx["count"] = int(idx.get("count") or 0) + 1
            idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "packet_id": packet_id,
        "path": str(path),
        "promoted_latest": packet["status"] == "GREEN",
        "false_green": 0,
    }


def claim_packet(
    root: Path,
    *,
    goal: str,
    pack_id: str,
    prefer_latest_pack: bool = True,
) -> dict[str, Any]:
    """
    Claim a recycled packet for this unit.
    Hit: same goal-class key, or LATEST for pack_id (soft reuse).
    """
    root = Path(root)
    t0 = time.perf_counter()
    pid = (pack_id or "default").lower()
    key = goal_class_key(goal, pid)
    d = packet_dir(root)
    exact = d / f"pkt_{key}.json"
    packet: dict[str, Any] | None = None
    hit_mode = "miss"

    if exact.is_file():
        try:
            packet = json.loads(exact.read_text(encoding="utf-8"))
            hit_mode = "exact_goal_class"
        except Exception:
            packet = None

    if packet is None and prefer_latest_pack:
        latest = d / f"LATEST_{pid}.json"
        if latest.is_file():
            try:
                packet = json.loads(latest.read_text(encoding="utf-8"))
                hit_mode = "latest_pack"
            except Exception:
                packet = None

    if not packet or not packet.get("tool_pack"):
        return {
            "ok": False,
            "hit": False,
            "hit_mode": "miss",
            "pack_id": pid,
            "key": key,
            "ms": round((time.perf_counter() - t0) * 1000, 2),
            "false_green": 0,
        }

    # bump hit counter
    try:
        packet["recycle_hits"] = int(packet.get("recycle_hits") or 0) + 1
        packet["last_claimed_utc"] = _utc()
        packet["last_claim_goal"] = (goal or "")[:200]
        with _LOCK:
            with root_lock(root):
                # write back to the file we loaded if possible
                ppath = d / f"{packet.get('packet_id') or f'pkt_{key}'}.json"
                if ppath.is_file() or hit_mode == "exact_goal_class":
                    ppath.write_text(json.dumps(packet, indent=2), encoding="utf-8")
                if hit_mode == "latest_pack":
                    (d / f"LATEST_{pid}.json").write_text(
                        json.dumps(packet, indent=2), encoding="utf-8"
                    )
    except Exception:
        pass

    return {
        "ok": True,
        "hit": True,
        "hit_mode": hit_mode,
        "packet_id": packet.get("packet_id"),
        "pack_id": pid,
        "key": key,
        "lane": packet.get("lane") or "fast",
        "status": packet.get("status"),
        "tool_pack": packet.get("tool_pack"),
        "cold": packet.get("cold"),
        "nodes_run_prev": packet.get("nodes_run"),
        "duration_ms_prev": packet.get("duration_ms"),
        "recycle_hits": packet.get("recycle_hits"),
        "ms": round((time.perf_counter() - t0) * 1000, 2),
        "false_green": 0,
        "law": "reuse tools+cold refs only; re-execute work for new goal",
    }


def prepare_unit_routing(
    root: Path,
    goal: str,
    *,
    force_full: bool = False,
    force_fast: bool = False,
) -> dict[str, Any]:
    """
    One-shot: classify pack → claim recycle → choose lane.
    Used by swarm / brain before execute.
    """
    from .hive_board import tools_for_goal

    t0 = time.perf_counter()
    tools = tools_for_goal(goal)
    pack_id = str(tools.get("pack_id") or "default")
    claimed = claim_packet(root, goal=goal, pack_id=pack_id)
    # if claim hit, prefer its tool list (already scoped)
    if claimed.get("hit") and (claimed.get("tool_pack") or {}).get("tools"):
        tools = {
            "pack_id": pack_id,
            "tools": list(claimed["tool_pack"]["tools"]),
            "count": len(claimed["tool_pack"]["tools"]),
            "law": "from packet recycle",
            "recycled": True,
        }
    lane = choose_lane(
        pack_id,
        force_full=force_full,
        force_fast=force_fast,
        recycled=claimed if claimed.get("hit") else None,
    )
    cold = (claimed.get("cold") if claimed.get("hit") else None) or {}
    return {
        "ok": True,
        "goal": (goal or "")[:300],
        "tool_pack": tools,
        "pack_id": pack_id,
        "lane": lane,
        "packet": claimed,
        "cold_from_packet": bool(cold.get("lesson_refs")),
        "cold": cold,
        "ms": round((time.perf_counter() - t0) * 1000, 2),
        "false_green": 0,
        "honesty": {
            "fast_lane_for_light_packs": pack_id in FAST_PACKS,
            "packet_recycle": bool(claimed.get("hit")),
            "still_executes_work": True,
        },
    }
