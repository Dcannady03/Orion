"""Core action models used by every Orion capability."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActionStatus(str, Enum):
    REQUESTED = "requested"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    DENIED = "denied"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Action:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "user"
    requires_approval: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)
    status: ActionStatus = ActionStatus.REQUESTED
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.type = self.type.strip().lower()
        self.source = self.source.strip().lower() or "user"
        if not self.type:
            raise ValueError("Action type cannot be empty.")
        if not isinstance(self.parameters, dict):
            raise TypeError("Action parameters must be a dictionary.")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    success: bool
    output: str = ""
    error: str = ""
    started_at: str = field(default_factory=utc_now)
    finished_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
