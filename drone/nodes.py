"""Controllable drone nodes — tools + shared Ollama top model (not full model per node)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .learn import WorkMemory
from .protocol import HandoffPacket, NodeResult, TaskEnvelope
from .tools import DroneToolkit, role_tool_dispatch


# Roles that call the shared top Ollama model (others use tools only)
_LM_ROLES = frozenset(
    {
        "plan",
        "pattern",
        "execute",
        "critic",
        "revise",
        "refine",
        "evolve",
        "seal",
    }
)


@dataclass
class DroneNode:
    """
    Controllable worker slot.

    Honesty:
      - Role + tools + optional shared LM — not a separate 0.5B/12B per node
      - Every role hits the real tool belt
      - LM only on key roles to protect 12GB VRAM
    """

    node_id: str
    hemisphere: str  # L | R | C
    role: str
    skill_tags: list[str]
    memory: WorkMemory
    lm_fn: Callable[[str], str] | None = None
    code_lm_fn: Callable[[str], str] | None = None  # second Ollama 8b code worker
    toolkit: DroneToolkit | None = None
    lm_roles: frozenset[str] = field(default_factory=lambda: _LM_ROLES)

    def run(
        self,
        task: TaskEnvelope,
        incoming: HandoffPacket | None,
    ) -> tuple[NodeResult, HandoffPacket]:
        t0 = time.perf_counter()
        wins = self.memory.similar_wins(task.goal, top_k=3)
        strength = self.memory.node_strength(self.node_id)

        prior = ""
        if incoming:
            prior = (
                f"from={incoming.from_node} notes={incoming.notes} "
                f"evidence={incoming.evidence[:5]}"
            )
        win_hints = [
            f"- past_win node={w.get('node_id')} goal={w.get('goal','')[:80]}"
            for w in wins
        ]

        steps = [
            f"[{self.node_id}] role={self.role} hemisphere={self.hemisphere}",
            f"goal={task.goal}",
            f"strength={strength:.2f}",
            f"prior={prior or 'chain-head'}",
            f"tools={'on' if self.toolkit else 'off'}",
            f"lm={'on' if self.lm_fn and self.role in self.lm_roles else 'off'}",
        ]
        if win_hints:
            steps.append("learned_from_past:")
            steps.extend(win_hints)
        else:
            steps.append("learned_from_past: none_yet")

        evidence = [
            f"node:{self.node_id}",
            f"role:{self.role}",
            f"wins_used:{len(wins)}",
            f"strength:{strength:.2f}",
        ]
        tool_ok = True
        body = ""

        # --- REAL TOOLS (all roles when toolkit present) ---
        if self.toolkit is not None:
            use_lm = self.lm_fn if self.role in self.lm_roles else None
            # execute uses code worker (8b); other LM roles use general lm_fn
            use_code = self.code_lm_fn if self.role in {"execute", "critic"} else None
            dispatched = role_tool_dispatch(
                self.toolkit,
                role=self.role,
                goal=task.goal,
                node_id=self.node_id,
                hemisphere=self.hemisphere,
                strength=strength,
                lm_fn=use_lm,
                code_lm_fn=use_code,
                prior_notes=prior,
                wins_n=len(wins),
            )
            body = dispatched.get("summary", "")
            steps.append(f"tool_dispatch: {body}")
            for ev in dispatched.get("evidence") or []:
                evidence.append(f"tool:{ev}")
            tool_ok = bool(dispatched.get("ok"))
            # compact tool result previews
            for tr in (dispatched.get("tool_results") or [])[:6]:
                if isinstance(tr, dict):
                    steps.append(
                        f"  tool={tr.get('tool')} ok={tr.get('ok')} "
                        f"path={tr.get('path') or tr.get('dest') or ''}"
                    )
        else:
            body = self._role_work_fallback(task, incoming, wins, strength)
            steps.append(body)
            # optional LM note without tools
            if self.lm_fn is not None and self.role in self.lm_roles:
                try:
                    assist = self.lm_fn(
                        f"DRONE {self.node_id} role={self.role}\nGOAL: {task.goal}\n"
                        f"CONTEXT: {body[:500]}\nReply with one short worker note."
                    )
                    steps.append(f"lm_assist: {assist[:300]}")
                except Exception as e:
                    steps.append(f"lm_assist_error: {e}")

        output = "\n".join(steps)
        hard_fail = False
        if self.toolkit is not None:
            hard_fail = bool(dispatched.get("hard_fail"))
        critical = self.role in {
            "execute",
            "critic",
            "verify",
            "revise",
            "seal",
        }
        if not task.goal.strip():
            outcome = "fail"
        elif critical and (not tool_ok or hard_fail):
            outcome = "fail"
        elif tool_ok:
            outcome = "success"
        else:
            outcome = "partial"

        learned = self.memory.record_node_work(
            node_id=self.node_id,
            goal=task.goal,
            skill_tags=self.skill_tags + list(task.skill_tags),
            outcome=outcome,
            evidence=evidence,
            output=output,
            task_id=task.task_id,
        )

        ms = (time.perf_counter() - t0) * 1000
        node_ok = outcome != "fail"
        result = NodeResult(
            node_id=self.node_id,
            hemisphere=self.hemisphere,
            ok=node_ok,
            output=output,
            evidence=evidence,
            skill_tags=self.skill_tags,
            xp_gain=int(learned.get("xp_gain", 0)),
            learned={
                "xp_gain": learned.get("xp_gain"),
                "skills_n": len(learned.get("skills", [])),
                "tools": bool(self.toolkit),
                "hard_fail": hard_fail,
            },
            duration_ms=round(ms, 2),
        )
        # Critical role failure can veto hemisphere continuation
        veto = critical and not node_ok
        handoff = HandoffPacket(
            from_node=self.node_id,
            to_node="",
            goal=task.goal,
            task_id=task.task_id,
            evidence=evidence,
            notes=body[:500],
            next_action=f"continue_after_{self.node_id}",
            veto=veto,
            skill_tags=self.skill_tags,
            metrics={
                "strength": strength,
                "xp_gain": result.xp_gain,
                "tools": bool(self.toolkit),
                "hard_fail": hard_fail,
            },
        )
        return result, handoff

    def _role_work_fallback(
        self,
        task: TaskEnvelope,
        incoming: HandoffPacket | None,
        wins: list[dict[str, Any]],
        strength: float,
    ) -> str:
        g = task.goal.strip()
        tag = ",".join(self.skill_tags)
        quality = "novice"
        if strength >= 40:
            quality = "competent"
        if strength >= 70:
            quality = "veteran"
        role = self.role
        if role in {"intake", "context"}:
            return f"{quality}: accept task domain={task.domain} tags=[{tag}] goal_len={len(g)}"
        if role in {"parse", "retrieve"}:
            bits = g.split()
            return f"{quality}: tokens={len(bits)} key={bits[:8]} past={len(wins)}"
        if role in {"plan", "pattern"}:
            return f"{quality}: plan_steps=parse→act→verify bias_strength={strength:.1f}"
        if role in {"decompose", "risk"}:
            return f"{quality}: split_goal risks={'low' if strength>30 else 'watch'}"
        if role in {"tool", "consistency"}:
            return f"{quality}: tool_ready checks=path,io,evidence"
        if role in {"execute", "critic"}:
            return f"{quality}: execute/critique against goal; reuse_wins={len(wins)}"
        if role in {"verify", "revise"}:
            return f"{quality}: verify completeness; revise if empty evidence"
        if role in {"log", "memory"}:
            return f"{quality}: persist trace to experience ledger"
        if role in {"refine", "evolve"}:
            return f"{quality}: refine output using skill score={strength:.1f}"
        if role in {"package", "pack"}:
            return f"{quality}: package artifacts for next hop"
        if role in {"handoff"}:
            return f"{quality}: callosum-ready packet"
        if role in {"seal"}:
            return f"{quality}: seal node complete for {self.hemisphere}"
        return f"{quality}: generic work on [{tag}]"
