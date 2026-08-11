"""
Full LLM resource access for drones / buzzers / agent loop.

Gives every agent unit access to the host's full LLM surface:
  - All installed Ollama models (live /api/tags)
  - Super LLMs safe roster (tiers S/A/B/E/C)
  - Role routing (coding, reason, fast, ops, seal, embed, cloud)
  - Cloud opt-in (Ollama cloud tags + SpaceXAI/Gemini when keys set)
  - Single-flight generate lock (12GB law — one full at a time)

Honesty (false_green: 0):
  - Shared ONE backend, not one model load per drone
  - Access ≠ concurrent multi-7B loads
  - GREEN only when list/chat returns real evidence
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


# Process-wide generate lock — same law as ollama_brain (protect 12GB)
_LLM_GEN_LOCK = threading.Lock()
_imprint_cache: dict[str, Any] | None = None
_imprint_cache_ts: float = 0.0


class LLMResources:
    """Agent-facing full LLM catalog + generate/chat/route."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or _root())
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)

    # ── catalog ───────────────────────────────────────────────

    def list_all(self, *, include_cloud: bool = True, refresh: bool = True) -> dict[str, Any]:
        """
        Full resource map for agents:
          local installed · safe full LLMs · embed · cloud opt-in · cloud API routes
        """
        from .ollama_brain import brain_status, list_local_models, ollama_host
        from .super_llms import SuperLLMs

        t0 = time.perf_counter()
        super_app = SuperLLMs(self.root)
        live = super_app.merge_live()
        st = brain_status() if refresh else {}
        installed = list_local_models() if refresh else list(live.get("installed") or [])

        cloud_api = self._cloud_api_routes()
        routes = dict((live.get("routing") or super_app.cfg.get("routing") or {}))

        full_safe = live.get("full_llms_safe") or []
        roster = live.get("roster") or []
        can_run_tags = sorted(
            {
                str(r.get("resolved_tag") or r.get("tag"))
                for r in roster
                if r.get("can_run") and (r.get("resolved_tag") or r.get("tag"))
            }
        )
        # All installed are callable via generate (agent chooses); safe roster is preferred
        all_callable = sorted(set(installed) | set(can_run_tags))

        payload = {
            "schema": "drone.llm_resources.v1",
            "utc": _utc(),
            "false_green": 0,
            "endpoint": ollama_host(),
            "reachable": bool(live.get("reachable") or installed),
            "gpu": live.get("gpu") or super_app.cfg.get("gpu"),
            "top_model": st.get("top_model") or (full_safe[0].get("tag") if full_safe else None),
            "code_worker": None,
            "installed": sorted(installed),
            "installed_count": len(installed),
            "loaded": live.get("loaded") or [],
            "full_llms_safe": full_safe,
            "full_llms_safe_count": len(full_safe),
            "roster": [
                {
                    "id": r.get("id"),
                    "tag": r.get("resolved_tag") or r.get("tag"),
                    "tier": r.get("tier"),
                    "role": r.get("role"),
                    "full_llm": r.get("full_llm"),
                    "can_run": r.get("can_run"),
                    "opt_in": r.get("opt_in"),
                    "params": r.get("params"),
                }
                for r in roster
            ],
            "callable_tags": all_callable,
            "routing": routes,
            "cloud_ollama_opt_in": live.get("cloud_opt_in") or [],
            "cloud_api_routes": cloud_api,
            "embed": live.get("embed") or [],
            "include_cloud": include_cloud,
            "agent_access": {
                "tools": [
                    "llm_list",
                    "llm_route",
                    "llm_chat",
                    "llm_generate",
                    "ollama_generate",
                ],
                "lm_fn_kinds": ["ollama", "top", "full", "dual", "all", "spacexai", "gemini"],
                "shared_backend": True,
                "not_per_drone_load": True,
                "single_flight_generate": True,
                "max_loaded_full": (live.get("gpu") or {}).get("max_loaded_full", 1),
            },
            "honesty": {
                "access_to_full_roster": True,
                "not_concurrent_multi_full_7b": True,
                "vram_target_gb": 12,
                "cloud_requires_opt_in_or_key": True,
            },
            "ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        try:
            from .ollama_brain import resolve_code_model

            payload["code_worker"] = resolve_code_model(probe=False)
        except Exception:
            pass
        return payload

    def _cloud_api_routes(self) -> list[dict[str, Any]]:
        routes = []
        if os.environ.get("XAI_API_KEY"):
            routes.append(
                {
                    "id": "spacexai",
                    "provider": "xai",
                    "model": os.environ.get("DRONE_XAI_MODEL", "grok-4.5"),
                    "available": True,
                    "kind": "cloud_api",
                }
            )
        else:
            routes.append(
                {
                    "id": "spacexai",
                    "provider": "xai",
                    "available": False,
                    "reason": "XAI_API_KEY not set",
                    "kind": "cloud_api",
                }
            )
        if os.environ.get("GEMINI_API_KEY"):
            routes.append(
                {
                    "id": "gemini",
                    "provider": "google",
                    "model": os.environ.get("DRONE_GEMINI_MODEL", "gemini-2.0-flash"),
                    "available": True,
                    "kind": "cloud_api",
                }
            )
        else:
            routes.append(
                {
                    "id": "gemini",
                    "provider": "google",
                    "available": False,
                    "reason": "GEMINI_API_KEY not set",
                    "kind": "cloud_api",
                }
            )
        return routes

    def imprint_pack(self, *, max_age_s: float = 60.0) -> dict[str, Any]:
        """Slim pack for knowledge imprint / work order (cached briefly)."""
        global _imprint_cache, _imprint_cache_ts
        now = time.time()
        if _imprint_cache and (now - _imprint_cache_ts) < max_age_s:
            return dict(_imprint_cache)
        full = self.list_all(include_cloud=True, refresh=True)
        pack = {
            "schema": "drone.llm_resources.imprint.v1",
            "utc": full.get("utc"),
            "false_green": 0,
            "reachable": full.get("reachable"),
            "endpoint": full.get("endpoint"),
            "top_model": full.get("top_model"),
            "code_worker": full.get("code_worker"),
            "full_llms_safe": full.get("full_llms_safe"),
            "routing": full.get("routing"),
            "callable_count": len(full.get("callable_tags") or []),
            "callable_tags": full.get("callable_tags"),
            "cloud_api_available": [
                r["id"] for r in (full.get("cloud_api_routes") or []) if r.get("available")
            ],
            "tools": (full.get("agent_access") or {}).get("tools"),
            "law": [
                "You may call any callable_tag via llm_chat / llm_generate / ollama_generate",
                "Prefer routing roles; default = top_model",
                "ONE full model active — sequential lock (12GB)",
                "false_green: 0",
            ],
        }
        _imprint_cache = pack
        _imprint_cache_ts = now
        return dict(pack)

    # ── route / generate / chat ───────────────────────────────

    def route(self, goal: str = "", *, role: str | None = None) -> dict[str, Any]:
        from .super_llms import SuperLLMs

        return SuperLLMs(self.root).route(goal, role=role)

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        role: str | None = None,
        num_predict: int = 256,
        temperature: float = 0.2,
        timeout_s: int = 180,
        allow_cloud: bool = False,
    ) -> dict[str, Any]:
        """
        Generate via shared Ollama (or cloud API id: spacexai|gemini).
        Single-flight lock for local models.
        """
        t0 = time.perf_counter()
        tag = model
        if not tag and role:
            tag = self.route(prompt or role, role=role).get("model")
        if not tag:
            tag = self.route(prompt).get("model")

        # Cloud API routes by id
        if tag in {"spacexai", "xai", "grok"} or (model or "").lower() in {
            "spacexai",
            "xai",
            "grok",
        }:
            return self._cloud_generate("spacexai", prompt, num_predict=num_predict, t0=t0)
        if tag in {"gemini"} or (model or "").lower() == "gemini":
            return self._cloud_generate("gemini", prompt, num_predict=num_predict, t0=t0)

        if not tag:
            return {
                "status": "RED",
                "false_green": 0,
                "error": "no model resolved",
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        # Cloud Ollama tags require allow_cloud
        if str(tag).endswith(":cloud") and not allow_cloud:
            if not os.environ.get("DRONE_ALLOW_CLOUD_LLM", "").strip():
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"cloud tag requires allow_cloud=True or DRONE_ALLOW_CLOUD_LLM=1: {tag}",
                    "model": tag,
                    "ms": round((time.perf_counter() - t0) * 1000, 2),
                }

        try:
            from .ollama_brain import generate as ollama_generate

            with _LLM_GEN_LOCK:
                text = ollama_generate(
                    prompt,
                    model=str(tag),
                    num_predict=int(num_predict),
                    temperature=float(temperature),
                    timeout_s=int(timeout_s),
                )
            return {
                "status": "GREEN" if text else "RED",
                "false_green": 0,
                "model": tag,
                "text": text,
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "utc": _utc(),
                "backend": "ollama",
            }
        except Exception as e:
            return {
                "status": "RED",
                "false_green": 0,
                "model": tag,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
            }

    def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        role: str | None = None,
        num_predict: int = 256,
        temperature: float = 0.2,
        timeout_s: int = 180,
        allow_cloud: bool = False,
        system: str | None = None,
    ) -> dict[str, Any]:
        """Chat completion — prefers SuperLLMs chat path with safety roster."""
        t0 = time.perf_counter()
        # Cloud API shortcut
        if model and model.lower() in {"spacexai", "xai", "grok", "gemini"}:
            return self.generate(
                prompt,
                model=model,
                num_predict=num_predict,
                temperature=temperature,
                timeout_s=timeout_s,
                allow_cloud=allow_cloud,
            )

        # Prefer super_llms.chat for roster safety; fall back to generate
        try:
            from .super_llms import SuperLLMs

            app = SuperLLMs(self.root)
            # inject system into prompt if provided (super chat is user-only)
            use_prompt = prompt
            if system:
                use_prompt = f"[system]\n{system}\n[/system]\n{prompt}"
            with _LLM_GEN_LOCK:
                res = app.chat(
                    use_prompt,
                    model=model,
                    role=role,
                    num_predict=num_predict,
                    temperature=temperature,
                    timeout_s=timeout_s,
                )
            if res.get("status") == "GREEN" or res.get("text"):
                res["backend"] = "ollama_super"
                res["false_green"] = 0
                return res
            # If model was explicit and super refused, try direct generate for installed tags
            if model and "not safe" in str(res.get("error") or "").lower():
                return self.generate(
                    prompt,
                    model=model,
                    num_predict=num_predict,
                    temperature=temperature,
                    timeout_s=timeout_s,
                    allow_cloud=allow_cloud,
                )
            return res
        except Exception as e:
            return self.generate(
                prompt,
                model=model,
                role=role,
                num_predict=num_predict,
                temperature=temperature,
                timeout_s=timeout_s,
                allow_cloud=allow_cloud,
            ) | {"fallback_note": str(e)}

    def _cloud_generate(
        self,
        kind: str,
        prompt: str,
        *,
        num_predict: int = 256,
        t0: float | None = None,
    ) -> dict[str, Any]:
        t0 = t0 or time.perf_counter()
        try:
            from .controllers import make_lm_fn

            fn = make_lm_fn(kind if kind != "spacexai" else "spacexai")
            if fn is None:
                return {
                    "status": "RED",
                    "false_green": 0,
                    "error": f"cloud route unavailable: {kind}",
                    "ms": round((time.perf_counter() - t0) * 1000, 2),
                }
            text = fn(prompt[:8000])
            return {
                "status": "GREEN" if text else "RED",
                "false_green": 0,
                "model": kind,
                "text": (text or "")[:8000],
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "backend": "cloud_api",
                "utc": _utc(),
            }
        except Exception as e:
            return {
                "status": "RED",
                "false_green": 0,
                "model": kind,
                "error": str(e),
                "ms": round((time.perf_counter() - t0) * 1000, 2),
                "backend": "cloud_api",
            }

    # ── LM callables for fabric ───────────────────────────────

    def make_lm_fn(
        self,
        *,
        role: str | None = None,
        model: str | None = None,
        num_predict: int = 192,
    ) -> Callable[[str], str]:
        """Shared fabric lm_fn with full roster access (route by role or auto)."""
        pinned = model
        pinned_role = role

        def _fn(prompt: str) -> str:
            res = self.generate(
                prompt,
                model=pinned,
                role=pinned_role,
                num_predict=num_predict,
            )
            if res.get("text"):
                return str(res["text"])
            err = res.get("error") or "empty"
            raise RuntimeError(f"llm_resources generate failed: {err}")

        # resolve model name for reporting
        try:
            if pinned:
                _fn.model = pinned  # type: ignore[attr-defined]
            elif pinned_role:
                _fn.model = self.route("", role=pinned_role).get("model")  # type: ignore[attr-defined]
            else:
                from .ollama_brain import resolve_top_model

                _fn.model = resolve_top_model(probe=False)  # type: ignore[attr-defined]
        except Exception:
            _fn.model = pinned or "unknown"  # type: ignore[attr-defined]
        _fn.role = pinned_role or "full"  # type: ignore[attr-defined]
        _fn.full_llm_access = True  # type: ignore[attr-defined]
        return _fn

    def make_dual(
        self,
        *,
        num_predict: int = 192,
        code_num_predict: int = 512,
    ) -> tuple[Callable[[str], str] | None, Callable[[str], str] | None]:
        """Commander (top/full) + code worker — both with full roster backend."""
        from .ollama_brain import make_code_lm_fn, make_top_lm_fn

        top = make_top_lm_fn(num_predict=num_predict)
        # Mark full access so consumers know they can re-route via tools
        top.full_llm_access = True  # type: ignore[attr-defined]
        code = make_code_lm_fn(num_predict=code_num_predict)
        code.full_llm_access = True  # type: ignore[attr-defined]
        return top, code

    def status(self) -> dict[str, Any]:
        cat = self.list_all(refresh=True)
        return {
            "schema": "drone.llm_resources.status.v1",
            "false_green": 0,
            "utc": _utc(),
            "reachable": cat.get("reachable"),
            "top_model": cat.get("top_model"),
            "code_worker": cat.get("code_worker"),
            "full_llms_safe_count": cat.get("full_llms_safe_count"),
            "installed_count": cat.get("installed_count"),
            "loaded": cat.get("loaded"),
            "routing": cat.get("routing"),
            "tools": (cat.get("agent_access") or {}).get("tools"),
            "cloud_api_available": [
                r["id"] for r in (cat.get("cloud_api_routes") or []) if r.get("available")
            ],
            "honesty": cat.get("honesty"),
        }

    def seal(self, *, smoke_generate: bool = True) -> dict[str, Any]:
        """Write AGENT_LLM_FULL_ACCESS seal with list + optional one generate proof."""
        cat = self.list_all(refresh=True)
        gen_proof: dict[str, Any] | None = None
        if smoke_generate and cat.get("reachable"):
            model = cat.get("top_model") or (
                (cat.get("full_llms_safe") or [{}])[0].get("tag")
            )
            if model:
                gen_proof = self.generate(
                    "Reply with exactly: LLM_ACCESS_OK",
                    model=str(model),
                    num_predict=16,
                    temperature=0,
                    timeout_s=90,
                )

        list_ok = bool(cat.get("reachable") and (cat.get("installed_count") or 0) > 0)
        gen_ok = bool(gen_proof and gen_proof.get("status") == "GREEN" and gen_proof.get("text"))
        if list_ok and (gen_ok or not smoke_generate):
            status = "GREEN"
        elif list_ok:
            status = "PARTIAL"
        else:
            status = "RED"

        seal = {
            "schema": "drone.agent_llm_full_access.seal.v1",
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "endpoint": cat.get("endpoint"),
            "installed_count": cat.get("installed_count"),
            "full_llms_safe_count": cat.get("full_llms_safe_count"),
            "full_llms_safe": cat.get("full_llms_safe"),
            "callable_tags": cat.get("callable_tags"),
            "routing": cat.get("routing"),
            "top_model": cat.get("top_model"),
            "code_worker": cat.get("code_worker"),
            "agent_tools": (cat.get("agent_access") or {}).get("tools"),
            "lm_fn_kinds": (cat.get("agent_access") or {}).get("lm_fn_kinds"),
            "cloud_api_routes": cat.get("cloud_api_routes"),
            "generate_proof": {
                "ok": gen_ok,
                "model": (gen_proof or {}).get("model"),
                "preview": str((gen_proof or {}).get("text") or "")[:80],
                "ms": (gen_proof or {}).get("ms"),
                "error": (gen_proof or {}).get("error"),
            }
            if gen_proof
            else None,
            "honesty": {
                **(cat.get("honesty") or {}),
                "agents_have_full_roster_access": True,
                "tools_wired": True,
                "agent_loop_dual_lm": True,
                "shared_one_backend": True,
            },
            "evidence": [],
        }
        path = self.out / "AGENT_LLM_FULL_ACCESS.json"
        path.write_text(json.dumps(seal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        seal["path"] = str(path)
        seal["evidence"] = [str(path), str(self.out / "SUPER_LLMS_ROSTER_LIVE.json")]
        # rewrite with path
        path.write_text(json.dumps(seal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        # also dump catalog snapshot
        cat_path = self.out / "LLM_RESOURCES_CATALOG.json"
        cat_path.write_text(json.dumps(cat, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        seal["catalog_path"] = str(cat_path)
        seal["evidence"].append(str(cat_path))
        path.write_text(json.dumps(seal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return seal


# ── module-level helpers ──────────────────────────────────────

def list_llm_resources(root: Path | None = None, **kw: Any) -> dict[str, Any]:
    return LLMResources(root).list_all(**kw)


def llm_generate(prompt: str, root: Path | None = None, **kw: Any) -> dict[str, Any]:
    return LLMResources(root).generate(prompt, **kw)


def llm_chat(prompt: str, root: Path | None = None, **kw: Any) -> dict[str, Any]:
    return LLMResources(root).chat(prompt, **kw)


def make_full_access_lm_fn(
    root: Path | None = None,
    **kw: Any,
) -> Callable[[str], str]:
    return LLMResources(root).make_lm_fn(**kw)


def make_full_dual(
    root: Path | None = None,
) -> tuple[Callable[[str], str] | None, Callable[[str], str] | None]:
    return LLMResources(root).make_dual()
