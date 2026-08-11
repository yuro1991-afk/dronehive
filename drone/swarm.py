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
    ) -> dict[str, Any]:
        """One fabric goal unit: imprint → codex? → L||R fabric → registry → recycle → next."""
        root = self.root
        wo_cfg = load_work_order_config(root)
        unit_id = f"fs_{uuid.uuid4().hex[:10]}"
        tags = list(skill_tags)
        if "work_order" not in tags:
            tags.insert(0, "work_order")
        if "fabric_swarm" not in tags:
            tags.append("fabric_swarm")

        task = build_task_slot(
            goal,
            source="fabric_swarm",
            domain=domain,
            skill_tags=tags,
            cfg=wo_cfg,
        )
        imprint = imprint_drone(
            root,
            buzzer_id=unit_id,
            generation=generation,
            task=task,
            controller_kind=controller_kind,
            controller_name=controller_name,
            swarm_system=self.SYSTEM,
        )

        scratch: dict[str, Any] = {
            "goal": goal,
            "unit_id": unit_id,
            "swarm_system": self.SYSTEM,
            "imprint_path": imprint.get("imprint_path"),
            "task_id": task.get("task_id"),
            "laws_bound": True,
            "received_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        codex_result: dict[str, Any] = {
            "attempted": False,
            "ok": False,
            "skipped": not bool(task.get("codex_required")),
        }
        if task.get("codex_required"):
            codex_result = query_codex(root, str(task.get("codex_query") or "stats"))
            scratch["codex"] = {
                "ok": codex_result.get("ok"),
                "query": codex_result.get("query"),
                "returncode": codex_result.get("returncode"),
            }

        t0 = time.perf_counter()
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
            },
            parallel_hemispheres=parallel_hemispheres,
        )
        ms = (time.perf_counter() - t0) * 1000

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
            "fabric": {
                "task_id": fabric_report.get("task_id"),
                "status": fabric_report.get("status"),
                "nodes_run": fabric_report.get("nodes_run"),
                "smarter_delta": fabric_report.get("smarter_delta"),
                "smart_after": fabric_report.get("smart_after"),
                "report_path": fabric_report.get("report_path"),
                "build_path": fabric_report.get("build_path"),
                "mode": fabric_report.get("mode"),
                "parallel_hemispheres": fabric_report.get("parallel_hemispheres"),
            },
            # bubble fabric fields for seal compatibility
            "task_id": fabric_report.get("task_id"),
            "nodes_run": fabric_report.get("nodes_run"),
            "smarter_delta": fabric_report.get("smarter_delta"),
            "report_path": fabric_report.get("report_path"),
            "build_path": fabric_report.get("build_path"),
            "seal_path": fabric_report.get("report_path"),
            "honesty": {
                "swarm_system": self.SYSTEM,
                "drones_are_full_models": False,
                "work_order_imprinted": True,
                "what_ran": "fabric multi-goal unit: imprint → L||R 24 drones → registry → recycle",
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
        seal = {
            "status": "GREEN" if ok else ("PARTIAL" if any(r.get("status") == "GREEN" for r in results) else "RED"),
            "false_green": 0,
            "mode": "swarm",
            "swarm_system": self.SYSTEM,
            "work_order": True,
            "swarm_id": swarm_id,
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
                }
                for r in results
            ],
            "errors": errors,
            "fabric_stats": after,
            "honesty": {
                "swarm_system": self.SYSTEM,
                "sister_system": "buzzer_hive",
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
        return seal
