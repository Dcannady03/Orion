"""Read-only application boundary for Orion Event Bus diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

from orion.application.events.bus import EventBus
from orion.application.events.store import EventHistory, EventStore
from orion.application.events.types import KNOWN_EVENT_TYPES
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class EventReferenceRequest:
    event_id: str


@dataclass(frozen=True)
class EventHistoryRequest:
    event_type: str = ""
    correlation_id: str = ""
    subject_id: str = ""
    severity: str = ""
    source: str = ""
    start_time: str = ""
    end_time: str = ""
    limit: int | None = None

    def filters(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "event_type": self.event_type,
                "correlation_id": self.correlation_id,
                "subject_id": self.subject_id,
                "severity": self.severity,
                "source": self.source,
                "start_time": self.start_time,
                "end_time": self.end_time,
            }.items()
            if value
        }


class EventApplicationHandler:
    """Expose bounded event history without publishing or domain mutation."""

    def __init__(
        self,
        event_bus: EventBus,
        event_store: EventStore | None,
        *,
        enabled: bool = True,
        known_event_types: tuple[str, ...] = KNOWN_EVENT_TYPES,
    ) -> None:
        if not isinstance(event_bus, EventBus):
            raise TypeError("Event application handler requires an EventBus.")
        if event_store is not None and not isinstance(event_store, EventStore):
            raise TypeError("Event application handler store must be EventStore or None.")
        self.bus = event_bus
        self.store = event_store
        self.enabled = bool(enabled)
        self.known_event_types = tuple(known_event_types)

    def status(self) -> ApplicationResult:
        latest = None
        warnings: tuple[str, ...] = ()
        if self.store is not None:
            try:
                latest, warnings = self.store.latest_at()
            except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
                warnings = (
                    f"Event Store status is unavailable ({type(exc).__name__}).",
                )
        subscribers = self.bus.list_subscribers()
        data = {
            "enabled": self.enabled,
            "store_enabled": self.store is not None,
            "store_path": str(self.store.root) if self.store is not None else "",
            "subscriber_count": len(subscribers),
            "known_event_types": list(self.known_event_types),
            "latest_event_at": latest,
            "warnings": list(warnings),
            "read_only": True,
        }
        lines = [
            "Orion Event Bus",
            "-" * 68,
            f"Enabled      : {'YES' if self.enabled else 'NO'}",
            f"Store        : {'ENABLED' if self.store is not None else 'DISABLED'}",
            f"Subscribers  : {len(subscribers)}",
            f"Known types  : {len(self.known_event_types)}",
            f"Latest event : {latest or 'None'}",
            "Observation only; event commands cannot publish or execute actions.",
        ]
        return ApplicationResult.success(
            "\n".join(lines),
            data=data,
            warnings=warnings,
            next_actions=("events list", "events types", "events subscribers"),
        )

    def list(self, request: EventHistoryRequest) -> ApplicationResult:
        if not isinstance(request, EventHistoryRequest):
            return self._failure("Event history requires a structured request.")
        if self.store is None:
            return self._failure("Event Store is disabled; no history is available.")
        try:
            history = self.store.history(limit=request.limit, **request.filters())
        except (
            OSError,
            PermissionError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(
                f"Event history could not be read ({type(exc).__name__})."
            )
        return self._history_result(history)

    def show(self, request: EventReferenceRequest) -> ApplicationResult:
        if not isinstance(request, EventReferenceRequest):
            return self._failure("Event show requires a structured reference.")
        if self.store is None:
            return self._failure("Event Store is disabled; no event can be shown.")
        try:
            event, warnings = self.store.get(request.event_id)
        except FileNotFoundError:
            return self._failure(f"Event not found: {request.event_id}")
        except (
            OSError,
            PermissionError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(
                f"Event could not be read ({type(exc).__name__})."
            )
        message = "\n".join((
            "Orion Event",
            "-" * 68,
            f"ID          : {event.event_id}",
            f"Type        : {event.event_type}",
            f"Occurred    : {event.occurred_at}",
            f"Source      : {event.source}",
            f"Severity    : {event.severity.value}",
            f"Correlation : {event.correlation_id or 'None'}",
            f"Causation   : {event.causation_id or 'None'}",
            f"Subject     : {event.subject_id or 'None'}",
            "Observation only.",
        ))
        return ApplicationResult.success(
            message,
            data={
                **event.to_dict(),
                "event": event.to_dict(),
                "replayed": False,
                "read_only": True,
            },
            warnings=warnings,
            next_actions=tuple(filter(None, (
                (
                    f"events correlation {event.correlation_id}"
                    if event.correlation_id else ""
                ),
                (
                    f"events subject {event.subject_id}"
                    if event.subject_id else ""
                ),
            ))),
        )

    def correlation(self, correlation_id: str, *, limit: int | None = None) -> ApplicationResult:
        normalized = str(correlation_id).strip()
        if not normalized:
            return self._failure("Correlation ID is required.")
        return self.list(EventHistoryRequest(
            correlation_id=normalized,
            limit=limit,
        ))

    def subject(self, subject_id: str, *, limit: int | None = None) -> ApplicationResult:
        normalized = str(subject_id).strip()
        if not normalized:
            return self._failure("Subject ID is required.")
        return self.list(EventHistoryRequest(
            subject_id=normalized,
            limit=limit,
        ))

    def types(self) -> ApplicationResult:
        return ApplicationResult.success(
            "\n".join((
                "Orion Event Types",
                "-" * 68,
                *(f"  {item}" for item in self.known_event_types),
            )),
            data={
                "event_types": list(self.known_event_types),
                "count": len(self.known_event_types),
                "read_only": True,
            },
            next_actions=("events list --type <event-type>",),
        )

    def subscribers(self) -> ApplicationResult:
        subscribers = self.bus.list_subscribers()
        return ApplicationResult.success(
            "\n".join((
                "Orion Event Subscribers",
                "-" * 68,
                *(
                    (
                        f"  {item.subscription_id} | {item.name}"
                        for item in subscribers
                    )
                    if subscribers
                    else ("  No subscribers registered.",)
                ),
                "Subscribers observe only and return no application actions.",
            )),
            data={
                "subscribers": [item.to_dict() for item in subscribers],
                "count": len(subscribers),
                "read_only": True,
            },
        )

    @staticmethod
    def _history_result(history: EventHistory) -> ApplicationResult:
        lines = ["Orion Events", "-" * 68]
        if not history.events:
            lines.append("No matching events.")
        lines.extend(
            f"{item.occurred_at} | {item.severity.value:<8} | "
            f"{item.event_type} | {item.event_id}"
            for item in history.events
        )
        lines.append("Newest first. Observation only.")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                **history.to_dict(),
                "read_only": True,
                "replayed": False,
            },
            warnings=history.warnings,
            next_actions=("events show <event-id>",),
        )

    @staticmethod
    def _failure(message: str) -> ApplicationResult:
        return ApplicationResult.failure(
            message,
            data={"read_only": True},
            errors=(message,),
            next_actions=("events status",),
        )
