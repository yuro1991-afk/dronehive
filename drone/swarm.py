"""
Fabric Swarm — multi-goal controllable drones (FABRIC SWARM path).

Modes:
  1) Parallel hemispheres: L-chain and R-chain run concurrently, then callosum
  2) Multi-goal swarm: N goals fan out with max_workers (machine-sized default 3)

Each goal unit is imprinted with the shared WORK ORDER (AI laws + task + codex),
then after execute: live registry → memory recycle → NEXT fresh imprint.

Sister system: drone/hive.py BuzzerHive (clean-slate buzzers).
Both share drone/work_order.py imprint law.

Honesty:
  - Controllable workers swarming in threads
  - Not 24 independent LLMs
  - Learning still on disk (thread-locked WorkMemory)
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .chain import BrainFabric
from .work_order import (
    build_task_slot,
    close_drone_lifecycle,
    imprint_drone,
    load_work_order_config,
    query_codex,
)


class DroneSwarm:
    """FABRIC SWARM — multi-goal fan-out + L||R hemispheres (not buzzer hive)."""

    SYSTEM = "fabric_swarm"

    def __init__(
        self,
        root: Path,
        lm_fn: Callable[[str], str] | None = None,
        max_workers: int = 3,
    ) -> None:
        self.root = Path(root)
        self.lm_fn = lm_fn
        # Machine fit: 12GB / Ollama parallel lane — default 3 concurrent fabric goals
        self.max_workers = max(1, min(int(max_workers), 8))
        # auto-wires code_lm (8b) when Ollama models present
        self.fabric = BrainFabric(self.root, lm_fn=lm_fn, enable_tools=True)

    def _run_one_imprinted(
        self,
        goal: str,
        *,
        controller_kind: str,
        controller_name: str,
        domain: str,
        skill_tags: list[str],
        parallel_hemispheres: bool,
        generation: int = 0,
        next_task: dict[str, Any] | None = None,
        board_wave_id: str | None = None,
        force_full: bool = False,
        force_fast: bool = False,
    ) -> dict[str, Any]:
        """One unit: packet recycle + lane pick (fast|full) + cold board → execute → deposit."""
        root = self.root
        wo_cfg = load_work_order_config(root)
        unit_id = f"fs_{uuid.uuid4().hex[:10]}"
        tags = list(skill_tags)
        if "work_order" not in tags:
            tags.insert(0, "work_order")
        if "fabric_swarm" not in tags:
            tags.append("fabric_swarm")

        # Route first: pack + recycle claim + fast/full (latency cut)
        from .packet_recycle import deposit_packet, prepare_unit_routing

        routing = prepare_unit_routing(
            root, goal, force_full=force_full, force_fast=force_fast
        )
        pack_id = str(routing.get("pack_id") or "default")
        lane_info = routing.get("lane") or {"lane": "full", "nodes": 24}
        lane = str(lane_info.get("lane") or "full")
        tool_pack = routing.get("tool_pack") or {}
        tool_scope = list(tool_pack.get("tools") or [])
        packet_hit = bool((routing.get("packet") or {}).get("hit"))

        task = build_task_slot(
            goal,
            source="fabric_swarm",
            domain=domain,
            skill_tags=tags,
            cfg=wo_cfg,
        )
        # Skip heavy knowledge rebuild when packet recycle has cold refs
        with_knowledge = not (
            packet_hit and (routing.get("cold") or {}).get("lesson_refs")
        )
        imprint = imprint_drone(
            root,
            buzzer_id=unit_id,
            generation=generation,
            task=task,
            controller_kind=controller_kind,
            controller_name=controller_name,
            swarm_system=self.SYSTEM,
            knowledge=(
                {
                    "ok": True,
                    "cold_mode": True,
                    "from_packet_recycle": True,
                    "packet_id": (routing.get("packet") or {}).get("packet_id"),
                    "school": {
                        "mode": "packet_recycle_refs",
                        "lessons": (routing.get("cold") or {}).get("lesson_refs") or [],
                        "lessons_imprinted": (routing.get("cold") or {}).get("lessons_n")
                        or 0,
                    },
                    "codex": {
                        "ok": (routing.get("cold") or {}).get("codex_ok"),
                        "query": (routing.get("cold") or {}).get("codex_query"),
                    },
                    "false_green": 0,
                }
                if not with_knowledge
                else None
            ),
            with_knowledge=with_knowledge,
        )
        # stamp tool pack from routing
        imprint["tool_pack"] = tool_pack
        imprint["lane"] = lane
        imprint["packet_recycle"] = routing.get("packet")

        # Board assignment (shared wave)
        assignment: dict[str, Any] = {}
        if board_wave_id:
            try:
                from .hive_board import HiveBoard

                board = HiveBoard(root)
                assignment = board.assign_unit(
                    board_wave_id, unit_id=unit_id, goal=goal
                )
                # Prefer recycle tool pack if present; else board assign
                if not tool_scope:
                    tool_scope = list(
                        (assignment.get("tool_pack") or {}).get("tools") or []
                    )
                # merge cold: packet first, else board
                if not (routing.get("cold") or {}).get("lesson_refs"):
                    routing["cold"] = assignment.get("cold") or {}
            except Exception as e:
                assignment = {"ok": False, "error": str(e)}
        if not tool_scope:
            tool_scope = list((imprint.get("tool_pack") or {}).get("tools") or [])

        cold_meta = routing.get("cold") or assignment.get("cold") or {}

        scratch: dict[str, Any] = {
            "goal": goal,
            "unit_id": unit_id,
            "swarm_system": self.SYSTEM,
            "imprint_path": imprint.get("imprint_path"),
            "task_id": task.get("task_id"),
            "laws_bound": True,
            "board_wave_id": board_wave_id,
            "tool_pack": tool_pack,
            "cold": cold_meta,
            "lane": lane,
            "lane_reason": lane_info.get("reason"),
            "packet_hit": packet_hit,
            "packet_id": (routing.get("packet") or {}).get("packet_id"),
            "received_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        codex_result: dict[str, Any] = {
            "attempted": False,
            "ok": False,
            "skipped": not bool(task.get("codex_required")),
        }
        if task.get("codex_required"):
            if cold_meta.get("codex_ok") or packet_hit:
                codex_result = {
                    "attempted": True,
                    "ok": True,
                    "mode": "packet_or_cold",
                    "query": cold_meta.get("codex_query"),
                    "skipped_cli": True,
                }
            else:
                codex_result = query_codex(root, str(task.get("codex_query") or "stats"))
            scratch["codex"] = {
                "ok": codex_result.get("ok"),
                "query": codex_result.get("query"),
                "returncode": codex_result.get("returncode"),
                "mode": codex_result.get("mode"),
            }

        t0 = time.perf_counter()
        if lane == "fast":
            from .fast_lane import FastLane

            tags_fast = tags + ["fast_lane", "packet_recycle"]
            fabric_report = FastLane(root, lm_fn=self.lm_fn).run(
                goal,
                controller_kind=controller_kind,
                controller_name=controller_name,
                domain=domain,
                skill_tags=tags_fast,
                tool_scope=tool_scope,
                board_wave_id=board_wave_id,
                unit_id=unit_id,
                payload={
                    "unit_id": unit_id,
                    "swarm_system": self.SYSTEM,
                    "work_order_task_id": task.get("task_id"),
                    "codex": codex_result,
                    "imprint_path": imprint.get("imprint_path"),
                    "tool_pack": tool_pack,
                    "cold_lesson_refs": cold_meta.get("lesson_refs") or [],
                    "cold_knowledge": True,
                    "packet_recycle": routing.get("packet"),
                    "lane": "fast",
                },
            )
            fabric_report["mode"] = "fast_lane"
            fabric_report["parallel_hemispheres"] = False
        else:
            fabric_report = self.fabric.run_task_swarm(
                goal=goal,
                controller_kind=controller_kind,
                controller_name=controller_name,
                domain=domain,
                skill_tags=tags,
                payload={
                    "unit_id": unit_id,
                    "swarm_system": self.SYSTEM,
                    "work_order_task_id": task.get("task_id"),
                    "codex": codex_result,
                    "imprint_path": imprint.get("imprint_path"),
                    "board_wave_id": board_wave_id,
                    "tool_scope": tool_scope,
                    "tool_pack": tool_pack,
                    "cold_lesson_refs": cold_meta.get("lesson_refs") or [],
                    "cold_knowledge": True,
                    "packet_recycle": routing.get("packet"),
                    "lane": "full",
                },
                parallel_hemispheres=parallel_hemispheres,
            )
        # Live progress chunk
        if board_wave_id:
            try:
                from .hive_board import HiveBoard

                HiveBoard(root).publish(
                    board_wave_id,
                    kind="unit_done",
                    payload={
                        "unit_id": unit_id,
                        "goal": (goal or "")[:200],
                        "status": fabric_report.get("status"),
                        "nodes_run": fabric_report.get("nodes_run"),
                        "duration_ms": fabric_report.get("duration_ms"),
                        "tools_scoped": len(tool_scope or []),
                        "lane": lane,
                        "packet_hit": packet_hit,
                    },
                    unit_id=unit_id,
                    tags=["done", "live", lane],
                )
            except Exception:
                pass
        ms = (time.perf_counter() - t0) * 1000

        # Deposit packet for next unit (cut next run)
        deposit = deposit_packet(
            root,
            goal=goal,
            pack_id=pack_id,
            tool_pack=tool_pack,
            cold=cold_meta,
            lane=lane,
            status=str(fabric_report.get("status") or "RED"),
            unit_id=unit_id,
            duration_ms=ms,
            nodes_run=int(fabric_report.get("nodes_run") or 0),
            board_wave_id=board_wave_id or "",
            evidence=[
                str(fabric_report.get("report_path") or ""),
                str(fabric_report.get("seal_path") or ""),
                str(imprint.get("imprint_path") or ""),
            ],
            extra={"lane_reason": lane_info.get("reason"), "packet_hit_in": packet_hit},
        )

        report: dict[str, Any] = {
            "status": fabric_report.get("status", "RED"),
            "false_green": 0,
            "work_order": True,
            "swarm_system": self.SYSTEM,
            "buzzer_id": unit_id,
            "unit_id": unit_id,
            "generation": generation,
            "goal": goal,
            "goal_source": "fabric_swarm",
            "task": task,
            "imprint_path": imprint.get("imprint_path"),
            "duration_ms": round(ms, 2),
            "codex": codex_result,
            "lane": lane,
            "lane_reason": lane_info.get("reason"),
            "packet_recycle": {
                "hit": packet_hit,
                "packet_id": (routing.get("packet") or {}).get("packet_id"),
                "hit_mode": (routing.get("packet") or {}).get("hit_mode"),
                "deposit": deposit,
            },
            "fabric": {
                "task_id": fabric_report.get("task_id"),
                "status": fabric_report.get("status"),
                "nodes_run": fabric_report.get("nodes_run"),
                "smarter_delta": fabric_report.get("smarter_delta"),
                "smart_after": fabric_report.get("smart_after"),
                "report_path": fabric_report.get("report_path"),
                "build_path": fabric_report.get("build_path"),
                "mode": fabric_report.get("mode") or lane,
                "parallel_hemispheres": fabric_report.get("parallel_hemispheres"),
                "lane": lane,
            },
            # bubble fabric fields for seal compatibility
            "task_id": fabric_report.get("task_id"),
            "nodes_run": fabric_report.get("nodes_run"),
            "smarter_delta": fabric_report.get("smarter_delta"),
            "report_path": fabric_report.get("report_path"),
            "build_path": fabric_report.get("build_path"),
            "seal_path": fabric_report.get("report_path")
            or fabric_report.get("seal_path"),
            "tool_pack": tool_pack,
            "board_wave_id": board_wave_id,
            "assignment_chunk_id": assignment.get("assignment_chunk_id"),
            "cold_lessons_n": cold_meta.get("lessons_n")
            or len(cold_meta.get("lesson_refs") or []),
            "honesty": {
                "swarm_system": self.SYSTEM,
                "drones_are_full_models": False,
                "work_order_imprinted": True,
                "cold_knowledge": True,
                "tools_scoped": True,
                "shared_hive_board": bool(board_wave_id),
                "packet_recycle": True,
                "fast_lane": lane == "fast",
                "what_ran": (
                    f"{'fast 5-node' if lane == 'fast' else 'full 24-node'} · "
                    f"pack={pack_id} · packet_hit={packet_hit} · deposit={deposit.get('packet_id')}"
                ),
            },
        }

        lifecycle = close_drone_lifecycle(
            root,
            buzzer_id=unit_id,
            generation=generation,
            imprint=imprint,
            scratch=scratch,
            result=report,
            next_task=next_task,
            model=controller_kind,
            swarm_system=self.SYSTEM,
        )
        report["lifecycle"] = {
            "live_registry_ok": bool((lifecycle.get("live_registry") or {}).get("ok")),
            "recycle_dir": (lifecycle.get("recycle") or {}).get("dump_dir"),
            "next_path": (lifecycle.get("next_imprint") or {}).get("path"),
            "false_green": 0,
        }
        report["lifecycle_detail"] = lifecycle
        return report

    def run_parallel_hemispheres(
        self,
        goal: str,
        controller_kind: str = "local",
        controller_name: str = "swarm",
        domain: str = "build",
        skill_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """One goal: imprint + L and R chains concurrent, then callosum merge + learn."""
        return self._run_one_imprinted(
            goal,
            controller_kind=controller_kind,
            controller_name=controller_name,
            domain=domain,
            skill_tags=skill_tags or ["build", "swarm", "fabric_swarm"],
            parallel_hemispheres=True,
        )

    def run_multi(
        self,
        goals: list[str],
        controller_kind: str = "local",
        controller_name: str = "swarm",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        parallel_hemispheres: bool = True,
    ) -> dict[str, Any]:
        """Many goals at once — true fabric swarm fan-out with work-order imprint per unit."""
        goals = [g.strip() for g in goals if (g or "").strip()]
        if not goals:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "no goals",
                "swarm_system": self.SYSTEM,
            }

        swarm_id = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        before = self.fabric.stats()
        tags = skill_tags or ["build", "swarm", "fabric_swarm"]

        # Open shared cold board for the wave — one knowledge pack per goal class
        board_wave_id = f"fs_{swarm_id}"
        board_meta: dict[str, Any] = {}
        try:
            from .hive_board import HiveBoard

            board_meta = HiveBoard(self.root).open_wave(
                goals,
                wave_id=board_wave_id,
                meta={
                    "swarm_system": self.SYSTEM,
                    "controller": controller_name,
                    "max_workers": self.max_workers,
                },
            )
        except Exception as e:
            board_meta = {"ok": False, "error": str(e), "wave_id": board_wave_id}

        results: list[dict[str, Any]] = []
        errors: list[str] = []

        def _one(idx_goal: tuple[int, str]) -> dict[str, Any]:
            idx, g = idx_goal
            # next unit's goal when known (for NEXT imprint)
            nxt = None
            if idx + 1 < len(goals):
                nxt = {
                    "task_id": f"fabric_swarm:next:{idx+1}",
                    "goal": goals[idx + 1],
                    "next_action": goals[idx + 1],
                    "source": "fabric_swarm",
                    "domain": domain,
                    "skill_tags": tags,
                    "codex_required": False,
                }
            return self._run_one_imprinted(
                g,
                controller_kind=controller_kind,
                controller_name=controller_name,
                domain=domain,
                skill_tags=tags,
                parallel_hemispheres=parallel_hemispheres,
                generation=idx,
                next_task=nxt,
                board_wave_id=board_wave_id,
            )

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futs = {pool.submit(_one, (i, g)): g for i, g in enumerate(goals)}
            for fut in as_completed(futs):
                g = futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    errors.append(f"{g}: {e}")
                    results.append(
                        {
                            "status": "RED",
                            "goal": g,
                            "error": str(e),
                            "false_green": 0,
                            "swarm_system": self.SYSTEM,
                            "work_order": True,
                        }
                    )

        after = self.fabric.stats()
        ms = (time.perf_counter() - t0) * 1000
        ok = all(r.get("status") == "GREEN" for r in results) and not errors
        board_seal: dict[str, Any] = {}
        try:
            from .hive_board import HiveBoard

            board_seal = HiveBoard(self.root).seal_wave(
                board_wave_id,
                extra={
                    "status": "GREEN" if ok else "PARTIAL",
                    "goals_n": len(goals),
                    "duration_ms": round(ms, 2),
                },
            )
        except Exception as e:
            board_seal = {"ok": False, "error": str(e)}
        seal = {
            "status": "GREEN" if ok else ("PARTIAL" if any(r.get("status") == "GREEN" for r in results) else "RED"),
            "false_green": 0,
            "mode": "swarm",
            "swarm_system": self.SYSTEM,
            "work_order": True,
            "swarm_id": swarm_id,
            "board_wave_id": board_wave_id,
            "board": board_meta,
            "board_seal": board_seal,
            "goals_n": len(goals),
            "max_workers": self.max_workers,
            "parallel_hemispheres": parallel_hemispheres,
            "drones_per_goal": 24,
            "drones_are_full_models": False,
            "duration_ms": round(ms, 2),
            "smart_before": before.get("smart_index"),
            "smart_after": after.get("smart_index"),
            "smarter_delta": round(
                float(after.get("smart_index", 0)) - float(before.get("smart_index", 0)),
                3,
            ),
            "results": [
                {
                    "goal": r.get("goal"),
                    "status": r.get("status"),
                    "task_id": r.get("task_id"),
                    "unit_id": r.get("unit_id") or r.get("buzzer_id"),
                    "imprint_path": r.get("imprint_path"),
                    "smarter_delta": r.get("smarter_delta"),
                    "nodes_run": r.get("nodes_run"),
                    "duration_ms": r.get("duration_ms"),
                    "report_path": r.get("report_path"),
                    "lifecycle": r.get("lifecycle"),
                    "work_order": r.get("work_order"),
                    "codex_attempted": bool((r.get("codex") or {}).get("attempted")),
                    "tool_pack": r.get("tool_pack"),
                    "cold_lessons_n": r.get("cold_lessons_n"),
                    "board_wave_id": r.get("board_wave_id"),
                    "lane": r.get("lane"),
                    "lane_reason": r.get("lane_reason"),
                    "packet_recycle": r.get("packet_recycle"),
                }
                for r in results
            ],
            "errors": errors,
            "fabric_stats": after,
            "lanes": {
                "fast": sum(1 for r in results if r.get("lane") == "fast"),
                "full": sum(1 for r in results if r.get("lane") == "full"),
            },
            "packet_hits": sum(
                1
                for r in results
                if (r.get("packet_recycle") or {}).get("hit")
            ),
            "honesty": {
                "swarm_system": self.SYSTEM,
                "sister_system": "buzzer_hive",
                "cold_knowledge": True,
                "scoped_tools_per_goal": True,
                "live_board_chunks": True,
                "packet_recycle": True,
                "fast_lane_auto": True,
                "what_ran": (
                    f"fabric_swarm fan-out max_workers={self.max_workers}; "
                    f"each goal imprinted work order + 24 drones; L||R={parallel_hemispheres}; "
                    f"registry+recycle per unit"
                ),
                "not_ran": "N full independent LLMs; not buzzer_hive clean-slate pool",
            },
        }
        out = self.root / "out" / f"swarm_{swarm_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        seal["report_path"] = str(out)

        # Silent observer: informed of full swarm (never user chat)
        try:
            from .observer import inform

            obs = inform(
                self.root,
                event="fabric_swarm_complete",
                drone_report={
                    "task_id": swarm_id,
                    "status": seal.get("status"),
                    "goal": f"swarm x{len(goals)}",
                    "mode": "swarm",
                    "nodes_run": sum(int(r.get("nodes_run") or 0) for r in results),
                    "duration_ms": seal.get("duration_ms"),
                    "report_path": seal.get("report_path"),
                    "fabric_stats": after,
                },
                extra={"goals": goals[:12], "results_n": len(results)},
            )
            seal["observer"] = {
                "status": obs.get("status"),
                "model": obs.get("model"),
                "user_facing": False,
                "informed": obs.get("informed"),
                "path": str(self.root / "data" / "observer" / "LATEST.json"),
            }
        except Exception as e:
            seal["observer"] = {"status": "RED", "error": str(e), "user_facing": False}

        # Third LLM: core critic after swarm
        try:
            from .core_critic import critique_once

            crit = critique_once(
                self.root,
                event="fabric_swarm_complete",
                extra={"swarm_id": swarm_id, "goals_n": len(goals)},
            )
            cobj = crit.get("critique") if isinstance(crit.get("critique"), dict) else {}
            seal["core_critic"] = {
                "status": crit.get("status"),
                "model": crit.get("model"),
                "user_facing": False,
                "engine_facing": True,
                "engine_health": cobj.get("engine_health"),
                "verdict": cobj.get("verdict"),
                "path": str(self.root / "data" / "core_critic" / "LATEST.json"),
            }
        except Exception as e:
            seal["core_critic"] = {
                "status": "RED",
                "error": str(e),
                "user_facing": False,
                "engine_facing": True,
            }

        out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        return seal
