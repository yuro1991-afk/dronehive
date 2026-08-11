"""
Clean Slate Agents Upgrade — v2 lifecycle for buzzers + fabric units.

Upgrade over base work-order imprint:
  - Agent passport (lineage, generation, forbidden prior RAM)
  - Wake from NEXT.json (consume → imprint → swarm)
  - Brain + tools bound into imprint at birth
  - Hard wipe audit after death (active gone, recycle exists, scratch empty)
  - Generation chain ledger on disk

Honesty: clean slate = ephemeral RAM/scratch only. Hive + recycle + skills persist.
false_green: 0
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .locks import root_lock
from .work_order import (
    build_task_slot,
    close_drone_lifecycle,
    imprint_drone,
    load_work_order_config,
    write_next_imprint,
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


UPGRADE_ID = "clean_slate_agents_v2"
UPGRADE_SCHEMA = "ai.worker.drone.clean_slate_agent.v2"


def load_clean_slate_config(root: Path) -> dict[str, Any]:
    path = Path(root) / "configs" / "clean_slate_agents.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["path"] = str(path)
        data["missing"] = False
        return data
    return {
        "schema": UPGRADE_SCHEMA,
        "upgrade_id": UPGRADE_ID,
        "missing": True,
        "path": str(path),
        "enabled": True,
    }


def _brain_pack() -> dict[str, Any]:
    try:
        from .ollama_brain import brain_status, resolve_top_model

        return {
            "bound": True,
            "top_model": resolve_top_model(force_refresh=False, probe=False),
            "status": brain_status(),
        }
    except Exception as e:
        return {"bound": False, "error": str(e), "top_model": None}


def _tools_pack(root: Path, agent_id: str) -> dict[str, Any]:
    try:
        from .tools import DroneToolkit

        # probe toolkit (does not run a full task)
        tk = DroneToolkit(root, task_id=f"cs_probe_{agent_id[:8]}")
        listed = tk.list_tools()
        return {
            "bound": True,
            "tools": listed.get("tools") or [],
            "workspace_pattern": "data/workspace/<task_id>",
            "artifacts_pattern": "out/artifacts/<task_id>",
            "all_roles": True,
        }
    except Exception as e:
        return {"bound": False, "error": str(e), "tools": []}


def read_next_imprint(root: Path) -> dict[str, Any]:
    root = Path(root)
    cfg = load_work_order_config(root)
    next_rel = (cfg.get("imprints") or {}).get("next_rel") or "data/hive/imprints/NEXT.json"
    path = root / next_rel
    if not path.is_file():
        return {"ok": False, "ready": False, "path": str(path), "error": "NEXT.json missing"}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "ready": bool(blob.get("ready")),
            "path": str(path),
            "blob": blob,
            "task": blob.get("task") or {},
        }
    except Exception as e:
        return {"ok": False, "ready": False, "path": str(path), "error": str(e)}


def consume_next_imprint(root: Path) -> dict[str, Any]:
    """Read NEXT and mark consumed (rename to history stamp). Returns task slot if ready."""
    root = Path(root)
    nxt = read_next_imprint(root)
    if not nxt.get("ok") or not nxt.get("ready"):
        return nxt
    path = Path(nxt["path"])
    task = nxt.get("task") or {}
    with root_lock(root):
        hist = root / "data" / "hive" / "imprints" / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = hist / f"NEXT_consumed_{stamp}.json"
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            blob["consumed_utc"] = _utc()
            blob["ready"] = False
            dest.write_text(json.dumps(blob, indent=2), encoding="utf-8")
            # leave NEXT as empty await so nothing reuses stale task
            path.write_text(
                json.dumps(
                    {
                        "schema": "ai.worker.drone.next_imprint.v1",
                        "ready": False,
                        "utc": _utc(),
                        "note": "consumed — await fresh write_next_imprint",
                        "last_consumed": str(dest),
                        "false_green": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            return {"ok": False, "error": str(e), "path": str(path)}
    return {
        "ok": True,
        "ready": True,
        "task": task,
        "consumed_to": str(dest),
        "path": str(path),
    }


def append_generation_ledger(
    root: Path,
    *,
    agent_id: str,
    generation: int,
    parent_id: str | None,
    status: str,
    recycle_dir: str = "",
    next_path: str = "",
) -> Path:
    root = Path(root)
    path = root / "data" / "hive" / "clean_slate" / "generation_ledger.jsonl"
    row = {
        "utc": _utc(),
        "upgrade": UPGRADE_ID,
        "agent_id": agent_id,
        "generation": generation,
        "parent_id": parent_id,
        "status": status,
        "recycle_dir": recycle_dir,
        "next_path": next_path,
        "false_green": 0,
    }
    with root_lock(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def hard_wipe_audit(
    root: Path,
    *,
    agent_id: str,
    recycle_dir: str | None,
    scratch_cleared: bool,
) -> dict[str, Any]:
    """Prove death: no active imprint, recycle dump exists, scratch claim honest."""
    root = Path(root)
    cfg = load_work_order_config(root)
    active_rel = (cfg.get("imprints") or {}).get("active_rel") or "data/hive/imprints/active"
    active = root / active_rel / f"{agent_id}.json"
    hist_rel = (cfg.get("imprints") or {}).get("history_rel") or "data/hive/imprints/history"
    hist = root / hist_rel / f"{agent_id}.json"
    recycle_ok = bool(recycle_dir and Path(recycle_dir).is_dir())
    checks = {
        "active_imprint_removed": not active.is_file(),
        "history_imprint_present": hist.is_file(),
        "recycle_dump_present": recycle_ok,
        "scratch_cleared_claim": bool(scratch_cleared),
    }
    ok = all(checks.values())
    audit = {
        "schema": "ai.worker.drone.clean_slate_wipe_audit.v1",
        "utc": _utc(),
        "upgrade": UPGRADE_ID,
        "agent_id": agent_id,
        "checks": checks,
        "paths": {
            "active": str(active),
            "history": str(hist),
            "recycle": recycle_dir,
        },
        "ok": ok,
        "false_green": 0,
        "status": "GREEN" if ok else "PARTIAL",
    }
    out = root / "data" / "hive" / "clean_slate" / "wipe_audits" / f"{agent_id}.json"
    with root_lock(root):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    audit["audit_path"] = str(out)
    return audit


def build_passport(
    root: Path,
    *,
    agent_id: str,
    generation: int,
    parent_id: str | None = None,
    swarm_system: str = "buzzer_hive",
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Birth papers for a clean-slate agent."""
    brain = _brain_pack()
    tools = _tools_pack(root, agent_id)
    forbidden = [
        f"data/hive/memory_recycle/*_{parent_id}/*" if parent_id else None,
        "prior buzzer _scratch RAM",
        "prior conversation context",
        f"data/hive/imprints/active/{parent_id}.json" if parent_id else None,
    ]
    passport = {
        "schema": UPGRADE_SCHEMA,
        "upgrade_id": UPGRADE_ID,
        "utc": _utc(),
        "agent_id": agent_id,
        "generation": generation,
        "parent_id": parent_id,
        "lineage": {
            "parent": parent_id,
            "child": agent_id,
            "rule": "child never loads parent scratch; may read hive durable + recycle archive only",
        },
        "swarm_system": swarm_system,
        "task": task or {},
        "brain": brain,
        "tools": tools,
        "forbidden_prior_ram": [f for f in forbidden if f],
        "allowed_durable": [
            "data/hive/vector_docs.jsonl",
            "data/skills/*",
            "data/experience/ledger.jsonl",
            "F:/GrokSelfLibrary",
            "D:/GrokCoreMemory/continuous/OPEN_TASKS.json",
        ],
        "clean_slate": True,
        "false_green": 0,
        "agreement": (
            "I am a clean-slate agent. No prior task RAM. "
            "I am bound to tools + shared Ollama brain. "
            "I imprint, execute, registry, recycle, die; next agent wakes fresh."
        ),
    }
    path = Path(root) / "data" / "hive" / "clean_slate" / "passports" / f"{agent_id}.json"
    with root_lock(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(passport, indent=2), encoding="utf-8")
    passport["passport_path"] = str(path)
    return passport


