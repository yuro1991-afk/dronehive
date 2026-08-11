"""
DroneHive Future Seer — multi-model speculative text future-seer.

Capabilities:
  - Read partial user text as it types (typeahead buffer)
  - Speculate intent + draft answers + task plans before Enter
  - Keep Ollama lanes hot (tiny + coder warm)
  - Tap Jane Super Cell helpers (muscle_dispatch / lanes) + Everest health
  - On commit: handoff to drones or helper probe/draft

Honesty (12GB 3060):
  - Speculative branches prefer tiny models first; at most one full draft
  - Drafts are PREVIEW until commit — not false greens
  - false_green: 0

CLI: python -m drone seer hot|type|commit|status|tui|seal
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _short() -> str:
    return uuid.uuid4().hex[:10]


class FutureSeer:
    """Top-tier multi-model speculative typeahead seer."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "future_seer.json"
        self.out = self.root / "out"
        self.state_dir = self.root / "data" / "seer"
        self.drafts_dir = self.state_dir / "drafts"
        self.journal = self.state_dir / "JOURNAL.jsonl"
        self.state_path = self.out / "FUTURE_SEER_STATE.json"
        self.hot_path = self.out / "FUTURE_SEER_HOT.json"
        self.seal_path = self.out / "FUTURE_SEER_SEAL.json"
        self.last_spec_path = self.out / "FUTURE_SEER_LAST_SPEC.json"
        self.cfg = self._load_cfg()
        for d in (self.out, self.state_dir, self.drafts_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._buffer = ""
        self._last_spec_key = ""
        self._last_spec: dict[str, Any] | None = None
        self._hot_stop = threading.Event()
        self._hot_thread: threading.Thread | None = None
        self._debounce_timer: threading.Timer | None = None
        self._on_spec: Callable[[dict[str, Any]], None] | None = None

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {}

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _journal(self, event: str, detail: dict[str, Any]) -> None:
        row = {"utc": _utc(), "event": event, "false_green": 0, **detail}
        with self._lock:
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _http_json(
        self, method: str, url: str, body: dict | None = None, timeout: float = 90
    ) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    # ── Ollama ────────────────────────────────────────────────

    def ollama_up(self) -> bool:
        try:
            self._http_json("GET", f"{ollama_host()}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def list_loaded(self) -> list[str]:
        try:
            data = self._http_json("GET", f"{ollama_host()}/api/ps", timeout=4)
            return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        except Exception:
            return []

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        num_predict: int = 128,
        temperature: float = 0.2,
        timeout_s: float = 90,
        keep_alive: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        ka = keep_alive or (self.cfg.get("hot_lanes") or {}).get("keep_alive") or "45m"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            body = {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": ka,
                "options": {"num_predict": num_predict, "temperature": temperature},
            }
            data = self._http_json(
                "POST", f"{ollama_host()}/api/chat", body, timeout=timeout_s
            )
            text = str((data.get("message") or {}).get("content", "")).strip()
            return {
                "ok": bool(text),
                "model": model,
                "text": text,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "model": model,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }

    # ── Hot lanes ─────────────────────────────────────────────

    def keep_hot(self, *, force: bool = False) -> dict[str, Any]:
        """Warm configured models so typeahead speculation is low-latency."""
        hot_cfg = self.cfg.get("hot_lanes") or {}
        if not hot_cfg.get("enabled", True) and not force:
            return {"status": "SKIP", "reason": "hot_lanes disabled", "false_green": 0}

        models = list(hot_cfg.get("warm_models") or ["llama3.2:3b"])
        npred = int(hot_cfg.get("num_predict_warm") or 4)
        results = []
        for m in models:
            # Prefer tiny first; coder 7b only if VRAM allows (after tinies)
            r = self.generate(
                m,
                "Reply OK",
                num_predict=npred,
                temperature=0,
                timeout_s=120,
            )
            results.append(
                {
                    "model": m,
                    "ok": r.get("ok"),
                    "ms": r.get("ms"),
                    "error": r.get("error"),
                }
            )

        # Jane muscle lanes probe (non-blocking soft)
        jane = self._jane_lanes()
        ever = self._everest_health()

        report = {
            "schema": "drone.future_seer.hot.v1",
            "status": "GREEN" if any(x.get("ok") for x in results) else "RED",
            "false_green": 0,
            "utc": _utc(),
            "endpoint": ollama_host(),
            "warmed": results,
            "loaded": self.list_loaded(),
            "jane_lanes": jane,
            "everest": ever,
            "path": str(self.hot_path),
        }
        self._write(self.hot_path, report)
        self._journal("keep_hot", {"status": report["status"], "models": models})
        return report

    def start_hot_loop(self) -> dict[str, Any]:
        """Background keep-alive pings."""
        if self._hot_thread and self._hot_thread.is_alive():
            return {"status": "RUNNING", "false_green": 0, "note": "already running"}
        self._hot_stop.clear()
        interval = float((self.cfg.get("hot_lanes") or {}).get("ping_interval_s") or 90)

        def _loop() -> None:
            while not self._hot_stop.is_set():
                try:
                    self.keep_hot()
                except Exception:
                    pass
                self._hot_stop.wait(interval)

        self._hot_thread = threading.Thread(target=_loop, name="seer-hot", daemon=True)
        self._hot_thread.start()
        return {
            "status": "STARTED",
            "false_green": 0,
            "interval_s": interval,
            "utc": _utc(),
        }

    def stop_hot_loop(self) -> dict[str, Any]:
        self._hot_stop.set()
        return {"status": "STOPPED", "false_green": 0, "utc": _utc()}

    # ── Jane / Ever taps ──────────────────────────────────────

    def _py(self) -> str:
        return os.environ.get(
            "DRONE_PYTHON",
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs",
                "Python",
                "Python312",
                "python.exe",
            ),
        )

    def _jane_lanes(self) -> dict[str, Any]:
        jane = self.cfg.get("jane") or {}
        md = Path(jane.get("muscle_dispatch") or "")
        if not md.is_file():
            return {"ok": False, "error": "muscle_dispatch missing", "path": str(md)}
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(jane.get("pythonpath_ai_center") or r"G:\AI-Center")
            p = subprocess.run(
                [self._py(), str(md), "--lanes"],
                capture_output=True,
                text=True,
                timeout=45,
                env=env,
                cwd=str(md.parent),
            )
            out = (p.stdout or "")[-2000:]
            ok = p.returncode == 0
            # append soft registry ping
            reg = Path(jane.get("live_registry") or "")
            if reg.parent.is_dir():
                try:
                    with reg.open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "utc": _utc(),
                                    "source": "future_seer",
                                    "event": "lanes_probe",
                                    "ok": ok,
                                    "false_green": 0,
                                }
                            )
                            + "\n"
                        )
                except OSError:
                    pass
            return {
                "ok": ok,
                "returncode": p.returncode,
                "stdout_tail": out,
                "stderr_tail": (p.stderr or "")[-500:],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _everest_health(self) -> dict[str, Any]:
        jane = self.cfg.get("jane") or {}
        cli = Path(jane.get("everest_cli") or "")
        if not cli.is_file():
            return {"ok": False, "error": "everest cli missing", "optional": True}
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(jane.get("pythonpath_ai_center") or r"G:\AI-Center")
            p = subprocess.run(
                [self._py(), str(cli), "health"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            try:
                data = json.loads(p.stdout or "{}")
            except json.JSONDecodeError:
                data = {"raw": (p.stdout or "")[:500]}
            return {
                "ok": bool(data.get("ok") or p.returncode == 0),
                "data": data,
                "returncode": p.returncode,
                "optional": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "optional": True}

    def summon_helper(
        self, expert: str, goal: str, *, run_id: str | None = None
    ) -> dict[str, Any]:
        """Tap Super Cell muscle helper (probe/draft/forge/seal)."""
        jane = self.cfg.get("jane") or {}
        md = Path(jane.get("muscle_dispatch") or "")
        if not md.is_file():
            return {"status": "RED", "false_green": 0, "error": "muscle_dispatch missing"}
        rid = run_id or f"seer-{time.strftime('%Y%m%d-%H%M%S')}"
        expert = (expert or "probe").lower().strip()
        if expert not in {"probe", "draft", "forge", "seal"}:
            expert = "probe"
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(jane.get("pythonpath_ai_center") or r"G:\AI-Center")
            p = subprocess.run(
                [
                    self._py(),
                    str(md),
                    "--expert",
                    expert,
                    "--goal",
                    goal[:800],
                    "--run-id",
                    rid,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
                cwd=str(md.parent),
            )
            outbox = Path(r"G:\AI-Center\agents\super-cell-4\parallel-runs") / rid
            seal = {
                "status": "GREEN" if p.returncode == 0 else "PARTIAL",
                "false_green": 0,
                "expert": expert,
                "run_id": rid,
                "returncode": p.returncode,
                "outbox": str(outbox) if outbox.is_dir() else None,
                "stdout_tail": (p.stdout or "")[-1500:],
                "stderr_tail": (p.stderr or "")[-800:],
                "utc": _utc(),
            }
            path = self._write(self.out / f"SEER_HELPER_{expert}_{rid}.json", seal)
            seal["path"] = str(path)
            self._journal("summon_helper", seal)
            return seal
        except Exception as e:
            return {"status": "RED", "false_green": 0, "error": str(e), "expert": expert}

    # ── Typeahead buffer ──────────────────────────────────────

    def set_buffer(self, text: str, *, speculate: bool = True) -> dict[str, Any]:
        """Replace typeahead buffer (full partial line)."""
        ta = self.cfg.get("typeahead") or {}
        max_len = int(ta.get("max_partial_len") or 400)
        self._buffer = (text or "")[:max_len]
        self._save_state()
        if speculate:
            return self._schedule_speculate()
        return {"buffer": self._buffer, "scheduled": False, "false_green": 0}

    def type_char(self, ch: str, *, speculate: bool = True) -> dict[str, Any]:
        """Append one character (or paste chunk)."""
        ta = self.cfg.get("typeahead") or {}
        max_len = int(ta.get("max_partial_len") or 400)
        self._buffer = (self._buffer + (ch or ""))[:max_len]
        self._save_state()
        if not speculate:
            return {"buffer": self._buffer, "scheduled": False}
        n = int(ta.get("speculate_every_n_chars") or 4)
        if len(self._buffer) >= int(ta.get("min_chars") or 8) and (
            len(self._buffer) % n == 0 or ch in " ?.!\n"
        ):
            return self._schedule_speculate()
        return {"buffer": self._buffer, "scheduled": False, "false_green": 0}

    def _schedule_speculate(self) -> dict[str, Any]:
        ta = self.cfg.get("typeahead") or {}
        debounce = float(ta.get("debounce_ms") or 280) / 1000.0
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()

            def _fire() -> None:
                try:
                    spec = self.speculate(self._buffer)
                    if self._on_spec:
                        self._on_spec(spec)
                except Exception:
                    pass

            self._debounce_timer = threading.Timer(debounce, _fire)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()
        return {
            "buffer": self._buffer,
            "scheduled": True,
            "debounce_ms": debounce * 1000,
            "false_green": 0,
        }

    def _save_state(self) -> None:
        self._write(
            self.state_path,
            {
                "utc": _utc(),
                "buffer": self._buffer,
                "buffer_len": len(self._buffer),
                "last_spec_key": self._last_spec_key,
                "loaded": self.list_loaded(),
                "false_green": 0,
            },
        )

    # ── Speculation (future seer core) ────────────────────────

    def speculate(self, partial: str | None = None) -> dict[str, Any]:
        """
        Multi-branch speculative preview for partial text.
        Branches: intent (tiny) → task_plan (tiny/smarts) → optional full draft.
        """
        text = (partial if partial is not None else self._buffer).strip()
        ta = self.cfg.get("typeahead") or {}
        min_chars = int(ta.get("min_chars") or 8)
        if len(text) < min_chars:
            return {
                "status": "WAIT",
                "false_green": 0,
                "reason": f"need>={min_chars} chars",
                "partial": text,
            }

        # de-dupe identical partials
        key = re.sub(r"\s+", " ", text.lower())[:200]
        if key == self._last_spec_key and self._last_spec:
            out = dict(self._last_spec)
            out["cached"] = True
            return out

        if not self.ollama_up():
            return {
                "status": "RED",
                "false_green": 0,
                "error": "ollama down",
                "hint": r'~\.ollama\start-agent-lanes.ps1',
            }

        spec_cfg = self.cfg.get("speculation") or {}
        intent_m = str(spec_cfg.get("intent_model") or "llama3.2:3b")
        draft_m = str(spec_cfg.get("draft_model") or "llama3.1:8b")
        code_m = str(spec_cfg.get("code_model") or "qwen2.5-coder:7b")
        smarts_m = str(spec_cfg.get("smarts_model") or "ai-smarts:latest")

        sid = f"spec_{_short()}"
        t0 = time.perf_counter()

        # --- Branch 1: INTENT (always tiny, hot) ---
        from drone.ai_protocol import system_for_role

        intent_sys = (
            system_for_role("intent_scout", root=self.root, talks_to_user=False)
            + " PARTIAL_INPUT allowed. Reply ONLY JSON: "
            '{"intent":"question|task|code|chat|command","confidence":0.0-1.0,'
            '"predicted_goal":"...","suggest_mode":"preview|handoff_fast|handoff_hive|'
            'helper_probe|helper_draft","keywords":["k1","k2"]}'
        )
        intent_r = self.generate(
            intent_m,
            f"PARTIAL:\n{text}\n\nJSON:",
            system=intent_sys,
            num_predict=int(spec_cfg.get("num_predict_intent") or 96),
            temperature=0.1,
            timeout_s=60,
        )
        intent = self._parse_json_obj(intent_r.get("text") or "") or {
            "intent": "chat",
            "confidence": 0.4,
            "predicted_goal": text,
            "suggest_mode": "preview",
            "keywords": [],
            "parse": "fallback",
        }

        # --- Branch 2: TASK PLAN (tiny/smarts, parallel-safe) ---
        plan_sys = (
            system_for_role("default", root=self.root, talks_to_user=False)
            + " ROLE=task_planner. Reply ONLY JSON: "
            '{"steps":["s1","s2"],"tools":["..."],"ready_to_execute":false,'
            '"risk":"low|med|high","summary":"one line"}'
        )
        plan_model = smarts_m if "code" not in str(intent.get("intent")) else intent_m
        plan_r = self.generate(
            plan_model,
            f"PARTIAL:\n{text}\nINTENT:{json.dumps(intent)[:400]}\n\nJSON plan:",
            system=plan_sys,
            num_predict=int(spec_cfg.get("num_predict_task") or 120),
            temperature=0.15,
            timeout_s=60,
        )
        plan = self._parse_json_obj(plan_r.get("text") or "") or {
            "steps": ["wait for full question"],
            "tools": [],
            "ready_to_execute": False,
            "risk": "low",
            "summary": "partial",
            "parse": "fallback",
        }

        # --- Branch 3: DRAFT ANSWER (one full model — role-picked) ---
        intent_name = str(intent.get("intent") or "chat")
        if intent_name == "code":
            draft_model = code_m
        else:
            draft_model = draft_m
        # Avoid loading full if confidence very low
        conf = float(intent.get("confidence") or 0.4)
        draft_text = ""
        draft_r: dict[str, Any] = {"ok": False, "skipped": True}
        if conf >= 0.35:
            # Draft is still AI2AI until commit/FACE — strip human assistant weights
            draft_sys = (
                system_for_role("default", root=self.root, talks_to_user=False)
                + " ROLE=spec_draft. PARTIAL_INPUT. Dense outline for FACE. "
                "Mark UNCERTAIN. No user greetings."
            )
            draft_r = self.generate(
                draft_model,
                f"PARTIAL USER TEXT:\n{text}\n\n"
                f"Predicted goal: {intent.get('predicted_goal')}\n"
                f"Write a speculative prepared answer:",
                system=draft_sys,
                num_predict=int(spec_cfg.get("num_predict_draft") or 160),
                temperature=0.25,
                timeout_s=120,
            )
            draft_text = draft_r.get("text") or ""

        ms = round((time.perf_counter() - t0) * 1000, 1)
        packet = {
            "schema": "drone.future_seer.spec.v1",
            "status": "GREEN" if intent_r.get("ok") else "PARTIAL",
            "false_green": 0,
            "speculative": True,
            "not_final": True,
            "utc": _utc(),
            "spec_id": sid,
            "partial": text,
            "duration_ms": ms,
            "intent": intent,
            "task_plan": plan,
            "draft": {
                "model": draft_model,
                "text": draft_text,
                "ok": bool(draft_r.get("ok")),
                "ms": draft_r.get("ms"),
                "skipped": draft_r.get("skipped"),
            },
            "models_used": {
                "intent": intent_m,
                "plan": plan_model,
                "draft": draft_model if conf >= 0.35 else None,
            },
            "suggest_mode": intent.get("suggest_mode") or "preview",
            "confidence": conf,
            "loaded": self.list_loaded(),
            "honesty": {
                "preview_only": True,
                "execute_requires_commit": True,
                "multi_model_not_all_loaded": True,
            },
        }

        # persist draft
        draft_path = self.drafts_dir / f"{sid}.json"
        self._write(draft_path, packet)
        packet["draft_path"] = str(draft_path)
        self._write(self.last_spec_path, packet)
        self._last_spec_key = key
        self._last_spec = packet
        self._journal(
            "speculate",
            {
                "spec_id": sid,
                "partial_len": len(text),
                "confidence": conf,
                "intent": intent.get("intent"),
                "ms": ms,
            },
        )
        return packet

    def _parse_json_obj(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if fence:
            raw = fence.group(1).strip()
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    # ── Commit (finalize / execute) ───────────────────────────

    def commit(
        self,
        text: str | None = None,
        *,
        mode: str | None = None,
        allow_auto: bool = False,
        use_last_spec: bool = True,
    ) -> dict[str, Any]:
        """
        User finished typing (Enter). Optionally execute task via drones/helpers.
        mode: preview | handoff_fast | handoff_hive | helper_probe | helper_draft
        """
        final = (text if text is not None else self._buffer).strip()
        if not final:
            return {"status": "RED", "false_green": 0, "error": "empty commit"}

        # refresh spec if needed
        spec = self._last_spec if use_last_spec and self._last_spec else None
        if not spec or (spec.get("partial") or "")[:40] != final[:40]:
            spec = self.speculate(final)

        conf = float((spec.get("intent") or {}).get("confidence") or 0)
        suggest = mode or spec.get("suggest_mode") or "preview"
        exec_cfg = self.cfg.get("execute") or {}
        threshold = float(exec_cfg.get("auto_execute_threshold") or 0.82)

        action = "preview"
        result: dict[str, Any] = {}

        if suggest == "preview" or (not allow_auto and suggest.startswith("handoff")):
            # default safe: preview pack only unless mode forced
            if mode and mode != "preview":
                action = mode
            else:
                action = "preview"
                result = {
                    "preview": True,
                    "draft": (spec.get("draft") or {}).get("text"),
                    "intent": spec.get("intent"),
                    "task_plan": spec.get("task_plan"),
                }

        if mode:
            action = mode

        # High confidence + allow_auto may upgrade preview → handoff_fast
        if allow_auto and action == "preview" and conf >= threshold:
            action = str(exec_cfg.get("default_mode") or "handoff_fast")

        if action == "handoff_fast":
            result = self._handoff(final, mode="fast")
        elif action == "handoff_hive":
            result = self._handoff(final, mode="hive")
        elif action == "helper_probe":
            result = self.summon_helper("probe", final)
        elif action == "helper_draft":
            result = self.summon_helper("draft", final)
        elif action == "preview":
            result = {
                "preview": True,
                "draft": (spec.get("draft") or {}).get("text"),
                "intent": spec.get("intent"),
                "task_plan": spec.get("task_plan"),
                "note": "commit preview only — pass mode= to execute",
            }

        commit_id = f"commit_{_short()}"
        seal = {
            "schema": "drone.future_seer.commit.v1",
            "status": result.get("status")
            or ("GREEN" if result.get("preview") or result.get("ok") else "PARTIAL"),
            "false_green": 0,
            "utc": _utc(),
            "commit_id": commit_id,
            "final_text": final,
            "action": action,
            "confidence": conf,
            "allow_auto": allow_auto,
            "spec_id": spec.get("spec_id"),
            "spec_path": spec.get("draft_path"),
            "result": result,
            "loaded": self.list_loaded(),
        }
        path = self._write(self.out / f"FUTURE_SEER_COMMIT_{commit_id}.json", seal)
        seal["path"] = str(path)
        self._write(self.out / "FUTURE_SEER_LAST_COMMIT.json", seal)
        self._buffer = ""
        self._save_state()
        self._journal("commit", {"commit_id": commit_id, "action": action, "status": seal["status"]})
        return seal

    def _handoff(self, goal: str, *, mode: str = "fast") -> dict[str, Any]:
        try:
            from drone.grok_handoff import GrokDroneHandoff

            bridge = GrokDroneHandoff(self.root)
            return bridge.handoff(
                goal,
                mode=mode if mode in {"fast", "full", "hive", "brain", "pro", "clone"} else "fast",
                controller="grok",
                notes="future_seer commit",
                skip_lane_check=False,
            )
        except Exception as e:
            return {"status": "RED", "false_green": 0, "error": str(e)}

    # ── Status / seal ─────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        hot = {}
        if self.hot_path.is_file():
            try:
                hot = json.loads(self.hot_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                hot = {}
        return {
            "schema": "drone.future_seer.status.v1",
            "app": self.cfg.get("name"),
            "version": self.cfg.get("version"),
            "false_green": 0,
            "utc": _utc(),
            "ollama": self.ollama_up(),
            "loaded": self.list_loaded(),
            "buffer_len": len(self._buffer),
            "buffer_preview": self._buffer[:120],
            "last_spec_id": (self._last_spec or {}).get("spec_id"),
            "hot_loop": bool(self._hot_thread and self._hot_thread.is_alive()),
            "hot_status": hot.get("status"),
            "paths": {
                "state": str(self.state_path),
                "hot": str(self.hot_path),
                "last_spec": str(self.last_spec_path),
                "drafts": str(self.drafts_dir),
                "seal": str(self.seal_path),
            },
            "safety_law": self.cfg.get("safety_law"),
        }

    def seal(self) -> dict[str, Any]:
        hot = self.keep_hot(force=True)
        # mini typeahead smoke
        sample = "how do I fix a python import error in"
        spec = self.speculate(sample)
        st = self.status()
        ok = (
            hot.get("status") == "GREEN"
            and spec.get("status") in {"GREEN", "PARTIAL"}
            and self.ollama_up()
        )
        seal = {
            "schema": "drone.future_seer.seal.v1",
            "app": "DroneHive Future Seer",
            "status": "GREEN" if ok else "PARTIAL",
            "false_green": 0,
            "utc": _utc(),
            "hot": {
                "status": hot.get("status"),
                "warmed": hot.get("warmed"),
                "jane_lanes_ok": (hot.get("jane_lanes") or {}).get("ok"),
                "everest_ok": (hot.get("everest") or {}).get("ok"),
            },
            "spec_smoke": {
                "status": spec.get("status"),
                "spec_id": spec.get("spec_id"),
                "intent": (spec.get("intent") or {}).get("intent"),
                "confidence": spec.get("confidence"),
                "draft_ok": (spec.get("draft") or {}).get("ok"),
                "path": spec.get("draft_path"),
            },
            "capabilities": [
                "typeahead_read",
                "multi_model_speculate",
                "hot_lanes",
                "jane_helper_tap",
                "everest_health",
                "drone_handoff_commit",
            ],
            "evidence": [
                str(self.hot_path),
                str(self.last_spec_path),
                str(self.seal_path),
                str(self.drafts_dir),
            ],
            "status_snapshot": st,
        }
        self._write(self.seal_path, seal)
        return seal


def run_tui(root: Path | None = None) -> int:
    """
    Interactive Future Seer TUI.
    Type freely — speculation fires as you type (line buffer).
    Enter = commit preview.  Commands: /hot /status /commit-fast /helper /quit
    """
    seer = FutureSeer(root)
    print("=" * 64)
    print("  DroneHive Future Seer — multi-model speculative typeahead")
    print("  Type partial questions; seer prepares intent+draft before Enter")
    print("  /hot  /status  /spec  /commit-fast  /helper-probe  /quit")
    print("=" * 64)
    print("Warming hot lanes…")
    hot = seer.keep_hot()
    print(f"  hot={hot.get('status')} loaded={hot.get('loaded')}")
    seer.start_hot_loop()

    def _show(spec: dict[str, Any]) -> None:
        if spec.get("status") == "WAIT":
            return
        intent = spec.get("intent") or {}
        draft = (spec.get("draft") or {}).get("text") or ""
        print()
        print(
            f"  ⚡ SEERintent={intent.get('intent')} "
            f"conf={intent.get('confidence')} "
            f"mode={spec.get('suggest_mode')} "
            f"ms={spec.get('duration_ms')}"
        )
        pred = intent.get("predicted_goal") or ""
        if pred:
            print(f"  → predicted: {pred[:160]}")
        if draft:
            print(f"  → draft: {draft[:280].replace(chr(10), ' ')}")
        print()

    seer._on_spec = _show

    while True:
        try:
            # Line-mode typeahead: user types a line; we also accept mid-line via /type
            line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            seer.stop_hot_loop()
            return 0

        if not line and not seer._buffer:
            continue

        low = line.strip().lower()
        if low in {"/q", "/quit", "quit", "exit"}:
            seer.stop_hot_loop()
            return 0
        if low == "/hot":
            print(json.dumps(seer.keep_hot(), indent=2, default=str)[:2000])
            continue
        if low == "/status":
            print(json.dumps(seer.status(), indent=2, default=str))
            continue
        if low == "/spec":
            print(json.dumps(seer.speculate(seer._buffer or line), indent=2, default=str)[:3000])
            continue
        if low.startswith("/type "):
            # simulate progressive typeahead
            chunk = line[6:]
            for i, ch in enumerate(chunk):
                seer.type_char(ch)
                if (i + 1) % 6 == 0:
                    time.sleep(0.05)
            # wait debounce
            time.sleep(0.4)
            if seer._last_spec:
                _show(seer._last_spec)
            continue
        if low == "/commit-fast":
            text = seer._buffer or (seer._last_spec or {}).get("partial") or ""
            print(json.dumps(seer.commit(text, mode="handoff_fast"), indent=2, default=str)[:2500])
            continue
        if low == "/helper-probe":
            text = seer._buffer or (seer._last_spec or {}).get("partial") or line
            print(json.dumps(seer.commit(text, mode="helper_probe"), indent=2, default=str)[:2500])
            continue
        if low.startswith("/"):
            print("cmds: /hot /status /spec /type <text> /commit-fast /helper-probe /quit")
            continue

        # Full line typed — treat as progressive then commit preview
        seer.set_buffer(line, speculate=True)
        time.sleep(0.35)
        if seer._last_spec:
            _show(seer._last_spec)
        # Enter = commit preview (safe default)
        seal = seer.commit(line, mode="preview")
        draft = ((seal.get("result") or {}).get("draft")) or ""
        print(f"[commit {seal.get('status')}] action={seal.get('action')}")
        if draft:
            print(draft[:600])
        print(f"evidence: {seal.get('path')}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone seer", description="Future Seer multi-model typeahead")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hot", help="warm lanes + jane/ever probe")
    sub.add_parser("hot-start", help="background hot loop")
    sub.add_parser("hot-stop")
    sub.add_parser("status")
    sub.add_parser("seal")
    sub.add_parser("tui")

    ty = sub.add_parser("type", help="set partial buffer + speculate")
    ty.add_argument("text", nargs="+")
    ty.add_argument("--no-spec", action="store_true")

    sp = sub.add_parser("spec", help="speculate on text")
    sp.add_argument("text", nargs="+")

    cm = sub.add_parser("commit", help="commit final text")
    cm.add_argument("text", nargs="*")
    cm.add_argument(
        "--mode",
        default="preview",
        choices=["preview", "handoff_fast", "handoff_hive", "helper_probe", "helper_draft"],
    )
    cm.add_argument("--auto", action="store_true")

    hp = sub.add_parser("helper", help="summon jane muscle helper")
    hp.add_argument("--expert", default="probe", choices=["probe", "draft", "forge", "seal"])
    hp.add_argument("goal", nargs="+")

    args = p.parse_args(argv)
    seer = FutureSeer(Path(args.root) if args.root else None)

    if args.cmd == "hot":
        print(json.dumps(seer.keep_hot(force=True), indent=2, default=str))
        return 0
    if args.cmd == "hot-start":
        print(json.dumps(seer.start_hot_loop(), indent=2))
        return 0
    if args.cmd == "hot-stop":
        print(json.dumps(seer.stop_hot_loop(), indent=2))
        return 0
    if args.cmd == "status":
        print(json.dumps(seer.status(), indent=2, default=str))
        return 0
    if args.cmd == "seal":
        out = seer.seal()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "tui":
        return run_tui(Path(args.root) if args.root else None)
    if args.cmd == "type":
        text = " ".join(args.text)
        out = seer.set_buffer(text, speculate=not args.no_spec)
        if not args.no_spec:
            # run immediate + wait debounce path
            out = seer.speculate(text)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "spec":
        text = " ".join(args.text)
        print(json.dumps(seer.speculate(text), indent=2, default=str))
        return 0
    if args.cmd == "commit":
        text = " ".join(args.text) if args.text else None
        print(
            json.dumps(
                seer.commit(text, mode=args.mode, allow_auto=bool(args.auto)),
                indent=2,
                default=str,
            )
        )
        return 0
    if args.cmd == "helper":
        goal = " ".join(args.goal)
        print(json.dumps(seer.summon_helper(args.expert, goal), indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
