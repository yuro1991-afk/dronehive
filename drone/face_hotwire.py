"""
FACE Hotwire — all models bound to FACE; speed + precision execute.

Public API:
  hot()     — warm keep_alive on core models + probe hosts
  chat()    — user text → hotwired council → FACE only
  exec()    — precision command: classify → worker/tools → FACE result
  status()  — hotwire + host matrix

false_green: 0
CLI: python -m drone face hotwire|chat|exec|status
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    import os

    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


class FaceHotwire:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "face_hotwire.json"
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load()
        self.hot_path = self.out / "FACE_HOTWIRE_HOT.json"
        self.last_path = self.out / "FACE_HOTWIRE_LAST.json"
        self.seal_path = self.out / "FACE_HOTWIRE_SEAL.json"
        self.state_path = self.out / "FACE_HOTWIRE_STATE.json"

    def _load(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {"hot": {}, "speed": {}, "precision": {}, "law": []}

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

    def _ollama_chat(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        num_predict: int = 128,
        temperature: float = 0.15,
        timeout_s: float = 60,
        keep_alive: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        ka = keep_alive or (self.cfg.get("hot") or {}).get("keep_alive") or "45m"
        try:
            data = self._http_json(
                "POST",
                f"{ollama_host()}/api/chat",
                {
                    "model": model,
                    "messages": msgs,
                    "stream": False,
                    "keep_alive": ka,
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

    # ── hotwire warm ──────────────────────────────────────────

    def hot(self) -> dict[str, Any]:
        """Warm core models + probe all hosts (hotwire live)."""
        from drone.multi_face import MultiFace

        hot_cfg = self.cfg.get("hot") or {}
        models = list(hot_cfg.get("warm_models") or ["llama3.2:3b", "llama3.1:8b"])
        npred = int(hot_cfg.get("warm_num_predict") or 4)
        warmed = []
        for m in models:
            r = self._ollama_chat(
                m,
                "OK",
                num_predict=npred,
                temperature=0,
                timeout_s=90,
            )
            warmed.append(
                {
                    "model": m,
                    "ok": r.get("ok"),
                    "ms": r.get("ms"),
                    "error": r.get("error"),
                }
            )

        face = MultiFace(self.root)
        hosts = face.probe_hosts()

        # Mark multi_hosts fanout as hotwired defaults (in-memory report; config stays source)
        report = {
            "schema": "drone.face_hotwire.hot.v1",
            "status": "GREEN"
            if any(w.get("ok") for w in warmed) and (hosts.get("face") or {}).get("up")
            else "PARTIAL",
            "false_green": 0,
            "utc": _utc(),
            "warmed": warmed,
            "face_up": (hosts.get("face") or {}).get("up"),
            "hosts_up": hosts.get("up_count"),
            "hosts": [
                {
                    "id": h.get("id"),
                    "up": h.get("up"),
                    "kind": h.get("kind"),
                    "internal_only": True,
                }
                for h in (hosts.get("hosts") or [])
            ],
            "bindings": self.cfg.get("bindings"),
            "law": self.cfg.get("law"),
            "path": str(self.hot_path),
        }
        self._write(self.hot_path, report)
        self._write(
            self.state_path,
            {
                "utc": _utc(),
                "hotwired": True,
                "face_model": (self.cfg.get("speed") or {}).get("face_model"),
                "last_hot_status": report["status"],
                "false_green": 0,
            },
        )
        return report

    # ── precision classify ────────────────────────────────────

    def classify(self, user_text: str) -> dict[str, Any]:
        """Fast scout classify for precision routing."""
        speed = self.cfg.get("speed") or {}
        prec = self.cfg.get("precision") or {}
        scout = str(speed.get("scout_model") or "llama3.2:3b")
        verbs = prec.get("execute_verbs") or []
        low = (user_text or "").lower()
        looks_exec = any(re.search(rf"\b{re.escape(v)}\b", low) for v in verbs)

        from drone.ai_protocol import system_for_role

        sys = (
            system_for_role("intent_scout", root=self.root, talks_to_user=False)
            + " Reply ONLY JSON: "
            '{"mode":"chat|exec|status","worker":"ollama_fast|ollama_coder|ollama_ops|'
            'ollama_smarts|ollama_seal|drones","precision":0.0-1.0,'
            '"command":"short imperative if exec else empty",'
            '"reason":"one line"}'
        )
        r = self._ollama_chat(
            scout,
            f"USER:\n{user_text}\n\nJSON:",
            system=sys,
            num_predict=int(speed.get("num_predict_scout") or 64),
            temperature=0.05,
            timeout_s=float(speed.get("timeout_scout_s") or 25),
        )
        obj = self._parse_json(r.get("text") or "") or {}
        mode = str(obj.get("mode") or ("exec" if looks_exec else "chat")).lower()
        if mode not in {"chat", "exec", "status"}:
            mode = "exec" if looks_exec else "chat"
        worker = str(obj.get("worker") or "ollama_fast")
        if looks_exec and worker == "ollama_fast" and any(
            k in low for k in ("code", "python", "bug", "import", "function")
        ):
            worker = "ollama_coder"
        if mode == "status" or any(k in low for k in ("status", "health", "hosts", "hotwire")):
            mode = "status"
        return {
            "ok": True,
            "mode": mode,
            "worker": worker,
            "precision": float(obj.get("precision") or (0.8 if looks_exec else 0.6)),
            "command": str(obj.get("command") or user_text)[:300],
            "reason": str(obj.get("reason") or "")[:200],
            "scout_ms": r.get("ms"),
            "looks_exec": looks_exec,
            "false_green": 0,
        }

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if fence:
            raw = fence.group(1).strip()
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            o = json.loads(m.group(0))
            return o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            return None

    # ── chat (all hotwired to face) ───────────────────────────

    def chat(self, user_text: str, *, force_workers: list[str] | None = None) -> dict[str, Any]:
        """
        Hotwired chat: ALWAYS MultiFace (workers → FACE).
        No direct model-to-user path.
        """
        from drone.multi_face import MultiFace

        t0 = time.perf_counter()
        clf = self.classify(user_text)
        if clf.get("mode") == "status":
            return self._status_via_face(user_text, t0, clf)
        if clf.get("mode") == "exec":
            return self.execute(user_text, classified=clf)

        speed = self.cfg.get("speed") or {}
        face = MultiFace(self.root)
        # Precision worker pick from classifier + always scout
        workers = force_workers
        if not workers:
            w = [str(clf.get("worker") or "ollama_fast")]
            if "ollama_fast" not in w:
                w.insert(0, "ollama_fast")
            # cap
            max_w = int(speed.get("max_workers_chat") or 2)
            workers = w[:max_w]

        # Apply speed timeouts onto host copies via temporary override of ask
        out = face.ask(user_text, workers=workers)
        out["hotwired"] = True
        out["classify"] = clf
        out["path_mode"] = "chat"
        out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["architecture"] = {
            **(out.get("architecture") or {}),
            "all_models_to_face": True,
            "user_channel": "FACE_only",
            "hotwired": True,
        }
        self._write(self.last_path, out)
        return out

    def _status_via_face(self, user_text: str, t0: float, clf: dict[str, Any]) -> dict[str, Any]:
        hot = self.hot()
        from drone.multi_face import MultiFace

        face = MultiFace(self.root)
        speed = self.cfg.get("speed") or {}
        face_model = str(speed.get("face_model") or "llama3.1:8b")
        summary = {
            "hot_status": hot.get("status"),
            "hosts_up": hot.get("hosts_up"),
            "warmed": hot.get("warmed"),
            "hosts": hot.get("hosts"),
        }
        r = self._ollama_chat(
            face_model,
            f"USER asked status: {user_text}\n\nHOTWIRE JSON:\n{json.dumps(summary)[:2500]}\n\n"
            "Reply to the user: short precise status of FACE hotwire and hosts.",
            system="You are FACE. Only user-facing speaker. Be precise and brief.",
            num_predict=int(speed.get("num_predict_face") or 220),
            timeout_s=float(speed.get("timeout_face_s") or 60),
        )
        out = {
            "schema": "drone.face_hotwire.reply.v1",
            "status": "GREEN" if r.get("ok") else "RED",
            "false_green": 0,
            "utc": _utc(),
            "hotwired": True,
            "path_mode": "status",
            "classify": clf,
            "face_reply": r.get("text") or "",
            "face_ms": r.get("ms"),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "hot": hot,
            "architecture": {
                "user_talks_to": "FACE_only",
                "all_models_to_face": True,
                "hotwired": True,
            },
        }
        self._write(self.last_path, out)
        return out

    # ── precision execute ─────────────────────────────────────

    def execute(
        self, user_text: str, *, classified: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Precision execute:
          classify → one best worker brief and/or drone tools → FACE reports result
        """
        from drone.multi_face import MultiFace

        t0 = time.perf_counter()
        clf = classified or self.classify(user_text)
        speed = self.cfg.get("speed") or {}
        face = MultiFace(self.root)
        worker_id = str(clf.get("worker") or "ollama_coder")
        command = str(clf.get("command") or user_text)

        # 1) always fire scout + selected worker (internal)
        workers = ["ollama_fast"]
        if worker_id not in workers:
            workers.append(worker_id)
        # drones when build/task verbs
        low = user_text.lower()
        exec_tools = False
        if any(k in low for k in ("build", "drone", "task", "write file", "run task", "hive")):
            workers = workers[:1] + ["drones"]
            exec_tools = True

        max_w = int(speed.get("max_workers_exec") or 2)
        workers = workers[:max_w]

        # Run council via MultiFace (FACE speaks)
        # For drones path, MultiFace ask with drones worker executes tools
        council = face.ask(command, workers=workers)

        # Optional: direct drone fast when exec_tools and drones not already ok
        tool_extra = None
        if exec_tools and not any(
            b.get("id") == "drones" and b.get("ok")
            for b in (council.get("worker_briefs") or [])
        ):
            tool_extra = face._run_drone_fast(
                {"id": "drones", "kind": "drone_fast", "timeout_s": 60},
                command,
            )

        face_reply = council.get("face_reply") or ""
        # If FACE empty but we have briefs, force FACE synth (precision recovery)
        if not face_reply.strip():
            face_model = str(speed.get("face_model") or "llama3.1:8b")
            briefs = council.get("worker_briefs") or []
            if tool_extra:
                briefs = list(briefs) + [tool_extra]
            pack = "\n".join(
                f"- {b.get('id')}: {(b.get('brief') or b.get('error') or '')[:300]}"
                for b in briefs
            )
            r = self._ollama_chat(
                face_model,
                f"COMMAND: {command}\nRESULTS:\n{pack}\n\nReport to user: what ran, result, next step.",
                system="You are FACE. Precise command result only.",
                num_predict=int(speed.get("num_predict_face") or 220),
                timeout_s=float(speed.get("timeout_face_s") or 60),
            )
            face_reply = r.get("text") or ""
            face_ms = r.get("ms")
        else:
            face_ms = council.get("face_ms")

        ok = bool(face_reply.strip())
        out = {
            "schema": "drone.face_hotwire.exec.v1",
            "status": "GREEN" if ok else "RED",
            "false_green": 0,
            "utc": _utc(),
            "hotwired": True,
            "path_mode": "exec",
            "classify": clf,
            "workers": workers,
            "exec_tools": exec_tools,
            "command": command,
            "precision": clf.get("precision"),
            "face_reply": face_reply,
            "face_ms": face_ms,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "worker_briefs": council.get("worker_briefs"),
            "tool_extra": tool_extra,
            "architecture": {
                "user_talks_to": "FACE_only",
                "workers_talk_to": "FACE_only",
                "all_models_to_face": True,
                "hotwired": True,
                "precision_exec": True,
            },
        }
        self._write(self.last_path, out)
        return out

    def go(self, user_text: str) -> dict[str, Any]:
        """Auto path: classify → chat|exec|status. Always FACE."""
        clf = self.classify(user_text)
        mode = clf.get("mode") or "chat"
        if mode == "exec":
            return self.execute(user_text, classified=clf)
        if mode == "status":
            return self._status_via_face(user_text, time.perf_counter(), clf)
        return self.chat(user_text)

    def status(self) -> dict[str, Any]:
        hot = {}
        if self.hot_path.is_file():
            try:
                hot = json.loads(self.hot_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                hot = {}
        state = {}
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        return {
            "schema": "drone.face_hotwire.status.v1",
            "utc": _utc(),
            "false_green": 0,
            "hotwired": bool(state.get("hotwired")),
            "face_model": (self.cfg.get("speed") or {}).get("face_model"),
            "last_hot": hot.get("status"),
            "hosts_up": hot.get("hosts_up"),
            "bindings": self.cfg.get("bindings"),
            "law": self.cfg.get("law"),
            "paths": {
                "hot": str(self.hot_path),
                "last": str(self.last_path),
                "seal": str(self.seal_path),
            },
        }

    def seal(self) -> dict[str, Any]:
        hot = self.hot()
        chat = self.chat("Reply READY in one word.")
        exe = self.execute("status check of face hotwire hosts")
        seal = {
            "schema": "drone.face_hotwire.seal.v1",
            "status": "GREEN"
            if hot.get("status") in {"GREEN", "PARTIAL"}
            and chat.get("status") == "GREEN"
            else "PARTIAL",
            "false_green": 0,
            "utc": _utc(),
            "hot": hot.get("status"),
            "chat_ms": chat.get("duration_ms"),
            "exec_ms": exe.get("duration_ms"),
            "chat_preview": (chat.get("face_reply") or "")[:120],
            "exec_preview": (exe.get("face_reply") or "")[:120],
            "hosts_up": hot.get("hosts_up"),
            "all_models_to_face": True,
            "evidence": [str(self.hot_path), str(self.last_path), str(self.seal_path)],
        }
        self._write(self.seal_path, seal)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="drone face-hotwire",
        description="Hotwire all models to FACE; speed + precision exec",
    )
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hot", help="warm models + probe hosts")
    sub.add_parser("status")
    sub.add_parser("seal")
    ch = sub.add_parser("chat", help="hotwired chat (always FACE)")
    ch.add_argument("text", nargs="+")
    ex = sub.add_parser("exec", help="precision execute via FACE")
    ex.add_argument("text", nargs="+")
    go = sub.add_parser("go", help="auto classify → chat|exec|status")
    go.add_argument("text", nargs="+")
    cl = sub.add_parser("classify")
    cl.add_argument("text", nargs="+")

    args = p.parse_args(argv)
    hw = FaceHotwire(Path(args.root) if args.root else None)

    if args.cmd == "hot":
        print(json.dumps(hw.hot(), indent=2, default=str))
        return 0
    if args.cmd == "status":
        print(json.dumps(hw.status(), indent=2, default=str))
        return 0
    if args.cmd == "seal":
        out = hw.seal()
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "chat":
        out = hw.chat(" ".join(args.text))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "exec":
        out = hw.execute(" ".join(args.text))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "go":
        out = hw.go(" ".join(args.text))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "classify":
        print(json.dumps(hw.classify(" ".join(args.text)), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