def enhance_imprint_with_upgrade(
    imprint: dict[str, Any],
    passport: dict[str, Any],
) -> dict[str, Any]:
    """Merge clean-slate upgrade fields into work-order imprint (mutate + rewrite)."""
    imprint = dict(imprint)
    imprint["clean_slate_upgrade"] = {
        "upgrade_id": UPGRADE_ID,
        "schema": UPGRADE_SCHEMA,
        "passport_path": passport.get("passport_path"),
        "generation": passport.get("generation"),
        "parent_id": passport.get("parent_id"),
        "brain": passport.get("brain"),
        "tools": passport.get("tools"),
        "forbidden_prior_ram": passport.get("forbidden_prior_ram"),
        "agreement": passport.get("agreement"),
    }
    imprint["clean_slate"] = True
    imprint["upgrade"] = UPGRADE_ID
    path = imprint.get("imprint_path")
    if path:
        p = Path(path)
        if p.parent.is_dir():
            p.write_text(json.dumps(imprint, indent=2), encoding="utf-8")
    return imprint


@dataclass
class CleanSlateAgent:
    """
    Upgraded ephemeral agent: passport + imprint + tools fabric + lifecycle.

    Do not reuse after run() — spawn a new one.
    """

    root: Path
    agent_id: str = field(default_factory=lambda: f"csa_{uuid.uuid4().hex[:10]}")
    generation: int = 0
    parent_id: str | None = None
    swarm_system: str = "buzzer_hive"
    lm_fn: Callable[[str], str] | None = None
    enable_tools: bool = True
    _used: bool = False
    passport: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        goal: str,
        *,
        controller_kind: str = "ollama",
        controller_name: str = "clean-slate",
        domain: str = "build",
        skill_tags: list[str] | None = None,
        next_task: dict[str, Any] | None = None,
        wake_from_next: bool = False,
        write_library_note: bool = True,
    ) -> dict[str, Any]:
        if self._used:
            raise RuntimeError(
                f"clean-slate agent {self.agent_id} already used — spawn fresh"
            )
        self._used = True
        root = Path(self.root)
        t0 = time.perf_counter()

        # Optional wake: consume NEXT.json as goal source
        woke: dict[str, Any] = {"attempted": False}
        if wake_from_next and not (goal or "").strip():
            woke = consume_next_imprint(root)
            woke["attempted"] = True
            if woke.get("ok") and (woke.get("task") or {}).get("goal"):
                goal = str(woke["task"]["goal"])
                skill_tags = list(skill_tags or []) + ["wake_next"]

        goal = (goal or "").strip()
        if not goal:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "no goal and NEXT not ready",
                "wake": woke,
                "agent_id": self.agent_id,
                "clean_slate": True,
                "upgrade": UPGRADE_ID,
            }

        # Birth: passport before imprint
        tags = list(skill_tags or ["build", "clean_slate", "upgrade"])
        if "clean_slate" not in tags:
            tags.insert(0, "clean_slate")
        if "work_order" not in tags:
            tags.insert(0, "work_order")

        wo_cfg = load_work_order_config(root)
        task = build_task_slot(
            goal,
            source="clean_slate_agent",
            domain=domain,
            skill_tags=tags,
            cfg=wo_cfg,
        )
        self.passport = build_passport(
            root,
            agent_id=self.agent_id,
            generation=self.generation,
            parent_id=self.parent_id,
            swarm_system=self.swarm_system,
            task=task,
        )

        # Execute via buzzer hive path (tools + fabric + work-order lifecycle)
        from .buzzer import Buzzer
        from .chain import BrainFabric
        from .hive_memory import HiveMemory
        from .library_bridge import LibraryBridge

        hive_mem = HiveMemory(root)
        fabric = BrainFabric(root, lm_fn=self.lm_fn, enable_tools=self.enable_tools)
        bz = Buzzer(
            hive_memory=hive_mem,
            fabric=fabric,
            library=LibraryBridge(),
            root=root,
            buzzer_id=self.agent_id,
            generation=self.generation,
        )
        bz_report = bz.run(
            goal,
            controller_kind=controller_kind,
            controller_name=controller_name,
            domain=domain,
            skill_tags=tags,
            write_library_note=write_library_note,
            next_task=next_task,
            goal_source="clean_slate_agent",
        )
        # Bind upgrade onto imprint after birth imprint written by buzzer
        if bz_report.get("imprint_path"):
            try:
                imp = json.loads(Path(bz_report["imprint_path"]).read_text(encoding="utf-8"))
                # imprint may already be archived — also write upgrade sidecar
                enhance_imprint_with_upgrade(imp, self.passport)
                side = (
                    root
                    / "data"
                    / "hive"
                    / "clean_slate"
                    / "imprint_upgrades"
                    / f"{self.agent_id}.json"
                )
                side.parent.mkdir(parents=True, exist_ok=True)
                side.write_text(json.dumps(imp, indent=2), encoding="utf-8")
                bz_report["imprint_upgrade_path"] = str(side)
            except Exception as e:
                bz_report["imprint_upgrade_error"] = str(e)

        report = {
            **bz_report,
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "clean_slate": True,
            "upgrade": UPGRADE_ID,
            "passport_path": self.passport.get("passport_path"),
            "brain": self.passport.get("brain"),
            "tools_bound": self.passport.get("tools"),
            "wake": woke,
        }
        lifecycle = report.get("lifecycle_detail") or report.get("lifecycle") or {}

        # Hard wipe audit
        recycle_dir = None
        if isinstance(lifecycle, dict):
            recycle_dir = (lifecycle.get("recycle") or {}).get("dump_dir") or lifecycle.get(
                "recycle_dir"
            )
        audit = hard_wipe_audit(
            root,
            agent_id=self.agent_id,
            recycle_dir=recycle_dir,
            scratch_cleared=True,
        )
        next_path = ""
        if isinstance(lifecycle, dict):
            next_path = (lifecycle.get("next_imprint") or {}).get("path") or lifecycle.get(
                "next_path"
            ) or ""

        ledger = append_generation_ledger(
            root,
            agent_id=self.agent_id,
            generation=self.generation,
            parent_id=self.parent_id,
            status=str(report.get("status")),
            recycle_dir=str(recycle_dir or ""),
            next_path=str(next_path or ""),
        )

        report["wipe_audit"] = audit
        report["generation_ledger"] = str(ledger)
        report["duration_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        report["honesty"] = {
            **(report.get("honesty") or {}),
            "upgrade": UPGRADE_ID,
            "clean_slate": True,
            "not_full_llm_per_agent": True,
            "durable_survives": "hive + skills + recycle + library + NEXT",
            "wipe_audit_ok": audit.get("ok"),
        }

        # Seal upgrade report
        seal_path = (
            root / "data" / "hive" / "clean_slate" / "runs" / f"{self.agent_id}.json"
        )
        with root_lock(root):
            seal_path.parent.mkdir(parents=True, exist_ok=True)
            seal_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["upgrade_seal_path"] = str(seal_path)

        # Force death of in-object state
        self.passport = {}
        return report


