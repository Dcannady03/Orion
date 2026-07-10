"""In-memory working context for the current Orion process."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock


class SessionMemory:
    """Thread-safe key/value memory that lasts until Orion shuts down."""

    def __init__(self) -> None:
        self._items: dict[str, str] = {}
        self._lock = RLock()

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = str(key).strip().lower()
        if not normalized:
            raise ValueError("Memory key cannot be empty.")
        if any(character.isspace() for character in normalized):
            raise ValueError("Memory keys cannot contain spaces.")
        return normalized

    def set(self, key: str, value: object) -> str:
        """Store a value and return the normalized key."""
        normalized = self._normalize_key(key)
        text = str(value).strip()
        if not text:
            raise ValueError("Memory value cannot be empty.")
        with self._lock:
            self._items[normalized] = text
        return normalized

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return a stored value or a default when the key is missing."""
        normalized = self._normalize_key(key)
        with self._lock:
            return self._items.get(normalized, default)

    def exists(self, key: str) -> bool:
        """Return whether a key exists."""
        normalized = self._normalize_key(key)
        with self._lock:
            return normalized in self._items

    def delete(self, key: str) -> bool:
        """Delete a key, returning True only when it existed."""
        normalized = self._normalize_key(key)
        with self._lock:
            return self._items.pop(normalized, None) is not None

    def clear(self) -> int:
        """Clear all entries and return how many were removed."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def all(self) -> Mapping[str, str]:
        """Return a detached snapshot of all memory entries."""
        with self._lock:
            return dict(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
