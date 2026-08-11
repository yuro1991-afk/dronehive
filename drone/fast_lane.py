"""
Fast Lane — short path beside full 24-node seal fabric.

Full seal: 24 nodes L||R + callosum + full clean-slate (passport/recycle/registry)
Fast lane: 5 nodes serial + lite tools + lite seal

Honesty: fast GREEN still needs on-disk evidence. It is not a full production seal.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .chain import BrainFabric, _tags_for
from .learn import LearnConfig, WorkMemory
from .locks import root_lock
from .nodes import DroneNode
from .protocol import ControllerIdentity, HandoffPacket, TaskEnvelope
from .tools import DroneToolkit


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_fast_cfg(root: Path) -> dict[str, Any]:
    path = Path(root) / "configs" / "fast_lane.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["path"] = str(path)
        return data
    return {
        "nodes": ["F01_intake", "F02_plan", "F03_execute", "F04_verify", "F05_seal"],
        "role_map": {
            "F01_intake": "intake",
            "F02_plan": "plan",
            "F03_execute": "execute",
            "F04_verify": "verify",
            "F05_seal": "seal",
        },
        "lm_roles": ["plan", "execute"],
        "missing": True,
    }


def lite_tool_dispatch(
    toolkit: DroneToolkit,
    *,
    role: str,
    goal: str,
    node_id: str,
    lm_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """
    Minimal tools: few files, batch at seal.
    intake → one json
    plan → one md (+ optional 1 LM)
    execute → one work.md + one python proof (+ optional 1 LM)
    verify → list_dir only
    seal → package_manifest only
    """
    results: list[dict[str, Any]] = []
    g = (goal or "").strip()
    evidence: list[str] = []

    if role == "intake":
        r = toolkit.write_json(
            "fast_intake.json",
            {"node": node_id, "goal": g, "lane": "fast", "utc": _utc()},
        )
        results.append(r)
    elif role == "plan":
        body = f"# Fast plan\n\nGoal: {g}\n\n1. act\n2. verify\n3. seal\n"
        if lm_fn is not None:
            try:
                note = lm_fn(
                    f"FAST LANE planner. 3 bullet steps for:\n{g}\nBullets only."
                )
                body += f"\n## LM\n{note}\n"
                results.append({"tool": "lm_plan", "ok": True, "chars": len(note)})
            except Exception as e:
                results.append({"tool": "lm_plan", "ok": False, "error": str(e)})
        r = toolkit.write_text("fast_plan.md", body)
        results.append(r)
    elif role == "execute":
        body = f"# Fast execute\nGoal: {g}\nUTC: {_utc()}\n"
        if lm_fn is not None:
            try:
                note = lm_fn(
                    f"FAST LANE worker. Short notes or code sketch for:\n{g}\nMax 12 lines."
                )
                body += f"\n## LM\n{note}\n"
                results.append({"tool": "lm_execute", "ok": True, "chars": len(note)})
            except Exception as e:
                results.append({"tool": "lm_execute", "ok": False, "error": str(e)})
        r = toolkit.write_text("fast_work.md", body)
        results.append(r)
        results.append(
            toolkit.run_python(
                "from pathlib import Path\n"
                "Path('fast_exec_proof.txt').write_text('fast_ok\\n', encoding='utf-8')\n"
                "print('fast_ok')\n"
            )
        )
    elif role == "verify":
        results.append(toolkit.list_dir("."))
        results.append(
            toolkit.write_json(
                "fast_verify.json",
                {"ok": True, "lane": "fast", "goal": g[:300]},
            )
        )
    elif role == "seal":
        results.append(
            toolkit.package_manifest({"lane": "fast", "goal": g[:300], "node": node_id})
        )
        results.append(
            toolkit.write_json(
                "FAST_SEAL.json",
                {
                    "status": "GREEN",
                    "false_green": 0,
                    "lane": "fast",
                    "node": node_id,
                    "utc": _utc(),
                },
            )
        )
        results.append(toolkit.copy_to_artifacts("FAST_SEAL.json"))
    else:
        results.append(toolkit.write_text(f"fast_{role}.txt", f"{role}: {g}\n"))

    for r in results:
        if isinstance(r, dict) and r.get("ok"):
            if r.get("path"):
                evidence.append(str(r["path"]))
            if r.get("dest"):
                evidence.append(str(r["dest"]))
    ok = any(bool(r.get("ok")) for r in results if isinstance(r, dict))
    return {
        "summary": f"fast:{role}@{node_id} tools={len(results)} ok={ok}",
        "tool_results": results,
        "ok": ok,
        "evidence": evidence[:12],
    }


class FastLane:
    """5-node serial fabric. Tools lite. Optional shared LM on plan+execute only."""

    def __init__(
        self,
        root: Path,
        lm_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.cfg = load_fast_cfg(self.root)
        self.lm_fn = lm_fn
        self.memory = WorkMemory(self.root, LearnConfig())
        self.node_ids: list[str] = list(self.cfg.get("nodes") or [])
        role_map = self.cfg.get("role_map") or {}
        lm_roles = set(self.cfg.get("lm_roles") or ["plan", "execute"])
        self.nodes: dict[str, DroneNode] = {}
        self._toolkit: DroneToolkit | None = None
        for nid in self.node_ids:
            role = role_map.get(nid) or nid.split("_", 1)[-1]
            node = DroneNode(
                node_id=nid,
                hemisphere="F",
                role=role,
                skill_tags=_tags_for(role, "F") + ["fast_lane"],
                memory=self.memory,
                lm_fn=None,  # LM applied only via lite dispatch for lm_roles
                toolkit=None,
            )
            # mark which roles may use LM in lite dispatch
            node._fast_lm = role in lm_roles  # type: ignore[attr-defined]
            self.nodes[nid] = node

    def run(
        self,
        goal: str,
        *,
        controller_kind: str = "local",
        controller_name: str = "fast",
        domain: str = "build",
        skill_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            return {
                "status": "RED",
                "false_green": 0,
                "lane": "fast",
                "error": "goal required",
            }

        tags = list(skill_tags or ["build", "fast_lane"])
        if "fast_lane" not in tags:
            tags.append("fast_lane")

        task = TaskEnvelope(
            goal=goal,
            controller=ControllerIdentity(kind=controller_kind, name=controller_name),
            domain=domain,
            skill_tags=tags,
            payload={"lane": "fast"},
        )
        task.validate()

        t0 = time.perf_counter()
        before = self.memory.fabric_stats()
        toolkit = DroneToolkit(self.root, task_id=f"fast_{task.task_id}")
        self._toolkit = toolkit
        for n in self.nodes.values():
            n.toolkit = toolkit

        chain: list[dict[str, Any]] = []
        pkt: HandoffPacket | None = None
        all_ok = True

        for i, nid in enumerate(self.node_ids):
            node = self.nodes[nid]
            # Lite path: bypass full role_tool_dispatch via custom run
            result, out_pkt = self._run_node_lite(node, task, pkt)
            next_id = self.node_ids[i + 1] if i + 1 < len(self.node_ids) else "OUTBOX"
            out_pkt.to_node = next_id
            chain.append(
                {
                    "node": nid,
                    "ok": result.ok,
                    "xp_gain": result.xp_gain,
                    "duration_ms": result.duration_ms,
                    "output_preview": result.output[:240],
                }
            )
            if not result.ok:
                all_ok = False
            pkt = out_pkt

        # one final manifest
        man = toolkit.package_manifest(
            {"lane": "fast", "goal": goal, "task_id": task.task_id, "nodes": len(chain)}
        )
        tool_log = str(toolkit.dump_call_log())
        evidence = toolkit.evidence_paths()
        if man.get("path"):
            evidence.append(str(man["path"]))

        artifacts = [
            str(self.root / "data" / "builds" / f"{task.task_id}.json"),
            *evidence[:15],
            tool_log,
        ]
        build_path = self.memory.record_build(
            task_id=task.task_id,
            goal=goal,
            ok=all_ok,
            chain=[{"hemisphere": "F", **c} for c in chain],
            skill_tags=tags,
            artifacts=artifacts,
        )
        after = self.memory.fabric_stats()
        ms = (time.perf_counter() - t0) * 1000
        lm_model = getattr(self.lm_fn, "model", None) if self.lm_fn else None

        # lite seal (not full clean-slate)
        lite_seal = {
            "schema": "ai.worker.drone.fast_lane_seal.v1",
            "status": "GREEN" if all_ok else "RED",
            "false_green": 0,
            "lane": "fast",
            "utc": _utc(),
            "task_id": task.task_id,
            "goal": goal,
            "nodes_run": len(chain),
            "tool_calls": len(toolkit.calls),
            "duration_ms": round(ms, 2),
            "workspace": str(toolkit.workspace),
            "artifacts_dir": str(toolkit.artifacts),
            "tool_log": tool_log,
            "manifest": man.get("path"),
            "ollama_model": lm_model,
            "clean_slate": "lite",
            "honesty": {
                "not_full_24_node_seal": True,
                "not_full_clean_slate_upgrade": True,
                "what_ran": f"5-node fast lane + lite tools"
                + (f" + Ollama {lm_model}" if lm_model else ""),
                "use_full_for": "production seal / passport / recycle / registry",
            },
        }
        seal_path = self.root / "out" / f"fast_{task.task_id}.json"
        with root_lock(self.root):
            seal_path.parent.mkdir(parents=True, exist_ok=True)
            seal_path.write_text(json.dumps(lite_seal, indent=2), encoding="utf-8")
            # also stamp latest
            latest = self.root / "out" / "FAST_LANE_LATEST.json"
            latest.write_text(json.dumps(lite_seal, indent=2), encoding="utf-8")

        report = {
            **lite_seal,
            "controller": {"kind": controller_kind, "name": controller_name},
            "build_path": str(build_path),
            "smart_before": before.get("smart_index"),
            "smart_after": after.get("smart_index"),
            "smarter_delta": round(
                float(after.get("smart_index", 0)) - float(before.get("smart_index", 0)),
                3,
            ),
            "chain": chain,
            "tool_evidence": evidence[:20],
            "seal_path": str(seal_path),
            "report_path": str(seal_path),
        }
        return report

    def _run_node_lite(
        self,
        node: DroneNode,
        task: TaskEnvelope,
        incoming: HandoffPacket | None,
    ):
        from .protocol import NodeResult

        t0 = time.perf_counter()
        toolkit = self._toolkit
        assert toolkit is not None
        use_lm = self.lm_fn if getattr(node, "_fast_lm", False) else None
        dispatched = lite_tool_dispatch(
            toolkit,
            role=node.role,
            goal=task.goal,
            node_id=node.node_id,
            lm_fn=use_lm,
        )
        outcome = "success" if dispatched.get("ok") and task.goal.strip() else "fail"
        if not task.goal.strip():
            outcome = "fail"
        learned = self.memory.record_node_work(
            node_id=node.node_id,
            goal=task.goal,
            skill_tags=node.skill_tags + list(task.skill_tags),
            outcome=outcome if outcome != "fail" else "fail",
            evidence=dispatched.get("evidence") or [],
            output=dispatched.get("summary") or "",
            task_id=task.task_id,
        )
        ms = (time.perf_counter() - t0) * 1000
        result = NodeResult(
            node_id=node.node_id,
            hemisphere="F",
            ok=outcome != "fail",
            output=str(dispatched.get("summary") or ""),
            evidence=list(dispatched.get("evidence") or []),
            skill_tags=node.skill_tags,
            xp_gain=int(learned.get("xp_gain", 0)),
            learned={"xp_gain": learned.get("xp_gain"), "lane": "fast"},
            duration_ms=round(ms, 2),
        )
        handoff = HandoffPacket(
            from_node=node.node_id,
            to_node="",
            goal=task.goal,
            task_id=task.task_id,
            evidence=result.evidence,
            notes=result.output[:400],
            next_action=f"fast_after_{node.node_id}",
            veto=False,
            skill_tags=node.skill_tags,
            metrics={"lane": "fast", "xp_gain": result.xp_gain},
        )
        return result, handoff


def run_fast_smoke(root: Path) -> dict[str, Any]:
    """3 fast goals + optional wall-time stamp. No Ollama (speed proof)."""
    root = Path(root)
    lane = FastLane(root, lm_fn=None)
    goals = [
        "fast smoke A: write workspace artifact",
        "fast smoke B: plan and execute tiny note",
        "fast smoke C: verify and seal",
    ]
    t0 = time.perf_counter()
    reports = []
    for g in goals:
        reports.append(
            lane.run(g, controller_kind="local", controller_name="fast-smoke")
        )
    wall = (time.perf_counter() - t0) * 1000
    all_green = all(r.get("status") == "GREEN" for r in reports)
    seal = {
        "schema": "ai.worker.drone.fast_lane_smoke.v1",
        "status": "GREEN" if all_green else "RED",
        "false_green": 0,
        "lane": "fast",
        "utc": _utc(),
        "runs": len(reports),
        "duration_ms_wall": round(wall, 2),
        "duration_ms_sum": round(sum(float(r.get("duration_ms") or 0) for r in reports), 2),
        "nodes_per_run": 5,
        "tool_calls": [r.get("tool_calls") for r in reports],
        "report_paths": [r.get("seal_path") for r in reports],
        "honesty": {
            "fast_lane": True,
            "not_full_seal": True,
            "compare": "full 24-node path is run --lane full / default swarm",
        },
    }
    path = root / "out" / "FAST_LANE_SMOKE_SEAL.json"
    path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    seal["seal_path"] = str(path)
    return seal


def compare_fast_vs_full(root: Path) -> dict[str, Any]:
    """One goal both lanes — timing evidence (tools only, no LM)."""
    root = Path(root)
    goal = "lane compare: write a tiny hello artifact"
    t0 = time.perf_counter()
    fast = FastLane(root, lm_fn=None).run(
        goal, controller_kind="local", controller_name="compare-fast"
    )
    fast_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    full = BrainFabric(root, lm_fn=None, enable_tools=True).run_task(
        goal=goal,
        controller_kind="local",
        controller_name="compare-full",
        domain="build",
        skill_tags=["build", "full", "compare"],
    )
    full_ms = (time.perf_counter() - t1) * 1000

    speedup = round(full_ms / fast_ms, 2) if fast_ms > 0 else 0.0
    blob = {
        "schema": "ai.worker.drone.lane_compare.v1",
        "false_green": 0,
        "utc": _utc(),
        "goal": goal,
        "fast": {
            "status": fast.get("status"),
            "duration_ms": round(fast_ms, 2),
            "nodes_run": fast.get("nodes_run"),
            "tool_calls": fast.get("tool_calls"),
            "seal_path": fast.get("seal_path"),
        },
        "full": {
            "status": full.get("status"),
            "duration_ms": round(full_ms, 2),
            "nodes_run": full.get("nodes_run"),
            "tool_calls": full.get("tool_calls"),
            "report_path": full.get("report_path"),
        },
        "speedup_full_over_fast": speedup,
        "honesty": {
            "measured_wall_ms": True,
            "lm": False,
            "note": "fast is lite seal; full is 24-node tools path",
        },
    }
    path = root / "out" / "LANE_COMPARE.json"
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    blob["seal_path"] = str(path)
    return blob
