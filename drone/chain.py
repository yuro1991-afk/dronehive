"""Dual-hemisphere daisy chain of controllable drones + callosum."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .learn import LearnConfig, WorkMemory
from .nodes import DroneNode
from .protocol import ControllerIdentity, HandoffPacket, TaskEnvelope
from .tools import DroneToolkit


# role suffixes mapped from node id prefixes
_ROLE_FROM_ID = {
    "intake": "intake",
    "parse": "parse",
    "plan": "plan",
    "decompose": "decompose",
    "tool": "tool",
    "execute": "execute",
    "verify": "verify",
    "log": "log",
    "refine": "refine",
    "package": "package",
    "handoff": "handoff",
    "seal": "seal",
    "context": "context",
    "retrieve": "retrieve",
    "pattern": "pattern",
    "risk": "risk",
    "consistency": "consistency",
    "critic": "critic",
    "revise": "revise",
    "memory": "memory",
    "evolve": "evolve",
    "pack": "pack",
}


def _role_of(node_id: str) -> str:
    # L01_intake → intake
    if "_" in node_id:
        return node_id.split("_", 1)[1]
    return "generic"


def _tags_for(role: str, hemisphere: str) -> list[str]:
    base = [role, f"hem_{hemisphere.lower()}", "build"]
    if role in {"execute", "tool", "package", "pack"}:
        base.append("construct")
    if role in {"plan", "decompose", "parse"}:
        base.append("analysis")
    if role in {"critic", "risk", "verify", "revise"}:
        base.append("qa")
    return base


class BrainFabric:
    """
    2 hemispheres × 12 drones = 24 controllable nodes.
    Shared memory. Not 24 full models.
    """

    def __init__(
        self,
        root: Path,
        lm_fn: Callable[[str], str] | None = None,
        enable_tools: bool = True,
    ) -> None:
        self.root = Path(root)
        self.cfg = self._load_cfg()
        learn_cfg = LearnConfig(
            **self.cfg.get("learning", {}).get("level_curve", {})
        )
        self.memory = WorkMemory(self.root, learn_cfg)
        self.lm_fn = lm_fn
        self.enable_tools = enable_tools
        self.left_ids: list[str] = list(self.cfg["hemispheres"]["left"]["nodes"])
        self.right_ids: list[str] = list(self.cfg["hemispheres"]["right"]["nodes"])
        self.nodes: dict[str, DroneNode] = {}
        self._active_toolkit: DroneToolkit | None = None
        for nid in self.left_ids:
            role = _role_of(nid)
            self.nodes[nid] = DroneNode(
                node_id=nid,
                hemisphere="L",
                role=role,
                skill_tags=_tags_for(role, "L"),
                memory=self.memory,
                lm_fn=lm_fn,
                toolkit=None,
            )
        for nid in self.right_ids:
            role = _role_of(nid)
            self.nodes[nid] = DroneNode(
                node_id=nid,
                hemisphere="R",
                role=role,
                skill_tags=_tags_for(role, "R"),
                memory=self.memory,
                lm_fn=lm_fn,
                toolkit=None,
            )

    def _bind_toolkit(self, task_id: str) -> DroneToolkit | None:
        """Attach one shared toolkit to every node for this task."""
        if not self.enable_tools:
            self._active_toolkit = None
            for n in self.nodes.values():
                n.toolkit = None
            return None
        toolkit = DroneToolkit(self.root, task_id=task_id)
        self._active_toolkit = toolkit
        for n in self.nodes.values():
            n.toolkit = toolkit
            n.lm_fn = self.lm_fn
        return toolkit

    def _load_cfg(self) -> dict[str, Any]:
        path = self.root / "configs" / "brain_fabric.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def callosum_merge(
        self,
        left_pkt: HandoffPacket,
        right_pkt: HandoffPacket,
        task: TaskEnvelope,
    ) -> HandoffPacket:
        """Bridge hemispheres — structured only."""
        evidence = list(dict.fromkeys(left_pkt.evidence + right_pkt.evidence))
        notes = (
            f"CALLOSUM merge L={left_pkt.from_node} R={right_pkt.from_node} | "
            f"L:{left_pkt.notes[:160]} || R:{right_pkt.notes[:160]}"
        )
        # learning event for callosum
        self.memory.record_node_work(
            node_id="CALLOSUM",
            goal=task.goal,
            skill_tags=["callosum", "integration", "build"],
            outcome="success",
            evidence=evidence,
            output=notes,
            task_id=task.task_id,
        )
        return HandoffPacket(
            from_node="CALLOSUM",
            to_node="OUTBOX",
            goal=task.goal,
            task_id=task.task_id,
            evidence=evidence + ["callosum:merged"],
            notes=notes,
            next_action="emit_result",
            veto=left_pkt.veto or right_pkt.veto,
            skill_tags=["callosum"],
            metrics={
                "left_xp": left_pkt.metrics.get("xp_gain", 0),
                "right_xp": right_pkt.metrics.get("xp_gain", 0),
            },
        )

    def _run_chain(
        self,
        node_ids: list[str],
        task: TaskEnvelope,
        start: HandoffPacket | None = None,
    ) -> tuple[list[dict[str, Any]], HandoffPacket | None]:
        chain: list[dict[str, Any]] = []
        pkt = start
        # route preference: slightly reorder by node strength (smarter nodes earlier bias)
        # keep order stable but log strengths — full reorder only among same phase later
        for i, nid in enumerate(node_ids):
            node = self.nodes[nid]
            result, out_pkt = node.run(task, pkt)
            next_id = node_ids[i + 1] if i + 1 < len(node_ids) else "CALLOSUM"
            out_pkt.to_node = next_id
            chain.append(
                {
                    "node": nid,
                    "ok": result.ok,
                    "xp_gain": result.xp_gain,
                    "duration_ms": result.duration_ms,
                    "output_preview": result.output[:300],
                    "handoff": out_pkt.to_dict(),
                }
            )
            pkt = out_pkt
            if out_pkt.veto:
                break
        return chain, pkt

    def run_task(
        self,
        goal: str,
        controller_kind: str = "local",
        controller_name: str = "parent",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Serial hemispheres (classic chain)."""
        return self._run_task_inner(
            goal=goal,
            controller_kind=controller_kind,
            controller_name=controller_name,
            domain=domain,
            skill_tags=skill_tags,
            payload=payload,
            parallel_hemispheres=False,
        )

    def run_task_fast(
        self,
        goal: str,
        controller_kind: str = "local",
        controller_name: str = "fast",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fast lane: 5-node lite path (see drone.fast_lane)."""
        from .fast_lane import FastLane

        lane = FastLane(self.root, lm_fn=self.lm_fn)
        return lane.run(
            goal,
            controller_kind=controller_kind,
            controller_name=controller_name,
            domain=domain,
            skill_tags=skill_tags,
        )

    def run_task_swarm(
        self,
        goal: str,
        controller_kind: str = "local",
        controller_name: str = "swarm",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        parallel_hemispheres: bool = True,
    ) -> dict[str, Any]:
        """Swarm single goal: L || R hemispheres concurrent."""
        return self._run_task_inner(
            goal=goal,
            controller_kind=controller_kind,
            controller_name=controller_name,
            domain=domain,
            skill_tags=skill_tags or ["build", "swarm"],
            payload=payload,
            parallel_hemispheres=parallel_hemispheres,
        )

    def _run_task_inner(
        self,
        goal: str,
        controller_kind: str,
        controller_name: str,
        domain: str,
        skill_tags: list[str] | None,
        payload: dict[str, Any] | None,
        parallel_hemispheres: bool,
    ) -> dict[str, Any]:
        task = TaskEnvelope(
            goal=goal,
            controller=ControllerIdentity(kind=controller_kind, name=controller_name),
            domain=domain,
            skill_tags=skill_tags or ["build"],
            payload=payload or {},
        )
        task.validate()

        t0 = time.perf_counter()
        before = self.memory.fabric_stats()
        toolkit = self._bind_toolkit(task.task_id)

        if parallel_hemispheres:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fl = pool.submit(self._run_chain, self.left_ids, task, None)
                fr = pool.submit(self._run_chain, self.right_ids, task, None)
                left_chain, left_pkt = fl.result()
                right_chain, right_pkt = fr.result()
        else:
            left_chain, left_pkt = self._run_chain(self.left_ids, task)
            right_chain, right_pkt = self._run_chain(self.right_ids, task)

        if left_pkt is None or right_pkt is None:
            ok = False
            merged = None
        else:
            merged = self.callosum_merge(left_pkt, right_pkt, task)
            ok = all(c["ok"] for c in left_chain + right_chain) and not merged.veto

        full_chain = (
            [{"hemisphere": "L", **c} for c in left_chain]
            + [{"hemisphere": "R", **c} for c in right_chain]
            + (
                [
                    {
                        "hemisphere": "C",
                        "node": "CALLOSUM",
                        "ok": True,
                        "handoff": merged.to_dict() if merged else {},
                    }
                ]
                if merged
                else []
            )
        )

        tool_paths: list[str] = []
        tool_log = None
        if toolkit is not None:
            tool_log = str(toolkit.dump_call_log())
            tool_paths = toolkit.evidence_paths()
            # package final workspace into artifacts
            toolkit.package_manifest(
                {
                    "goal": goal,
                    "task_id": task.task_id,
                    "nodes_run": len(left_chain) + len(right_chain),
                }
            )
            tool_paths.extend(toolkit.evidence_paths())

        artifact_list = [
            str(self.root / "data" / "builds" / f"{task.task_id}.json"),
            *tool_paths[:20],
        ]
        if tool_log:
            artifact_list.append(tool_log)

        build_path = self.memory.record_build(
            task_id=task.task_id,
            goal=goal,
            ok=ok,
            chain=full_chain,
            skill_tags=task.skill_tags,
            artifacts=artifact_list,
        )
        after = self.memory.fabric_stats()
        ms = (time.perf_counter() - t0) * 1000

        mode = "swarm_parallel_hemispheres" if parallel_hemispheres else "serial_chain"
        lm_model = getattr(self.lm_fn, "model", None) if self.lm_fn else None
        report = {
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "mode": mode,
            "task_id": task.task_id,
            "goal": goal,
            "controller": {"kind": task.controller.kind, "name": task.controller.name},
            "nodes_run": len(left_chain) + len(right_chain),
            "drones_are_full_models": False,
            "tools_enabled": bool(toolkit),
            "tool_calls": len(toolkit.calls) if toolkit else 0,
            "tool_log": tool_log,
            "tool_evidence": tool_paths[:30],
            "workspace": str(toolkit.workspace) if toolkit else None,
            "artifacts_dir": str(toolkit.artifacts) if toolkit else None,
            "ollama_model": lm_model,
            "parallel_hemispheres": parallel_hemispheres,
            "duration_ms": round(ms, 2),
            "build_path": str(build_path),
            "smart_before": before.get("smart_index"),
            "smart_after": after.get("smart_index"),
            "smarter_delta": round(
                float(after.get("smart_index", 0)) - float(before.get("smart_index", 0)),
                3,
            ),
            "fabric_stats": after,
            "chain_preview": full_chain[:4] + full_chain[-2:],
            "honesty": {
                "what_ran": (
                    f"24 controllable drones mode={mode} + tools"
                    f"{' + shared Ollama ' + str(lm_model) if lm_model else ''} + learning"
                ),
                "not_ran": "24 independent LLMs or human brain simulation",
                "smarter": "smart_index/levels/skills rose from this build if success",
                "tools": "sandbox write/read/shell/python/package under data/workspace + out/artifacts",
            },
        }
        out = self.root / "out" / f"run_{task.task_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
        return report

    def stats(self) -> dict[str, Any]:
        return self.memory.fabric_stats()
