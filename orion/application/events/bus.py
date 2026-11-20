"""Synchronous observation-only publish, subscribe, and replay."""
from __future__ import annotations

from dataclasses import dataclass
import re
from threading import RLock, local
from typing import Mapping, Protocol

from orion.application.events.models import OrionEvent
from orion.application.events.store import EventHistory, EventStore


@dataclass(frozen=True)
class EventDelivery:
    event: OrionEvent
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.event, OrionEvent):
            raise TypeError("Event delivery requires an OrionEvent.")
        if not isinstance(self.replayed, bool):
            raise TypeError("Event replay marker must be boolean.")

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event.to_dict(),
            "replayed": self.replayed,
        }


class EventSubscriber(Protocol):
    def handle_event(self, delivery: EventDelivery) -> None:
        """Observe one immutable event delivery."""


@dataclass(frozen=True)
class EventSubscription:
    subscription_id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "subscription_id": self.subscription_id,
            "name": self.name,
        }


@dataclass(frozen=True)
class EventPublishResult:
    event: OrionEvent
    persisted: bool
    delivered_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event.to_dict(),
            "persisted": self.persisted,
            "delivered_count": self.delivered_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EventReplayResult:
    delivered_count: int
    event_ids: tuple[str, ...]
    replayed: bool = True
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "delivered_count": self.delivered_count,
            "event_ids": list(self.event_ids),
            "replayed": self.replayed,
            "warnings": list(self.warnings),
        }


class EventBus:
    """Persist events, then synchronously notify isolated observers."""

    def __init__(self, store: EventStore | None) -> None:
        if store is not None and not isinstance(store, EventStore):
            raise TypeError("Event Bus store must be an EventStore or None.")
        self.store = store
        self._subscribers: dict[str, tuple[EventSubscription, EventSubscriber]] = {}
        self._lock = RLock()
        self._publishing = local()

    def subscribe(
        self,
        subscriber: EventSubscriber,
        *,
        name: str | None = None,
    ) -> str:
        self._validate_subscriber(subscriber)
        selected_name = str(name or type(subscriber).__name__).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", selected_name.casefold()).strip("-")
        if not slug or len(slug) > 80:
            raise ValueError("Event subscriber name is invalid.")
        subscription_id = f"subscription-{slug}"
        subscription = EventSubscription(subscription_id, selected_name)
        with self._lock:
            if subscription_id in self._subscribers:
                raise ValueError(
                    f"Event subscriber is already registered: {selected_name}"
                )
            self._subscribers[subscription_id] = (subscription, subscriber)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        normalized = str(subscription_id).strip().casefold()
        with self._lock:
            return self._subscribers.pop(normalized, None) is not None

    def list_subscribers(self) -> tuple[EventSubscription, ...]:
        with self._lock:
            return tuple(item[0] for item in self._subscribers.values())

    def publish(self, event: OrionEvent) -> EventPublishResult:
        validated = OrionEvent.from_value(event.to_dict())
        if (
            getattr(self._publishing, "active", False)
            or getattr(self._publishing, "replaying", False)
        ):
            raise RuntimeError(
                "Event publishing from a delivery callback is not allowed."
            )
        self._publishing.active = True
        try:
            persisted = False
            if self.store is not None:
                self.store.append(validated)
                persisted = True
            with self._lock:
                subscribers = tuple(self._subscribers.values())
            warnings: list[str] = []
            delivered = 0
            delivery = EventDelivery(validated, replayed=False)
            for subscription, subscriber in subscribers:
                try:
                    subscriber.handle_event(delivery)
                    delivered += 1
                except Exception as exc:  # Observer failures are isolated by design.
                    warnings.append(
                        f"Event subscriber '{subscription.name}' failed safely "
                        f"({type(exc).__name__})."
                    )
            return EventPublishResult(
                validated,
                persisted,
                delivered,
                tuple(warnings),
            )
        finally:
            self._publishing.active = False

    def replay(
        self,
        subscriber: EventSubscriber,
        *,
        filters: Mapping[str, object] | None = None,
        limit: int | None = None,
    ) -> EventReplayResult:
        self._validate_subscriber(subscriber)
        if self.store is None:
            raise RuntimeError("Event replay requires the Event Store.")
        selected = dict(filters or {})
        allowed = {
            "event_type",
            "correlation_id",
            "subject_id",
            "severity",
            "source",
            "start_time",
            "end_time",
        }
        unknown = set(selected) - allowed
        if unknown:
            raise ValueError(f"Unknown event replay filters: {sorted(unknown)}")
        history: EventHistory = self.store.history(limit=limit, **selected)
        warnings = list(history.warnings)
        delivered = 0
        event_ids: list[str] = []
        if (
            getattr(self._publishing, "active", False)
            or getattr(self._publishing, "replaying", False)
        ):
            raise RuntimeError("Nested Event Bus replay is not allowed.")
        self._publishing.replaying = True
        try:
            for event in reversed(history.events):
                try:
                    subscriber.handle_event(EventDelivery(event, replayed=True))
                    delivered += 1
                    event_ids.append(event.event_id)
                except Exception as exc:
                    warnings.append(
                        f"Replay subscriber failed safely ({type(exc).__name__})."
                    )
        finally:
            self._publishing.replaying = False
        return EventReplayResult(
            delivered,
            tuple(event_ids),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_subscriber(subscriber: EventSubscriber) -> None:
        if subscriber is None or not callable(getattr(subscriber, "handle_event", None)):
            raise TypeError(
                "Event subscriber must expose handle_event(EventDelivery)."
            )
