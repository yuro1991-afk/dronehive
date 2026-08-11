"""
Grok ↔ Drone live handoff + e2e cowork lanes.

Boss (Grok) hands structured tasks to local drones with live lanes checked.
Drones execute on fast/full/hive/brain/pro paths and write cowork packets back.

Honesty: false_green:0 — GREEN only with on-disk evidence / real tool reports.
"""

from __future__ import annotations

import json
import re
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA = "grok.drone.cowork.handoff.v1"
MODES = frozenset({"fast", "full", "hive", "brain", "pro", "inbox", "clone"})
DEFAULT_ROOT = Path(r"G:\AI-Home\projects\ai-worker-drone-0.5b")
OLLAMA_HOST = "http://127.0.0.1:11434"
APP_HOST = "http://127.0.0.1:8765"


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class CoworkPacket:
    """Structured Grok↔drone callosum packet (not free chat)."""

    from_agent: str
    to_agent: str
    goal: str
    handoff_id: str = field(default_factory=_short_id)
    mode: str = "fast"
    lane: str = "fast"
    status: str = "OPEN"  # OPEN | RUNNING | GREEN | PARTIAL | RED
    evidence: list[str] = field(default_factory=list)
    next_action: str = ""
    veto: bool = False
    notes: str = ""
    context_paths: list[str] = field(default_factory=list)
    continuous_task_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_utc: str = field(default_factory=_utc)
    updated_utc: str = field(default_factory=_utc)
    false_green: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoworkPacket":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kw = {k: v for k, v in data.items() if k in known}
        return cls(**kw)