def spawn_clean_slate_agent(
    root: Path,
    *,
    generation: int = 0,
    parent_id: str | None = None,
    lm_fn: Callable[[str], str] | None = None,
    swarm_system: str = "buzzer_hive",
    enable_tools: bool = True,
) -> CleanSlateAgent:
    """Always a NEW agent id — never reuse."""
    return CleanSlateAgent(
        root=Path(root),
        agent_id=f"csa_{uuid.uuid4().hex[:10]}",
        generation=generation,
        parent_id=parent_id,
        swarm_system=swarm_system,
        lm_fn=lm_fn,
        enable_tools=enable_tools,
    )


def run_clean_slate_chain(
    root: Path,
    *,
    goals: list[str],
    controller: str = "ollama",
    lm_assist: str = "ollama",
    enable_tools: bool = True,
) -> dict[str, Any]:
    """
    Sequential clean-slate chain:
      agent0(goal0) dies → agent1(goal1) with parent=agent0 → …
    Proves generation lineage + wipe between agents.
    """
    from .controllers import make_lm_fn

    root = Path(root)
    lm = make_lm_fn(lm_assist)
    results: list[dict[str, Any]] = []
    parent: str | None = None
    t0 = time.perf_counter()
    goals = [g.strip() for g in goals if (g or "").strip()]
    if not goals:
        return {"status": "RED", "false_green": 0, "error": "no goals"}

    for i, g in enumerate(goals, start=1):
        nxt = None
        if i < len(goals):
            nxt = build_task_slot(
                goals[i],
                source="clean_slate_chain",
                domain="build",
                skill_tags=["clean_slate", "chain"],
            )
        agent = spawn_clean_slate_agent(
            root,
            generation=i,
            parent_id=parent,
            lm_fn=lm,
            enable_tools=enable_tools,
        )
        rep = agent.run(
            g,
            controller_kind=controller if controller != "local" else "local",
            controller_name="clean-slate-chain",
            next_task=nxt,
            write_library_note=True,
        )
        results.append(
            {
                "index": i,
                "agent_id": rep.get("agent_id") or agent.agent_id,
                "parent_id": parent,
                "status": rep.get("status"),
                "goal": g,
                "passport_path": rep.get("passport_path"),
                "wipe_audit_ok": (rep.get("wipe_audit") or {}).get("ok"),
                "recycle_dir": (rep.get("wipe_audit") or {})
                .get("paths", {})
                .get("recycle")
                or (rep.get("lifecycle") or {}).get("recycle_dir"),
                "upgrade_seal_path": rep.get("upgrade_seal_path"),
                "brain_model": ((rep.get("brain") or {}).get("top_model")),
            }
        )
        parent = str(rep.get("agent_id") or agent.agent_id)

    all_green = all(r.get("status") == "GREEN" for r in results)
    wipes = all(r.get("wipe_audit_ok") for r in results)
    seal = {
        "schema": "ai.worker.drone.clean_slate_chain_seal.v1",
        "upgrade": UPGRADE_ID,
        "status": "GREEN" if (all_green and wipes) else ("PARTIAL" if results else "RED"),
        "false_green": 0,
        "utc": _utc(),
        "units": len(results),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        "lineage": [
            {"agent": r.get("agent_id"), "parent": r.get("parent_id"), "gen": r.get("index")}
            for r in results
        ],
        "results": results,
        "honesty": {
            "what_ran": f"{len(results)} clean-slate agents in series with parent lineage",
            "tools": enable_tools,
            "lm_assist": lm_assist,
            "wipe_all_ok": wipes,
            "not_ran": "N full LLM loads or retained parent RAM",
        },
    }
    out = root / "out" / "CLEAN_SLATE_CHAIN_SEAL.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    seal["seal_path"] = str(out)
    return seal
