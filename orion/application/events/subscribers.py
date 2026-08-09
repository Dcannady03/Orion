"""Minimal observation-only Event Bus subscribers."""
from __future__ import annotations

import logging

from orion.application.events.bus import EventDelivery


class DiagnosticEventLogger:
    """Write concise event facts to Python logging without producing events."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("orion.events")

    def handle_event(self, delivery: EventDelivery) -> None:
        event = delivery.event
        self.logger.info(
            "orion_event type=%s id=%s source=%s subject=%s correlation=%s replayed=%s",
            event.event_type,
            event.event_id,
            event.source,
            event.subject_id or "-",
            event.correlation_id or "-",
            delivery.replayed,
        )


class InMemoryEventSubscriber:
    """Bounded observer useful for diagnostics and tests."""

    def __init__(self, *, max_deliveries: int = 1_000) -> None:
        if (
            isinstance(max_deliveries, bool)
            or not isinstance(max_deliveries, int)
            or not 1 <= max_deliveries <= 10_000
        ):
            raise ValueError("In-memory subscriber limit must be between 1 and 10000.")
        self.max_deliveries = max_deliveries
        self.deliveries: list[EventDelivery] = []

    def handle_event(self, delivery: EventDelivery) -> None:
        if not isinstance(delivery, EventDelivery):
            raise TypeError("In-memory subscriber requires an EventDelivery.")
        if len(self.deliveries) >= self.max_deliveries:
            self.deliveries.pop(0)
        self.deliveries.append(delivery)
