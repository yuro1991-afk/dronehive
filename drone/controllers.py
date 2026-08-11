"""Parent AI controllers — only base models command drones."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

from .protocol import ALLOWED_CONTROLLERS, ControllerIdentity


def make_lm_fn(kind: str) -> Callable[[str], str] | None:
    """
    Optional shared LM assist for drones (ONE Ollama server, not per-drone model).

    kinds:
      ollama|top|local-ollama → top working model
      full|dual|all|resources  → full roster access (route + any tag via tools)
      coding|code              → coding route model
      fast|reason|ops|seal     → role-routed model from super_llms
      spacexai|xai|grok|gemini → cloud API (keys required)
    """
    k = (kind or "none").lower().strip()
    if k in {"none", "off", ""}:
        return None
    if k in {"ollama", "top", "local-ollama"}:
        from .ollama_brain import make_top_lm_fn

        return make_top_lm_fn(num_predict=int(os.environ.get("DRONE_NUM_PREDICT", "192")))
    if k in {"full", "dual", "all", "resources", "llm", "super"}:
        # Full LLM resource access — top callable + full_llm_access flag;
        # agents also get llm_list/chat/generate tools for any roster tag.
        from .llm_resources import make_full_access_lm_fn

        return make_full_access_lm_fn(
            num_predict=int(os.environ.get("DRONE_NUM_PREDICT", "192"))
        )
    if k in {"coding", "code", "coder"}:
        from .llm_resources import make_full_access_lm_fn

        return make_full_access_lm_fn(
            role="coding",
            num_predict=int(os.environ.get("DRONE_NUM_PREDICT", "256")),
        )
    if k in {"fast", "reason", "ops", "seal", "smarts", "embed"}:
        from .llm_resources import make_full_access_lm_fn

        return make_full_access_lm_fn(
            role=k,
            num_predict=int(os.environ.get("DRONE_NUM_PREDICT", "192")),
        )
    if k in {"spacexai", "xai", "grok"}:
        return _spacexai_fn
    if k == "gemini":
        return _gemini_fn
    if k == "local":
        # local controller without LM is fine; use none
        return None
    return None


def make_code_lm_fn(kind: str = "ollama") -> Callable[[str], str] | None:
    """
    Second Ollama model (llama3.1:8b class) for real .py workload.
    Same server, sequential lock — not a second daemon unless user sets host.
    """
    k = (kind or "none").lower().strip()
    if k in {"none", "off", ""}:
        return None
    if k in {"ollama", "top", "local-ollama", "code", "8b", "1.8"}:
        from .ollama_brain import make_code_lm_fn as _mk

        return _mk(num_predict=int(os.environ.get("DRONE_CODE_NUM_PREDICT", "768")))
    return None


def make_dual_ollama() -> tuple[Callable[[str], str] | None, Callable[[str], str] | None]:
    """Commander/top LM + code worker (8b)."""
    return make_lm_fn("ollama"), make_code_lm_fn("ollama")


def _ollama_fn(prompt: str) -> str:
    """Legacy direct ollama; prefer make_top_lm_fn."""
    from .ollama_brain import generate

    return generate(prompt)


def _spacexai_fn(prompt: str) -> str:
    key = os.environ.get("XAI_API_KEY", "")
    if not key:
        raise RuntimeError("XAI_API_KEY not set for spacexai controller assist")
    model = os.environ.get("DRONE_XAI_MODEL", "grok-4.5")
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 64,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.x.ai/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _gemini_fn(prompt: str) -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = os.environ.get("DRONE_GEMINI_MODEL", "gemini-2.0-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    body = json.dumps(
        {"contents": [{"parts": [{"text": prompt}]}]}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def validate_controller(kind: str, name: str = "parent") -> ControllerIdentity:
    # allow "ollama" as controller when commanding via local ollama parent
    k = (kind or "").lower().strip()
    if k in {"top", "local-ollama"}:
        k = "ollama"
    ident = ControllerIdentity(kind=k, name=name)
    ident.validate()
    return ident


def describe_controllers() -> dict[str, Any]:
    try:
        from .ollama_brain import brain_status

        ollama = brain_status()
    except Exception as e:
        ollama = {"error": str(e)}
    llm_res: dict[str, Any] = {}
    try:
        from .llm_resources import LLMResources

        llm_res = LLMResources().status()
    except Exception as e:
        llm_res = {"error": str(e)}
    return {
        "allowed": sorted(ALLOWED_CONTROLLERS),
        "human_viewer_chat": False,
        "note": "Drones accept tasks only from AI controllers, not viewer chat training.",
        "ollama_brain": ollama,
        "llm_resources": llm_res,
        "lm_fn_kinds": [
            "none",
            "ollama",
            "top",
            "full",
            "dual",
            "all",
            "coding",
            "fast",
            "reason",
            "ops",
            "seal",
            "spacexai",
            "gemini",
        ],
        "tools": "wired — llm_list/route/chat/generate + ollama_generate (full roster)",
    }
