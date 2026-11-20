"""Filterable, severity-aware search across Orion's runtime logs.

Orion writes no traditional file logs: the Event Store JSONL files under
``user_root/events/`` are the runtime log. Plain text files under
``user_root/logs/`` (for example the network monitor's logs) are searched too,
so one command covers both sources. The search is read-only and never
modifies any log file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from orion.application.events.models import EventSeverity

if TYPE_CHECKING:
    from orion.application.events.store import EventStore

DEFAULT_LOG_LIMIT = 50
MAX_LOG_LIMIT = 1_000
MAX_LOG_FILES = 50
MAX_LOG_FILE_BYTES = 10 * 1024 * 1024
MAX_LOG_MESSAGE_LENGTH = 500
LOG_FILE_SUFFIXES = (".log", ".txt")
LOG_SOURCES = ("all", "events", "files")

_SEVERITY_KEYWORDS = {
    "critical": "critical",
    "fatal": "critical",
    "error": "error",
    "warn": "warning",
    "warning": "warning",
    "notice": "notice",
    "info": "info",
    "debug": "debug",
    "trace": "debug",
}
_SEVERITY_PATTERN = re.compile(
    r"\b(critical|fatal|error|warn|warning|notice|info|debug|trace)\b",
    re.IGNORECASE,
)
_TIMESTAMP_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)"
)


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, assuming UTC when no offset is given."""
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _severity_from_text(line: str) -> str:
    """Guess a plain-log line's severity from its first severity keyword."""
    match = _SEVERITY_PATTERN.search(line)
    if match is None:
        return EventSeverity.INFO.value
    return _SEVERITY_KEYWORDS[match.group(1).lower()]


@dataclass(frozen=True)
class LogEntry:
    """One log line, normalized across the Event Store and plain log files."""

    occurred_at: str
    severity: str
    source: str
    message: str
    event_type: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "occurred_at": self.occurred_at,
            "severity": self.severity,
            "source": self.source,
            "message": self.message,
            "event_type": self.event_type,
        }


@dataclass(frozen=True)
class LogSearchRequest:
    """Filters for a log search. Every filter is optional."""

    severity: str | None = None
    event_type: str | None = None
    search: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    limit: int = DEFAULT_LOG_LIMIT
    source: str = "all"


@dataclass(frozen=True)
class LogSearchResult:
    """The entries a search found, newest first, plus any search warnings."""

    entries: tuple[LogEntry, ...]
    warnings: tuple[str, ...]
    limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "warnings": list(self.warnings),
            "limit": self.limit,
        }


