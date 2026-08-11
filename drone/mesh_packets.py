"""
Super Mesh packets + recycling bus.

Packets are structured M2M handoffs (not free chat). Lifecycle:
  emit → active → consume (conduct/face/tools) → recycle pool → re-inject or archive

false_green: 0
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short() -> str:
    return uuid.uuid4().hex[:12]


class MeshPacket:
    """Callosum-compatible mesh packet."""

    SCHEMA = "drone.super_mesh.packet.v1"
    FIELDS = (
        "id",
        "from_node",
        "to_node",
        "goal",
        "task_id",
        "payload",
        "evidence",
        "next_action",
        "veto",
        "skill_tags",
        "metrics",
        "hemi",
        "generation",
        "parent_ids",
        "recycled",
        "recycle_count",
        "utc",
        "false_green",
    )

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        d = dict(data or {})
        d.update(kwargs)
        self.id = str(d.get("id") or f"pkt_{_short()}")
        self.from_node = str(d.get("from_node") or "unknown")
        self.to_node = str(d.get("to_node") or "fx-reason")
        self.goal = str(d.get("goal") or "")
        self.task_id = str(d.get("task_id") or "")
        self.payload = str(d.get("payload") or d.get("brief") or d.get("notes") or "")
        self.evidence = list(d.get("evidence") or [])
        self.next_action = str(d.get("next_action") or "")
        self.veto = bool(d.get("veto") or False)
        self.skill_tags = list(d.get("skill_tags") or [])
        self.metrics = dict(d.get("metrics") or {})
        self.hemi = str(d.get("hemi") or "unknown")
        self.generation = int(d.get("generation") or 0)
        self.parent_ids = list(d.get("parent_ids") or [])
        self.recycled = bool(d.get("recycled") or False)
        self.recycle_count = int(d.get("recycle_count") or 0)
        self.utc = str(d.get("utc") or _utc())
        self.false_green = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "id": self.id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "goal": self.goal,
            "task_id": self.task_id,
            "payload": self.payload,
            "evidence": self.evidence,
            "next_action": self.next_action,
            "veto": self.veto,
            "skill_tags": self.skill_tags,
            "metrics": self.metrics,
            "hemi": self.hemi,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "recycled": self.recycled,
            "recycle_count": self.recycle_count,
            "utc": self.utc,
            "false_green": 0,
            "sha256": self.sha256(),
        }

    def sha256(self) -> str:
        raw = json.dumps(
            {
                "from": self.from_node,
                "to": self.to_node,
                "payload": self.payload[:2000],
                "evidence": self.evidence[:20],
                "gen": self.generation,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def preview(self, n: int = 160) -> str:
        return (self.payload or "")[:n]

    def clone_recycled(self, *, to_node: str = "fx-reason", extra_tag: str = "recycled") -> "MeshPacket":
        """Create next-generation recycled packet for re-injection."""
        return MeshPacket(
            id=f"pkt_{_short()}",
            from_node=self.from_node,
            to_node=to_node,
            goal=self.goal,
            task_id=self.task_id,
            payload=self.payload,
            evidence=list(self.evidence) + [f"recycled_from:{self.id}"],
            next_action=self.next_action,
            veto=self.veto,
            skill_tags=list(dict.fromkeys(self.skill_tags + [extra_tag])),
            metrics={**self.metrics, "prior_id": self.id},
            hemi=self.hemi,
            generation=self.generation + 1,
            parent_ids=list(dict.fromkeys(self.parent_ids + [self.id])),
            recycled=True,
            recycle_count=self.recycle_count + 1,
            utc=_utc(),
        )


class PacketRecycleBus:
    """
    Active + recycle pool for mesh packets.

    - emit(): put on active ring
    - consume(): take for conduct/tools/face (does not delete — marks pending recycle)
    - recycle(): move consumed → recycle pool + archive dump
    - reinject(): pull best recycled packets into next membrane
    - flush_archive(): durable dump under data/super_mesh/recycle/
    """

    def __init__(self, root: Path, *, max_active: int = 64, max_recycle: int = 128) -> None:
        self.root = Path(root)
        self.data = self.root / "data" / "super_mesh"
        self.recycle_dir = self.data / "recycle"
        self.active_path = self.data / "PACKETS_ACTIVE.json"
        self.pool_path = self.data / "PACKETS_RECYCLE_POOL.json"
        self.index_path = self.data / "PACKETS_INDEX.jsonl"
        self.recycle_dir.mkdir(parents=True, exist_ok=True)
        self.max_active = max_active
        self.max_recycle = max_recycle
        self.active: list[dict[str, Any]] = self._load_list(self.active_path)
        self.pool: list[dict[str, Any]] = self._load_list(self.pool_path)

    def _load_list(self, path: Path) -> list[dict[str, Any]]:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = data.get("packets") if isinstance(data, dict) else data
                return list(rows or [])
            except (json.JSONDecodeError, OSError, TypeError):
                return []
        return []

    def _save_list(self, path: Path, packets: list[dict[str, Any]], schema: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "utc": _utc(),
                    "count": len(packets),
                    "packets": packets,
                    "false_green": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _index(self, event: str, pkt: dict[str, Any]) -> None:
        row = {
            "utc": _utc(),
            "event": event,
            "id": pkt.get("id"),
            "from": pkt.get("from_node"),
            "to": pkt.get("to_node"),
            "recycled": pkt.get("recycled"),
            "generation": pkt.get("generation"),
            "sha256": pkt.get("sha256"),
            "false_green": 0,
        }
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def persist(self) -> None:
        # trim rings
        if len(self.active) > self.max_active:
            overflow = self.active[: -self.max_active]
            self.active = self.active[-self.max_active :]
            for p in overflow:
                self._archive_one(p, reason="active_overflow")
                self.pool.append(p)
        if len(self.pool) > self.max_recycle:
            old = self.pool[: -self.max_recycle]
            self.pool = self.pool[-self.max_recycle :]
            for p in old:
                self._archive_one(p, reason="pool_overflow")
        self._save_list(self.active_path, self.active, "drone.super_mesh.packets_active.v1")
        self._save_list(self.pool_path, self.pool, "drone.super_mesh.packets_recycle.v1")

    def _archive_one(self, pkt: dict[str, Any], reason: str = "recycle") -> Path:
        day = time.strftime("%Y%m%d", time.gmtime())
        folder = self.recycle_dir / day
        folder.mkdir(parents=True, exist_ok=True)
        pid = str(pkt.get("id") or _short())
        path = folder / f"{pid}.json"
        blob = dict(pkt)
        blob["archive_reason"] = reason
        blob["archived_utc"] = _utc()
        blob["false_green"] = 0
        path.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def emit(self, packet: MeshPacket | dict[str, Any]) -> dict[str, Any]:
        pkt = packet.to_dict() if isinstance(packet, MeshPacket) else dict(packet)
        pkt.setdefault("schema", MeshPacket.SCHEMA)
        pkt["false_green"] = 0
        pkt.setdefault("utc", _utc())
        if "sha256" not in pkt:
            pkt["sha256"] = MeshPacket(pkt).sha256()
        self.active.append(pkt)
        self._index("emit", pkt)
        self.persist()
        return pkt

    def emit_many(self, packets: list[MeshPacket | dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for p in packets:
            out.append(self.emit(p))
        return out

    def list_active(self, limit: int = 32) -> list[dict[str, Any]]:
        return list(self.active[-limit:])

    def list_pool(self, limit: int = 32) -> list[dict[str, Any]]:
        return list(self.pool[-limit:])

    def consume(self, *, task_id: str = "", limit: int = 32) -> list[dict[str, Any]]:
        """Snapshot active packets for this task (keep in active until recycle)."""
        rows = []
        for p in reversed(self.active):
            if task_id and p.get("task_id") and p.get("task_id") != task_id:
                continue
            rows.append(p)
            if len(rows) >= limit:
                break
        rows.reverse()
        for p in rows:
            self._index("consume", p)
        return rows

    def recycle(
        self,
        packet_ids: list[str] | None = None,
        *,
        reinject: bool = True,
        reason: str = "consumed",
    ) -> dict[str, Any]:
        """
        Move packets from active → recycle pool + archive.
        If reinject: push next-gen clones back to active for next tick.
        """
        want = set(packet_ids) if packet_ids else None
        kept: list[dict[str, Any]] = []
        recycled: list[dict[str, Any]] = []
        reinjected: list[dict[str, Any]] = []

        for p in self.active:
            pid = str(p.get("id") or "")
            if want is not None and pid not in want:
                kept.append(p)
                continue
            # archive original
            arch = self._archive_one(p, reason=reason)
            p2 = dict(p)
            p2["recycled"] = True
            p2["recycle_count"] = int(p.get("recycle_count") or 0) + 1
            p2["archive_path"] = str(arch)
            self.pool.append(p2)
            recycled.append(p2)
            self._index("recycle", p2)
            if reinject:
                nxt = MeshPacket(p).clone_recycled()
                reinjected.append(self.emit(nxt))  # emit will re-persist

        if want is not None:
            self.active = kept
            # emit() already appended reinjected; avoid double-count by filtering
            # emit adds to active — reinjected already in active via emit()
        else:
            # recycle all that were active before reinject
            reinjected_ids = {r.get("id") for r in reinjected}
            self.active = [p for p in self.active if p.get("id") in reinjected_ids]

        self.persist()
        return {
            "schema": "drone.super_mesh.recycle.v1",
            "utc": _utc(),
            "false_green": 0,
            "recycled_n": len(recycled),
            "reinjected_n": len(reinjected),
            "active_n": len(self.active),
            "pool_n": len(self.pool),
            "recycled_ids": [p.get("id") for p in recycled],
            "reinjected_ids": [p.get("id") for p in reinjected],
            "recycle_dir": str(self.recycle_dir),
        }

    def reinject_best(
        self,
        *,
        limit: int = 4,
        goal: str = "",
        min_generation: int = 0,
    ) -> list[dict[str, Any]]:
        """Pick high-value recycled packets for membrane injection."""
        scored: list[tuple[float, dict[str, Any]]] = []
        for p in self.pool:
            if int(p.get("generation") or 0) < min_generation and p.get("recycled"):
                pass
            score = 0.3
            score += 0.1 * min(5, int(p.get("recycle_count") or 0))
            score += 0.05 * min(10, len(p.get("evidence") or []))
            if goal and goal[:40].lower() in (p.get("goal") or "").lower():
                score += 0.4
            if p.get("veto"):
                score -= 0.5
            if (p.get("payload") or "").strip():
                score += 0.2
            scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = []
        for sc, p in scored[:limit]:
            nxt = MeshPacket(p).clone_recycled(extra_tag="reinject")
            d = self.emit(nxt)
            d["_reinject_score"] = round(sc, 4)
            chosen.append(d)
        return chosen

    def membrane_block(self, packets: list[dict[str, Any]], *, max_chars: int = 1800) -> str:
        lines = ["[RECYCLED_PACKETS]"]
        used = 0
        for p in packets:
            line = (
                f"- {p.get('id')} gen={p.get('generation')} "
                f"from={p.get('from_node')}→{p.get('to_node')}: "
                f"{(p.get('payload') or '')[:180]}"
            )
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def merge_packets(
        self,
        packets: list[dict[str, Any]],
        *,
        from_node: str = "m2m-callosum",
        to_node: str = "fx-reason",
        goal: str = "",
        task_id: str = "",
    ) -> MeshPacket:
        """Callosum-style merge → single packet."""
        evidence: list[str] = []
        payloads: list[str] = []
        tags: list[str] = ["callosum", "merge"]
        parent_ids: list[str] = []
        for p in packets:
            if p.get("payload"):
                payloads.append(f"{p.get('from_node')}: {p.get('payload')}")
            evidence.extend(list(p.get("evidence") or []))
            evidence.append(f"merged:{p.get('id')}")
            parent_ids.append(str(p.get("id")))
            tags.extend(list(p.get("skill_tags") or []))
        evidence = list(dict.fromkeys(evidence))[:40]
        tags = list(dict.fromkeys(tags))[:20]
        body = "\n".join(payloads)[:4000]
        return MeshPacket(
            from_node=from_node,
            to_node=to_node,
            goal=goal,
            task_id=task_id,
            payload=body,
            evidence=evidence,
            next_action="conduct",
            skill_tags=tags,
            hemi="callosum",
            generation=max([int(p.get("generation") or 0) for p in packets] + [0]) + 1,
            parent_ids=parent_ids,
            metrics={"merged_n": len(packets)},
        )

    def status(self) -> dict[str, Any]:
        archive_n = 0
        if self.recycle_dir.is_dir():
            archive_n = sum(1 for _ in self.recycle_dir.rglob("*.json"))
        return {
            "schema": "drone.super_mesh.packet_bus.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "active_n": len(self.active),
            "pool_n": len(self.pool),
            "archive_n": archive_n,
            "recycle_dir": str(self.recycle_dir),
            "active_path": str(self.active_path),
            "pool_path": str(self.pool_path),
            "index_path": str(self.index_path),
        }