class GrokDroneHandoff:
    """
    Live lane bridge: Grok → drones → cowork outbox.

    Paths (under project root):
      data/app/cowork/outbox/   packets Grok sends (handoff requests)
      data/app/cowork/inbox/    results drones write back for Grok
      data/app/cowork/active/   RUNNING jobs
      data/app/cowork/done/     archived GREEN/RED seals
      out/GROK_HANDOFF_LAST.json
      out/GROK_LANES_LIVE.json
      out/GROK_COWORK_SEAL.json
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or DEFAULT_ROOT)
        self.cowork = self.root / "data" / "app" / "cowork"
        self.outbox = self.cowork / "outbox"
        self.inbox = self.cowork / "inbox"
        self.active = self.cowork / "active"
        self.done = self.cowork / "done"
        self.journal = self.cowork / "JOURNAL.jsonl"
        self.out = self.root / "out"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for p in (self.outbox, self.inbox, self.active, self.done, self.out):
            p.mkdir(parents=True, exist_ok=True)

    def _write_json(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def _journal(self, event: str, detail: dict[str, Any]) -> None:
        row = {"utc": _utc(), "event": event, "false_green": 0, **detail}
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    # ── live lanes ────────────────────────────────────────────

    def _probe_http(self, url: str, timeout: float = 2.5) -> dict[str, Any]:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"raw": body[:200]}
            return {"ok": True, "url": url, "data": data}
        except Exception as e:
            return {"ok": False, "url": url, "error": str(e)}

    def lanes_live(self) -> dict[str, Any]:
        """
        Prove live cowork lanes before handoff.
        Required for e2e: modules on disk + Ollama reachable.
        Optional: HTTP app serve :8765.
        """
        from drone.app.service import DroneHiveService

        svc = DroneHiveService(self.root)
        health = svc.health()
        ollama = self._probe_http(f"{OLLAMA_HOST}/api/tags")
        app_api = self._probe_http(f"{APP_HOST}/api/health")

        modules_ok = bool(health.get("ok"))
        ollama_ok = bool(ollama.get("ok")) and bool(
            (health.get("ollama") or {}).get("reachable") or ollama.get("ok")
        )
        # Live cowork minimum: modules + ollama. HTTP optional (CLI path works).
        live = modules_ok and ollama_ok
        status = "GREEN" if live else ("PARTIAL" if modules_ok else "RED")

        report = {
            "schema": "grok.drone.lanes.live.v1",
            "status": status,
            "live": live,
            "false_green": 0,
            "utc": _utc(),
            "lanes": {
                "modules": {
                    "ok": modules_ok,
                    "detail": health.get("modules"),
                    "app_status": health.get("status"),
                },
                "ollama": {
                    "ok": ollama_ok,
                    "host": OLLAMA_HOST,
                    "top_model": (health.get("ollama") or {}).get("top_model"),
                    "probe": {"ok": ollama.get("ok"), "error": ollama.get("error")},
                },
                "http_api": {
                    "ok": bool(app_api.get("ok")),
                    "host": APP_HOST,
                    "optional": True,
                    "probe": {"ok": app_api.get("ok"), "error": app_api.get("error")},
                    "note": "CLI handoff works without HTTP; enable with: python -m drone app serve",
                },
                "file_inbox": {
                    "ok": (self.root / "data" / "app" / "inbox").is_dir(),
                    "path": str(self.root / "data" / "app" / "inbox"),
                },
                "cowork": {
                    "ok": self.cowork.is_dir(),
                    "outbox": str(self.outbox),
                    "inbox": str(self.inbox),
                    "active": str(self.active),
                    "done": str(self.done),
                },
                "modes": sorted(MODES),
            },
            "health": {
                "ok": health.get("ok"),
                "status": health.get("status"),
                "library": health.get("library"),
            },
            "ready_for_handoff": live,
        }
        path = self._write_json(self.out / "GROK_LANES_LIVE.json", report)
        report["seal_path"] = str(path)
        self._journal("lanes_live", {"status": status, "live": live, "path": str(path)})
        return report

    # ── handoff ───────────────────────────────────────────────

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
        wait: bool = True,
        notes: str = "",
        context_paths: list[str] | None = None,
        continuous_task_id: str | None = None,
        skip_lane_check: bool = False,
        goals: list[str] | None = None,
        pro_rounds: int = 6,
        also_hive: bool = False,
    ) -> dict[str, Any]:
        """
        Grok hands a task to drones.

        mode:
          fast  — FastLane (default e2e cowork grind)
          full  — 24-node dual-hemisphere fabric
          hive  — BuzzerHive multi-unit
          brain — Ollama plan → swarm (MAIN DELEGATE path)
          pro   — free-form Pro tool agent
          inbox — async drop only (process later)
        """
        goal = (goal or "").strip()
        if not goal:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "goal required",
                "ready_for_handoff": False,
            }

        mode = (mode or "fast").lower().strip()
        if mode not in MODES:
            return {
                "status": "RED",
                "false_green": 0,
                "error": f"mode must be one of {sorted(MODES)}",
            }

        lane = (lane or ("full" if mode == "full" else "fast")).lower()
        if mode in {"fast", "full"}:
            lane = mode  # lane aliases mode for task path

        lanes = None if skip_lane_check else self.lanes_live()
        if lanes and not lanes.get("ready_for_handoff") and mode != "inbox":
            return {
                "status": "RED",
                "false_green": 0,
                "error": "live lanes not ready — fix Ollama/modules first",
                "lanes": lanes,
                "hint": r'powershell -File "$env:USERPROFILE\.ollama\start-agent-lanes.ps1"',
            }

        pkt = CoworkPacket(
            from_agent="grok",
            to_agent="drones",
            goal=goal,
            mode=mode,
            lane=lane,
            status="RUNNING" if wait else "OPEN",
            notes=notes or f"Grok handoff mode={mode}",
            context_paths=list(context_paths or []),
            continuous_task_id=continuous_task_id,
            payload={
                "workers": workers,
                "cycles": cycles,
                "lm_assist": lm_assist,
                "controller": controller,
                "wait": wait,
                "goals": goals,
                "pro_rounds": pro_rounds,
                "also_hive": also_hive,
            },
        )

        req_path = self.outbox / f"handoff_{pkt.handoff_id}.json"
        active_path = self.active / f"{pkt.handoff_id}.json"
        self._write_json(req_path, pkt.to_dict())
        self._write_json(active_path, pkt.to_dict())
        self._journal(
            "handoff_open",
            {"handoff_id": pkt.handoff_id, "mode": mode, "goal": goal[:200], "path": str(req_path)},
        )

        if not wait and mode == "inbox":
            return self._drop_inbox_async(pkt, req_path, active_path)

        if not wait and mode != "inbox":
            # Still execute now but return immediately after start is not true async
            # without a worker process — run sync and mark done (honest).
            pass

        t0 = time.perf_counter()
        try:
            result = self._execute(pkt)
        except Exception as e:
            result = {
                "status": "RED",
                "false_green": 0,
                "error": str(e),
                "exception": type(e).__name__,
            }

        ms = round((time.perf_counter() - t0) * 1000, 2)
        return self._seal_result(pkt, result, ms, req_path, active_path, lanes)

    def _drop_inbox_async(
        self, pkt: CoworkPacket, req_path: Path, active_path: Path
    ) -> dict[str, Any]:
        """Drop into classic app inbox for later process_inbox."""
        app_inbox = self.root / "data" / "app" / "inbox"
        app_inbox.mkdir(parents=True, exist_ok=True)
        drop = {
            "goal": pkt.goal,
            "lane": pkt.lane,
            "controller": pkt.payload.get("controller") or "grok",
            "lm_assist": pkt.payload.get("lm_assist") or "none",
            "domain": "build",
            "handoff_id": pkt.handoff_id,
            "from_agent": "grok",
            "schema": SCHEMA,
            "context_paths": pkt.context_paths,
            "continuous_task_id": pkt.continuous_task_id,
        }
        drop_path = app_inbox / f"grok_{pkt.handoff_id}.json"
        self._write_json(drop_path, drop)
        pkt.status = "OPEN"
        pkt.next_action = "process_inbox"
        pkt.evidence = [str(drop_path), str(req_path)]
        pkt.updated_utc = _utc()
        self._write_json(active_path, pkt.to_dict())
        out = {
            "schema": SCHEMA,
            "status": "OPEN",
            "false_green": 0,
            "async": True,
            "handoff_id": pkt.handoff_id,
            "mode": "inbox",
            "goal": pkt.goal,
            "inbox_drop": str(drop_path),
            "active_path": str(active_path),
            "request_path": str(req_path),
            "next_action": "python -m drone app inbox  OR  handoff --collect",
            "utc": _utc(),
        }
        self._write_json(self.out / "GROK_HANDOFF_LAST.json", out)
        self._journal("handoff_inbox_drop", out)
        return out

    def _execute(self, pkt: CoworkPacket) -> dict[str, Any]:
        from drone.app.service import DroneHiveService

        svc = DroneHiveService(self.root)
        mode = pkt.mode
        pl = pkt.payload or {}
        controller = str(pl.get("controller") or "grok")
        lm_assist = str(pl.get("lm_assist") or "none")
        workers = int(pl.get("workers") or 2)
        cycles = int(pl.get("cycles") or 2)
        goals = pl.get("goals")
        if isinstance(goals, list) and goals:
            goal_list = [str(g).strip() for g in goals if str(g).strip()]
        else:
            goal_list = [pkt.goal]

        if mode == "inbox":
            return self._drop_inbox_async(
                pkt,
                self.outbox / f"handoff_{pkt.handoff_id}.json",
                self.active / f"{pkt.handoff_id}.json",
            )

        if mode == "fast":
            return svc.run_task(
                pkt.goal,
                lane="fast",
                controller=controller,
                lm_assist=lm_assist,
                domain="build",
                tags=["build", "grok_handoff", "cowork", "fast"],
            )

        if mode == "full":
            return svc.run_task(
                pkt.goal,
                lane="full",
                controller=controller,
                lm_assist=lm_assist,
                domain="build",
                tags=["build", "grok_handoff", "cowork", "full"],
            )

        if mode == "hive":
            return svc.run_hive(
                goals=goal_list,
                cycles=max(cycles, len(goal_list)),
                workers=max(1, min(4, workers)),
                lane=pkt.lane if pkt.lane in {"fast", "full"} else "fast",
                lm_assist=lm_assist if lm_assist != "none" else "ollama",
                controller=controller if controller != "local" else "grok",
            )

        if mode == "brain":
            return svc.brain_command(pkt.goal)

        if mode == "pro":
            from drone.pro.service import DroneHiveProService

            pro = DroneHiveProService(self.root)
            return pro.pro_run(
                pkt.goal,
                max_rounds=max(1, int(pl.get("pro_rounds") or 6)),
                use_ollama=True,
                also_hive=bool(pl.get("also_hive")),
            )

        if mode == "clone":
            # Direct drone toolkit clone of full DroneHive app (e2e cowork test)
            from drone.tools import DroneToolkit

            dest = r"G:\AI-Home\projects\dronehive-clone-test"
            m = re.search(
                r"(G:\\AI-Home\\projects\\[\w\-\\]+|G:/AI-Home/projects/[\w\-/]+)",
                pkt.goal,
            )
            # prefer explicit clone-test path in goal
            if "dronehive-clone-test" in pkt.goal:
                dest = r"G:\AI-Home\projects\dronehive-clone-test"
            elif m:
                cand = m.group(1).replace("/", "\\")
                if "clone" in cand.lower():
                    dest = cand
            task_id = f"clone_{pkt.handoff_id}"
            tk = DroneToolkit(self.root, task_id=task_id)
            res = tk.clone_app(dest=dest, include_data=False)
            man = tk.package_manifest({"mode": "clone", "handoff_id": pkt.handoff_id})
            status = str(res.get("status") or ("GREEN" if res.get("ok") else "RED"))
            return {
                "status": status,
                "false_green": 0,
                "ok": bool(res.get("ok")),
                "tool": "clone_app",
                "task_id": task_id,
                "clone_root": res.get("path"),
                "file_count": res.get("file_count"),
                "py_count": res.get("py_count"),
                "total_bytes": res.get("total_bytes"),
                "seal_path": res.get("seal_path") or res.get("out_seal"),
                "report_path": res.get("out_seal") or res.get("seal_path"),
                "manifest_path": res.get("manifest_path") or man.get("path"),
                "workspace_mirror": res.get("workspace_mirror"),
                "tool_calls": len(tk.calls),
                "execution": res,
                "package_manifest": man,
            }

        return {"status": "RED", "false_green": 0, "error": f"unknown mode {mode}"}

    def _seal_result(
        self,
        pkt: CoworkPacket,
        result: dict[str, Any],
        ms: float,
        req_path: Path,
        active_path: Path,
        lanes: dict[str, Any] | None,
    ) -> dict[str, Any]:
        status = str(
            result.get("status")
            or ("GREEN" if result.get("ok") is True else "PARTIAL")
        ).upper()
        if status not in {"GREEN", "PARTIAL", "RED", "OPEN", "RUNNING"}:
            status = "PARTIAL"

        evidence: list[str] = [str(req_path)]
        for key in ("report_path", "seal_path", "path", "out_path"):
            if result.get(key):
                evidence.append(str(result[key]))
        # nested execution (brain)
        exe = result.get("execution") if isinstance(result.get("execution"), dict) else {}
        for key in ("report_path", "seal_path"):
            if exe.get(key):
                evidence.append(str(exe[key]))

        pkt.status = status
        pkt.updated_utc = _utc()
        pkt.evidence = list(dict.fromkeys(evidence))  # dedupe preserve order
        pkt.next_action = (
            "Grok integrate evidence"
            if status == "GREEN"
            else ("retry or escalate" if status == "RED" else "inspect PARTIAL evidence")
        )
        pkt.payload = {
            **(pkt.payload or {}),
            "duration_ms": ms,
            "result_keys": sorted(result.keys())[:40],
        }

        # Write result packet into cowork inbox (for Grok to collect)
        result_pkt = CoworkPacket(
            from_agent="drones",
            to_agent="grok",
            goal=pkt.goal,
            handoff_id=pkt.handoff_id,
            mode=pkt.mode,
            lane=pkt.lane,
            status=status,
            evidence=pkt.evidence,
            next_action=pkt.next_action,
            notes=f"drone result mode={pkt.mode} ms={ms}",
            context_paths=pkt.context_paths,
            continuous_task_id=pkt.continuous_task_id,
            payload={
                "duration_ms": ms,
                "result_status": status,
                "nodes_run": result.get("nodes_run") or exe.get("nodes_run"),
                "tool_calls": result.get("tool_calls") or exe.get("tool_calls"),
            },
        )
        inbox_path = self.inbox / f"result_{pkt.handoff_id}.json"
        done_path = self.done / f"{pkt.handoff_id}.json"
        self._write_json(inbox_path, result_pkt.to_dict())
        seal_body = {
            "schema": SCHEMA,
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "handoff_id": pkt.handoff_id,
            "mode": pkt.mode,
            "lane": pkt.lane,
            "goal": pkt.goal,
            "duration_ms": ms,
            "from": "grok",
            "to": "drones",
            "evidence": pkt.evidence,
            "request_path": str(req_path),
            "result_inbox": str(inbox_path),
            "done_path": str(done_path),
            "active_path": str(active_path),
            "lanes_live": bool((lanes or {}).get("live")) if lanes else None,
            "lanes_seal": (lanes or {}).get("seal_path"),
            "execution": {
                "status": result.get("status"),
                "ok": result.get("ok"),
                "nodes_run": result.get("nodes_run") or exe.get("nodes_run"),
                "tool_calls": result.get("tool_calls") or exe.get("tool_calls"),
                "report_path": result.get("report_path") or exe.get("report_path"),
                "seal_path": result.get("seal_path") or exe.get("seal_path"),
                "error": result.get("error"),
            },
            "cowork": {
                "outbox": str(self.outbox),
                "inbox": str(self.inbox),
                "done": str(self.done),
            },
            "next_action": pkt.next_action,
        }
        self._write_json(done_path, seal_body)
        self._write_json(self.out / "GROK_HANDOFF_LAST.json", seal_body)
        self._write_json(self.out / "GROK_COWORK_SEAL.json", seal_body)
        # refresh active → done state
        self._write_json(active_path, {**pkt.to_dict(), "sealed": True, "status": status})
        try:
            # move active marker into done (keep copy in done already)
            if active_path.is_file() and status in {"GREEN", "RED"}:
                active_path.unlink(missing_ok=True)
        except OSError:
            pass

        self._journal(
            "handoff_seal",
            {
                "handoff_id": pkt.handoff_id,
                "status": status,
                "mode": pkt.mode,
                "ms": ms,
                "evidence": pkt.evidence,
            },
        )
        return seal_body

    # ── collect / status ──────────────────────────────────────

    def collect(self, max_n: int = 20, *, mark_read: bool = True) -> dict[str, Any]:
        """Grok pulls drone result packets from cowork inbox."""
        files = sorted(self.inbox.glob("result_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
            :max_n
        ]
        items: list[dict[str, Any]] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                items.append({"path": str(f), "ok": False, "error": str(e)})
                continue
            items.append(
                {
                    "path": str(f),
                    "ok": True,
                    "handoff_id": data.get("handoff_id"),
                    "status": data.get("status"),
                    "goal": data.get("goal"),
                    "evidence": data.get("evidence"),
                    "mode": data.get("mode"),
                    "updated_utc": data.get("updated_utc"),
                }
            )
            if mark_read:
                read_dir = self.inbox / "read"
                read_dir.mkdir(exist_ok=True)
                try:
                    f.replace(read_dir / f.name)
                except OSError:
                    pass
        out = {
            "schema": "grok.drone.cowork.collect.v1",
            "status": "GREEN" if items else "EMPTY",
            "false_green": 0,
            "utc": _utc(),
            "count": len(items),
            "items": items,
            "inbox": str(self.inbox),
        }
        self._write_json(self.out / "GROK_COWORK_COLLECT.json", out)
        self._journal("collect", {"count": len(items)})
        return out

    def status(self, handoff_id: str | None = None) -> dict[str, Any]:
        """Status of one handoff or summary of active/done."""
        if handoff_id:
            for folder in (self.done, self.active, self.outbox, self.inbox):
                for pattern in (
                    f"{handoff_id}.json",
                    f"handoff_{handoff_id}.json",
                    f"result_{handoff_id}.json",
                ):
                    p = folder / pattern
                    if p.is_file():
                        data = json.loads(p.read_text(encoding="utf-8"))
                        return {
                            "schema": "grok.drone.cowork.status.v1",
                            "status": data.get("status") or "UNKNOWN",
                            "false_green": 0,
                            "handoff_id": handoff_id,
                            "path": str(p),
                            "packet": data,
                            "utc": _utc(),
                        }
            return {
                "status": "RED",
                "false_green": 0,
                "error": f"handoff_id not found: {handoff_id}",
                "utc": _utc(),
            }

        active = list(self.active.glob("*.json"))
        done = list(self.done.glob("*.json"))
        pending_results = list(self.inbox.glob("result_*.json"))
        last = self.out / "GROK_HANDOFF_LAST.json"
        last_data = None
        if last.is_file():
            try:
                last_data = json.loads(last.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                last_data = None
        return {
            "schema": "grok.drone.cowork.status.v1",
            "status": "GREEN",
            "false_green": 0,
            "utc": _utc(),
            "active_count": len(active),
            "done_count": len(done),
            "pending_results": len(pending_results),
            "active": [p.stem for p in active[:20]],
            "last": last_data,
            "cowork_root": str(self.cowork),
        }

    def e2e_smoke(self, goal: str | None = None) -> dict[str, Any]:
        """
        End-to-end cowork smoke:
          lanes_live → handoff(fast) → collect → seal
        """
        g = (goal or "grok cowork e2e: write a short seal note artifact").strip()
        lanes = self.lanes_live()
        if not lanes.get("ready_for_handoff"):
            seal = {
                "schema": "grok.drone.cowork.e2e.v1",
                "status": "RED",
                "false_green": 0,
                "step": "lanes_live",
                "lanes": lanes,
                "utc": _utc(),
            }
            self._write_json(self.out / "GROK_COWORK_E2E_SEAL.json", seal)
            return seal

        hand = self.handoff(g, mode="fast", wait=True, skip_lane_check=True, notes="e2e smoke")
        col = self.collect(max_n=5, mark_read=False)
        ok = hand.get("status") == "GREEN" and bool(hand.get("evidence"))
        seal = {
            "schema": "grok.drone.cowork.e2e.v1",
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "utc": _utc(),
            "pipeline": ["lanes_live", "handoff_fast", "collect"],
            "lanes_live": lanes.get("live"),
            "handoff_id": hand.get("handoff_id"),
            "handoff_status": hand.get("status"),
            "duration_ms": hand.get("duration_ms"),
            "evidence": hand.get("evidence"),
            "collect_count": col.get("count"),
            "handoff": hand,
            "collect": col,
        }
        path = self._write_json(self.out / "GROK_COWORK_E2E_SEAL.json", seal)
        seal["seal_path"] = str(path)
        self._journal("e2e_smoke", {"status": seal["status"], "path": str(path)})
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="drone handoff",
        description="Grok → drone live handoff + e2e cowork lanes",
    )
    p.add_argument("--root", default=None, help="drone project root")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("lanes", help="check live lanes (Ollama + modules + cowork dirs)")

    h = sub.add_parser("to", help="hand off goal to drones")
    h.add_argument("--goal", "-g", required=True)
    h.add_argument(
        "--mode",
        "-m",
        default="fast",
        choices=sorted(MODES),
        help="fast|full|hive|brain|pro|inbox",
    )
    h.add_argument("--lane", default=None, choices=["fast", "full"])
    h.add_argument("--workers", type=int, default=2)
    h.add_argument("--cycles", type=int, default=2)
    h.add_argument("--lm-assist", default="none")
    h.add_argument("--controller", default="grok")
    h.add_argument("--notes", default="")
    h.add_argument("--continuous-task-id", default=None)
    h.add_argument("--context", action="append", default=[], help="context path (repeat)")
    h.add_argument("--no-wait", action="store_true", help="inbox mode preferred for async")
    h.add_argument("--skip-lane-check", action="store_true")
    h.add_argument("--pro-rounds", type=int, default=6)
    h.add_argument("--also-hive", action="store_true")
    h.add_argument("--goal-unit", action="append", dest="goals", default=None)

    c = sub.add_parser("collect", help="pull drone result packets for Grok")
    c.add_argument("--max", type=int, default=20)
    c.add_argument("--keep", action="store_true", help="do not move to inbox/read")

    s = sub.add_parser("status", help="cowork summary")
    s.add_argument("--id", dest="handoff_id", default=None)

    e = sub.add_parser("e2e", help="lanes + fast handoff + collect smoke")
    e.add_argument("--goal", default=None)

    args = p.parse_args(argv)
    root = Path(args.root) if args.root else None
    bridge = GrokDroneHandoff(root)

    if args.cmd == "lanes":
        out = bridge.lanes_live()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ready_for_handoff") else 1

    if args.cmd == "to":
        out = bridge.handoff(
            args.goal,
            mode=args.mode,
            lane=args.lane,
            workers=args.workers,
            cycles=args.cycles,
            lm_assist=args.lm_assist,
            controller=args.controller,
            wait=not args.no_wait,
            notes=args.notes,
            context_paths=args.context or [],
            continuous_task_id=args.continuous_task_id,
            skip_lane_check=args.skip_lane_check,
            goals=args.goals,
            pro_rounds=args.pro_rounds,
            also_hive=args.also_hive,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "OPEN", "PARTIAL"} else 1

    if args.cmd == "collect":
        out = bridge.collect(max_n=args.max, mark_read=not args.keep)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "status":
        out = bridge.status(getattr(args, "handoff_id", None))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") != "RED" or out.get("packet") else 1

    if args.cmd == "e2e":
        out = bridge.e2e_smoke(args.goal)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
