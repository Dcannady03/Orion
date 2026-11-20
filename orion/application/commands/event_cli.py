"""Thin read-only CLI adapter for Orion Event Bus history."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.events import (
    EventApplicationHandler,
    EventHistoryRequest,
    EventReferenceRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


_COMMANDS = frozenset({
    "status",
    "list",
    "show",
    "correlation",
    "subject",
    "types",
    "subscribers",
})
_LIST_OPTIONS = {
    "--type": "event_type",
    "--correlation": "correlation_id",
    "--subject": "subject_id",
    "--severity": "severity",
    "--source": "source",
    "--start": "start_time",
    "--end": "end_time",
    "--limit": "limit",
}


class EventCliAdapter:
    """Parse read-only event diagnostics and render ApplicationResult."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        application = getattr(runtime, "event_application", None)
        self.application = application
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        if not isinstance(self.application, EventApplicationHandler):
            return self._render(ApplicationResult.failure(
                "Event application handler is unavailable.",
                data={"read_only": True},
                errors=("Event application handler is unavailable.",),
            ))
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(self._usage(f"Event command could not be read: {exc}"))
        if not tokens:
            return self._render(self.application.status())
        command = tokens[0].casefold()
        if command not in _COMMANDS:
            return self._render(self._usage(
                f"Unknown Event command: {tokens[0]}"
            ))
        try:
            if command == "status":
                self._no_arguments(tokens)
                result = self.application.status()
            elif command == "list":
                result = self.application.list(self._history_request(tokens[1:]))
            elif command == "show":
                result = self.application.show(EventReferenceRequest(
                    self._single_value(tokens, "Event ID")
                ))
            elif command == "correlation":
                value, limit = self._value_and_limit(tokens, "Correlation ID")
                result = self.application.correlation(value, limit=limit)
            elif command == "subject":
                value, limit = self._value_and_limit(tokens, "Subject ID")
                result = self.application.subject(value, limit=limit)
            elif command == "types":
                self._no_arguments(tokens)
                result = self.application.types()
            else:
                self._no_arguments(tokens)
                result = self.application.subscribers()
        except ValueError as exc:
            result = self._usage(str(exc))
        return self._render(result)

    @staticmethod
    def _history_request(tokens: list[str]) -> EventHistoryRequest:
        values: dict[str, object] = {}
        index = 0
        while index < len(tokens):
            option = tokens[index].casefold()
            if option not in _LIST_OPTIONS:
                raise ValueError(f"Unknown event list option: {tokens[index]}")
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                raise ValueError(f"{tokens[index]} requires a value.")
            field = _LIST_OPTIONS[option]
            if field in values:
                raise ValueError(f"{tokens[index]} may only be supplied once.")
            raw_value = tokens[index + 1]
            if field == "limit":
                try:
                    values[field] = int(raw_value)
                except ValueError as exc:
                    raise ValueError("--limit must be an integer.") from exc
            else:
                values[field] = raw_value
            index += 2
        return EventHistoryRequest(**values)

    @staticmethod
    def _single_value(tokens: list[str], label: str) -> str:
        if len(tokens) != 2 or not tokens[1].strip():
            raise ValueError(f"{label} is required.")
        return tokens[1].strip()

    @staticmethod
    def _value_and_limit(
        tokens: list[str],
        label: str,
    ) -> tuple[str, int | None]:
        if len(tokens) < 2 or not tokens[1].strip():
            raise ValueError(f"{label} is required.")
        if len(tokens) == 2:
            return tokens[1].strip(), None
        if len(tokens) == 4 and tokens[2].casefold() == "--limit":
            try:
                return tokens[1].strip(), int(tokens[3])
            except ValueError as exc:
                raise ValueError("--limit must be an integer.") from exc
        raise ValueError(f"Usage requires {label} and optional --limit <count>.")

    @staticmethod
    def _no_arguments(tokens: list[str]) -> None:
        if len(tokens) != 1:
            raise ValueError(f"events {tokens[0]} accepts no arguments.")

    @staticmethod
    def _usage(detail: str = "") -> ApplicationResult:
        message = (
            f"{detail}\n" if detail else ""
        ) + (
            "Usage: events <status|list|show|correlation|subject|types|subscribers>\n"
            "All Event Bus commands are read-only. Arbitrary publishing is unavailable."
        )
        return ApplicationResult.failure(
            message,
            data={"read_only": True},
            errors=(detail or "An Event Bus subcommand is required.",),
        )

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result


def dispatch_events(runtime, raw_command: str) -> bool:
    """Recognize and delegate only the read-only Event command family."""
    normalized = str(raw_command).strip().casefold()
    if normalized != "events" and not normalized.startswith("events "):
        return False
    payload = str(raw_command).strip()[len("events"):].strip()
    EventCliAdapter(runtime).handle(payload)
    return True
