"""Composable, fault-isolated briefing architecture for Orion Morning Star."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Protocol, runtime_checkable


class BriefingPriority(IntEnum):
    """Lower values render first so urgent information leads the briefing."""

    CRITICAL = 10
    IMPORTANT = 20
    INFORMATIONAL = 30


@dataclass(frozen=True, slots=True)
class BriefingItem:
    """One truthful piece of information contributed by a briefing provider."""

    title: str
    message: str
    priority: BriefingPriority = BriefingPriority.INFORMATIONAL
    source: str = "system"
    icon: str = "-"

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Briefing item title cannot be empty.")
        if not self.message.strip():
            raise ValueError("Briefing item message cannot be empty.")
        if not self.source.strip():
            raise ValueError("Briefing item source cannot be empty.")


@runtime_checkable
class BriefingProvider(Protocol):
    """Contract implemented by services that contribute startup information."""

    @property
    def name(self) -> str: ...

    def get_briefing(self) -> Iterable[BriefingItem]: ...


@dataclass(frozen=True, slots=True)
class BriefingProviderError:
    provider: str
    message: str


@dataclass(frozen=True, slots=True)
class Briefing:
    items: tuple[BriefingItem, ...]
    errors: tuple[BriefingProviderError, ...] = ()


class BriefingService:
    """Collect and prioritize information without coupling startup to providers."""

    def __init__(self) -> None:
        self._providers: dict[str, BriefingProvider] = {}

    @staticmethod
    def _key(name: str) -> str:
        value = " ".join(name.strip().lower().split())
        if not value:
            raise ValueError("Briefing provider name cannot be empty.")
        return value

    def register_provider(self, provider: BriefingProvider, *, replace: bool = False) -> None:
        if not isinstance(provider, BriefingProvider):
            raise TypeError("Provider must implement the BriefingProvider contract.")
        key = self._key(provider.name)
        if key in self._providers and not replace:
            raise KeyError(f"Briefing provider is already registered: {provider.name}")
        self._providers[key] = provider

    def remove_provider(self, name: str) -> BriefingProvider:
        key = self._key(name)
        if key not in self._providers:
            raise KeyError(f"Briefing provider is not registered: {name}")
        return self._providers.pop(key)

    def provider_names(self) -> tuple[str, ...]:
        return tuple(provider.name for provider in self._providers.values())

    def build(self) -> Briefing:
        items: list[tuple[int, int, BriefingItem]] = []
        errors: list[BriefingProviderError] = []
        sequence = 0
        for provider in self._providers.values():
            try:
                provided = provider.get_briefing()
                for item in provided:
                    if not isinstance(item, BriefingItem):
                        raise TypeError("Provider returned a non-BriefingItem value.")
                    items.append((int(item.priority), sequence, item))
                    sequence += 1
            except Exception as exc:  # providers must never prevent Orion startup
                errors.append(BriefingProviderError(provider.name, str(exc)))
        items.sort(key=lambda value: (value[0], value[1]))
        return Briefing(tuple(value[2] for value in items), tuple(errors))


class SystemBriefingProvider:
    """Contribute only live facts already known by Orion's initialized core."""

    name = "System"

    def __init__(self, orion) -> None:
        self.orion = orion

    def get_briefing(self) -> tuple[BriefingItem, ...]:
        app_count = len(self.orion.application_catalog.applications())
        return (
            BriefingItem("Workspace", f"{self.orion.workspace_manager.root.name} is ready", source=self.name, icon="[OK]"),
            BriefingItem("AI", f"{self.orion.ai_provider.name()} is connected", source=self.name, icon="[OK]"),
            BriefingItem("Applications", f"{app_count} discovered", source=self.name, icon="[OK]"),
        )
