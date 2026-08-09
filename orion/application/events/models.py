"""Immutable, JSON-safe models for Orion's observation-only Event Bus."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias
from uuid import uuid4

from orion.application.events.types import KNOWN_EVENT_TYPES


EVENT_SCHEMA_VERSION = 1
DEFAULT_MAX_EVENT_BYTES = 65_536
EVENT_ID_PATTERN = re.compile(r"event-[a-f0-9]{32}")
EVENT_TYPE_PATTERN = re.compile(
    r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}"
)
SOURCE_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,79}")
_JSON_SCALARS = (str, int, float, bool, type(None))
_SENSITIVE_KEYS = frozenset({
    "api_key",
    "access_token",
    "refresh_token",
    "oauth_token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "credential",
    "credentials",
    "private_key",
})
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),
    re.compile(
        r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

JSONValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


class EventSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @classmethod
    def parse(cls, value: object) -> "EventSeverity":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(f"Event severity must be one of: {allowed}.") from exc


def canonical_json(value: object) -> str:
    """Encode canonical compact JSON for storage and size validation."""
    return json.dumps(
        _thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(f"_{item}") for item in _SENSITIVE_KEYS
    )


def _sensitive_string(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)


def _freeze_json(
    value: Any,
    *,
    label: str,
    depth: int = 0,
    item_budget: list[int] | None = None,
) -> JSONValue:
    if depth > 10:
        raise ValueError(f"{label} exceeds the maximum nesting depth.")
    budget = item_budget if item_budget is not None else [4_096]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError(f"{label} contains too many values.")
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{label} object keys must be strings.")
            normalized_key = key.strip()
            if not normalized_key or len(normalized_key) > 128:
                raise ValueError(f"{label} object keys must be 1-128 characters.")
            if _sensitive_key(normalized_key):
                raise ValueError(
                    f"{label} cannot contain sensitive field '{normalized_key}'."
                )
            frozen[normalized_key] = _freeze_json(
                item,
                label=label,
                depth=depth + 1,
                item_budget=budget,
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(
                item,
                label=label,
                depth=depth + 1,
                item_budget=budget,
            )
            for item in value
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError(f"{label} numbers must be finite.")
    if isinstance(value, str):
        if len(value) > 16_384:
            raise ValueError(f"{label} strings must not exceed 16384 characters.")
        if _sensitive_string(value):
            raise ValueError(f"{label} cannot contain a secret-like value.")
    if isinstance(value, _JSON_SCALARS):
        return value
    raise TypeError(
        f"{label} must contain only JSON primitives; "
        f"received {type(value).__name__}."
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _required_text(value: object, label: str, maximum: int) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > maximum:
        raise ValueError(f"{label} must not exceed {maximum} characters.")
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{label} cannot contain control characters.")
    return text


def _optional_text(value: object, label: str, maximum: int) -> str | None:
    if value in {None, ""}:
        return None
    return _required_text(value, label, maximum)


def _utc_timestamp(value: object) -> str:
    text = _required_text(value, "Event timestamp", 40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Event timestamp must be ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("Event timestamp must use UTC.")
    normalized = parsed.astimezone(timezone.utc).isoformat()
    return normalized.replace("+00:00", "Z")


@dataclass(frozen=True)
class OrionEvent:
    event_id: str
    event_type: str
    occurred_at: str
    source: str
    severity: EventSeverity | str = EventSeverity.INFO
    correlation_id: str | None = None
    causation_id: str | None = None
    subject_id: str | None = None
    schema_version: int = EVENT_SCHEMA_VERSION
    data: Mapping[str, JSONValue] = field(default_factory=dict)
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event_id = _required_text(self.event_id, "Event ID", 38).lower()
        if not EVENT_ID_PATTERN.fullmatch(event_id):
            raise ValueError("Event ID has an invalid format.")
        object.__setattr__(self, "event_id", event_id)
        event_type = _required_text(self.event_type, "Event type", 160)
        if not EVENT_TYPE_PATTERN.fullmatch(event_type):
            raise ValueError(
                "Event type must be lowercase dot-separated domain.resource.action."
            )
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "occurred_at", _utc_timestamp(self.occurred_at))
        source = _required_text(self.source, "Event source", 80).lower()
        if not SOURCE_PATTERN.fullmatch(source):
            raise ValueError("Event source has an invalid format.")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "severity", EventSeverity.parse(self.severity))
        object.__setattr__(
            self,
            "correlation_id",
            _optional_text(self.correlation_id, "Correlation ID", 160),
        )
        causation_id = _optional_text(self.causation_id, "Causation ID", 38)
        if causation_id and not EVENT_ID_PATTERN.fullmatch(causation_id):
            raise ValueError("Causation ID must reference an Orion event ID.")
        object.__setattr__(self, "causation_id", causation_id)
        object.__setattr__(
            self,
            "subject_id",
            _optional_text(self.subject_id, "Event subject ID", 160),
        )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != EVENT_SCHEMA_VERSION
        ):
            raise ValueError(
                f"Unsupported event schema version: {self.schema_version}."
            )
        object.__setattr__(
            self,
            "data",
            _freeze_json(dict(self.data), label="Event data"),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_json(dict(self.metadata), label="Event metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "severity": self.severity.value,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "subject_id": self.subject_id,
            "schema_version": self.schema_version,
            "data": _thaw_json(self.data),
            "metadata": _thaw_json(self.metadata),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def from_value(cls, value: object) -> "OrionEvent":
        if not isinstance(value, Mapping):
            raise TypeError("Event record must be a JSON object.")
        fields = {
            "event_id",
            "event_type",
            "occurred_at",
            "source",
            "severity",
            "correlation_id",
            "causation_id",
            "subject_id",
            "schema_version",
            "data",
            "metadata",
        }
        unknown = set(value) - fields
        missing = fields - set(value)
        if unknown:
            raise ValueError(f"Event record contains unknown fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Event record is missing fields: {sorted(missing)}")
        return cls(**dict(value))


class EventFactory:
    """Create allowlisted, bounded events with deterministic test seams."""

    def __init__(
        self,
        *,
        known_event_types: tuple[str, ...] = KNOWN_EVENT_TYPES,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        clock=None,
        id_factory=None,
    ) -> None:
        known = tuple(dict.fromkeys(str(item).strip() for item in known_event_types))
        if not known or any(not EVENT_TYPE_PATTERN.fullmatch(item) for item in known):
            raise ValueError("Event Factory requires valid known event types.")
        if (
            isinstance(max_event_bytes, bool)
            or not isinstance(max_event_bytes, int)
            or not 1_024 <= max_event_bytes <= 1_048_576
        ):
            raise ValueError("Maximum event bytes must be between 1024 and 1048576.")
        self.known_event_types = known
        self.max_event_bytes = max_event_bytes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"event-{uuid4().hex}")

    def create(
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
    ) -> OrionEvent:
        normalized_type = str(event_type).strip()
        if normalized_type not in self.known_event_types:
            raise ValueError(f"Event type is not registered: {normalized_type}")
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("Event clock must return a datetime.")
        if now.tzinfo is None:
            raise ValueError("Event clock must return a timezone-aware datetime.")
        event = OrionEvent(
            event_id=str(self._id_factory()).strip().lower(),
            event_type=normalized_type,
            occurred_at=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            source=source,
            severity=severity,
            correlation_id=correlation_id,
            causation_id=causation_id,
            subject_id=subject_id,
            data=data or {},
            metadata=metadata or {},
        )
        size = len((canonical_json(event.to_dict()) + "\n").encode("utf-8"))
        if size > self.max_event_bytes:
            raise ValueError(
                f"Event exceeds the configured {self.max_event_bytes}-byte limit."
            )
        return event
