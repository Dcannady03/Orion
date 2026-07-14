"""Common contracts for Orion integrations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


class ServiceState(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    state: ServiceState
    message: str

    @property
    def available(self) -> bool:
        return self.state is ServiceState.AVAILABLE


@dataclass(frozen=True, slots=True)
class ServiceResult:
    success: bool
    output: str = ""
    data: Mapping[str, Any] | None = None
    error: str = ""


@runtime_checkable
class OrionService(Protocol):
    """Small stable contract shared by external Orion services."""

    @property
    def name(self) -> str: ...

    def is_available(self) -> bool: ...

    def get_status(self) -> ServiceStatus: ...

    def handle_request(self, request: str) -> ServiceResult: ...
