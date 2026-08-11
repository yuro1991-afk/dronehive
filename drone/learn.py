"""
Learn from work: more they build, smarter they get.

Honest scope:
  - Durable experience ledger (JSONL)
  - Per-drone + global skill scores
  - Level/XP curve from successful builds
  - Retrieval of past wins for similar goals
  - Routing bias toward stronger nodes

Not claimed here:
  - Full biological brain simulation
  - Instant SOTA language model via XP alone
  - Per-build full 0.5B gradient train (optional later hook)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .locks import root_lock


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class SkillState:
    name: str
    score: float = 0.0  # 0..100
    successes: int = 0
    fails: int = 0
    builds: int = 0
    xp: int = 0
    level: int = 1
    last_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 3),
            "successes": self.successes,
            "fails": self.fails,
            "builds": self.builds,
            "xp": self.xp,
            "level": self.level,
            "last_utc": self.last_utc,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillState":
        return cls(
            name=d["name"],
            score=float(d.get("score", 0)),
            successes=int(d.get("successes", 0)),
            fails=int(d.get("fails", 0)),
            builds=int(d.get("builds", 0)),
            xp=int(d.get("xp", 0)),
            level=int(d.get("level", 1)),
            last_utc=str(d.get("last_utc", "")),
        )


@dataclass
class LearnConfig:
    xp_per_success: int = 10
    xp_per_partial: int = 3
    xp_per_fail: int = 1
    level_base_xp: int = 50


class WorkMemory:
    """Disk-backed experience + skills. Smarter = measurable on disk. Swarm-safe."""

    def __init__(self, root: Path, cfg: LearnConfig | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or LearnConfig()
        self._lock = root_lock(self.root)
        self.exp_dir = self.root / "data" / "experience"
        self.skills_dir = self.root / "data" / "skills"
        self.builds_dir = self.root / "data" / "builds"
        for d in (self.exp_dir, self.skills_dir, self.builds_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.exp_dir / "ledger.jsonl"
        self.global_skills_path = self.skills_dir / "global.json"
        self.node_skills_path = self.skills_dir / "nodes.json"
        self.stats_path = self.skills_dir / "stats.json"
        self._global: dict[str, SkillState] = {}
        self._nodes: dict[str, dict[str, SkillState]] = {}
        with self._lock:
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if self.global_skills_path.exists():
            raw = json.loads(self.global_skills_path.read_text(encoding="utf-8"))
            self._global = {k: SkillState.from_dict(v) for k, v in raw.items()}
        if self.node_skills_path.exists():
            raw = json.loads(self.node_skills_path.read_text(encoding="utf-8"))
            self._nodes = {
                nid: {k: SkillState.from_dict(v) for k, v in skills.items()}
                for nid, skills in raw.items()
            }

    def _load(self) -> None:
        with self._lock:
            self._load_unlocked()

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.global_skills_path.write_text(
            json.dumps({k: v.to_dict() for k, v in self._global.items()}, indent=2),
            encoding="utf-8",
        )
        self.node_skills_path.write_text(
            json.dumps(
                {
                    nid: {k: v.to_dict() for k, v in skills.items()}
                    for nid, skills in self._nodes.items()
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.stats_path.write_text(
            json.dumps(self._fabric_stats_unlocked(), indent=2),
            encoding="utf-8",
        )

    def _level_for_xp(self, xp: int) -> int:
        # level 1 at 0; each level needs level_base * level XP cumulative-ish
        base = max(1, self.cfg.level_base_xp)
        level = 1
        need = base
        remaining = xp
        while remaining >= need:
            remaining -= need
            level += 1
            need = base * level
        return level

    def _bump(
        self,
        skill: SkillState,
        outcome: str,
        weight: float = 1.0,
    ) -> int:
        skill.builds += 1
        skill.last_utc = _utc()
        if outcome == "success":
            skill.successes += 1
            xp = int(self.cfg.xp_per_success * weight)
            skill.score = min(100.0, skill.score + 2.5 * weight)
        elif outcome == "partial":
            xp = int(self.cfg.xp_per_partial * weight)
            skill.score = min(100.0, skill.score + 0.8 * weight)
        else:
            skill.fails += 1
            xp = int(self.cfg.xp_per_fail * weight)
            skill.score = max(0.0, skill.score - 0.5 * weight)
        skill.xp += xp
        skill.level = self._level_for_xp(skill.xp)
        return xp

    def record_node_work(
        self,
        *,
        node_id: str,
        goal: str,
        skill_tags: list[str],
        outcome: str,
        evidence: list[str],
        output: str,
        task_id: str,
    ) -> dict[str, Any]:
        tags = skill_tags or ["general"]
        total_xp = 0
        updated: list[dict[str, Any]] = []
        with self._lock:
            # re-read disk so concurrent buzzers don't clobber each other
            self._load_unlocked()
            node_map = self._nodes.setdefault(node_id, {})
            for tag in tags:
                g = self._global.setdefault(tag, SkillState(name=tag))
                n = node_map.setdefault(tag, SkillState(name=tag))
                total_xp += self._bump(g, outcome)
                total_xp += self._bump(n, outcome, weight=1.0)
                updated.append({"scope": "global", **g.to_dict()})
                updated.append({"scope": f"node:{node_id}", **n.to_dict()})

            entry = {
                "utc": _utc(),
                "task_id": task_id,
                "node_id": node_id,
                "goal": goal,
                "skill_tags": tags,
                "outcome": outcome,
                "evidence": evidence[:20],
                "output_hash": hashlib.sha256((output or "").encode("utf-8")).hexdigest()[:16],
                "output_preview": (output or "")[:400],
                "xp_gain": total_xp,
            }
            with self.ledger_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._save_unlocked()
        return {"xp_gain": total_xp, "skills": updated, "entry": entry}

    def record_build(
        self,
        *,
        task_id: str,
        goal: str,
        ok: bool,
        chain: list[dict[str, Any]],
        skill_tags: list[str],
        artifacts: list[str],
    ) -> Path:
        """Whole-fabric build record. More builds → higher fabric smarts."""
        outcome = "success" if ok else "fail"
        # fabric-level skill
        self.record_node_work(
            node_id="FABRIC",
            goal=goal,
            skill_tags=skill_tags or ["build"],
            outcome=outcome,
            evidence=artifacts,
            output=json.dumps({"nodes": len(chain), "ok": ok}),
            task_id=task_id,
        )
        path = self.builds_dir / f"{task_id}.json"
        with self._lock:
            blob = {
                "task_id": task_id,
                "goal": goal,
                "ok": ok,
                "utc": _utc(),
                "skill_tags": skill_tags,
                "artifacts": artifacts,
                "chain": chain,
                "fabric_stats": self._fabric_stats_unlocked(),
            }
            path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        return path

    def similar_wins(self, goal: str, top_k: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            if not self.ledger_path.exists():
                return []
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        q = _tokens(goal)
        scored: list[tuple[float, dict[str, Any]]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("outcome") != "success":
                continue
            s = _jaccard(q, _tokens(e.get("goal", "")))
            if s > 0:
                scored.append((s, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def node_strength(self, node_id: str) -> float:
        with self._lock:
            skills = self._nodes.get(node_id, {})
            if not skills:
                return 0.0
            return sum(s.score for s in skills.values()) / len(skills)

    def fabric_stats(self) -> dict[str, Any]:
        with self._lock:
            return self._fabric_stats_unlocked()

    def _fabric_stats_unlocked(self) -> dict[str, Any]:
        builds = len(list(self.builds_dir.glob("*.json")))
        ledger_lines = 0
        if self.ledger_path.exists():
            with self.ledger_path.open(encoding="utf-8") as f:
                ledger_lines = sum(1 for _ in f)
        global_level = 1
        global_xp = 0
        if "build" in self._global:
            global_level = self._global["build"].level
            global_xp = self._global["build"].xp
        mean_score = 0.0
        if self._global:
            mean_score = sum(s.score for s in self._global.values()) / len(self._global)
        smart_index = round(
            math.log1p(builds) * 10.0 + mean_score + math.log1p(ledger_lines),
            3,
        )
        return {
            "builds": builds,
            "ledger_events": ledger_lines,
            "global_skills": {k: v.to_dict() for k, v in self._global.items()},
            "build_level": global_level,
            "build_xp": global_xp,
            "mean_skill_score": round(mean_score, 3),
            "smart_index": smart_index,
            "honesty": {
                "smarter_means": "higher smart_index, levels, skill scores, more win retrieval",
                "not_claimed": "human-level AGI or full 0.5B finetune per build",
            },
        }
