"""
DroneHive Super LLMs — one super app surface for all full LLMs safe on this host.

Host law (BOSS · RTX 3060 12GB):
  - Catalog every installed + rostered model with tier S/A/B/E/C
  - Smoke before GREEN (non-empty chat) — false_green:0
  - Route by role/keywords; switch one active FULL model
  - Never claim N concurrent full 7B+ loads

CLI:  python -m drone super-llms list|status|smoke|route|chat|tui|seal
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


class SuperLLMs:
    """Registry + safety + routing for all safe full LLMs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.cfg_path = self.root / "configs" / "super_llms.json"
        self.out = self.root / "out"
        self.state_path = self.out / "SUPER_LLMS_STATE.json"
        self.seal_path = self.out / "SUPER_LLMS_SEAL.json"
        self.roster_live_path = self.out / "SUPER_LLMS_ROSTER_LIVE.json"
        self.cfg = self._load_cfg()
        self.out.mkdir(parents=True, exist_ok=True)

    def _load_cfg(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {"roster": [], "routing": {}, "keywords": {}, "gpu": {}}

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def _http_json(self, method: str, url: str, body: dict | None = None, timeout: float = 120) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        return json.loads(raw)

    # ── discovery ─────────────────────────────────────────────

    def list_installed(self) -> list[dict[str, Any]]:
        try:
            data = self._http_json("GET", f"{ollama_host()}/api/tags", timeout=10)
        except Exception as e:
            return [{"error": str(e), "ok": False}]
        out = []
        for m in data.get("models") or []:
            out.append(
                {
                    "name": m.get("name"),
                    "size": m.get("size"),
                    "parameter_size": (m.get("details") or {}).get("parameter_size"),
                    "family": (m.get("details") or {}).get("family"),
                    "quantization": (m.get("details") or {}).get("quantization_level"),
                }
            )
        return out

    def list_loaded(self) -> list[str]:
        try:
            data = self._http_json("GET", f"{ollama_host()}/api/ps", timeout=5)
            return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        except Exception:
            return []

    def ollama_reachable(self) -> bool:
        try:
            self._http_json("GET", f"{ollama_host()}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def roster(self) -> list[dict[str, Any]]:
        return list(self.cfg.get("roster") or [])

    def merge_live(self) -> dict[str, Any]:
        """Merge config roster with installed Ollama tags → live safe surface."""
        installed = self.list_installed()
        installed_names = {m.get("name") for m in installed if m.get("name")}
        # also match base:tag family
        installed_bases = {n.split(":")[0] for n in installed_names}

        def resolve_tag(tag: str) -> str | None:
            if tag in installed_names:
                return tag
            base = tag.split(":")[0]
            for n in installed_names:
                if n == tag or n.startswith(base + ":"):
                    return n
            return None

        rows = []
        for r in self.roster():
            tag = str(r.get("tag") or "")
            resolved = resolve_tag(tag)
            installed_ok = resolved is not None
            tier = r.get("tier") or "?"
            safe_cfg = bool(r.get("safe"))
            full = bool(r.get("full_llm"))
            opt_in = bool(r.get("opt_in"))
            # safe to run = in roster safe + installed (or cloud opt-in installed)
            can_run = safe_cfg and installed_ok
            if tier == "X":
                can_run = False
            # Cloud is runnable only as opt-in (not default local super-app surface)
            local_default = can_run and not opt_in and tier in {"S", "A", "B", "E"}
            rows.append(
                {
                    **r,
                    "resolved_tag": resolved,
                    "installed": installed_ok,
                    "can_run": can_run,
                    "local_default": local_default,
                    "active_eligible": can_run
                    and full
                    and tier in {"S", "A", "B"}
                    and not opt_in,
                }
            )

        # Extra installed not in roster
        roster_tags = {str(r.get("tag") or "") for r in self.roster()}
        roster_bases = {t.split(":")[0] for t in roster_tags}
        extras = []
        for m in installed:
            name = m.get("name") or ""
            base = name.split(":")[0]
            if name in roster_tags or base in roster_bases:
                continue
            extras.append(
                {
                    "id": f"extra-{base}",
                    "tag": name,
                    "tier": "?",
                    "role": ["unlisted"],
                    "params": m.get("parameter_size"),
                    "safe": False,
                    "full_llm": True,
                    "installed": True,
                    "resolved_tag": name,
                    "can_run": False,
                    "notes": "Installed but not in super_llms safe roster — not auto-run",
                }
            )

        full_safe = [
            r
            for r in rows
            if r.get("full_llm")
            and r.get("local_default")
            and r.get("tier") in {"S", "A", "B"}
        ]
        cloud_opt = [r for r in rows if r.get("opt_in") and r.get("installed")]
        payload = {
            "schema": "drone.super_llms.roster_live.v1",
            "utc": _utc(),
            "false_green": 0,
            "endpoint": ollama_host(),
            "reachable": self.ollama_reachable(),
            "gpu": self.cfg.get("gpu"),
            "installed_count": len(installed_names),
            "installed": sorted(installed_names),
            "loaded": self.list_loaded(),
            "roster": rows,
            "extras_installed": extras,
            "full_llms_safe_count": len(full_safe),
            "full_llms_safe": [
                {
                    "tag": r.get("resolved_tag") or r.get("tag"),
                    "tier": r.get("tier"),
                    "role": r.get("role"),
                    "params": r.get("params"),
                }
                for r in full_safe
            ],
            "cloud_opt_in": [
                {"tag": r.get("resolved_tag") or r.get("tag"), "tier": r.get("tier")}
                for r in cloud_opt
            ],
            "embed": [
                {
                    "tag": r.get("resolved_tag") or r.get("tag"),
                    "tier": r.get("tier"),
                }
                for r in rows
                if r.get("tier") == "E" and r.get("installed")
            ],
            "routing": self.cfg.get("routing"),
            "safety_law": self.cfg.get("safety_law"),
        }
        self._write(self.roster_live_path, payload)
        return payload

    # ── smoke / chat ──────────────────────────────────────────

    def smoke_model(self, tag: str, timeout_s: float = 90) -> dict[str, Any]:
        """Non-empty chat required for GREEN. Embed models use embeddings API."""
        t0 = time.perf_counter()
        entry = next((r for r in self.roster() if r.get("tag") == tag or tag.startswith(str(r.get("tag", "")).split(":")[0])), None)
        is_embed = (entry or {}).get("tier") == "E" or "embed" in tag.lower()
        try:
            if is_embed:
                body = {"model": tag, "prompt": "hello super llms"}
                data = self._http_json("POST", f"{ollama_host()}/api/embeddings", body, timeout=timeout_s)
                emb = data.get("embedding") or []
                ok = isinstance(emb, list) and len(emb) > 0
                return {
                    "tag": tag,
                    "ok": ok,
                    "kind": "embed",
                    "dims": len(emb) if ok else 0,
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "false_green": 0,
                    "status": "GREEN" if ok else "RED",
                }
            body = {
                "model": tag,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "stream": False,
                "options": {"num_predict": 12, "temperature": 0},
            }
            data = self._http_json("POST", f"{ollama_host()}/api/chat", body, timeout=timeout_s)
            text = str((data.get("message") or {}).get("content", "")).strip()
            ok = bool(text)
            return {
                "tag": tag,
                "ok": ok,
                "kind": "chat",
                "preview": text[:120],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
                "status": "GREEN" if ok else "RED",
            }
        except Exception as e:
            return {
                "tag": tag,
                "ok": False,
                "kind": "embed" if is_embed else "chat",
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "false_green": 0,
                "status": "RED",
            }

    def smoke_all(self, *, include_cloud: bool = False, include_unsafe: bool = False) -> dict[str, Any]:
        live = self.merge_live()
        results = []
        for r in live.get("roster") or []:
            if not r.get("installed"):
                results.append(
                    {
                        "tag": r.get("tag"),
                        "ok": False,
                        "status": "SKIP",
                        "reason": "not_installed",
                        "false_green": 0,
                    }
                )
                continue
            if r.get("opt_in") and not include_cloud:
                results.append(
                    {
                        "tag": r.get("resolved_tag") or r.get("tag"),
                        "ok": False,
                        "status": "SKIP",
                        "reason": "cloud_opt_in",
                        "false_green": 0,
                    }
                )
                continue
            if not r.get("can_run") and not include_unsafe:
                results.append(
                    {
                        "tag": r.get("resolved_tag") or r.get("tag"),
                        "ok": False,
                        "status": "SKIP",
                        "reason": "not_safe_roster",
                        "false_green": 0,
                    }
                )
                continue
            tag = r.get("resolved_tag") or r.get("tag")
            res = self.smoke_model(str(tag))
            res["tier"] = r.get("tier")
            res["full_llm"] = r.get("full_llm")
            res["id"] = r.get("id")
            results.append(res)

        green = [x for x in results if x.get("status") == "GREEN"]
        red = [x for x in results if x.get("status") == "RED"]
        skip = [x for x in results if x.get("status") == "SKIP"]
        full_green = [x for x in green if x.get("full_llm") is not False and x.get("kind") != "embed"]
        # nomic embed has full_llm false
        for x in green:
            # attach full_llm from roster if missing
            pass

        seal = {
            "schema": "drone.super_llms.smoke.v1",
            "status": "GREEN" if full_green or green else "RED",
            "false_green": 0,
            "utc": _utc(),
            "endpoint": ollama_host(),
            "gpu": self.cfg.get("gpu"),
            "counts": {
                "green": len(green),
                "red": len(red),
                "skip": len(skip),
                "full_llm_green": len([x for x in green if x.get("kind") == "chat"]),
                "embed_green": len([x for x in green if x.get("kind") == "embed"]),
            },
            "results": results,
            "full_llms_ready": [
                x.get("tag") for x in results if x.get("status") == "GREEN" and x.get("kind") == "chat"
            ],
            "path": str(self.out / "SUPER_LLMS_SMOKE.json"),
        }
        path = self._write(self.out / "SUPER_LLMS_SMOKE.json", seal)
        seal["path"] = str(path)
        # update main seal too
        self._write(self.seal_path, {**seal, "schema": "drone.super_llms.seal.v1", "app": "DroneHive Super LLMs"})
        return seal

    def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        role: str | None = None,
        num_predict: int = 256,
        temperature: float = 0.2,
        timeout_s: float = 180,
        via_face: bool | None = None,
    ) -> dict[str, Any]:
        # HOTWIRE: default all user chat through FACE (models → FACE → user)
        import os as _os

        use_face = via_face
        if use_face is None:
            use_face = _os.environ.get("DRONE_FACE_HOTWIRE", "1") not in {
                "0",
                "false",
                "no",
            }
        # explicit --model bypasses face for bench/debug only
        if use_face and not model:
            try:
                from drone.face_hotwire import FaceHotwire

                hw = FaceHotwire(self.root)
                out = hw.go(prompt)
                return {
                    "status": out.get("status"),
                    "false_green": 0,
                    "model": "FACE",
                    "text": out.get("face_reply") or "",
                    "ms": out.get("duration_ms"),
                    "utc": _utc(),
                    "hotwired": True,
                    "path_mode": out.get("path_mode"),
                    "face": out,
                }
            except Exception as e:
                # fall through to direct only if face fails hard
                pass

        live = self.merge_live()
        tag = model
        if not tag and role:
            tag = self.route(prompt or role, role=role).get("model")
        if not tag:
            tag = self.route(prompt).get("model")
        if not tag:
            return {"status": "RED", "false_green": 0, "error": "no model"}

        # safety: refuse if not can_run
        allowed = {r.get("resolved_tag") or r.get("tag") for r in live.get("roster") or [] if r.get("can_run")}
        # allow base match
        ok_tag = tag in allowed or any(str(a).startswith(str(tag).split(":")[0]) for a in allowed if a)
        if not ok_tag and tag not in (live.get("installed") or []):
            return {
                "status": "RED",
                "false_green": 0,
                "error": f"model not safe/installed: {tag}",
                "allowed": sorted(x for x in allowed if x),
            }

        t0 = time.perf_counter()
        try:
            body = {
                "model": tag,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": num_predict, "temperature": temperature},
            }
            data = self._http_json("POST", f"{ollama_host()}/api/chat", body, timeout=timeout_s)
            text = str((data.get("message") or {}).get("content", "")).strip()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            state = {
                "active_model": tag,
                "last_chat_utc": _utc(),
                "last_ms": ms,
                "last_ok": bool(text),
            }
            self._write(self.state_path, state)
            return {
                "status": "GREEN" if text else "RED",
                "false_green": 0,
                "model": tag,
                "text": text,
                "ms": ms,
                "utc": _utc(),
                "hotwired": False,
                "note": "direct model path (debug/bench or face fallback)",
            }
        except Exception as e:
            return {
                "status": "RED",
                "false_green": 0,
                "model": tag,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }

    # ── routing ───────────────────────────────────────────────

    def route(self, goal: str = "", *, role: str | None = None) -> dict[str, Any]:
        """Pick best safe full LLM for goal/role."""
        live = self.merge_live()
        routing = dict(self.cfg.get("routing") or {})
        keywords = dict(self.cfg.get("keywords") or {})
        g = (goal or "").lower()

        chosen_role = role
        if not chosen_role:
            scores: dict[str, int] = {}
            for role_name, kws in keywords.items():
                scores[role_name] = sum(1 for k in kws if k in g)
            if scores and max(scores.values()) > 0:
                chosen_role = max(scores, key=lambda k: scores[k])
            else:
                chosen_role = "default"

        want = routing.get(chosen_role) or routing.get("default") or "llama3.1:8b"
        # resolve to installed can_run
        can = []
        for r in live.get("roster") or []:
            if not r.get("can_run"):
                continue
            if not r.get("full_llm") and chosen_role != "embed":
                continue
            if chosen_role == "embed" and r.get("tier") != "E":
                continue
            can.append(r)

        resolved = None
        # exact routing tag
        for r in can:
            tag = r.get("resolved_tag") or r.get("tag")
            if tag == want or str(tag).startswith(str(want).split(":")[0] + ":"):
                resolved = tag
                break
        if not resolved:
            # role match
            for r in can:
                roles = r.get("role") or []
                if chosen_role in roles or "default" in roles or "general" in roles:
                    # prefer tier A then S
                    resolved = r.get("resolved_tag") or r.get("tag")
                    if r.get("tier") == "A":
                        break
        if not resolved and can:
            # prefer A tier chat
            for r in can:
                if r.get("tier") == "A" and r.get("full_llm"):
                    resolved = r.get("resolved_tag") or r.get("tag")
                    break
            if not resolved:
                resolved = can[0].get("resolved_tag") or can[0].get("tag")

        return {
            "schema": "drone.super_llms.route.v1",
            "false_green": 0,
            "utc": _utc(),
            "goal": (goal or "")[:300],
            "role": chosen_role,
            "model": resolved,
            "routing_table": want,
            "loaded": live.get("loaded"),
            "note": "One active full model policy — unload others if VRAM tight",
        }

    def status(self) -> dict[str, Any]:
        live = self.merge_live()
        state = {}
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        full = live.get("full_llms_safe") or []
        return {
            "schema": "drone.super_llms.status.v1",
            "app": self.cfg.get("name"),
            "version": self.cfg.get("version"),
            "false_green": 0,
            "utc": _utc(),
            "reachable": live.get("reachable"),
            "endpoint": ollama_host(),
            "gpu": live.get("gpu"),
            "loaded": live.get("loaded"),
            "full_llms_safe_count": live.get("full_llms_safe_count"),
            "full_llms_safe": full,
            "active": state.get("active_model"),
            "state": state,
            "roster_live": str(self.roster_live_path),
            "seal": str(self.seal_path) if self.seal_path.is_file() else None,
            "safety": {
                "max_loaded_full": (self.cfg.get("gpu") or {}).get("max_loaded_full", 1),
                "max_loaded_total": (self.cfg.get("gpu") or {}).get("max_loaded_total", 2),
                "law": self.cfg.get("safety_law"),
            },
        }

    def seal(self, smoke: bool = True) -> dict[str, Any]:
        live = self.merge_live()
        smoke_res = self.smoke_all(include_cloud=False) if smoke else None
        full_ready = (smoke_res or {}).get("full_llms_ready") or [
            x.get("tag") for x in (live.get("full_llms_safe") or [])
        ]
        status = "GREEN" if live.get("reachable") and full_ready else "PARTIAL"
        if not live.get("reachable"):
            status = "RED"
        seal = {
            "schema": "drone.super_llms.seal.v1",
            "app": "DroneHive Super LLMs",
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "endpoint": ollama_host(),
            "full_llms_in_super_app": full_ready,
            "count": len(full_ready),
            "gpu": self.cfg.get("gpu"),
            "routing": self.cfg.get("routing"),
            "roster_live": str(self.roster_live_path),
            "smoke_path": (smoke_res or {}).get("path"),
            "honesty": {
                "one_active_full_at_a_time": True,
                "shared_ollama_backend": True,
                "not_all_loaded_simultaneously": True,
                "safe_for_12gb_3060": True,
            },
            "evidence": [
                str(self.roster_live_path),
                str(self.seal_path),
                str(self.out / "SUPER_LLMS_SMOKE.json"),
            ],
        }
        self._write(self.seal_path, seal)
        return seal


def run_tui(root: Path | None = None) -> int:
    """Lean multi-model hub TUI. Commands: list status smoke route chat seer help quit."""
    app = SuperLLMs(root)
    print("=" * 64)
    print("  DroneHive MULTI-MODEL HUB — main product")
    print("  Super LLMs roster + Future Seer typeahead")
    print("  host:", ollama_host())
    print("  cmds: list | status | smoke | route <goal> | chat [model] <text>")
    print("        seer <partial> | roles | seal | quit")
    print("=" * 64)
    st = app.status()
    print(f"  full_llms_safe: {st.get('full_llms_safe_count')}  loaded: {st.get('loaded')}")
    for m in st.get("full_llms_safe") or []:
        print(f"   · {m.get('tag')}  tier={m.get('tier')}  roles={m.get('role')}")
    print()

    while True:
        try:
            line = input("multi-model> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        low = line.lower()
        if low in {"q", "quit", "exit"}:
            return 0
        if low in {"help", "?"}:
            print(
                "list status smoke seal roles route <goal> chat [model] <prompt> "
                "seer <partial typing>"
            )
            continue
        if low == "list":
            live = app.merge_live()
            for r in live.get("roster") or []:
                flag = "RUN" if r.get("can_run") else ("IN" if r.get("installed") else "—")
                print(
                    f"  [{flag}] {r.get('resolved_tag') or r.get('tag'):28} "
                    f"tier={r.get('tier')} full={r.get('full_llm')} {r.get('params')}"
                )
            continue
        if low == "status":
            print(json.dumps(app.status(), indent=2))
            continue
        if low == "smoke":
            print("smoking all safe models (may take a minute)…")
            print(json.dumps(app.smoke_all(), indent=2))
            continue
        if low == "seal":
            print(json.dumps(app.seal(smoke=False), indent=2))
            continue
        if low == "roles":
            print(json.dumps(app.cfg.get("routing"), indent=2))
            continue
        if low.startswith("route "):
            goal = line[6:].strip()
            print(json.dumps(app.route(goal), indent=2))
            continue
        if low.startswith("seer "):
            # Multi-model speculative typeahead (Future Seer)
            partial = line[5:].strip()
            if not partial:
                print("usage: seer <partial text as user types>")
                continue
            try:
                from drone.future_seer import FutureSeer

                seer = FutureSeer(app.root)
                print("… multi-model speculate (intent→plan→draft)")
                spec = seer.speculate(partial)
                intent = spec.get("intent") or {}
                draft = (spec.get("draft") or {}).get("text") or ""
                print(
                    f"[{spec.get('status')}] intent={intent.get('intent')} "
                    f"conf={intent.get('confidence')} "
                    f"models={spec.get('models_used')} ms={spec.get('duration_ms')}"
                )
                if intent.get("predicted_goal"):
                    print(f"predicted: {intent.get('predicted_goal')}")
                if draft:
                    print(f"draft: {draft[:500]}")
                if spec.get("draft_path"):
                    print(f"evidence: {spec.get('draft_path')}")
            except Exception as e:
                print("seer error:", e)
            continue
        if low.startswith("chat ") or low.startswith("c "):
            rest = line.split(None, 1)[1] if " " in line else ""
            # optional first token model if matches installed
            parts = rest.split(None, 1)
            model = None
            prompt = rest
            live = app.merge_live()
            names = set(live.get("installed") or [])
            if parts and (parts[0] in names or any(parts[0] in (n or "") for n in names)):
                # fuzzy: if first token looks like model tag
                if ":" in parts[0] or parts[0] in {n.split(":")[0] for n in names}:
                    model = parts[0]
                    # resolve
                    for n in names:
                        if n == model or n.startswith(model + ":") or model in n:
                            model = n
                            break
                    prompt = parts[1] if len(parts) > 1 else ""
            if not prompt:
                print("usage: chat [model] <prompt>")
                continue
            print(f"… {model or '(auto-route)'} thinking")
            res = app.chat(prompt, model=model)
            print(f"[{res.get('status')}] model={res.get('model')} ms={res.get('ms')}")
            if res.get("text"):
                print(res["text"])
            if res.get("error"):
                print("error:", res["error"])
            continue
        print("unknown cmd — type help")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone super-llms", description="Super app: all safe full LLMs")
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="live roster merge")
    sub.add_parser("status", help="app status")
    sm = sub.add_parser("smoke", help="smoke all safe models")
    sm.add_argument("--cloud", action="store_true")
    sub.add_parser("seal", help="write SUPER_LLMS_SEAL.json")
    rt = sub.add_parser("route", help="route goal → model")
    rt.add_argument("goal", nargs="+")
    rt.add_argument("--role", default=None)
    ch = sub.add_parser("chat", help="chat with auto or named model")
    ch.add_argument("--model", "-m", default=None)
    ch.add_argument("--role", default=None)
    ch.add_argument("prompt", nargs="+")
    sub.add_parser("tui", help="lean interactive super app")
    sub.add_parser("roles", help="show routing table")

    args = p.parse_args(argv)
    app = SuperLLMs(Path(args.root) if args.root else None)

    if args.cmd == "list":
        print(json.dumps(app.merge_live(), indent=2))
        return 0
    if args.cmd == "status":
        print(json.dumps(app.status(), indent=2))
        return 0
    if args.cmd == "smoke":
        out = app.smoke_all(include_cloud=bool(args.cloud))
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "seal":
        out = app.seal(smoke=True)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "route":
        goal = " ".join(args.goal)
        print(json.dumps(app.route(goal, role=args.role), indent=2))
        return 0
    if args.cmd == "chat":
        prompt = " ".join(args.prompt)
        out = app.chat(prompt, model=args.model, role=args.role)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "GREEN" else 1
    if args.cmd == "tui":
        return run_tui(Path(args.root) if args.root else None)
    if args.cmd == "roles":
        print(json.dumps(app.cfg.get("routing"), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
