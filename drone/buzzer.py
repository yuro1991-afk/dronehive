"""
Buzzer — one ephemeral worker bee (BUZZER HIVE path).

Lifecycle (law):
  1. Spawn CLEAN SLATE + imprint WORK ORDER (laws + task + codex how-to)
  2. Receive task + library pack + hive retrieval
  3. Codex query when task requires model/stack knowledge
  4. Run fabric SWARM mode: L || R hemispheres + callosum + learn
  5. Update hive durable memory (+ optional library note)
  6. Write live registry · dump memory recycle · write NEXT fresh imprint
  7. Die (object discarded; only disk remains)

Sister system: drone/swarm.py DroneSwarm (FABRIC multi-goal fan-out).
Both share the same work order imprint law.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .chain import BrainFabric
from .hive_memory import HiveMemory
from .library_bridge import LibraryBridge
from .work_order import (
    build_task_slot,
    close_drone_lifecycle,
    imprint_drone,
    load_work_order_config,
    query_codex,
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Buzzer:
    """Ephemeral clean-slate worker. Do not reuse after run()."""

    hive_memory: HiveMemory
    fabric: BrainFabric
    library: LibraryBridge
    root: Path
    buzzer_id: str = field(default_factory=lambda: f"bz_{uuid.uuid4().hex[:10]}")
    generation: int = 0
    _used: bool = False
    _scratch: dict[str, Any] = field(default_factory=dict)
    _imprint: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        goal: str,
        *,
        controller_kind: str = "local",
        controller_name: str = "hive",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        write_library_note: bool = True,
        post_live_write: bool = False,
        goal_source: str = "explicit",
        next_task: dict[str, Any] | None = None,
        codex_required: bool | None = None,
        codex_query: str = "",
        lane: str = "full",
    ) -> dict[str, Any]:
        if self._used:
            raise RuntimeError(
                f"buzzer {self.buzzer_id} already used — spawn a fresh buzzer (clean slate)"
            )
        self._used = True
        t0 = time.perf_counter()
        root = Path(self.root)
        wo_cfg = load_work_order_config(root)
        lane_mode = (lane or "full").lower().strip()
        if lane_mode not in {"fast", "full"}:
            lane_mode = "full"

        lib_pack = self.library.pack_for_buzzer(goal=goal)
        effective_goal = (goal or lib_pack.get("goal") or "").strip()
        source = goal_source or lib_pack.get("goal_source") or "explicit"

        if not effective_goal:
            task = build_task_slot("", source=str(source), domain=domain, skill_tags=skill_tags, cfg=wo_cfg)
            self._imprint = imprint_drone(
                root,
                buzzer_id=self.buzzer_id,
                generation=self.generation,
                task=task,
                controller_kind=controller_kind,
                controller_name=controller_name,
                swarm_system="buzzer_hive",
            )
            report = {
                "status": "RED",
                "false_green": 0,
                "buzzer_id": self.buzzer_id,
                "generation": self.generation,
                "error": "no goal from caller or library open tasks",
                "library_pack": lib_pack,
                "clean_slate": True,
                "work_order": True,
                "swarm_system": "buzzer_hive",
                "imprint_path": self._imprint.get("imprint_path"),
                "utc": _utc(),
            }
            seal = self.hive_memory.save_buzzer_seal(self.buzzer_id, report)
            report["seal_path"] = str(seal)
            report["lifecycle"] = close_drone_lifecycle(
                root,
                buzzer_id=self.buzzer_id,
                generation=self.generation,
                imprint=self._imprint,
                scratch=self._scratch,
                result=report,
                next_task=next_task,
                model=controller_kind,
                swarm_system="buzzer_hive",
            )
            self._scratch.clear()
            return report

        tags = list(skill_tags or ["build", "hive", "buzzer", "swarm"])
        if "work_order" not in tags:
            tags.insert(0, "work_order")

        task = build_task_slot(
            effective_goal,
            source=str(source),
            domain=domain,
            skill_tags=tags,
            codex_required=codex_required,
            codex_query=codex_query,
            cfg=wo_cfg,
        )
        self._imprint = imprint_drone(
            root,
            buzzer_id=self.buzzer_id,
            generation=self.generation,
            task=task,
            controller_kind=controller_kind,
            controller_name=controller_name,
            swarm_system="buzzer_hive",
        )
        # Clean Slate Agents upgrade: full passport only on full lane
        if lane_mode == "fast":
            self._scratch_upgrade = {
                "upgrade": "clean_slate_lite",
                "lane": "fast",
                "passport_path": None,
            }
            if isinstance(self._imprint, dict):
                self._imprint["lane"] = "fast"
                self._imprint["clean_slate"] = "lite"
        else:
            try:
                from .clean_slate import (
                    UPGRADE_ID,
                    build_passport,
                    enhance_imprint_with_upgrade,
                )

                passport = build_passport(
                    root,
                    agent_id=self.buzzer_id,
                    generation=self.generation,
                    parent_id=None,
                    swarm_system="buzzer_hive",
                    task=task,
                )
                self._imprint = enhance_imprint_with_upgrade(self._imprint, passport)
                self._scratch_upgrade = {
                    "upgrade": UPGRADE_ID,
                    "passport_path": passport.get("passport_path"),
                }
            except Exception as e:
                self._scratch_upgrade = {"upgrade_error": str(e)}

        prior_docs = self.hive_memory.retrieve(effective_goal, top_k=5)
        self._scratch = {
            "goal": effective_goal,
            "lib_goal_source": lib_pack.get("goal_source"),
            "prior_docs": prior_docs,
            "received_utc": _utc(),
            "imprint_path": self._imprint.get("imprint_path"),
            "task_id": task.get("task_id"),
            "swarm_system": "buzzer_hive",
            "laws_bound": True,
            "clean_slate": True,
            **(getattr(self, "_scratch_upgrade", {}) or {}),
        }

        # Latency: prefer knowledge already imprinted (in-process codex + school).
        # Avoid second subprocess codex when imprint.knowledge.codex is ok.
        kpack = (self._imprint or {}).get("knowledge") or {}
        k_codex = (kpack.get("codex") or {}) if isinstance(kpack, dict) else {}
        codex_result: dict[str, Any] = {
            "attempted": False,
            "ok": False,
            "skipped": not bool(task.get("codex_required")),
            "mode": "none",
        }
        if task.get("codex_required"):
            if k_codex.get("ok"):
                codex_result = {
                    "attempted": True,
                    "ok": True,
                    "mode": "imprint_pack",
                    "query": k_codex.get("query") or task.get("codex_query"),
                    "ms": k_codex.get("ms"),
                    "data": k_codex.get("data"),
                    "skipped_cli": True,
                }
            else:
                codex_result = query_codex(
                    root,
                    str(task.get("codex_query") or "stats"),
                    mode="fast",
                )
            self._scratch["codex"] = {
                "ok": codex_result.get("ok"),
                "query": codex_result.get("query"),
                "mode": codex_result.get("mode"),
                "returncode": codex_result.get("returncode"),
            }
            self._scratch["knowledge_ms"] = (kpack or {}).get("ms")
            self._scratch["knowledge_lessons"] = (
                (kpack.get("school") or {}).get("lessons_imprinted")
                if isinstance(kpack, dict)
                else 0
            )

        # Slim knowledge text for tools/LM (capped) — imprint already has full pack
        knowledge_text = ""
        try:
            from .knowledge_imprint import knowledge_prompt_block

            knowledge_text = knowledge_prompt_block(kpack if isinstance(kpack, dict) else {})
        except Exception:
            knowledge_text = ""

        if lane_mode == "fast":
            fabric_report = self.fabric.run_task_fast(
                goal=effective_goal,
                controller_kind=controller_kind,
                controller_name=controller_name,
                domain=domain,
                skill_tags=tags + ["fast_lane"],
                payload={
                    "buzzer_id": self.buzzer_id,
                    "generation": self.generation,
                    "lane": "fast",
                    "swarm_system": "buzzer_hive",
                    "knowledge_imprint": True,
                    "cold_knowledge": True,
                    "tool_scope": (self._imprint or {}).get("tool_pack", {}).get("tools")
                    if isinstance(self._imprint, dict)
                    else None,
                    "knowledge_ms": (kpack or {}).get("ms") if isinstance(kpack, dict) else None,
                    "knowledge_lessons": (kpack.get("school") or {}).get("lessons_imprinted")
                    if isinstance(kpack, dict)
                    else 0,
                    "knowledge_text": knowledge_text[:800] if knowledge_text else "",
                },
            )
            # Fast lane: skip heavy clean-slate passport path extras already done;
            # still record hive doc + lite lifecycle below.
        else:
            fabric_report = self.fabric.run_task_swarm(
                goal=effective_goal,
                controller_kind=controller_kind,
                controller_name=controller_name,
                domain=domain,
                skill_tags=tags,
                payload={
                    "buzzer_id": self.buzzer_id,
                    "generation": self.generation,
                    "prior_hive_docs": prior_docs,
                    "library_goal_source": lib_pack.get("goal_source"),
                    "work_order_task_id": task.get("task_id"),
                    "codex": codex_result,
                    "imprint_path": self._imprint.get("imprint_path"),
                    "swarm_system": "buzzer_hive",
                    "lane": "full",
                    "knowledge_imprint": True,
                    "cold_knowledge": True,
                    "tool_scope": (self._imprint or {}).get("tool_pack", {}).get("tools")
                    if isinstance(self._imprint, dict)
                    else None,
                    "tool_pack": (self._imprint or {}).get("tool_pack")
                    if isinstance(self._imprint, dict)
                    else None,
                    "knowledge_ms": (kpack or {}).get("ms") if isinstance(kpack, dict) else None,
                    "knowledge_lessons": (kpack.get("school") or {}).get("lessons_imprinted")
                    if isinstance(kpack, dict)
                    else 0,
                    # cold: short ref block only — not full lesson bodies
                    "knowledge_text": (knowledge_text or "")[:800],
                },
                parallel_hemispheres=True,
            )

        text = (
            f"buzzer={self.buzzer_id} gen={self.generation} system=buzzer_hive "
            f"status={fabric_report.get('status')} "
            f"smart_delta={fabric_report.get('smarter_delta')} "
            f"report={fabric_report.get('report_path')} "
            f"task_id={task.get('task_id')} codex={codex_result.get('ok')}"
        )
        doc = self.hive_memory.write_doc(
            buzzer_id=self.buzzer_id,
            goal=effective_goal,
            text=text,
            tags=tags,
            evidence=[
                fabric_report.get("report_path", ""),
                fabric_report.get("build_path", ""),
                self._imprint.get("imprint_path", ""),
            ],
            task_id=str(fabric_report.get("task_id", "") or task.get("task_id", "")),
            status=str(fabric_report.get("status", "PARTIAL")),
        )

        lib_note_path = None
        post_live = {"attempted": False, "ok": False}
        if write_library_note:
            lib_note_path = str(
                self.library.write_hive_note(
                    self.hive_memory.outbox,
                    {
                        "buzzer_id": self.buzzer_id,
                        "generation": self.generation,
                        "swarm_system": "buzzer_hive",
                        "goal": effective_goal,
                        "fabric_status": fabric_report.get("status"),
                        "task_id": fabric_report.get("task_id") or task.get("task_id"),
                        "work_order_task_id": task.get("task_id"),
                        "smart_delta": fabric_report.get("smarter_delta"),
                        "doc_id": doc.get("id"),
                        "report_path": fabric_report.get("report_path"),
                        "imprint_path": self._imprint.get("imprint_path"),
                        "codex_used": bool(codex_result.get("attempted")),
                    },
                )
            )
        if post_live_write:
            post_live = self.library.try_post_live_write()

        self.hive_memory.bump_completed()
        ms = (time.perf_counter() - t0) * 1000

        report = {
            "status": fabric_report.get("status", "RED"),
            "false_green": 0,
            "buzzer_id": self.buzzer_id,
            "generation": self.generation,
            "lane": lane_mode,
            "clean_slate": True if lane_mode != "fast" else "lite",
            "work_order": True,
            "swarm_system": "buzzer_hive",
            "goal": effective_goal,
            "goal_source": source,
            "task": task,
            "imprint_path": self._imprint.get("imprint_path"),
            "duration_ms": round(ms, 2),
            "prior_hive_hits": len(prior_docs),
            "hive_doc_id": doc.get("id"),
            "library_note_path": lib_note_path,
            "post_live_write": post_live,
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
            "library_connected": bool(
                lib_pack.get("library_status", {}).get("library_exists")
            ),
            "honesty": {
                "buzzer_is_full_llm": False,
                "swarm_system": "buzzer_hive",
                "clean_slate_means": "this buzzer has no prior buzzer RAM; hive+library+registry+recycle persist",
                "fabric_mode": fabric_report.get("mode"),
                "parallel_hemispheres": fabric_report.get("parallel_hemispheres"),
                "vector_db": "token_jaccard_jsonl not dense FAISS",
                "drones_are_full_models": False,
                "work_order_imprinted": True,
            },
            "utc": _utc(),
        }
        seal = self.hive_memory.save_buzzer_seal(self.buzzer_id, report)
        report["seal_path"] = str(seal)

        lifecycle = close_drone_lifecycle(
            root,
            buzzer_id=self.buzzer_id,
            generation=self.generation,
            imprint=self._imprint,
            scratch=self._scratch,
            result=report,
            next_task=next_task,
            model=controller_kind,
            swarm_system="buzzer_hive",
        )
        report["lifecycle"] = {
            "live_registry_ok": bool((lifecycle.get("live_registry") or {}).get("ok")),
            "recycle_dir": (lifecycle.get("recycle") or {}).get("dump_dir"),
            "next_path": (lifecycle.get("next_imprint") or {}).get("path"),
            "false_green": 0,
        }
        report["lifecycle_detail"] = lifecycle

        # Two-way AI bus: push result to all host AI surfaces (write path)
        try:
            from .ai_bus import AIBus

            bus = AIBus(root)
            bus_out = bus.sync_write(
                {
                    **report,
                    "knowledge": (self._imprint or {}).get("knowledge"),
                    "evidence": [
                        report.get("seal_path"),
                        report.get("imprint_path"),
                        report.get("library_note_path"),
                        (report.get("fabric") or {}).get("report_path"),
                    ],
                },
                unit_id=self.buzzer_id,
                goal=effective_goal,
            )
            report["ai_bus"] = {
                "ok": bus_out.get("ok"),
                "ok_count": bus_out.get("ok_count"),
                "channel_count": bus_out.get("channel_count"),
                "path": bus_out.get("path"),
                "false_green": 0,
            }
        except Exception as e:
            report["ai_bus"] = {"ok": False, "error": str(e), "false_green": 0}

        # Re-seal after lifecycle + ai_bus so disk seal includes two-way proof
        try:
            seal = self.hive_memory.save_buzzer_seal(self.buzzer_id, report)
            report["seal_path"] = str(seal)
        except Exception as e:
            report["reseal_error"] = str(e)

        # Clean Slate upgrade: full wipe audit only on full lane
        passport_path = (getattr(self, "_scratch_upgrade", {}) or {}).get("passport_path")
        if not passport_path:
            passport_path = (self._imprint.get("clean_slate_upgrade") or {}).get(
                "passport_path"
            )
        if lane_mode == "fast":
            report["clean_slate_upgrade"] = {
                "mode": "lite",
                "note": "fast lane skips full passport wipe audit; use --lane full for production",
                "false_green": 0,
            }
            report["honesty"] = {
                **(report.get("honesty") or {}),
                "lane": "fast",
                "not_full_clean_slate": True,
            }
        else:
            try:
                from .clean_slate import (
                    UPGRADE_ID,
                    append_generation_ledger,
                    hard_wipe_audit,
                )

                self._scratch.clear()
                audit = hard_wipe_audit(
                    root,
                    agent_id=self.buzzer_id,
                    recycle_dir=(lifecycle.get("recycle") or {}).get("dump_dir"),
                    scratch_cleared=True,
                )
                ledger = append_generation_ledger(
                    root,
                    agent_id=self.buzzer_id,
                    generation=self.generation,
                    parent_id=None,
                    status=str(report.get("status")),
                    recycle_dir=str(
                        (lifecycle.get("recycle") or {}).get("dump_dir") or ""
                    ),
                    next_path=str(
                        (lifecycle.get("next_imprint") or {}).get("path") or ""
                    ),
                )
                report["clean_slate_upgrade"] = {
                    "upgrade": UPGRADE_ID,
                    "wipe_audit": audit,
                    "generation_ledger": str(ledger),
                    "passport_path": passport_path,
                }
                report["honesty"] = {
                    **(report.get("honesty") or {}),
                    "upgrade": UPGRADE_ID,
                    "wipe_audit_ok": audit.get("ok"),
                    "lane": "full",
                }
            except Exception as e:
                report["clean_slate_upgrade"] = {"error": str(e), "false_green": 0}

        self._scratch.clear()
        self._scratch = {}
        self._imprint = {}
        return report


def spawn_buzzer(
    root: Path,
    *,
    generation: int,
    lm_fn: Callable[[str], str] | None = None,
    library: LibraryBridge | None = None,
    enable_tools: bool = True,
) -> Buzzer:
    """Always returns a NEW clean-slate buzzer. Never reuse IDs."""
    root = Path(root)
    hive_mem = HiveMemory(root)
    gen_n = hive_mem.bump_spawned()
    fabric = BrainFabric(root, lm_fn=lm_fn, enable_tools=enable_tools)
    lib = library or LibraryBridge()
    return Buzzer(
        hive_memory=hive_mem,
        fabric=fabric,
        library=lib,
        root=root,
        generation=generation if generation else gen_n,
    )
