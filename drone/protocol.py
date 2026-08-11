"""Structured packets for AI-controller-only drones."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


ALLOWED_CONTROLLERS = frozenset(
    {"ollama", "gemini", "spacexai", "xai", "grok", "local"}
)


@dataclass
class ControllerIdentity:
    kind: str
    name: str = "parent"
    token: str = ""

    def validate(self) -> None:
        k = (self.kind or "").lower().strip()
        if k not in ALLOWED_CONTROLLERS:
            raise ValueError(
                f"controller kind '{self.kind}' not allowed; "
                f"drones are AI-controlled only: {sorted(ALLOWED_CONTROLLERS)}"
            )
        self.kind = k


@dataclass
class TaskEnvelope:
    """Parent AI → fabric task. Not a human chat message."""

    goal: str
    controller: ControllerIdentity
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    domain: str = "build"
    skill_tags: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    created_utc: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def validate(self) -> None:
        if not (self.goal or "").strip():
            raise ValueError("goal required")
        self.controller.validate()


@dataclass
class HandoffPacket:
    """Daisy-chain / callosum packet between drones."""

    from_node: str
    to_node: str
    goal: str
    task_id: str
    evidence: list[str] = field(default_factory=list)
    notes: str = ""
    next_action: str = ""
    veto: bool = False
    skill_tags: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NodeResult:
    node_id: str
    hemisphere: str
    ok: bool
    output: str
    evidence: list[str]
    skill_tags: list[str]
    xp_gain: int
    learned: dict[str, Any]
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
