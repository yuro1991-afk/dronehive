"""
AI-only channel protocol — strip human weights from models that talk only to AI.

FACE keeps human-facing language. All other models get CHANNEL=AI2AI systems.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_protocol(root: Path | None = None) -> dict[str, Any]:
    root = Path(root or _root())
    path = root / "configs" / "ai_protocol.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "channels": {
            "AI2AI": {
                "system_prefix": (
                    "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_GREETING. NO_FLUFF. "
                    "OUTPUT=DENSE_TECHNICAL."
                )
            },
            "FACE": {
                "system_prefix": "CHANNEL=FACE. TARGET=USER. Clear sentences."
            },
        },
        "roles": {},
        "banned_phrases": [],
    }


def system_for_role(
    role: str,
    *,
    root: Path | None = None,
    extra: str = "",
    talks_to_user: bool = False,
) -> str:
    """Build system prompt with human weights stripped for AI2AI roles."""
    proto = load_protocol(root)
    if talks_to_user:
        ch = (proto.get("channels") or {}).get("FACE") or {}
        prefix = str(ch.get("system_prefix") or "CHANNEL=FACE. TARGET=USER.")
    else:
        ch = (proto.get("channels") or {}).get("AI2AI") or {}
        prefix = str(
            ch.get("system_prefix")
            or "CHANNEL=AI2AI. NO_USER_ADDRESS. NO_FLUFF. OUTPUT=DENSE_TECHNICAL."
        )
    roles = proto.get("roles") or {}
    role_line = str(roles.get(role) or roles.get("default") or f"ROLE={role}.")
    parts = [prefix, role_line]
    if extra:
        parts.append(extra.strip())
    return " ".join(parts)


def system_tool_planner(root: Path | None = None, extra: str = "") -> str:
    proto = load_protocol(root)
    ch = (proto.get("channels") or {}).get("TOOL_PLANNER") or {}
    prefix = str(
        ch.get("system_prefix")
        or "CHANNEL=AI2AI_TOOL. JSON_ONLY. NO_USER_CHAT."
    )
    role = str((proto.get("roles") or {}).get("planner") or "ROLE=tool_planner.")
    return f"{prefix} {role} {extra}".strip()


def system_face(root: Path | None = None, extra: str = "") -> str:
    proto = load_protocol(root)
    ch = (proto.get("channels") or {}).get("FACE") or {}
    prefix = str(
        ch.get("system_prefix")
        or "CHANNEL=FACE. TARGET=USER. Clear complete sentences. User is boss."
    )
    role = str((proto.get("roles") or {}).get("face") or "ROLE=face_speaker.")
    return f"{prefix} {role} {extra}".strip()


def strip_human_fluff(text: str, root: Path | None = None) -> str:
    """Post-filter common human-assistant fluff from AI2AI outputs."""
    proto = load_protocol(root)
    banned = list(proto.get("banned_phrases") or [])
    out = text or ""
    for phrase in banned:
        out = re.sub(re.escape(phrase), "", out, flags=re.I)
    # strip leading greeting lines
    lines = []
    for line in out.splitlines():
        low = line.strip().lower()
        if low in {"hi!", "hello!", "hey!", "sure!", "absolutely!"}:
            continue
        if low.startswith(("hi ", "hello ", "hey ", "sure,", "certainly,")):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def apply_to_clone_systems(clones: list[dict[str, Any]], root: Path | None = None) -> list[dict[str, Any]]:
    """Rewrite clone SYSTEM strings by principal channel."""
    out = []
    for c in clones:
        c = dict(c)
        talks = bool(c.get("talks_to_user"))
        role = str(c.get("role") or c.get("id") or "default")
        # map clone roles to protocol roles
        role_map = {
            "face_speaker": "face",
            "intent_scout": "intent_scout",
            "fast_scout": "fast_scout",
            "ontology_worker": "ontology_worker",
            "code_worker": "code_worker",
            "ops_worker": "ops_worker",
            "critic_worker": "critic_worker",
            "tight_reason": "tight_reason",
            "cloud_opt_in": "cloud_worker",
            "embed_only": "default",
        }
        proto_role = role_map.get(role, role)
        if talks:
            # FACE only — human channel; do not attach worker role line
            ch = (load_protocol(root).get("channels") or {}).get("FACE") or {}
            prefix = str(
                ch.get("system_prefix")
                or "CHANNEL=FACE. TARGET=USER. Clear complete sentences."
            )
            c["system"] = (
                f"{prefix} ROLE=face_speaker. Synthesize AI2AI worker briefs into "
                "one user reply. Do not invent evidence. User is boss. false_green:0."
            )
        elif c.get("chat") is False:
            c["system"] = "CHANNEL=EMBED. NO_CHAT."
        else:
            c["system"] = system_for_role(proto_role, root=root, talks_to_user=False)
        c["strip_human_weights"] = not talks
        c["channel"] = "FACE" if talks else "AI2AI"
        out.append(c)
    return out
