"""Central registry for Orion services and skills."""

from __future__ import annotations

from collections.abc import Iterator
from threading import RLock
from typing import Any, TypeVar


T = TypeVar("T")


class ServiceRegistry:
    """Store and retrieve shared Orion services by normalized name.

    The registry owns no service lifecycle yet. It provides one canonical place
    for Orion subsystems to discover shared dependencies without using globals.
    """

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._lock = RLock()

    @staticmethod
    def _normalize(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("Service name must be a string.")
        normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized:
            raise ValueError("Service name cannot be empty.")
        if not normalized.replace("_", "").isalnum():
            raise ValueError("Service name may contain only letters, numbers, spaces, hyphens, and underscores.")
        return normalized

    def register(self, name: str, service: T, *, replace: bool = False) -> T:
        """Register a service and return the same instance."""
        normalized = self._normalize(name)
        if service is None:
            raise ValueError("Service instance cannot be None.")
        with self._lock:
            if normalized in self._services and not replace:
                raise KeyError(f"Service is already registered: {normalized}")
            self._services[normalized] = service
        return service

    def get(self, name: str, expected_type: type[T] | None = None) -> T:
        """Return a registered service or raise KeyError."""
        normalized = self._normalize(name)
        with self._lock:
            if normalized not in self._services:
                raise KeyError(f"Service is not registered: {normalized}")
            service = self._services[normalized]
        if expected_type is not None and not isinstance(service, expected_type):
            raise TypeError(
                f"Service '{normalized}' is {type(service).__name__}, "
                f"not {expected_type.__name__}."
            )
        return service

    def find(self, name: str, default: T | None = None) -> Any | T | None:
        """Return a service if present, otherwise a default value."""
        try:
            normalized = self._normalize(name)
        except (TypeError, ValueError):
            return default
        with self._lock:
            return self._services.get(normalized, default)

    def contains(self, name: str) -> bool:
        """Return whether a service name is registered."""
        try:
            normalized = self._normalize(name)
        except (TypeError, ValueError):
            return False
        with self._lock:
            return normalized in self._services

    def remove(self, name: str) -> Any:
        """Remove and return a service."""
        normalized = self._normalize(name)
        with self._lock:
            if normalized not in self._services:
                raise KeyError(f"Service is not registered: {normalized}")
            return self._services.pop(normalized)

    def names(self) -> tuple[str, ...]:
        """Return service names in registration order."""
        with self._lock:
            return tuple(self._services.keys())

    def snapshot(self) -> dict[str, Any]:
        """Return a detached mapping of registered services."""
        with self._lock:
            return dict(self._services)

    def __getitem__(self, name: str) -> Any:
        return self.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.contains(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __len__(self) -> int:
        with self._lock:
            return len(self._services)
