"""
Shared Ollama brain for the drone fabric / buzzer hive.

Top model policy (12GB RTX 3060, honest):
  Prefer largest *installed* quality model that fits:
    1) gemma4:12b  (~7GB)  — TOP on this host if present
    2) llama3.1:8b
    3) qwen2.5-coder:7b  (coding bias)
    4) qwen2.5:7b / mistral:7b
    5) smaller fallbacks

ONE shared backend — not one model load per drone.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


# Preference order: best *working* quality for this VRAM class.
# Note (host truth 2026-08-11): gemma4:12b is installed but returns empty chat
# content on BOSS — demoted until it answers. llama3.1:8b / qwen2.5-coder:7b win.
TOP_PREFERENCE = [
    "llama3.1:8b",
    "qwen2.5-coder:7b",
    "qwen2.5:7b",
    "mistral:7b",
    "gemma4:12b",
    "qwen2.5:3b",
    "llama3.2:3b",
    "ai-smarts:latest",
]

_lock = threading.Lock()
_resolved_model: str | None = None
_tags_cache: list[str] | None = None
_broken_models: set[str] = set()


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def list_local_models(timeout_s: int = 10) -> list[str]:
    global _tags_cache
    try:
        req = urllib.request.Request(f"{ollama_host()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models") or [] if m.get("name")]
        _tags_cache = names
        return names
    except Exception:
        return list(_tags_cache or [])


def _model_smoke_ok(model: str, timeout_s: int = 45) -> bool:
    """True only if model returns non-empty content (host truth)."""
    if model in _broken_models:
        return False
    try:
        host = ollama_host()
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "stream": False,
                "options": {"num_predict": 8},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = str((data.get("message") or {}).get("content", "")).strip()
        if text:
            return True
        _broken_models.add(model)
        return False
    except Exception:
        _broken_models.add(model)
        return False


def resolve_top_model(force_refresh: bool = False, probe: bool = True) -> str:
    """Pick best installed *working* model. Env DRONE_OLLAMA_MODEL wins if it works."""
    global _resolved_model
    with _lock:
        if _resolved_model and not force_refresh:
            return _resolved_model

        env_m = (os.environ.get("DRONE_OLLAMA_MODEL") or "").strip()
        installed = list_local_models()
        installed_set = set(installed)
        installed_base = {n.split(":")[0] for n in installed}

        def present(tag: str) -> str | None:
            if tag in installed_set:
                return tag
            base = tag.split(":")[0]
            for n in installed:
                if n == tag or n.startswith(base + ":"):
                    return n
            if base in installed_base:
                for n in installed:
                    if n.startswith(base):
                        return n
            return None

        candidates: list[str] = []
        if env_m:
            hit = present(env_m) or env_m
            candidates.append(hit)
        for pref in TOP_PREFERENCE:
            hit = present(pref)
            if hit and hit not in candidates:
                candidates.append(hit)
        for n in installed:
            if n not in candidates and not n.endswith(":cloud"):
                candidates.append(n)

        if not probe:
            _resolved_model = candidates[0] if candidates else "llama3.1:8b"
            return _resolved_model

        for c in candidates:
            if _model_smoke_ok(c):
                _resolved_model = c
                os.environ["DRONE_OLLAMA_MODEL"] = c
                return c

        # last resort: name only (may fail later — honesty)
        _resolved_model = candidates[0] if candidates else "llama3.1:8b"
        return _resolved_model


def brain_status() -> dict[str, Any]:
    models = list_local_models()
    top = resolve_top_model(force_refresh=True)
    return {
        "host": ollama_host(),
        "top_model": top,
        "installed": models,
        "preference": TOP_PREFERENCE,
        "env_override": os.environ.get("DRONE_OLLAMA_MODEL") or None,
        "reachable": bool(models) or _ping(),
        "honesty": {
            "shared_one_backend": True,
            "not_per_drone_model": True,
            "vram_target_gb": 12,
        },
    }


def _ping(timeout_s: int = 3) -> bool:
    try:
        req = urllib.request.Request(f"{ollama_host()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception:
        return False


_gen_lock = threading.Lock()


def generate(
    prompt: str,
    *,
    model: str | None = None,
    num_predict: int = 192,
    temperature: float = 0.2,
    timeout_s: int = 180,
) -> str:
    """
    Single-flight generate — protects 12GB from dual-hemisphere double-load thrash.
    Tries /api/chat first (better for gemma/instruct), falls back to /api/generate.
    """
    model = model or resolve_top_model()
    host = ollama_host()

    def _chat() -> str:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_GREETING. NO_FLUFF. "
                            "OUTPUT=DENSE_TECHNICAL. Short practical only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {
                    "num_predict": int(num_predict),
                    "temperature": float(temperature),
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data.get("message") or {}
        return str(msg.get("content", "")).strip()

    def _gen() -> str:
        body = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": int(num_predict),
                    "temperature": float(temperature),
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("response", "")).strip()

    with _gen_lock:
        try:
            text = _chat()
            if text:
                return text
        except Exception:
            pass
        text = _gen()
        if not text:
            raise RuntimeError(f"ollama empty response model={model}")
        return text


def make_top_lm_fn(
    num_predict: int = 192,
    roles_heavy: bool = True,
) -> Callable[[str], str]:
    """
    Shared LM callable for fabric. Uses top resolved Ollama model.
    Thread-safe enough for low parallel (hive caps workers when LM on).
    """
    model = resolve_top_model()
    # pin env so tools.ollama_generate matches
    os.environ["DRONE_OLLAMA_MODEL"] = model

    def _fn(prompt: str) -> str:
        return generate(prompt, model=model, num_predict=num_predict)

    _fn.model = model  # type: ignore[attr-defined]
    return _fn


# Code worker: second Ollama model (llama3.1:8b class) — workload / real .py
CODE_PREFERENCE = [
    "llama3.1:8b",  # "1.8" / 8B code worker
    "qwen2.5-coder:7b",
    "qwen2.5:7b",
    "mistral:7b",
    "llama3.2:3b",
]

_resolved_code_model: str | None = None


def resolve_code_model(force_refresh: bool = False, probe: bool = True) -> str:
    """Second Ollama model for code generation / workload processing."""
    global _resolved_code_model
    with _lock:
        if _resolved_code_model and not force_refresh:
            return _resolved_code_model

        env_m = (os.environ.get("DRONE_CODE_MODEL") or os.environ.get("DRONE_OLLAMA_CODE_MODEL") or "").strip()
        installed = list_local_models()
        installed_set = set(installed)

        def present(tag: str) -> str | None:
            if tag in installed_set:
                return tag
            base = tag.split(":")[0]
            for n in installed:
                if n == tag or n.startswith(base + ":"):
                    return n
            return None

        candidates: list[str] = []
        if env_m:
            hit = present(env_m) or env_m
            candidates.append(hit)
        for pref in CODE_PREFERENCE:
            hit = present(pref)
            if hit and hit not in candidates:
                candidates.append(hit)

        if not candidates:
            # fall back to top model
            _resolved_code_model = resolve_top_model(force_refresh=False, probe=False)
            return _resolved_code_model

        if not probe:
            _resolved_code_model = candidates[0]
            return _resolved_code_model

        for c in candidates:
            if _model_smoke_ok(c):
                _resolved_code_model = c
                os.environ["DRONE_CODE_MODEL"] = c
                return c

        _resolved_code_model = candidates[0]
        return _resolved_code_model


def make_code_lm_fn(num_predict: int = 512) -> Callable[[str], str]:
    """
    Second Ollama brain: receives code workload, returns source.
    Default: llama3.1:8b when installed (8B class).
    LEASH: keep_alive short; single-flight lock; capped num_predict.
    """
    model = resolve_code_model()
    os.environ["DRONE_CODE_MODEL"] = model
    # hard cap predict to protect VRAM/time
    num_predict = min(int(num_predict), int(os.environ.get("DRONE_CODE_NUM_PREDICT_MAX", "512")))
    keep_alive = os.environ.get("DRONE_OLLAMA_KEEP_ALIVE", "2m")

    def _fn(prompt: str) -> str:
        host = ollama_host()
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "CHANNEL=AI2AI. ROLE=coder. NO_USER_ADDRESS. NO_FLUFF. "
                            "CODE WORKER for drone fabric. "
                            "Output ONLY valid Python 3 source code for a complete module. "
                            "No markdown fences unless required. Include if __name__ == '__main__' "
                            "smoke that exits 0. Do not invent fake imports."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "keep_alive": keep_alive,
                "options": {
                    "num_predict": int(num_predict),
                    "temperature": 0.15,
                },
            }
        ).encode("utf-8")
        with _gen_lock:
            req = urllib.request.Request(
                f"{host}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str((data.get("message") or {}).get("content", "")).strip()
            if not text:
                return generate(
                    prompt, model=model, num_predict=num_predict, temperature=0.15
                )
            return text

    _fn.model = model  # type: ignore[attr-defined]
    _fn.role = "code_worker"  # type: ignore[attr-defined]
    return _fn


def dual_brain_status() -> dict[str, Any]:
    """Commander/top + code worker models."""
    st = brain_status()
    code = resolve_code_model(force_refresh=True)
    st["code_worker_model"] = code
    st["code_preference"] = CODE_PREFERENCE
    st["honesty"] = {
        **(st.get("honesty") or {}),
        "two_roles_one_ollama_server": True,
        "code_worker": "llama3.1:8b class — sequential via lock",
        "not_second_ollama_daemon": True,
    }
    return st


def load_ollama_config(root: Path) -> dict[str, Any]:
    path = Path(root) / "configs" / "ollama_top.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "default_model": "gemma4:12b",
        "preference": TOP_PREFERENCE,
        "num_predict": 192,
        "lm_roles": [
            "plan",
            "pattern",
            "execute",
            "critic",
            "revise",
            "refine",
            "evolve",
            "seal",
        ],
    }