class LogViewer:
    """Search the Event Store and plain log files as one unified log."""

    def __init__(
        self,
        logs_root: str | Path,
        event_store: "EventStore | None" = None,
    ) -> None:
        self._logs_root = Path(logs_root)
        self._event_store = event_store

    def search(self, request: LogSearchRequest) -> LogSearchResult:
        """Return matching log entries, newest first, bounded by the limit."""
        severity = ""
        if request.severity:
            severity = EventSeverity.parse(request.severity).value
        source = (request.source or "all").strip().lower()
        if source not in LOG_SOURCES:
            raise ValueError(
                f"Log source must be one of: {', '.join(LOG_SOURCES)}."
            )
        limit = request.limit
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Log limit must be an integer.")
        limit = max(1, min(limit, MAX_LOG_LIMIT))
        start = _parse_timestamp(request.start_time or "")
        end = _parse_timestamp(request.end_time or "")
        if request.start_time and start is None:
            raise ValueError("Log start time must be an ISO-8601 timestamp.")
        if request.end_time and end is None:
            raise ValueError("Log end time must be an ISO-8601 timestamp.")
        if start is not None and end is not None and start > end:
            raise ValueError("Log start time cannot follow its end time.")
        needle = (request.search or "").strip().lower()
        event_type = (request.event_type or "").strip()

        warnings: list[str] = []
        entries: list[LogEntry] = []
        if source in {"all", "events"}:
            entries.extend(
                self._event_entries(
                    severity=severity,
                    event_type=event_type,
                    needle=needle,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    limit=limit,
                    warnings=warnings,
                )
            )
        if source in {"all", "files"}:
            entries.extend(
                self._file_entries(
                    severity=severity,
                    needle=needle,
                    start=start,
                    end=end,
                    limit=limit,
                    warnings=warnings,
                )
            )
        entries.sort(key=_entry_sort_key, reverse=True)
        return LogSearchResult(tuple(entries[:limit]), tuple(warnings), limit)

    def _event_entries(
        self,
        *,
        severity: str,
        event_type: str,
        needle: str,
        start_time: str | None,
        end_time: str | None,
        limit: int,
        warnings: list[str],
    ) -> list[LogEntry]:
        if self._event_store is None:
            return []
        history = self._event_store.history(
            event_type=event_type or None,
            severity=severity or None,
            start_time=start_time or None,
            end_time=end_time or None,
            limit=limit,
        )
        warnings.extend(history.warnings)
        found: list[LogEntry] = []
        for event in history.events:
            message = _event_message(event)
            if needle and needle not in (
                f"{event.event_type} {event.source} {message}".lower()
            ):
                continue
            found.append(
                LogEntry(
                    occurred_at=event.occurred_at,
                    severity=event.severity.value
                    if isinstance(event.severity, EventSeverity)
                    else str(event.severity),
                    source="events",
                    message=message,
                    event_type=event.event_type,
                )
            )
        return found

    def _file_entries(
        self,
        *,
        severity: str,
        needle: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        warnings: list[str],
    ) -> list[LogEntry]:
        if not self._logs_root.is_dir():
            return []
        try:
            candidates = [
                path
                for path in self._logs_root.rglob("*")
                if path.is_file() and path.suffix.lower() in LOG_FILE_SUFFIXES
            ]
        except OSError as exc:
            warnings.append(f"Could not list log files: {exc}")
            return []
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        if len(candidates) > MAX_LOG_FILES:
            warnings.append(
                f"Only the {MAX_LOG_FILES} newest of {len(candidates)} "
                "log files were searched."
            )
            candidates = candidates[:MAX_LOG_FILES]
        found: list[LogEntry] = []
        for path in candidates:
            if len(found) >= limit:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_LOG_FILE_BYTES:
                warnings.append(
                    f"Skipped {path.name}: larger than "
                    f"{MAX_LOG_FILE_BYTES // (1024 * 1024)} MiB."
                )
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                warnings.append(f"Could not read {path.name}: {exc}")
                continue
            relative = path.relative_to(self._logs_root).as_posix()
            for line in reversed(lines):
                if len(found) >= limit:
                    break
                entry = _parse_log_line(line, relative)
                if entry is None:
                    continue
                if severity and entry.severity != severity:
                    continue
                if needle and needle not in f"{relative} {entry.message}".lower():
                    continue
                if (start is not None or end is not None) and not _in_time_range(
                    entry.occurred_at, start, end
                ):
                    continue
                found.append(entry)
        return found


def _entry_sort_key(entry: LogEntry) -> tuple[int, str]:
    """Sort newest first; entries without a timestamp sort last."""
    if entry.occurred_at:
        return (1, entry.occurred_at)
    return (0, "")


def _plain(value: Any) -> Any:
    """Return a plain JSON-compatible copy of a nested value."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _event_message(event: object) -> str:
    """Render an event as one readable log line."""
    event_type = str(getattr(event, "event_type", ""))
    data = getattr(event, "data", {}) or {}
    detail = ""
    if isinstance(data, Mapping):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            detail = message.strip()
        elif data:
            detail = json.dumps(_plain(data), sort_keys=True, ensure_ascii=False)
    text = f"{event_type}: {detail}" if detail else event_type
    if len(text) > MAX_LOG_MESSAGE_LENGTH:
        text = text[:MAX_LOG_MESSAGE_LENGTH - 3] + "..."
    return text


def _parse_log_line(line: str, source: str) -> LogEntry | None:
    stripped = line.strip()
    if not stripped:
        return None
    occurred_at = ""
    match = _TIMESTAMP_PATTERN.search(stripped)
    if match is not None:
        parsed = _parse_timestamp(match.group(1))
        if parsed is not None:
            occurred_at = parsed.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    message = stripped
    if len(message) > MAX_LOG_MESSAGE_LENGTH:
        message = message[:MAX_LOG_MESSAGE_LENGTH - 3] + "..."
    return LogEntry(
        occurred_at=occurred_at,
        severity=_severity_from_text(stripped),
        source=source,
        message=message,
    )


def _in_time_range(
    occurred_at: str, start: datetime | None, end: datetime | None
) -> bool:
    """True when a timestamp falls inside the requested range.

    Entries without a parseable timestamp cannot be placed in a range,
    so a time filter excludes them.
    """
    parsed = _parse_timestamp(occurred_at)
    if parsed is None:
        return False
    if start is not None and parsed < start:
        return False
    if end is not None and parsed > end:
        return False
    return True
