"""
Bridge: Buzzer Hive ↔ Grok Self Library + continuous task board.

Honesty:
  - Real paths on this host (F: library, D: continuous).
  - Does not invent library contents.
  - Writebacks only when files/tools exist and writes succeed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Host defaults (BOSS Windows)
DEFAULT_LIBRARY_ROOT = Path(r"F:\GrokSelfLibrary")
DEFAULT_CONTINUOUS_TASKS = Path(r"D:\GrokCoreMemory\continuous\OPEN_TASKS.json")
DEFAULT_PYTHON = Path(
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


class LibraryBridge:
    """Pull context / open tasks from library; optional post-live writeback."""

    def __init__(
        self,
        library_root: Path | None = None,
        continuous_tasks: Path | None = None,
        python_exe: Path | None = None,
    ) -> None:
        self.library_root = Path(library_root or DEFAULT_LIBRARY_ROOT)
        self.continuous_tasks = Path(continuous_tasks or DEFAULT_CONTINUOUS_TASKS)
        self.python_exe = Path(python_exe or DEFAULT_PYTHON)
        self.recall_py = self.library_root / "bin" / "recall.py"
        self.write_py = self.library_root / "bin" / "write_complete_memory.py"
        self.hot_min = self.library_root / "HOT.min.json"
        self.knowledge_pack = self.library_root / "knowledge" / "PACK.min.json"

    def status(self) -> dict[str, Any]:
        return {
            "library_root": str(self.library_root),
            "library_exists": self.library_root.is_dir(),
            "recall_py": str(self.recall_py),
            "recall_exists": self.recall_py.is_file(),
            "write_py_exists": self.write_py.is_file(),
            "hot_min_exists": self.hot_min.is_file(),
            "knowledge_pack_exists": self.knowledge_pack.is_file(),
            "continuous_tasks": str(self.continuous_tasks),
            "continuous_tasks_exists": self.continuous_tasks.is_file(),
            "python_exe": str(self.python_exe),
            "python_exists": self.python_exe.is_file(),
            "utc": _utc(),
        }

    def pull_hot(self) -> dict[str, Any]:
        """Read HOT.min.json if present. No fabrication if missing."""
        if not self.hot_min.is_file():
            return {
                "ok": False,
                "error": "HOT.min.json missing",
                "path": str(self.hot_min),
            }
        try:
            data = json.loads(self.hot_min.read_text(encoding="utf-8"))
            return {"ok": True, "path": str(self.hot_min), "data": data}
        except Exception as e:
            return {"ok": False, "error": str(e), "path": str(self.hot_min)}

    def pull_open_tasks(self, max_n: int = 8) -> dict[str, Any]:
        """Read OPEN tasks from continuous board."""
        if not self.continuous_tasks.is_file():
            return {
                "ok": False,
                "error": "OPEN_TASKS.json missing",
                "path": str(self.continuous_tasks),
                "tasks": [],
            }
        try:
            raw = json.loads(self.continuous_tasks.read_text(encoding="utf-8"))
            tasks = raw.get("tasks") or []
            open_ones = [
                t
                for t in tasks
                if str(t.get("status", "")).upper() in {"OPEN", "IN_PROGRESS"}
            ]
            # priority ascending (1 = highest)
            open_ones.sort(key=lambda t: int(t.get("priority", 99)))
            slim = []
            for t in open_ones[:max_n]:
                slim.append(
                    {
                        "id": t.get("id"),
                        "title": t.get("title"),
                        "status": t.get("status"),
                        "priority": t.get("priority"),
                        "next_action": t.get("next_action") or t.get("next"),
                        "summary": (t.get("summary") or "")[:400],
                        "context_paths": (t.get("context_paths") or [])[:8],
                    }
                )
            return {
                "ok": True,
                "path": str(self.continuous_tasks),
                "count_open": len(open_ones),
                "tasks": slim,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "path": str(self.continuous_tasks),
                "tasks": [],
            }

    def recall_key(self, key: str, timeout_s: int = 30) -> dict[str, Any]:
        """Optional CLI recall via F: library."""
        if not self.recall_py.is_file() or not self.python_exe.is_file():
            return {
                "ok": False,
                "error": "recall.py or python missing",
                "key": key,
            }
        try:
            proc = subprocess.run(
                [str(self.python_exe), str(self.recall_py), "get", key],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.library_root),
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            payload: Any = out
            try:
                payload = json.loads(out) if out else None
            except json.JSONDecodeError:
                pass
            return {
                "ok": proc.returncode == 0,
                "key": key,
                "returncode": proc.returncode,
                "data": payload,
                "stderr": err[:500] if err else "",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "key": key}

    def write_hive_note(self, hive_outbox: Path, note: dict[str, Any]) -> Path:
        """
        Durable hive → library-facing outbox on disk.
        Does NOT fake a successful F: HOT rewrite unless write_complete_memory succeeds.
        """
        hive_outbox = Path(hive_outbox)
        hive_outbox.mkdir(parents=True, exist_ok=True)
        path = hive_outbox / f"hive_note_{int(time.time())}_{note.get('buzzer_id', 'x')}.json"
        blob = {
            "schema": "ai.worker.drone.hive_library_note.v1",
            "utc": _utc(),
            "library_root": str(self.library_root),
            "note": note,
        }
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        return path

    def try_post_live_write(self, timeout_s: int = 60) -> dict[str, Any]:
        """
        Best-effort: run write_complete_memory.py --trigger hive-buzzer.
        Returns real exit code. Never reports GREEN write without evidence.
        """
        if not self.write_py.is_file() or not self.python_exe.is_file():
            return {
                "ok": False,
                "attempted": False,
                "error": "write_complete_memory.py or python missing",
            }
        try:
            proc = subprocess.run(
                [
                    str(self.python_exe),
                    str(self.write_py),
                    "--trigger",
                    "hive-buzzer",
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.library_root),
            )
            return {
                "ok": proc.returncode == 0,
                "attempted": True,
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-800:],
                "stderr_tail": (proc.stderr or "")[-400:],
            }
        except Exception as e:
            return {"ok": False, "attempted": True, "error": str(e)}

    def pack_for_buzzer(self, goal: str | None = None) -> dict[str, Any]:
        """
        Fresh data pack a clean-slate buzzer receives from the library/hive feed.
        Optionally tags that AI bus two-way wire is available (read path).
        """
        status = self.status()
        open_tasks = self.pull_open_tasks()
        hot = self.pull_hot()
        # Prefer open task as default goal if none given
        chosen_goal = (goal or "").strip()
        source = "explicit"
        if not chosen_goal and open_tasks.get("tasks"):
            t0 = open_tasks["tasks"][0]
            chosen_goal = (
                f"{t0.get('title')}: {t0.get('next_action') or t0.get('summary') or ''}"
            ).strip()
            source = f"open_task:{t0.get('id')}"
        return {
            "utc": _utc(),
            "library_status": status,
            "goal": chosen_goal,
            "goal_source": source,
            "open_tasks": open_tasks,
            "hot_ok": bool(hot.get("ok")),
            "hot_path": hot.get("path"),
            # keep HOT small: keys only if dict
            "hot_keys": (
                sorted(list(hot["data"].keys()))[:40]
                if isinstance(hot.get("data"), dict)
                else []
            ),
            "ai_bus": {
                "two_way": True,
                "module": "drone.ai_bus.AIBus",
                "read_channels": [
                    "self_library",
                    "continuous",
                    "codex",
                    "curriculum",
                    "instai",
                    "helper_school",
                    "hive_memory",
                    "hive_board",
                    "live_registry",
                    "ai_smarts",
                    "reference",
                    "work_experience",
                ],
                "write_on_close": "sync_write",
            },
            "honesty": {
                "clean_slate_buzzer_gets": "this pack only + hive durable memory, not prior buzzer RAM",
                "hot_full_body_included": False,
                "ai_surfaces_two_way": True,
            },
        }
