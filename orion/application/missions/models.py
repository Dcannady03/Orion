"""Immutable, JSON-safe Mission Engine Phase 1 models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Mapping

from orion.application.events.models import EVENT_ID_PATTERN, EVENT_TYPE_PATTERN


MISSION_SCHEMA_VERSION = 1
MISSION_ID_PATTERN = re.compile(r"mission-[a-f0-9]{32}")
LINK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")
MISSION_LINK_TYPES = frozenset({
    "goal",
    "proposal",
    "team_task",
    "team_run",
    "command_center_job",
    "approval",
    "validation",
    "documentation_review",
})


class MissionStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AWAITING_REVIEW = "awaiting_review"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: object) -> "MissionStatus":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Mission status must be one of: {choices}.") from exc


class MissionStage(str, Enum):
    PROPOSAL = "proposal"
    TEAM_PLANNING = "team_planning"
    APPROVAL = "approval"
    IMPLEMENTATION = "implementation"
    FINAL_REVIEW = "final_review"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: object) -> "MissionStage":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Mission stage must be one of: {choices}.") from exc


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label} is required.")
    if len(text) > maximum:
        raise ValueError(f"{label} must not exceed {maximum} characters.")
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{label} cannot contain control characters.")
    return text


def _timestamp(value: object, label: str, *, required: bool = False) -> str:
    text = _text(value, label, 40, required=required)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strings(values: object, label: str, *, maximum: int = 500) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"{label} must be a list.")
    result = tuple(
        _text(item, label, maximum, required=True)
        for item in values
    )
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class MissionLink:
    link_type: str
    subject_id: str
    source: str
    linked_at: str

    def __post_init__(self) -> None:
        link_type = _text(self.link_type, "Mission link type", 40, required=True)
        if link_type not in MISSION_LINK_TYPES:
            raise ValueError(f"Unsupported Mission link type: {link_type}")
        subject_id = _text(self.subject_id, "Mission link subject", 160, required=True)
        if not LINK_ID_PATTERN.fullmatch(subject_id):
            raise ValueError("Mission link subject has an invalid format.")
        object.__setattr__(self, "link_type", link_type)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(
            self,
            "source",
            _text(self.source, "Mission link source", 80, required=True),
        )
        object.__setattr__(
            self,
            "linked_at",
            _timestamp(self.linked_at, "Mission link timestamp", required=True),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "link_type": self.link_type,
            "subject_id": self.subject_id,
            "source": self.source,
            "linked_at": self.linked_at,
        }

    @classmethod
    def from_value(cls, value: object) -> "MissionLink":
        if not isinstance(value, Mapping):
            raise TypeError("Mission link must be a JSON object.")
        fields = {"link_type", "subject_id", "source", "linked_at"}
        if set(value) != fields:
            raise ValueError("Mission link fields are invalid.")
        return cls(**dict(value))


@dataclass(frozen=True)
class MissionEventRef:
    event_id: str
    event_type: str
    occurred_at: str
    source: str
    subject_id: str = ""

    def __post_init__(self) -> None:
        event_id = _text(self.event_id, "Mission event ID", 38, required=True).lower()
        if not EVENT_ID_PATTERN.fullmatch(event_id):
            raise ValueError("Mission event ID has an invalid format.")
        event_type = _text(self.event_type, "Mission event type", 160, required=True)
        if not EVENT_TYPE_PATTERN.fullmatch(event_type):
            raise ValueError("Mission event type has an invalid format.")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(
            self,
            "occurred_at",
            _timestamp(self.occurred_at, "Mission event timestamp", required=True),
        )
        object.__setattr__(
            self,
            "source",
            _text(self.source, "Mission event source", 80, required=True),
        )
        subject = _text(self.subject_id, "Mission event subject", 160)
        if subject and not LINK_ID_PATTERN.fullmatch(subject):
            raise ValueError("Mission event subject has an invalid format.")
        object.__setattr__(self, "subject_id", subject)

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "subject_id": self.subject_id,
        }

    @classmethod
    def from_value(cls, value: object) -> "MissionEventRef":
        if not isinstance(value, Mapping):
            raise TypeError("Mission event reference must be a JSON object.")
        fields = {"event_id", "event_type", "occurred_at", "source", "subject_id"}
        if set(value) != fields:
            raise ValueError("Mission event reference fields are invalid.")
        return cls(**dict(value))


@dataclass(frozen=True)
class Mission:
    schema_version: int
    mission_id: str
    goal_id: str
    proposal_id: str
    proposal_version: int
    title: str
    goal_text: str
    classification: str
    workspace: str
    department: str
    priority: str
    proposal_status: str
    status: MissionStatus | str
    stage: MissionStage | str
    progress: int
    created_at: str
    updated_at: str
    completed_at: str = ""
    failed_at: str = ""
    current_action: str = ""
    next_action: str = ""
    next_action_reason: str = ""
    links: tuple[MissionLink, ...] = ()
    event_refs: tuple[MissionEventRef, ...] = ()
    event_cursor: int = 0
    last_event_id: str = ""
    last_event_at: str = ""
    warnings: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != MISSION_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported Mission schema version: {self.schema_version}."
            )
        mission_id = _text(self.mission_id, "Mission ID", 40, required=True).lower()
        if not MISSION_ID_PATTERN.fullmatch(mission_id):
            raise ValueError("Mission ID has an invalid format.")
        object.__setattr__(self, "mission_id", mission_id)
        for name, label, maximum, required in (
            ("goal_id", "Mission Goal ID", 120, True),
            ("proposal_id", "Mission Proposal ID", 160, True),
            ("title", "Mission title", 200, True),
            ("goal_text", "Mission goal", 4_000, True),
            ("classification", "Mission classification", 100, True),
            ("workspace", "Mission workspace", 1_000, False),
            ("department", "Mission department", 200, False),
            ("priority", "Mission priority", 40, True),
            ("proposal_status", "Mission proposal status", 40, True),
            ("current_action", "Mission current action", 160, False),
            ("next_action", "Mission next action", 500, False),
            ("next_action_reason", "Mission next-action reason", 500, False),
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), label, maximum, required=required),
            )
        if (
            isinstance(self.proposal_version, bool)
            or not isinstance(self.proposal_version, int)
            or self.proposal_version < 1
        ):
            raise ValueError("Mission proposal version must be positive.")
        object.__setattr__(self, "status", MissionStatus.parse(self.status))
        object.__setattr__(self, "stage", MissionStage.parse(self.stage))
        if (
            isinstance(self.progress, bool)
            or not isinstance(self.progress, int)
            or not 0 <= self.progress <= 100
        ):
            raise ValueError("Mission progress must be an integer from 0 through 100.")
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "Mission creation timestamp", required=True),
        )
        object.__setattr__(
            self,
            "updated_at",
            _timestamp(self.updated_at, "Mission update timestamp", required=True),
        )
        object.__setattr__(
            self,
            "completed_at",
            _timestamp(self.completed_at, "Mission completion timestamp"),
        )
        object.__setattr__(
            self,
            "failed_at",
            _timestamp(self.failed_at, "Mission failure timestamp"),
        )
        links = tuple(self.links)
        if any(not isinstance(item, MissionLink) for item in links):
            raise TypeError("Mission links must contain MissionLink records.")
        if len({item.link_type for item in links}) != len(links):
            raise ValueError("Mission links must have unique link types.")
        object.__setattr__(self, "links", links)
        refs = tuple(self.event_refs)
        if any(not isinstance(item, MissionEventRef) for item in refs):
            raise TypeError("Mission event references are invalid.")
        if len({item.event_id for item in refs}) != len(refs):
            raise ValueError("Mission event references must be unique.")
        object.__setattr__(self, "event_refs", refs)
        if (
            isinstance(self.event_cursor, bool)
            or not isinstance(self.event_cursor, int)
            or self.event_cursor < 0
            or self.event_cursor != len(refs)
        ):
            raise ValueError("Mission event cursor must equal the event reference count.")
        last_event_id = _text(self.last_event_id, "Mission last event ID", 38).lower()
        if last_event_id and not EVENT_ID_PATTERN.fullmatch(last_event_id):
            raise ValueError("Mission last event ID has an invalid format.")
        object.__setattr__(self, "last_event_id", last_event_id)
        object.__setattr__(
            self,
            "last_event_at",
            _timestamp(self.last_event_at, "Mission last event timestamp"),
        )
        if refs:
            if last_event_id != refs[-1].event_id or self.last_event_at != refs[-1].occurred_at:
                raise ValueError("Mission last event must match its final event reference.")
        elif last_event_id or self.last_event_at:
            raise ValueError("Mission without event references cannot name a last event.")
        object.__setattr__(self, "warnings", _strings(self.warnings, "Mission warning"))
        object.__setattr__(self, "risks", _strings(self.risks, "Mission risk"))

    def link(self, link_type: str) -> MissionLink | None:
        return next((item for item in self.links if item.link_type == link_type), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "goal_id": self.goal_id,
            "proposal_id": self.proposal_id,
            "proposal_version": self.proposal_version,
            "title": self.title,
            "goal_text": self.goal_text,
            "classification": self.classification,
            "workspace": self.workspace,
            "department": self.department,
            "priority": self.priority,
            "proposal_status": self.proposal_status,
            "status": self.status.value,
            "stage": self.stage.value,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "current_action": self.current_action,
            "next_action": self.next_action,
            "next_action_reason": self.next_action_reason,
            "links": [item.to_dict() for item in self.links],
            "event_refs": [item.to_dict() for item in self.event_refs],
            "event_cursor": self.event_cursor,
            "last_event_id": self.last_event_id,
            "last_event_at": self.last_event_at,
            "warnings": list(self.warnings),
            "risks": list(self.risks),
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
    def from_value(cls, value: object) -> "Mission":
        if not isinstance(value, Mapping):
            raise TypeError("Mission record must be a JSON object.")
        fields = {
            "schema_version", "mission_id", "goal_id", "proposal_id",
            "proposal_version", "title", "goal_text", "classification",
            "workspace", "department", "priority", "proposal_status", "status",
            "stage", "progress", "created_at", "updated_at", "completed_at",
            "failed_at", "current_action", "next_action", "next_action_reason",
            "links", "event_refs", "event_cursor", "last_event_id",
            "last_event_at", "warnings", "risks",
        }
        missing = fields - set(value)
        unknown = set(value) - fields
        if missing or unknown:
            raise ValueError(
                f"Mission record fields are invalid; missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}."
            )
        data = dict(value)
        raw_links = data.get("links")
        raw_refs = data.get("event_refs")
        if not isinstance(raw_links, list) or not isinstance(raw_refs, list):
            raise TypeError("Mission links and event references must be JSON arrays.")
        data["links"] = tuple(MissionLink.from_value(item) for item in raw_links)
        data["event_refs"] = tuple(
            MissionEventRef.from_value(item) for item in raw_refs
        )
        return cls(**data)


@dataclass(frozen=True)
class MissionValidation:
    mission_id: str
    valid: bool
    checked_at: str
    schema_valid: bool
    proposal_exists: bool
    proposal_identity_valid: bool
    goal_identity_valid: bool
    path_safe: bool
    links_valid: bool
    event_cursor_valid: bool
    projection_matches: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checked_at",
            _timestamp(self.checked_at, "Mission validation timestamp", required=True),
        )
        object.__setattr__(self, "warnings", _strings(self.warnings, "Validation warning"))
        object.__setattr__(self, "errors", _strings(self.errors, "Validation error"))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "valid": self.valid,
            "checked_at": self.checked_at,
            "schema_valid": self.schema_valid,
            "proposal_exists": self.proposal_exists,
            "proposal_identity_valid": self.proposal_identity_valid,
            "goal_identity_valid": self.goal_identity_valid,
            "path_safe": self.path_safe,
            "links_valid": self.links_valid,
            "event_cursor_valid": self.event_cursor_valid,
            "projection_matches": self.projection_matches,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }
