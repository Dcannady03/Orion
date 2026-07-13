"""Persistent, workspace-owned conversation history service."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from orion.conversation.message import ConversationMessage


class ConversationService:
    """Store and retrieve conversation messages in ``.orion/conversations``."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._lock = RLock()
        self._root = Path(workspace_root).expanduser().resolve()

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def conversation_dir(self) -> Path:
        return self._root / ".orion" / "conversations"

    def bind(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        self._root = root

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _path(self, day: str | None = None) -> Path:
        return self.conversation_dir / f"{day or self._day()}.json"

    @staticmethod
    def _read(path: Path) -> list[ConversationMessage]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read conversation history from {path.name}: {exc}") from exc
        if not isinstance(raw, list):
            raise ValueError(f"Conversation history in {path.name} must be a JSON array.")
        return [ConversationMessage.from_dict(item) for item in raw]

    @staticmethod
    def _write(path: Path, messages: list[ConversationMessage]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        payload = [message.to_dict() for message in messages]
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(path)

    def add(self, role: str, content: str) -> ConversationMessage:
        message = ConversationMessage.create(role, content)
        with self._lock:
            path = self._path()
            messages = self._read(path)
            messages.append(message)
            self._write(path, messages)
        return message

    def recent(self, limit: int = 10) -> list[ConversationMessage]:
        if limit < 1:
            raise ValueError("Conversation limit must be at least 1.")
        files = sorted(self.conversation_dir.glob("*.json"), reverse=True) if self.conversation_dir.exists() else []
        messages: list[ConversationMessage] = []
        for path in files:
            messages = self._read(path) + messages
            if len(messages) >= limit:
                break
        return messages[-limit:]

    def search(self, query: str, limit: int = 20) -> list[ConversationMessage]:
        term = query.strip().casefold()
        if not term:
            raise ValueError("Conversation search query cannot be empty.")
        results = [message for message in self.recent(1000) if term in message.content.casefold()]
        return results[-limit:]

    def clear_today(self) -> int:
        with self._lock:
            path = self._path()
            count = len(self._read(path))
            if path.exists():
                path.unlink()
            return count

    def count(self) -> int:
        return len(self.recent(100000))
