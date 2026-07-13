"""Conversation message model."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    timestamp: str

    @classmethod
    def create(cls, role: str, content: str) -> "ConversationMessage":
        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        text = content.strip()
        if not text:
            raise ValueError("Conversation content cannot be empty.")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return cls(normalized_role, text, stamp)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConversationMessage":
        return cls(str(value["role"]), str(value["content"]), str(value["timestamp"]))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
