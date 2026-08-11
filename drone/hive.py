"""
Buzzer Hive — PARALLEL swarm of clean-slate buzzers.

Loop:
  fan-out N buzzers at once → each runs task → writes hive memory → dies
  as slots free → immediately spawn next fresh buzzer (Fresh Agent Evolution)

Connected to F:\\GrokSelfLibrary + continuous OPEN_TASKS + dual-hemisphere fabric.

Honesty:
  - Parallel controllable workers, NOT N full LLM loads
  - Thread pool + shared disk locks
  - false_green: 0
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from .buzzer import spawn_buzzer
from .controllers import make_lm_fn, validate_controller
from .hive_memory import HiveMemory
from .library_bridge import LibraryBridge


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class BuzzerHive:
    """Hive orchestrator — default mode is parallel swarm."""

    def __init__(
        self,
        root: Path,
        lm_assist: str = "ollama",
        library: LibraryBridge | None = None,
        lane: str = "full",
    ) -> None:
        self.root = Path(root)
        self.library = library or LibraryBridge()
        self.memory = HiveMemory(self.root)
        self.lm_assist = (lm_assist or "ollama").lower().strip()
        self.lane = (lane or "full").lower().strip()
        if self.lane not in {"fast", "full"}:
            self.lane = "full"
        # Fast lane defaults to no LM unless user forces ollama
        if self.lane == "fast" and self.lm_assist in {"ollama", "top"}:
            # keep if explicitly ollama — user may want LM on fast plan/execute only
            pass
        self.lm_fn: Callable[[str], str] | None = make_lm_fn(self.lm_assist)
        self.cfg = self._load_cfg()

    def _load_cfg(self) -> dict[str, Any]:
        path = self.root / "configs" / "buzzer_hive.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {
            "schema": "ai.worker.drone.buzzer_hive.v1",
            "max_buzzers_default": 6,
            "max_parallel_workers": 4,
            "fresh_after_each_task": True,
            "mode_default": "parallel",
        }

    def default_workers(self) -> int:
        """Cap workers honestly — lower if shared Ollama LM assist."""
        cfg_w = int(self.cfg.get("max_parallel_workers", 4))
        if self.lm_assist not in {"none", "off", "local", ""}:
            # shared GPU/backend — don't thrash
            return max(1, min(cfg_w, int(self.cfg.get("max_parallel_with_lm", 2))))
        return max(1, cfg_w)

    def status(self) -> dict[str, Any]:
        from .work_order import show_work_order

        return {
            "root": str(self.root),
            "swarm_system": "buzzer_hive",
            "sister_system": "fabric_swarm",
            "work_order": show_work_order(self.root),
            "hive": self.memory.stats(),
            "library": self.library.status(),
            "config": self.cfg,
            "default_workers": self.default_workers(),
            "lm_assist": self.lm_assist,
            "honesty": {
                "full_llm_per_buzzer": False,
                "fresh_agent_evolution": True,
                "parallel_swarm": True,
                "vector_db": "token_jaccard_jsonl",
                "two_systems": {
                    "buzzer_hive": "clean-slate buzzers (this module)",
                    "fabric_swarm": "multi-goal 24-drone fabric fan-out (drone.swarm)",
                },
            },
            "utc": _utc(),
        }

    def _goals_from_sources(
        self,
        goals: list[str] | None,
        count: int,
        pull_library: bool,
    ) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        if goals:
            for g in goals:
                g = (g or "").strip()
                if g:
                    planned.append({"goal": g, "source": "explicit"})
        for item in self.memory.dequeue_all():
            g = (item.get("goal") or "").strip()
            if g:
                planned.append({"goal": g, "source": f"queue:{item.get('id')}"})
        if pull_library:
            pack = self.library.pack_for_buzzer(goal=None)
            for t in (pack.get("open_tasks") or {}).get("tasks") or []:
                title = (t.get("title") or "").strip()
                nxt = (t.get("next_action") or "").strip()
                g = f"{title}: {nxt}".strip(": ").strip()
                if g:
                    planned.append({"goal": g, "source": f"library_task:{t.get('id')}"})
        if not planned and count > 0:
            pack = self.library.pack_for_buzzer(goal=None)
            g = (pack.get("goal") or "hive heartbeat: update memory and report status").strip()
            planned.append({"goal": g, "source": pack.get("goal_source", "heartbeat")})
        if count > 0 and len(planned) > count:
            planned = planned[:count]
        while 0 < count and len(planned) < count:
            planned.append(
                {
                    "goal": (
                        f"swarm unit {len(planned)+1}: "
                        "consolidate hive memory and library link"
                    ),
                    "source": "swarm_pad",
                }
            )
        return planned

    def _run_one_buzzer(
        self,
        *,
        index: int,
        item: dict[str, Any],
        controller: str,
        controller_name: str,
        domain: str,
        write_library_note: bool,
        post_live_write: bool,
        live_counter: list[int],
        live_lock: threading.Lock,
        peak_box: list[int],
    ) -> dict[str, Any]:
        """Worker body: spawn fresh buzzer, run, die. Tracks live concurrency."""
        with live_lock:
            live_counter[0] += 1
            if live_counter[0] > peak_box[0]:
                peak_box[0] = live_counter[0]
            self.memory.note_parallel(live_counter[0])
            live_now = live_counter[0]

        started = time.time()
        started_utc = _utc()
        try:
            buzzer = spawn_buzzer(
                self.root,
                generation=index,
                lm_fn=self.lm_fn,
                library=self.library,
                enable_tools=True,
            )
            # next unit task for fresh imprint after this buzzer dies (set by caller if known)
            next_task = item.get("next_task")
            report = buzzer.run(
                item["goal"],
                controller_kind=controller,
                controller_name=controller_name,
                domain=domain,
                skill_tags=[
                    "work_order",
                    "build",
                    "hive",
                    "buzzer",
                    "swarm",
                    "buzzer_hive",
                    item.get("source", "task")[:32],
                ],
                write_library_note=write_library_note,
                post_live_write=post_live_write,
                goal_source=str(item.get("source") or "explicit"),
                next_task=next_task,
                lane=str(item.get("lane") or getattr(self, "lane", "full") or "full"),
            )
            # death
            buzzer = None  # noqa: F841
        except Exception as e:
            report = {
                "status": "RED",
                "false_green": 0,
                "error": str(e),
                "buzzer_id": f"failed_{index}",
                "goal": item.get("goal"),
                "swarm_system": "buzzer_hive",
                "work_order": True,
                "fabric": {},
            }
        finally:
            with live_lock:
                live_counter[0] -= 1

        ended = time.time()
        return {
            "cycle": index,
            "slot_parallel_at_start": live_now,
            "buzzer_id": report.get("buzzer_id"),
            "status": report.get("status"),
            "goal": report.get("goal") or item.get("goal"),
            "goal_source": item.get("source"),
            "swarm_system": report.get("swarm_system") or "buzzer_hive",
            "work_order": report.get("work_order", True),
            "imprint_path": report.get("imprint_path"),
            "lifecycle": report.get("lifecycle"),
            "seal_path": report.get("seal_path"),
            "hive_doc_id": report.get("hive_doc_id"),
            "library_note_path": report.get("library_note_path"),
            "fabric_report": (report.get("fabric") or {}).get("report_path"),
            "smarter_delta": (report.get("fabric") or {}).get("smarter_delta"),
            "started_utc": started_utc,
            "ended_utc": _utc(),
            "duration_ms": round((ended - started) * 1000, 2),
            "error": report.get("error"),
        }

    def run_swarm(
        self,
        *,
        goals: list[str] | None = None,
        cycles: int = 6,
        workers: int | None = None,
        parallel: bool = True,
        controller: str = "local",
        controller_name: str = "hive",
        pull_library: bool = True,
        write_library_note: bool = True,
        post_live_write: bool = False,
        domain: str = "build",
        lane: str | None = None,
    ) -> dict[str, Any]:
        """
        PARALLEL swarm (default):
          - up to `workers` clean-slate buzzers concurrent
          - when one finishes → immediately spawn next fresh buzzer for remaining work
          - all write durable hive memory under locks
          - lane=fast → 5-node lite path; lane=full → 24-node seal
        """
        if lane:
            self.lane = (lane or "full").lower().strip()
            if self.lane not in {"fast", "full"}:
                self.lane = "full"
        # Fast lane: more parallel workers OK (no heavy dual-hemisphere by default)
        if self.lane == "fast" and workers is None:
            workers = int(self.cfg.get("max_parallel_workers") or 4)
        validate_controller(controller, controller_name)
        planned = self._goals_from_sources(goals, cycles, pull_library)
        for p in planned:
            p["lane"] = self.lane
        if not planned:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "no goals planned",
                "swarm_system": "buzzer_hive",
                "work_order": True,
                "hive": self.memory.stats(),
                "library": self.library.status(),
            }

        # Chain next_task so each dying buzzer imprints NEXT for the following unit
        for i, item in enumerate(planned):
            if i + 1 < len(planned):
                nxt = planned[i + 1]
                item["next_task"] = {
                    "task_id": f"buzzer_hive:next:{i+1}",
                    "goal": nxt.get("goal"),
                    "next_action": nxt.get("goal"),
                    "source": nxt.get("source") or "swarm",
                    "domain": domain,
                    "skill_tags": ["work_order", "hive", "buzzer", "buzzer_hive"],
                    "codex_required": False,
                }
            else:
                item["next_task"] = {
                    "task_id": "buzzer_hive:await",
                    "goal": "await next swarm unit from library or queue",
                    "next_action": "pull library open tasks or hive queue",
                    "source": "await",
                    "domain": "ops",
                    "skill_tags": ["work_order", "hive", "await"],
                    "codex_required": False,
                }

        n_workers = max(1, int(workers if workers is not None else self.default_workers()))
        if not parallel:
            n_workers = 1
        n_workers = min(n_workers, len(planned))

        self.memory.bump_wave()
        live_counter = [0]
        peak_box = [0]
        live_lock = threading.Lock()
        results: list[dict[str, Any]] = []
        t0 = time.perf_counter()

        if n_workers == 1:
            for i, item in enumerate(planned, start=1):
                results.append(
                    self._run_one_buzzer(
                        index=i,
                        item=item,
                        controller=controller,
                        controller_name=controller_name,
                        domain=domain,
                        write_library_note=write_library_note,
                        post_live_write=post_live_write and i == len(planned),
                        live_counter=live_counter,
                        live_lock=live_lock,
                        peak_box=peak_box,
                    )
                )
        else:
            # True swarm: keep worker pool saturated; each completion frees a slot
            # for the next fresh buzzer immediately.
            with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="buzzer") as pool:
                futures = {}
                next_i = 0

                def submit(idx: int, item: dict[str, Any], is_last: bool) -> None:
                    fut = pool.submit(
                        self._run_one_buzzer,
                        index=idx,
                        item=item,
                        controller=controller,
                        controller_name=controller_name,
                        domain=domain,
                        write_library_note=write_library_note,
                        post_live_write=post_live_write and is_last,
                        live_counter=live_counter,
                        live_lock=live_lock,
                        peak_box=peak_box,
                    )
                    futures[fut] = idx

                # prime pool
                while next_i < len(planned) and len(futures) < n_workers:
                    item = planned[next_i]
                    next_i += 1
                    submit(next_i, item, is_last=(next_i == len(planned) and post_live_write))

                completed_order: list[dict[str, Any]] = []
                while futures:
                    done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                    for fut in done:
                        futures.pop(fut, None)
                        try:
                            completed_order.append(fut.result())
                        except Exception as e:
                            completed_order.append(
                                {
                                    "status": "RED",
                                    "false_green": 0,
                                    "error": str(e),
                                    "cycle": -1,
                                }
                            )
                        # immediately spawn next fresh buzzer into free slot
                        if next_i < len(planned):
                            item = planned[next_i]
                            next_i += 1
                            submit(
                                next_i,
                                item,
                                is_last=(next_i == len(planned) and post_live_write),
                            )

                # stable order by cycle index
                completed_order.sort(key=lambda r: int(r.get("cycle") or 0))
                results = completed_order

        wall_ms = (time.perf_counter() - t0) * 1000
        sum_ms = sum(float(r.get("duration_ms") or 0) for r in results)
        all_green = all(r.get("status") == "GREEN" for r in results) and len(results) > 0
        # parallel proof: wall should be meaningfully under sum when workers>1 and n>1
        parallel_speedup = round(sum_ms / wall_ms, 3) if wall_ms > 0 else 0.0
        hive_stats = self.memory.stats()

        seal = {
            "schema": "ai.worker.drone.hive_swarm_seal.v1",
            "status": "GREEN" if all_green else ("PARTIAL" if results else "RED"),
            "false_green": 0,
            "utc": _utc(),
            "lane": getattr(self, "lane", "full"),
            "swarm_system": "buzzer_hive",
            "work_order": True,
            "mode": "parallel" if n_workers > 1 else "serial",
            "swarm": True,
            "workers": n_workers,
            "peak_parallel_observed": peak_box[0],
            "units": len(results),
            "duration_ms_wall": round(wall_ms, 2),
            "duration_ms_sum_buzzers": round(sum_ms, 2),
            "parallel_speedup_vs_serial_sum": parallel_speedup,
            "fresh_buzzer_each_unit": True,
            "immediate_respawn_on_slot_free": n_workers > 1,
            "imprinted_units": sum(1 for r in results if r.get("imprint_path")),
            "results": results,
            "hive": hive_stats,
            "library": self.library.status(),
            "honesty": {
                "swarm_system": "buzzer_hive",
                "sister_system": "fabric_swarm",
                "work_order_imprinted": True,
                "what_ran": (
                    f"{len(results)} clean-slate buzzers in parallel "
                    f"(workers={n_workers}, peak={peak_box[0]}) "
                    "× each imprinted WORK ORDER + L||R fabric + registry + recycle"
                ),
                "not_ran": "N independent full LLM loads or human brain simulation",
                "fresh_agent_evolution": (
                    "each unit discarded prior buzzer RAM; free slot immediately "
                    "spawns next fresh buzzer; hive docs + skills persist"
                ),
                "double_parallel": "buzzers fan-out || AND L||R chains inside each buzzer",
                "library_link": "status + open tasks pull + outbox notes",
                "vector_db": "token Jaccard JSONL — not dense embeddings",
                "parallel_proof": (
                    f"wall_ms={round(wall_ms,2)} sum_ms={round(sum_ms,2)} "
                    f"speedup={parallel_speedup}x peak_parallel={peak_box[0]}"
                ),
            },
        }
        out = self.root / "out" / "HIVE_SWARM_SEAL.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        seal["seal_path"] = str(out)
        return seal

    def enqueue(self, goal: str) -> dict[str, Any]:
        return self.memory.enqueue(goal)

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self.memory.retrieve(query, top_k=top_k)
