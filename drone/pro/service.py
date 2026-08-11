"""DroneHive Pro service — premium command path."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from drone.app.config import app_root, ensure_app_dirs, load_app_config
from drone.app.service import DroneHiveService
from drone.pro.tool_agent import run_pro_agent


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class DroneHiveProService(DroneHiveService):
    """Extends v1 service with Pro free-form agent + richer brain path."""

    edition = "pro"
    version = "2.0.0"

    def health(self) -> dict[str, Any]:
        base = super().health()
        base["edition"] = self.edition
        base["version"] = self.version
        base["pro"] = {
            "free_form_tools": True,
            "tool_agent": "drone.pro.tool_agent.run_pro_agent",
            "tier": "premium",
        }
        return base

    def pro_run(
        self,
        goal: str,
        *,
        max_rounds: int = 8,
        use_ollama: bool = True,
        also_hive: bool = False,
    ) -> dict[str, Any]:
        """
        Premium path: free-form Ollama tool loop producing real workspace files.
        Optionally also fire a small hive swarm after (richer people get both).
        """
        agent = run_pro_agent(
            self.root,
            goal,
            max_rounds=max_rounds,
            use_ollama=use_ollama,
        )
        hive_seal = None
        if also_hive and agent.get("status") in {"GREEN", "PARTIAL"}:
            hive_seal = self.run_hive(
                goals=[
                    f"pro peer: package {goal[:80]}",
                    f"pro peer: verify artifacts for {goal[:60]}",
                ],
                cycles=2,
                workers=2,
                lane="fast",
                lm_assist="ollama",
                controller="ollama",
            )
        seal = {
            "schema": "drone.hive.pro.run.v1",
            "edition": "pro",
            "version": "2.0.0",
            "status": agent.get("status"),
            "false_green": 0,
            "utc": _utc(),
            "goal": goal,
            "agent": agent,
            "hive_followup": hive_seal,
            "pipeline": [
                "user_command",
                "pro_free_form_tool_agent",
                "optional_hive_followup" if also_hive else "agent_only",
            ],
            "seal_path": agent.get("seal_path"),
            "evidence": agent.get("evidence") or [],
            "tool_calls": agent.get("tool_calls"),
            "units": 1 + (int(hive_seal.get("units") or 0) if hive_seal else 0),
            "peak_parallel_observed": (hive_seal or {}).get("peak_parallel_observed") or 1,
        }
        out = self.root / "out" / "PRO_RUN_LAST.json"
        out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        seal["report_path"] = str(out)
        self._write_outbox("pro", seal)
        return seal

    def brain_command(self, user_command: str) -> dict[str, Any]:
        """
        Pro main input: free-form tool agent first (richer capability),
        then optional v1 brain plan for swarm if command looks multi-unit.
        """
        cmd = (user_command or "").strip()
        low = cmd.lower()
        multi = any(
            k in low
            for k in ("swarm", "hive", "parallel", "buzzers", "multi", "fleet")
        )
        return self.pro_run(cmd, max_rounds=8, use_ollama=True, also_hive=multi)
