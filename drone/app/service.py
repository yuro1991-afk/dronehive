"""
DroneHive service facade — modular entry to fabric / hive / fast / clean-slate.

All external links (HTTP, file, library) go through this API.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .config import app_root, ensure_app_dirs, env_overrides, load_app_config


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class DroneHiveService:
    """Standalone modular service surface."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or app_root())
        self.cfg = env_overrides(load_app_config(self.root))
        self.dirs = ensure_app_dirs(self.root, self.cfg)

    def health(self) -> dict[str, Any]:
        from drone.ollama_brain import brain_status
        from drone.library_bridge import LibraryBridge
        from drone.tools import DroneToolkit

        ollama = brain_status()
        lib = LibraryBridge().status()
        try:
            tools = DroneToolkit(self.root, task_id="ops_probe").list_tools()
            tools_ok = bool(tools.get("ok"))
        except Exception as e:
            tools = {"ok": False, "error": str(e)}
            tools_ok = False

        modules = {
            "fabric": (self.root / "drone" / "chain.py").is_file(),
            "hive": (self.root / "drone" / "hive.py").is_file(),
            "fast_lane": (self.root / "drone" / "fast_lane.py").is_file(),
            "clean_slate": (self.root / "drone" / "clean_slate.py").is_file(),
            "tools": (self.root / "drone" / "tools.py").is_file(),
            "links": (self.root / "drone" / "app" / "links.py").is_file(),
            "api": (self.root / "drone" / "app" / "api.py").is_file(),
        }
        ok = all(modules.values()) and tools_ok
        return {
            "ok": ok,
            "status": "GREEN" if ok else "PARTIAL",
            "false_green": 0,
            "app": self.cfg.get("name"),
            "version": self.cfg.get("version"),
            "utc": _utc(),
            "root": str(self.root),
            "modules": modules,
            "ollama": {
                "reachable": ollama.get("reachable"),
                "top_model": ollama.get("top_model"),
            },
            "library": {
                "exists": lib.get("library_exists"),
                "continuous": lib.get("continuous_tasks_exists"),
            },
            "tools": tools,
            "lanes": ["fast", "full"],
            "endpoints": {
                "health": "/api/health",
                "status": "/api/status",
                "task": "/api/v1/task",
                "fast": "/api/v1/fast",
                "hive": "/api/v1/hive",
                "handoff": "/api/v1/handoff",
                "lanes": "/api/v1/lanes",
                "cowork_collect": "/api/v1/cowork/collect",
                "openai_chat": "/v1/chat/completions",
                "links": "/api/v1/links",
            },
            "grok_cowork": {
                "cli": "python -m drone handoff lanes|to|collect|status|e2e",
                "packet_schema": "grok.drone.cowork.handoff.v1",
                "dirs": "data/app/cowork/{outbox,inbox,active,done}",
            },
        }

    def status(self) -> dict[str, Any]:
        from drone.hive import BuzzerHive
        from drone.chain import BrainFabric

        fabric = BrainFabric(self.root, lm_fn=None, enable_tools=True)
        hive = BuzzerHive(self.root, lm_assist="none", lane="fast")
        return {
            "app": self.cfg.get("name"),
            "version": self.cfg.get("version"),
            "utc": _utc(),
            "fabric_stats": fabric.stats(),
            "hive": hive.status(),
            "config": {
                "host": self.cfg.get("host"),
                "port": self.cfg.get("port"),
                "lane_default": self.cfg.get("lane_default"),
                "modularity": self.cfg.get("modularity"),
            },
            "dirs": {k: str(v) for k, v in self.dirs.items()},
            "false_green": 0,
        }

    def brain_command(self, user_command: str) -> dict[str, Any]:
        """
        Main input path: user command → Ollama brain plans → swarm/ops execute.

        Writes out/BRAIN_LAST_PLAN.json + out/BRAIN_DELEGATE_LAST.json.
        """
        from drone.app.brain_delegate import brain_command as _brain

        seal = _brain(self, user_command)
        self._write_outbox("brain", seal)
        return seal

    def run_task(
        self,
        goal: str,
        *,
        lane: str | None = None,
        controller: str | None = None,
        lm_assist: str | None = None,
        domain: str = "build",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        from drone.controllers import make_lm_fn
        from drone.chain import BrainFabric
        from drone.fast_lane import FastLane

        lane = (lane or self.cfg.get("lane_default") or "fast").lower()
        controller = controller or self.cfg.get("controller_default") or "local"
        lm_assist = lm_assist if lm_assist is not None else self.cfg.get("lm_assist_default") or "none"
        lm = make_lm_fn(lm_assist)
        tags = tags or ["build", "app", "universal"]

        if lane == "fast":
            report = FastLane(self.root, lm_fn=lm).run(
                goal,
                controller_kind=controller,
                controller_name="dronehive-app",
                domain=domain,
                skill_tags=tags + ["fast_lane"],
            )
        else:
            fabric = BrainFabric(self.root, lm_fn=lm, enable_tools=True)
            report = fabric.run_task(
                goal=goal,
                controller_kind=controller,
                controller_name="dronehive-app",
                domain=domain,
                skill_tags=tags + ["full_lane"],
            )
        report["app"] = self.cfg.get("name")
        report["lane"] = lane
        self._write_outbox("task", report)
        return report

    def run_hive(
        self,
        goals: list[str] | None = None,
        *,
        cycles: int = 2,
        workers: int = 2,
        lane: str = "fast",
        lm_assist: str = "none",
        controller: str = "local",
    ) -> dict[str, Any]:
        from drone.hive import BuzzerHive

        h = BuzzerHive(self.root, lm_assist=lm_assist, lane=lane)
        seal = h.run_swarm(
            goals=goals,
            cycles=cycles,
            workers=workers,
            parallel=True,
            controller=controller,
            controller_name="dronehive-app",
            pull_library=not bool(goals),
            write_library_note=True,
            lane=lane,
        )
        seal["app"] = self.cfg.get("name")
        self._write_outbox("hive", seal)
        return seal

    def _write_outbox(self, kind: str, payload: dict[str, Any]) -> Path:
        outbox = self.dirs["outbox"]
        name = f"{kind}_{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
        path = outbox / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def handoff(
        self,
        goal: str,
        *,
        mode: str = "fast",
        lane: str | None = None,
        workers: int = 2,
        cycles: int = 2,
        lm_assist: str = "none",
        controller: str = "grok",
        notes: str = "",
        context_paths: list[str] | None = None,
        continuous_task_id: str | None = None,
        goals: list[str] | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Grok → drones live handoff (cowork packet + lanes)."""
        from drone.grok_handoff import GrokDroneHandoff

        bridge = GrokDroneHandoff(self.root)
        seal = bridge.handoff(
            goal,
            mode=mode,
            lane=lane,
            workers=workers,
            cycles=cycles,
            lm_assist=lm_assist,
            controller=controller,
            wait=wait,
            notes=notes,
            context_paths=context_paths,
            continuous_task_id=continuous_task_id,
            goals=goals,
        )
        self._write_outbox("handoff", seal)
        return seal

    def lanes_live(self) -> dict[str, Any]:
        from drone.grok_handoff import GrokDroneHandoff

        return GrokDroneHandoff(self.root).lanes_live()

    def super_llms_status(self) -> dict[str, Any]:
        from drone.super_llms import SuperLLMs

        return SuperLLMs(self.root).status()

    def super_llms_route(self, goal: str, role: str | None = None) -> dict[str, Any]:
        from drone.super_llms import SuperLLMs

        return SuperLLMs(self.root).route(goal, role=role)

    def super_llms_chat(
        self, prompt: str, *, model: str | None = None, role: str | None = None
    ) -> dict[str, Any]:
        from drone.super_llms import SuperLLMs

        return SuperLLMs(self.root).chat(prompt, model=model, role=role)

    def seer_type(self, text: str) -> dict[str, Any]:
        from drone.future_seer import FutureSeer

        return FutureSeer(self.root).speculate(text)

    def seer_commit(
        self, text: str, *, mode: str = "preview", allow_auto: bool = False
    ) -> dict[str, Any]:
        from drone.future_seer import FutureSeer

        return FutureSeer(self.root).commit(text, mode=mode, allow_auto=allow_auto)

    def seer_hot(self) -> dict[str, Any]:
        from drone.future_seer import FutureSeer

        return FutureSeer(self.root).keep_hot(force=True)

    def seer_status(self) -> dict[str, Any]:
        from drone.future_seer import FutureSeer

        return FutureSeer(self.root).status()

    def face_hosts(self) -> dict[str, Any]:
        from drone.multi_face import MultiFace

        return MultiFace(self.root).probe_hosts()

    def face_ask(
        self, text: str, *, include_opt_in: bool = False, workers: list[str] | None = None
    ) -> dict[str, Any]:
        from drone.multi_face import MultiFace

        return MultiFace(self.root).ask(
            text, include_opt_in=include_opt_in, workers=workers
        )

    def collect_cowork(self, max_n: int = 20) -> dict[str, Any]:
        from drone.grok_handoff import GrokDroneHandoff

        return GrokDroneHandoff(self.root).collect(max_n=max_n)

    def process_inbox(self, max_n: int = 10) -> dict[str, Any]:
        """Universal file-drop: read JSON goals from inbox, run tasks, move to outbox."""
        inbox = self.dirs["inbox"]
        processed = []
        files = sorted(inbox.glob("*.json"))[:max_n]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                processed.append({"file": str(f), "ok": False, "error": str(e)})
                continue
            goal = (data.get("goal") or data.get("content") or "").strip()
            if not goal:
                processed.append({"file": str(f), "ok": False, "error": "no goal"})
                continue
            lane = data.get("lane") or self.cfg.get("lane_default") or "fast"
            report = self.run_task(
                goal,
                lane=lane,
                controller=data.get("controller"),
                lm_assist=data.get("lm_assist"),
                domain=data.get("domain") or "build",
            )
            # archive inbox file
            done = self.dirs["outbox"] / f"inbox_done_{f.name}"
            try:
                f.replace(done)
            except OSError:
                pass
            processed.append(
                {
                    "file": str(f),
                    "ok": report.get("status") == "GREEN",
                    "status": report.get("status"),
                    "report_path": report.get("report_path") or report.get("seal_path"),
                }
            )
        return {
            "ok": True,
            "processed": len(processed),
            "results": processed,
            "false_green": 0,
            "utc": _utc(),
        }
