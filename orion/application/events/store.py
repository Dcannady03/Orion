"""Append-only external JSONL persistence for Orion events."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
import time
from types import MappingProxyType
from typing import Iterator, Mapping

from orion.application.events.models import (
    DEFAULT_MAX_EVENT_BYTES,
    EVENT_ID_PATTERN,
    EVENT_TYPE_PATTERN,
    EventSeverity,
    OrionEvent,
    canonical_json,
)


DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 1_000
_DAILY_FILE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")


@dataclass(frozen=True)
class EventHistory:
    events: tuple[OrionEvent, ...] = ()
    warnings: tuple[str, ...] = ()
    limit: int = DEFAULT_HISTORY_LIMIT
    filters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(not isinstance(item, OrionEvent) for item in self.events):
            raise TypeError("Event history may contain only OrionEvent records.")
        object.__setattr__(
            self,
            "warnings",
            tuple(str(item) for item in self.warnings),
        )
        object.__setattr__(
            self,
            "filters",
            MappingProxyType(dict(self.filters)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "events": [item.to_dict() for item in self.events],
            "count": len(self.events),
            "limit": self.limit,
            "filters": dict(self.filters),
            "warnings": list(self.warnings),
        }


class EventStore:
    """Persist immutable events in UTC daily append-only JSONL logs."""

    def __init__(
        self,
        root: str | Path,
        *,
        forbidden_root: str | Path | None = None,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        history_default_limit: int = DEFAULT_HISTORY_LIMIT,
        history_max_limit: int = MAX_HISTORY_LIMIT,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        candidate = Path(root).expanduser()
        self.root = candidate if candidate.is_absolute() else candidate.absolute()
        self.forbidden_root = (
            Path(forbidden_root).expanduser().resolve()
            if forbidden_root is not None
            else None
        )
        if (
            self.forbidden_root is not None
            and self._within(self.root.resolve(), self.forbidden_root)
        ):
            raise ValueError("Event storage cannot be inside the application repository.")
        if (
            isinstance(max_event_bytes, bool)
            or not isinstance(max_event_bytes, int)
            or not 1_024 <= max_event_bytes <= 1_048_576
        ):
            raise ValueError("Maximum event bytes must be between 1024 and 1048576.")
        if (
            isinstance(history_default_limit, bool)
            or not isinstance(history_default_limit, int)
            or history_default_limit < 1
        ):
            raise ValueError("Default event history limit must be positive.")
        if (
            isinstance(history_max_limit, bool)
            or not isinstance(history_max_limit, int)
            or history_max_limit < history_default_limit
            or history_max_limit > 10_000
        ):
            raise ValueError(
                "Maximum event history limit must include the default and not exceed 10000."
            )
        if not 0.05 <= float(lock_timeout_seconds) <= 30.0:
            raise ValueError("Event lock timeout must be between 0.05 and 30 seconds.")
        self.max_event_bytes = max_event_bytes
        self.history_default_limit = history_default_limit
        self.history_max_limit = history_max_limit
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._lock = RLock()

    def append(self, event: OrionEvent) -> Path:
        validated = OrionEvent.from_value(event.to_dict())
        encoded = (canonical_json(validated.to_dict()) + "\n").encode("utf-8")
        if len(encoded) > self.max_event_bytes:
            raise ValueError(
                f"Event exceeds the configured {self.max_event_bytes}-byte limit."
            )
        occurred = self._parse_time(validated.occurred_at, "Event timestamp")
        path = self.root / f"{occurred.date().isoformat()}.jsonl"
        with self._lock:
            self._ensure_root()
            with self._daily_lock(path):
                self._validate_log_file(path, optional=True)
                created = not path.exists()
                separator = (
                    b"\n"
                    if not created and self._missing_final_newline(path)
                    else b""
                )
                with path.open("ab", buffering=0) as handle:
                    handle.write(separator + encoded)
                    os.fsync(handle.fileno())
                if created:
                    self._restrict_permissions(path)
        return path

    def history(
        self,
        *,
        event_type: str | None = None,
        correlation_id: str | None = None,
        subject_id: str | None = None,
        severity: EventSeverity | str | None = None,
        source: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
    ) -> EventHistory:
        selected_limit = self._limit(limit)
        normalized_type = str(event_type or "").strip()
        if normalized_type and not EVENT_TYPE_PATTERN.fullmatch(normalized_type):
            raise ValueError("Event history type filter is invalid.")
        normalized_severity = (
            EventSeverity.parse(severity).value
            if severity not in {None, ""}
            else ""
        )
        start = self._parse_time(start_time, "History start") if start_time else None
        end = self._parse_time(end_time, "History end") if end_time else None
        if start and end and start > end:
            raise ValueError("Event history start cannot follow its end.")
        filters = {
            key: value
            for key, value in {
                "event_type": normalized_type,
                "correlation_id": str(correlation_id or "").strip(),
                "subject_id": str(subject_id or "").strip(),
                "severity": normalized_severity,
                "source": str(source or "").strip(),
                "start_time": start_time or "",
                "end_time": end_time or "",
            }.items()
            if value
        }
        events: list[OrionEvent] = []
        warnings: list[str] = []
        with self._lock:
            for path in self._daily_files(warnings):
                for raw_line in self._reverse_lines(path):
                    try:
                        event = self._decode_line(raw_line, path)
                    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                        self._warning(
                            warnings,
                            f"Malformed event record ignored in {path.name}.",
                        )
                        continue
                    if not self._matches(
                        event,
                        event_type=normalized_type,
                        correlation_id=str(correlation_id or "").strip(),
                        subject_id=str(subject_id or "").strip(),
                        severity=normalized_severity,
                        source=str(source or "").strip(),
                        start=start,
                        end=end,
                    ):
                        continue
                    events.append(event)
                    if len(events) >= selected_limit:
                        return EventHistory(
                            tuple(events),
                            tuple(warnings),
                            selected_limit,
                            filters,
                        )
        return EventHistory(
            tuple(events),
            tuple(warnings),
            selected_limit,
            filters,
        )

    def get(self, event_id: str) -> tuple[OrionEvent, tuple[str, ...]]:
        normalized = str(event_id).strip().lower()
        if not EVENT_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Event ID has an invalid format.")
        warnings: list[str] = []
        with self._lock:
            for path in self._daily_files(warnings):
                for raw_line in self._reverse_lines(path):
                    try:
                        event = self._decode_line(raw_line, path)
                    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                        self._warning(
                            warnings,
                            f"Malformed event record ignored in {path.name}.",
                        )
                        continue
                    if event.event_id == normalized:
                        return event, tuple(warnings)
        raise FileNotFoundError(f"Event not found: {normalized}")

    def latest_at(self) -> tuple[str | None, tuple[str, ...]]:
        history = self.history(limit=1)
        return (
            history.events[0].occurred_at if history.events else None,
            history.warnings,
        )

    def _daily_files(self, warnings: list[str]) -> tuple[Path, ...]:
        if not self.root.exists():
            return ()
        try:
            self._validate_directory(self.root)
        except (OSError, PermissionError, ValueError) as exc:
            self._warning(warnings, f"Event store is unavailable: {type(exc).__name__}.")
            return ()
        paths: list[Path] = []
        for path in self.root.iterdir():
            if not _DAILY_FILE_PATTERN.fullmatch(path.name):
                continue
            try:
                self._validate_log_file(path)
            except (OSError, PermissionError, ValueError):
                self._warning(
                    warnings,
                    f"Unsafe event log ignored: {path.name}.",
                )
                continue
            paths.append(path)
        return tuple(sorted(paths, key=lambda item: item.name, reverse=True))

    def _decode_line(self, raw_line: bytes, path: Path) -> OrionEvent:
        if len(raw_line) + 1 > self.max_event_bytes:
            raise ValueError(f"Event record exceeds size limit in {path.name}.")
        if not raw_line.strip():
            raise ValueError("Event record is empty.")
        value = json.loads(raw_line.decode("utf-8"))
        return OrionEvent.from_value(value)

    @staticmethod
    def _missing_final_newline(path: Path) -> bool:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"

    @staticmethod
    def _reverse_lines(path: Path, chunk_size: int = 65_536) -> Iterator[bytes]:
        """Yield non-empty lines newest-first without loading a whole log."""
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b""
            while position > 0:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                buffer = handle.read(read_size) + buffer
                parts = buffer.split(b"\n")
                buffer = parts[0]
                for line in reversed(parts[1:]):
                    if line:
                        yield line
            if buffer:
                yield buffer

    @staticmethod
    def _matches(
        event: OrionEvent,
        *,
        event_type: str,
        correlation_id: str,
        subject_id: str,
        severity: str,
        source: str,
        start: datetime | None,
        end: datetime | None,
    ) -> bool:
        occurred = datetime.fromisoformat(
            event.occurred_at.replace("Z", "+00:00")
        )
        return (
            (not event_type or event.event_type == event_type)
            and (not correlation_id or event.correlation_id == correlation_id)
            and (not subject_id or event.subject_id == subject_id)
            and (not severity or event.severity.value == severity)
            and (not source or event.source == source)
            and (start is None or occurred >= start)
            and (end is None or occurred <= end)
        )

    def _limit(self, value: int | None) -> int:
        selected = self.history_default_limit if value is None else value
        if isinstance(selected, bool) or not isinstance(selected, int):
            raise ValueError("Event history limit must be an integer.")
        if not 1 <= selected <= self.history_max_limit:
            raise ValueError(
                f"Event history limit must be between 1 and {self.history_max_limit}."
            )
        return selected

    @staticmethod
    def _parse_time(value: object, label: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO 8601 timestamp.") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{label} must include a timezone.")
        return parsed.astimezone(timezone.utc)

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise PermissionError("Event storage cannot be a symlink.")
        if (
            self.forbidden_root is not None
            and self._within(self.root.resolve(), self.forbidden_root)
        ):
            raise PermissionError("Event storage cannot be inside the application repository.")
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_directory(self.root)
        self._restrict_permissions(self.root)

    @contextmanager
    def _daily_lock(self, path: Path) -> Iterator[None]:
        lock_path = path.with_name(f".{path.name}.lock")
        deadline = time.monotonic() + self.lock_timeout_seconds
        handle = None
        while handle is None:
            if lock_path.is_symlink():
                raise PermissionError("Event log lock cannot be a symlink.")
            try:
                handle = lock_path.open("x", encoding="utf-8", newline="\n")
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Event log is already being appended: {path.name}"
                    ) from exc
                time.sleep(0.005)
        try:
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
            self._restrict_permissions(lock_path)
            yield
        finally:
            try:
                handle.close()
            finally:
                try:
                    lock_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _validate_directory(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Event storage cannot be a symlink.")
        if not path.is_dir():
            raise NotADirectoryError("Event storage path is not a directory.")

    @staticmethod
    def _validate_log_file(path: Path, *, optional: bool = False) -> None:
        if not path.exists():
            if optional:
                return
            raise FileNotFoundError(path.name)
        if path.is_symlink():
            raise PermissionError("Event log symlinks are not allowed.")
        if not path.is_file():
            raise ValueError("Event log path is not a file.")

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        try:
            mode = (
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                if path.is_dir()
                else stat.S_IRUSR | stat.S_IWUSR
            )
            os.chmod(path, mode)
        except OSError:
            pass

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _warning(warnings: list[str], message: str) -> None:
        if len(warnings) < 50 and message not in warnings:
            warnings.append(message)
