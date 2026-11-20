"""Narrow best-effort publishing helper for application boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from orion.application.events.bus import EventBus, EventPublishResult
from orion.application.events.models import EventFactory, EventSeverity
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class EventPublication:
    event_id: str = ""
    warnings: tuple[str, ...] = ()
    result: EventPublishResult | None = None


class EventPublisher:
    """Construct and publish selected safe lifecycle facts."""

    def __init__(
        self,
        factory: EventFactory,
        bus: EventBus,
        *,
        enabled: bool = True,
    ) -> None:
        if not isinstance(factory, EventFactory):
            raise TypeError("Event Publisher requires an EventFactory.")
        if not isinstance(bus, EventBus):
            raise TypeError("Event Publisher requires an EventBus.")
        self.factory = factory
        self.bus = bus
        self.enabled = bool(enabled)

    def publish_lifecycle_event(
        self,
        *,
        event_type: str,
        source: str,
        severity: EventSeverity | str = EventSeverity.INFO,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        subject_id: str | None = None,
        data: Mapping[str, object] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> EventPublication:
        if not self.enabled:
            return EventPublication()
        try:
            event = self.factory.create(
                event_type=event_type,
                source=source,
                severity=severity,
                correlation_id=correlation_id,
                causation_id=causation_id,
                subject_id=subject_id,
                data=data,
                metadata=metadata,
            )
            result = self.bus.publish(event)
        except Exception as exc:
            return EventPublication(
                warnings=(
                    "The domain operation succeeded, but its observability event "
                    f"failed safely ({type(exc).__name__}).",
                ),
            )
        return EventPublication(
            event_id=event.event_id,
            warnings=result.warnings,
            result=result,
        )

    @staticmethod
    def attach(
        result: ApplicationResult,
        *publications: EventPublication,
    ) -> ApplicationResult:
        if not isinstance(result, ApplicationResult):
            raise TypeError("Event metadata can only attach to ApplicationResult.")
        event_ids = tuple(
            publication.event_id
            for publication in publications
            if publication.event_id
        )
        observability_warnings = tuple(
            warning
            for publication in publications
            for warning in publication.warnings
        )
        if not event_ids and not observability_warnings:
            return result
        data = dict(result.data)
        if event_ids:
            existing = tuple(data.get("event_ids", ()))
            data["event_ids"] = tuple(dict.fromkeys((*existing, *event_ids)))
        status = (
            "warning"
            if result.status == "success" and observability_warnings
            else result.status
        )
        return ApplicationResult(
            status,
            result.message,
            data=data,
            warnings=tuple(dict.fromkeys(
                (*result.warnings, *observability_warnings)
            )),
            errors=result.errors,
            next_actions=result.next_actions,
        )
