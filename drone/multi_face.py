"""
Multi-Host FACE — all models/hosts talk to ONE face; face talks to user.

Architecture:
  User
    ↕  (only path)
  FACE (llama3.1:8b speaker)
    ↑ internal briefs only
  Workers: Ollama roles · Hermes · OpenClaw · OpenCode · Super Cell · Drones · Everest · cloud opt-in

false_green: 0 — SKIP down hosts; never invent worker text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _py() -> str:
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


class MultiFace:
    """Single user-facing voice; multi-host internal council."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "multi_hosts.json"
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load()
        self.seal_path = self.out / "MULTI_FACE_SEAL.json"
        self.last_path = self.out / "MULTI_FACE_LAST.json"
        self.roster_path = self.out / "MULTI_HOSTS_LIVE.json"

    def _load(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {"face": {}, "hosts": [], "fanout": {}}

    def _write(self, path: Path, data: Any) -> Path:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _http_json(
        self, method: str, url: str, body: dict | None = None, timeout: float = 90
    ) -> dict[str, Any]:
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def _port_open(self, port: int, host: str = "127.0.0.1") -> bool:
        import socket

        try:
            with socket.create_connection((host, port), timeout=0.8):
                return True
        except OSError:
            return False

    def _ollama_chat(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        num_predict: int = 160,
        temperature: float = 0.2,
        timeout_s: float = 90,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            data = self._http_json(
                "POST",
                f"{ollama_host()}/api/chat",
                {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_predict": num_predict,
                        "temperature": temperature,
                    },
                },
                timeout=timeout_s,
            )
            text = str((data.get("message") or {}).get("content", "")).strip()
            return {
                "ok": bool(text),
                "text": text,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
            }

    # ── host probe ────────────────────────────────────────────

    def probe_hosts(self) -> dict[str, Any]:
        hosts = list(self.cfg.get("hosts") or [])
        face = self.cfg.get("face") or {}
        rows = []
        ollama_up = False
        try:
            tags = self._http_json("GET", f"{ollama_host()}/api/tags", timeout=3)
            ollama_up = True
            installed = {m.get("name") for m in (tags.get("models") or [])}
        except Exception:
            installed = set()

        for h in hosts:
            hid = h.get("id")
            kind = h.get("kind")
            row: dict[str, Any] = {
                "id": hid,
                "kind": kind,
                "role": h.get("role"),
                "enabled": bool(h.get("enabled")),
                "internal_only": bool(h.get("internal_only", True)),
                "opt_in": bool(h.get("opt_in")),
                "up": False,
                "detail": {},
            }
            if not h.get("enabled"):
                row["up"] = False
                row["detail"] = {"reason": "disabled"}
                rows.append(row)
                continue

            if kind == "ollama":
                model = h.get("model")
                present = model in installed or any(
                    str(n).startswith(str(model).split(":")[0] + ":") for n in installed
                )
                row["up"] = ollama_up and present
                row["detail"] = {"model": model, "ollama": ollama_up, "installed": present}

            elif kind == "hermes":
                exe = Path(h.get("exe") or "")
                port = int(h.get("port") or 9119)
                row["up"] = exe.is_file()
                row["detail"] = {
                    "exe": str(exe),
                    "exe_ok": exe.is_file(),
                    "serve_port": port,
                    "serve_up": self._port_open(port),
                }

            elif kind == "openclaw":
                exe = h.get("exe") or shutil.which("openclaw")
                port = int(h.get("gateway_port") or 19100)
                row["up"] = bool(exe and Path(str(exe)).exists()) or bool(shutil.which("openclaw"))
                row["detail"] = {
                    "exe": exe,
                    "gateway_port": port,
                    "gateway_up": self._port_open(port),
                }

            elif kind == "opencode":
                exe = h.get("exe") or shutil.which("opencode")
                p = Path(str(exe)) if exe else None
                row["up"] = bool(p and p.exists()) or bool(shutil.which("opencode"))
                row["detail"] = {"exe": exe}

            elif kind == "muscle":
                exe = Path(h.get("exe") or "")
                row["up"] = exe.is_file()
                row["detail"] = {"exe": str(exe)}

            elif kind == "drone_fast":
                row["up"] = (self.root / "drone" / "fast_lane.py").is_file() and ollama_up
                row["detail"] = {"root": str(self.root)}

            elif kind == "everest":
                exe = Path(h.get("exe") or "")
                row["up"] = exe.is_file()
                row["detail"] = {"exe": str(exe)}

            elif kind in {"spacexai", "gemini"}:
                envk = "XAI_API_KEY" if kind == "spacexai" else "GEMINI_API_KEY"
                row["up"] = bool(os.environ.get(envk))
                row["detail"] = {"env": envk, "set": bool(os.environ.get(envk)), "opt_in": True}

            else:
                row["detail"] = {"reason": "unknown_kind"}

            rows.append(row)

        face_model = face.get("model")
        face_ok = ollama_up and (
            face_model in installed
            or any(str(n).startswith(str(face_model).split(":")[0] + ":") for n in installed)
        )
        payload = {
            "schema": "drone.multi_hosts.live.v1",
            "utc": _utc(),
            "false_green": 0,
            "face": {
                "model": face_model,
                "up": face_ok,
                "role": "user_facing_speaker_only",
            },
            "ollama_up": ollama_up,
            "hosts": rows,
            "up_count": sum(1 for r in rows if r.get("up")),
            "law": self.cfg.get("law"),
        }
        self._write(self.roster_path, payload)
        return payload

    # ── worker brief (INTERNAL ONLY) ──────────────────────────

    def _worker_system(self, role: str) -> str:
        from drone.ai_protocol import strip_human_fluff, system_for_role

        # Human weights stripped — AI2AI only
        return system_for_role(str(role or "default"), root=self.root, talks_to_user=False)

    def _run_ollama_worker(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        model = str(host.get("model") or "llama3.2:3b")
        from drone.ai_protocol import strip_human_fluff

        r = self._ollama_chat(
            model,
            f"GOAL_PACKET:\n{user_goal}\n\nEMIT_BRIEF_FOR_FACE:",
            system=self._worker_system(str(host.get("role") or host.get("id"))),
            num_predict=140,
            temperature=0.1,
            timeout_s=float(host.get("timeout_s") or 60),
        )
        brief = strip_human_fluff(r.get("text") or "", root=self.root)
        return {
            "id": host.get("id"),
            "kind": "ollama",
            "role": host.get("role"),
            "model": model,
            "ok": r.get("ok") and bool(brief),
            "brief": brief[:1200],
            "ms": r.get("ms"),
            "error": r.get("error"),
            "internal_only": True,
            "channel": "AI2AI",
            "strip_human_weights": True,
            "false_green": 0,
        }

    def _run_hermes(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        exe = Path(host.get("exe") or "")
        if not exe.is_file():
            return {
                "id": "hermes",
                "ok": False,
                "skipped": True,
                "error": "hermes.exe missing",
                "internal_only": True,
                "false_green": 0,
            }
        prompt = (
            "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_FLUFF. "
            f"Brief FACE on: {user_goal[:400]}. "
            "5 bullets max. Dense technical only."
        )
        t0 = time.perf_counter()
        try:
            # Non-interactive short path; yolo for headless
            cmd = [
                str(exe),
                "-z",
                prompt,
                "-m",
                "qwen2.5:7b",
                "--yolo",
            ]
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=float(host.get("timeout_s") or 120),
                cwd=str(exe.parent),
            )
            text = (p.stdout or "").strip() or (p.stderr or "").strip()
            # keep tail — hermes can be chatty
            brief = text[-1500:] if len(text) > 1500 else text
            return {
                "id": "hermes",
                "kind": "hermes",
                "role": host.get("role"),
                "ok": p.returncode == 0 and bool(brief),
                "brief": brief[:1200],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "returncode": p.returncode,
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "hermes",
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_openclaw(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        exe = host.get("exe") or shutil.which("openclaw")
        if not exe or not Path(str(exe)).exists():
            return {
                "id": "openclaw",
                "ok": False,
                "skipped": True,
                "error": "openclaw missing",
                "internal_only": True,
                "false_green": 0,
            }
        msg = (
            "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_FLUFF. "
            f"Goal: {user_goal[:350]}. 5 bullets for FACE."
        )
        t0 = time.perf_counter()
        try:
            # openclaw agent --local --json
            cmd = [
                "cmd",
                "/c",
                str(exe),
                "agent",
                "--agent",
                str(host.get("agent") or "supercell"),
                "--message",
                msg,
                "--model",
                str(host.get("model") or "ollama/qwen2.5-coder:7b"),
                "--local",
                "--json",
            ]
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=float(host.get("timeout_s") or 120),
            )
            out = (p.stdout or "").strip()
            brief = out[-1500:] if out else (p.stderr or "")[-800:]
            return {
                "id": "openclaw",
                "kind": "openclaw",
                "role": host.get("role"),
                "ok": p.returncode == 0 and bool(brief),
                "brief": brief[:1200],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "returncode": p.returncode,
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "openclaw",
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_opencode(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        exe = host.get("exe") or shutil.which("opencode")
        if not exe or not Path(str(exe)).exists():
            return {
                "id": "opencode",
                "ok": False,
                "skipped": True,
                "error": "opencode missing",
                "internal_only": True,
                "false_green": 0,
            }
        t0 = time.perf_counter()
        try:
            # Best-effort: opencode run / prompt variants differ by version
            cmd = ["cmd", "/c", str(exe), "run", user_goal[:300]]
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=float(host.get("timeout_s") or 90),
            )
            out = (p.stdout or p.stderr or "").strip()
            if not out and p.returncode != 0:
                # fallback help probe — still report presence
                p2 = subprocess.run(
                    ["cmd", "/c", str(exe), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                out = f"opencode available (help ok={p2.returncode==0}); run may need interactive session"
                return {
                    "id": "opencode",
                    "kind": "opencode",
                    "ok": True,
                    "brief": out[:500],
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "note": "non-interactive brief limited",
                    "internal_only": True,
                    "false_green": 0,
                }
            return {
                "id": "opencode",
                "kind": "opencode",
                "ok": bool(out),
                "brief": out[-1200:],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "returncode": p.returncode,
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "opencode",
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_muscle(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        exe = Path(host.get("exe") or "")
        if not exe.is_file():
            return {
                "id": "supercell_muscle",
                "ok": False,
                "skipped": True,
                "error": "muscle_dispatch missing",
                "internal_only": True,
                "false_green": 0,
            }
        t0 = time.perf_counter()
        expert = str(host.get("expert") or "probe")
        run_id = f"face-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = r"G:\AI-Center"
            p = subprocess.run(
                [
                    _py(),
                    str(exe),
                    "--expert",
                    expert,
                    "--goal",
                    f"[INTERNAL brief for FACE] {user_goal[:500]}",
                    "--run-id",
                    run_id,
                ],
                capture_output=True,
                text=True,
                timeout=float(host.get("timeout_s") or 120),
                env=env,
                cwd=str(exe.parent),
            )
            brief = (p.stdout or "")[-1200:]
            return {
                "id": "supercell_muscle",
                "kind": "muscle",
                "expert": expert,
                "run_id": run_id,
                "ok": p.returncode == 0,
                "brief": brief or f"muscle exit={p.returncode}",
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "returncode": p.returncode,
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "supercell_muscle",
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_drone_fast(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            from drone.app.service import DroneHiveService

            report = DroneHiveService(self.root).run_task(
                f"[INTERNAL for FACE] {user_goal[:300]}",
                lane="fast",
                controller="local",
                lm_assist="none",
                tags=["build", "face_worker", "internal"],
            )
            return {
                "id": "drones",
                "kind": "drone_fast",
                "ok": report.get("status") == "GREEN",
                "brief": (
                    f"drone status={report.get('status')} "
                    f"nodes={report.get('nodes_run')} "
                    f"tools={report.get('tool_calls')} "
                    f"path={report.get('report_path') or report.get('seal_path')}"
                ),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "status": report.get("status"),
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "drones",
                "ok": False,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_everest(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        exe = Path(host.get("exe") or "")
        if not exe.is_file():
            return {
                "id": "everest",
                "ok": False,
                "skipped": True,
                "error": "everest cli missing",
                "internal_only": True,
                "false_green": 0,
            }
        t0 = time.perf_counter()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = r"G:\AI-Center"
            p = subprocess.run(
                [_py(), str(exe), "health"],
                capture_output=True,
                text=True,
                timeout=float(host.get("timeout_s") or 30),
                env=env,
            )
            return {
                "id": "everest",
                "kind": "everest",
                "ok": p.returncode == 0,
                "brief": (p.stdout or p.stderr or "")[:800],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": "everest",
                "ok": False,
                "error": str(e),
                "internal_only": True,
                "false_green": 0,
            }

    def _run_cloud(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        kind = host.get("kind")
        t0 = time.perf_counter()
        try:
            from drone.controllers import make_lm_fn

            fn = make_lm_fn(str(kind))
            if not fn:
                return {
                    "id": host.get("id"),
                    "ok": False,
                    "skipped": True,
                    "error": "no lm fn / key",
                    "internal_only": True,
                    "false_green": 0,
                }
            text = fn(
                "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_FLUFF. "
                f"Goal: {user_goal[:400]}. 5 bullets for FACE."
            )
            return {
                "id": host.get("id"),
                "kind": kind,
                "ok": bool(text),
                "brief": (text or "")[:1200],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "internal_only": True,
                "false_green": 0,
            }
        except Exception as e:
            return {
                "id": host.get("id"),
                "ok": False,
                "error": str(e),
                "internal_only": True,
                "false_green": 0,
            }

    def _select_workers(
        self, user_goal: str, live: dict[str, Any], *, include_opt_in: bool = False
    ) -> list[dict[str, Any]]:
        fan = self.cfg.get("fanout") or {}
        hosts_cfg = {h.get("id"): h for h in (self.cfg.get("hosts") or [])}
        live_map = {h.get("id"): h for h in (live.get("hosts") or [])}
        g = (user_goal or "").lower()
        chosen: list[str] = []

        for hid in fan.get("always") or ["ollama_fast"]:
            if hid not in chosen:
                chosen.append(hid)

        if fan.get("pick_by_keywords", True):
            for hid, h in hosts_cfg.items():
                whens = h.get("when") or []
                if whens and any(w in g for w in whens):
                    if hid not in chosen:
                        chosen.append(hid)

        # include external hosts if up
        for hid in fan.get("include_hosts_if_up") or []:
            row = live_map.get(hid) or {}
            if row.get("up") and hid not in chosen:
                chosen.append(hid)

        # muscle if jane keywords
        if any(k in g for k in ("helper", "probe", "jane", "super cell", "swarm")):
            if "supercell_muscle" not in chosen:
                chosen.append("supercell_muscle")

        if include_opt_in:
            for hid, h in hosts_cfg.items():
                if h.get("opt_in") and hid not in chosen:
                    chosen.append(hid)

        # filter enabled + up (ollama models need up; hermes can be exe-only)
        max_w = int(fan.get("max_workers") or 3)
        heavy = 0
        heavy_limit = int(fan.get("heavy_limit") or 1)
        out: list[dict[str, Any]] = []
        for hid in chosen:
            h = hosts_cfg.get(hid)
            if not h or not h.get("enabled"):
                continue
            row = live_map.get(hid) or {}
            if h.get("opt_in") and not include_opt_in:
                continue
            if not row.get("up") and h.get("kind") not in {"hermes", "openclaw", "opencode"}:
                # still try hermes/openclaw/opencode if exe exists
                if h.get("kind") in {"hermes", "openclaw", "opencode", "muscle", "everest"}:
                    if not row.get("up"):
                        continue
                else:
                    continue
            # heavy = 7b+ ollama
            is_heavy = h.get("kind") == "ollama" and any(
                x in str(h.get("model") or "") for x in ("7b", "8b", "12b")
            )
            if is_heavy:
                if heavy >= heavy_limit:
                    continue
                heavy += 1
            out.append(h)
            if len(out) >= max_w:
                break
        return out

    def _dispatch_worker(self, host: dict[str, Any], user_goal: str) -> dict[str, Any]:
        kind = host.get("kind")
        if kind == "ollama":
            return self._run_ollama_worker(host, user_goal)
        if kind == "hermes":
            return self._run_hermes(host, user_goal)
        if kind == "openclaw":
            return self._run_openclaw(host, user_goal)
        if kind == "opencode":
            return self._run_opencode(host, user_goal)
        if kind == "muscle":
            return self._run_muscle(host, user_goal)
        if kind == "drone_fast":
            return self._run_drone_fast(host, user_goal)
        if kind == "everest":
            return self._run_everest(host, user_goal)
        if kind in {"spacexai", "gemini"}:
            return self._run_cloud(host, user_goal)
        return {
            "id": host.get("id"),
            "ok": False,
            "error": f"unknown kind {kind}",
            "internal_only": True,
            "false_green": 0,
        }

    # ── FACE ask ──────────────────────────────────────────────

    def ask(
        self,
        user_text: str,
        *,
        include_opt_in: bool = False,
        workers: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Full path: fan workers (internal) → FACE synthesizes user reply.
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return {"status": "RED", "false_green": 0, "error": "empty user text"}

        t0 = time.perf_counter()
        live = self.probe_hosts()
        face = self.cfg.get("face") or {}
        if not (live.get("face") or {}).get("up"):
            return {
                "status": "RED",
                "false_green": 0,
                "error": "FACE model not available on Ollama",
                "face": face.get("model"),
            }

        if workers:
            hosts_cfg = {h.get("id"): h for h in (self.cfg.get("hosts") or [])}
            selected = [hosts_cfg[w] for w in workers if w in hosts_cfg]
        else:
            selected = self._select_workers(
                user_text, live, include_opt_in=include_opt_in
            )

        briefs: list[dict[str, Any]] = []
        # sequential heavy-safe: small pool
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(selected)))) as pool:
            futs = {
                pool.submit(self._dispatch_worker, h, user_text): h.get("id")
                for h in selected
            }
            for fut in as_completed(futs):
                try:
                    briefs.append(fut.result())
                except Exception as e:
                    briefs.append(
                        {
                            "id": futs[fut],
                            "ok": False,
                            "error": str(e),
                            "internal_only": True,
                            "false_green": 0,
                        }
                    )

        # Build INTERNAL packet for FACE only
        lines = []
        for b in briefs:
            tag = b.get("id")
            if b.get("ok") and b.get("brief"):
                lines.append(f"### {tag} ({b.get('role') or b.get('kind')})\n{b.get('brief')}")
            else:
                err = b.get("error") or b.get("skipped") or "failed"
                lines.append(f"### {tag}\nSKIP/FAIL: {err}")

        council = "\n\n".join(lines) if lines else "(no worker briefs)"
        from drone.ai_protocol import system_face

        face_prompt = (
            f"USER SAID:\n{user_text}\n\n"
            f"AI2AI COUNCIL BRIEFS (machine channel, not for raw dump):\n{council}\n\n"
            "Write the final reply TO THE USER now."
        )
        face_r = self._ollama_chat(
            str(face.get("model") or "llama3.1:8b"),
            face_prompt,
            system=str(
                face.get("system")
                or system_face(
                    self.root,
                    extra="Use council briefs. Do not invent worker results.",
                )
            ),
            num_predict=int(face.get("num_predict") or 384),
            temperature=float(face.get("temperature") or 0.3),
            timeout_s=180,
        )

        ms = round((time.perf_counter() - t0) * 1000, 1)
        ok = bool(face_r.get("ok"))
        seal = {
            "schema": "drone.multi_face.reply.v1",
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "utc": _utc(),
            "duration_ms": ms,
            "architecture": {
                "user_talks_to": "FACE_only",
                "workers_talk_to": "FACE_only",
                "face_model": face.get("model"),
            },
            "user": user_text,
            "face_reply": face_r.get("text") or "",
            "face_ms": face_r.get("ms"),
            "workers_selected": [h.get("id") for h in selected],
            "worker_briefs": briefs,
            "hosts_live": str(self.roster_path),
            "honesty": {
                "workers_not_user_facing": True,
                "skipped_hosts_honest": True,
                "one_face_speaker": True,
            },
        }
        self._write(self.last_path, seal)
        return seal

    def seal(self) -> dict[str, Any]:
        live = self.probe_hosts()
        # tiny ask to prove face path
        sample = self.ask("Say ready in one short sentence.", workers=["ollama_fast"])
        seal = {
            "schema": "drone.multi_face.seal.v1",
            "status": "GREEN"
            if live.get("face", {}).get("up") and sample.get("status") == "GREEN"
            else "PARTIAL",
            "false_green": 0,
            "utc": _utc(),
            "face": live.get("face"),
            "hosts_up": live.get("up_count"),
            "hosts": [
                {"id": h.get("id"), "up": h.get("up"), "kind": h.get("kind")}
                for h in (live.get("hosts") or [])
            ],
            "sample_status": sample.get("status"),
            "sample_reply": (sample.get("face_reply") or "")[:200],
            "evidence": [str(self.roster_path), str(self.last_path), str(self.seal_path)],
            "law": self.cfg.get("law"),
        }
        self._write(self.seal_path, seal)
        return seal


def _chat_stream(role: str, text: str) -> None:
    line = (text or "").replace("\n", " ").strip()
    if line:
        print(f"CHAT|{role}|{line[:500]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="drone face",
        description="Multi-host FACE: all workers → one speaker → user",
    )
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hosts", help="probe all hosts live")
    sub.add_parser("seal")
    ask = sub.add_parser("ask", help="user text → workers → FACE reply")
    ask.add_argument("text", nargs="+")
    ask.add_argument("--opt-in", action="store_true", help="include cloud hosts")
    ask.add_argument(
        "--workers",
        default=None,
        help="comma ids: ollama_fast,hermes,openclaw,opencode,...",
    )
    ask.add_argument("--stream", action="store_true", help="CHAT| lines for TUI")

    args = p.parse_args(argv)
    face = MultiFace(Path(args.root) if args.root else None)

    if args.cmd == "hosts":
        print(json.dumps(face.probe_hosts(), indent=2, default=str))
        return 0
    if args.cmd == "seal":
        out = face.seal()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "ask":
        text = " ".join(args.text)
        workers = None
        if args.workers:
            workers = [w.strip() for w in args.workers.split(",") if w.strip()]
        if args.stream:
            _chat_stream("system", "FACE council starting…")
        out = face.ask(text, include_opt_in=bool(args.opt_in), workers=workers)
        if args.stream:
            for b in out.get("worker_briefs") or []:
                st = "ok" if b.get("ok") else "skip"
                _chat_stream("tool", f"{b.get('id')} · {st}")
            _chat_stream("face", out.get("face_reply") or "")
            print("SEAL|" + json.dumps(out, ensure_ascii=False, default=str), flush=True)
        else:
            print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
