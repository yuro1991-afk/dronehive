"""
Hive durable memory (honest Vector-DB-lite).

What this IS:
  - On-disk document store + token Jaccard retrieval (same family as learn.similar_wins)
  - Survives buzzer death; hive knowledge only

What this is NOT:
  - FAISS / dense embeddings / full commercial Vector DB
  - Magical cross-session LLM weights
"""

from __future__ import annotations

import hashlib
import json
import re
import time
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


class HiveMemory:
    """Durable hive store. Buzzers write here then die. Thread-safe for swarm."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = root_lock(self.root)
        self.dir = self.root / "data" / "hive"
        self.docs_path = self.dir / "vector_docs.jsonl"
        self.meta_path = self.dir / "hive_meta.json"
        self.queue_path = self.dir / "task_queue.jsonl"
        self.outbox = self.dir / "library_outbox"
        self.buzzers_dir = self.dir / "buzzers"
        with self._lock:
            for d in (self.dir, self.outbox, self.buzzers_dir):
                d.mkdir(parents=True, exist_ok=True)
            if not self.meta_path.exists():
                self._write_meta_unlocked(
                    {
                        "schema": "ai.worker.drone.hive_meta.v1",
                        "created_utc": _utc(),
                        "buzzers_spawned": 0,
                        "buzzers_completed": 0,
                        "docs": 0,
                        "swarm_waves": 0,
                        "peak_parallel": 0,
                        "honesty": {
                            "vector_db": "token_jaccard_jsonl",
                            "dense_embeddings": False,
                        },
                    }
                )

    def _read_meta_unlocked(self) -> dict[str, Any]:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def _write_meta_unlocked(self, meta: dict[str, Any]) -> None:
        meta["updated_utc"] = _utc()
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _read_meta(self) -> dict[str, Any]:
        with self._lock:
            return self._read_meta_unlocked()

    def _write_meta(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self._write_meta_unlocked(meta)

    def bump_spawned(self) -> int:
        with self._lock:
            m = self._read_meta_unlocked()
            m["buzzers_spawned"] = int(m.get("buzzers_spawned", 0)) + 1
            self._write_meta_unlocked(m)
            return int(m["buzzers_spawned"])

    def bump_completed(self) -> int:
        with self._lock:
            m = self._read_meta_unlocked()
            m["buzzers_completed"] = int(m.get("buzzers_completed", 0)) + 1
            self._write_meta_unlocked(m)
            return int(m["buzzers_completed"])

    def note_parallel(self, live: int) -> None:
        """Track peak concurrent buzzers for honesty/evidence."""
        with self._lock:
            m = self._read_meta_unlocked()
            peak = int(m.get("peak_parallel", 0))
            if live > peak:
                m["peak_parallel"] = live
            self._write_meta_unlocked(m)

    def bump_wave(self) -> int:
        with self._lock:
            m = self._read_meta_unlocked()
            m["swarm_waves"] = int(m.get("swarm_waves", 0)) + 1
            self._write_meta_unlocked(m)
            return int(m["swarm_waves"])

    def enqueue(self, goal: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = {
            "utc": _utc(),
            "goal": goal,
            "meta": meta or {},
            "id": hashlib.sha256(f"{_utc()}:{goal}".encode()).hexdigest()[:12],
        }
        with self._lock:
            with self.queue_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def dequeue_all(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.queue_path.exists():
                return []
            items: list[dict[str, Any]] = []
            with self.queue_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            self.queue_path.write_text("", encoding="utf-8")
            return items

    def write_doc(
        self,
        *,
        buzzer_id: str,
        goal: str,
        text: str,
        tags: list[str] | None = None,
        evidence: list[str] | None = None,
        task_id: str = "",
        status: str = "PARTIAL",
    ) -> dict[str, Any]:
        doc_id = hashlib.sha256(
            f"{buzzer_id}:{task_id}:{goal}:{_utc()}".encode()
        ).hexdigest()[:16]
        doc = {
            "id": doc_id,
            "utc": _utc(),
            "buzzer_id": buzzer_id,
            "task_id": task_id,
            "goal": goal,
            "text": (text or "")[:4000],
            "tags": tags or [],
            "evidence": (evidence or [])[:30],
            "status": status,
            "token_count": len(_tokens(f"{goal} {text}")),
        }
        with self._lock:
            with self.docs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            m = self._read_meta_unlocked()
            m["docs"] = int(m.get("docs", 0)) + 1
            self._write_meta_unlocked(m)
        return doc

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            if not self.docs_path.exists():
                return []
            lines = self.docs_path.read_text(encoding="utf-8").splitlines()
        q = _tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = _jaccard(q, _tokens(f"{d.get('goal','')} {d.get('text','')}"))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, d in scored[:top_k]:
            slim = {
                "score": round(s, 4),
                "id": d.get("id"),
                "goal": d.get("goal"),
                "status": d.get("status"),
                "buzzer_id": d.get("buzzer_id"),
                "text_preview": (d.get("text") or "")[:240],
                "evidence": (d.get("evidence") or [])[:5],
            }
            out.append(slim)
        return out

    def save_buzzer_seal(self, buzzer_id: str, report: dict[str, Any]) -> Path:
        path = self.buzzers_dir / f"{buzzer_id}.json"
        with self._lock:
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path

    def stats(self) -> dict[str, Any]:
        with self._lock:
            m = self._read_meta_unlocked()
            docs = 0
            if self.docs_path.exists():
                with self.docs_path.open(encoding="utf-8") as f:
                    docs = sum(1 for line in f if line.strip())
        return {
            **m,
            "docs_counted": docs,
            "docs_path": str(self.docs_path),
            "meta_path": str(self.meta_path),
            "outbox": str(self.outbox),
        }
