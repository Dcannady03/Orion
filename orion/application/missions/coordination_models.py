"""Immutable JSON-safe models for one-operation Mission coordination."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from orion.application.events.models import EVENT_ID_PATTERN
from orion.application.missions.models import LINK_ID_PATTERN, MISSION_ID_PATTERN


ADVANCE_TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}")
ATTEMPT_ID_PATTERN = re.compile(r"mission-attempt-[a-f0-9]{32}")
CAPABILITY_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
AUDIT_STATES = frozenset({"reserved", "succeeded", "failed", "uncertain"})
_JSON_SCALARS = (str, int, float, bool, type(None))


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


def _freeze_json(value: Any, label: str) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError(f"{label} keys must be strings.")
        return MappingProxyType({
            key: _freeze_json(item, label)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, label) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError(f"{label} numbers must be finite.")
    if isinstance(value, _JSON_SCALARS):
        return value
    raise TypeError(f"{label} must contain only JSON-compatible values.")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _mission_id(value: object) -> str:
    selected = _text(value, "Mission ID", 40, required=True).lower()
    if not MISSION_ID_PATTERN.fullmatch(selected):
        raise ValueError("Mission ID has an invalid format.")
    return selected


@dataclass(frozen=True)
class MissionNextOperation:
    mission_id: str
    capability_id: str
    operation_type: str
    subject_id: str
    reason: str
    requires_confirmation: bool
    requires_downstream_approval: bool
    mutates_state: bool
    required_inputs: tuple[str, ...] = ()
    resolved_inputs: Mapping[str, object] = field(default_factory=dict)
    cli_representation: str = ""
    blocked_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        capability = _text(self.capability_id, "Mission capability", 100)
        if capability and not CAPABILITY_ID_PATTERN.fullmatch(capability):
            raise ValueError("Mission capability ID has an invalid format.")
        operation_type = _text(self.operation_type, "Mission operation type", 80)
        subject_id = _text(self.subject_id, "Mission operation subject", 160)
        if subject_id and not LINK_ID_PATTERN.fullmatch(subject_id):
            raise ValueError("Mission operation subject has an invalid format.")
        blocked_reason = _text(
            self.blocked_reason,
            "Mission operation blocked reason",
            500,
        )
        if blocked_reason:
            if capability or operation_type or subject_id or self.cli_representation:
                raise ValueError("Blocked Mission operations cannot name a dispatch target.")
        elif not capability or not operation_type or not subject_id:
            raise ValueError("Eligible Mission operations require capability and subject IDs.")
        for name in (
            "requires_confirmation",
            "requires_downstream_approval",
            "mutates_state",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean.")
        required_inputs = tuple(
            _text(item, "Mission required input", 80, required=True)
            for item in self.required_inputs
        )
        if len(set(required_inputs)) != len(required_inputs):
            raise ValueError("Mission required inputs must be unique.")
        resolved = _freeze_json(dict(self.resolved_inputs), "Mission resolved inputs")
        if set(resolved) - set(required_inputs):
            raise ValueError("Mission resolved inputs are not declared as required.")
        object.__setattr__(self, "capability_id", capability)
        object.__setattr__(self, "operation_type", operation_type)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "reason", _text(
            self.reason,
            "Mission operation reason",
            500,
            required=True,
        ))
        object.__setattr__(self, "required_inputs", required_inputs)
        object.__setattr__(self, "resolved_inputs", resolved)
        object.__setattr__(self, "cli_representation", _text(
            self.cli_representation,
            "Mission operation CLI representation",
            500,
        ))
        object.__setattr__(self, "blocked_reason", blocked_reason)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reason)

    @classmethod
    def blocked_operation(cls, mission_id: str, reason: str) -> "MissionNextOperation":
        return cls(
            mission_id=mission_id,
            capability_id="",
            operation_type="",
            subject_id="",
            reason=reason,
            requires_confirmation=False,
            requires_downstream_approval=False,
            mutates_state=False,
            blocked_reason=reason,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "capability_id": self.capability_id,
            "operation_type": self.operation_type,
            "operation": self.operation_type,
            "subject_id": self.subject_id,
            "target_id": self.subject_id,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "requires_downstream_approval": self.requires_downstream_approval,
            "mutates_state": self.mutates_state,
            "required_inputs": list(self.required_inputs),
            "resolved_inputs": _thaw_json(self.resolved_inputs),
            "cli_representation": self.cli_representation,
            "cli_command": self.cli_representation,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class MissionAdvancePreview:
    mission_id: str
    status: str
    stage: str
    progress: int
    mission_updated_at: str
    last_event_id: str
    operation: MissionNextOperation
    advance_token: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        if not isinstance(self.operation, MissionNextOperation):
            raise TypeError("Mission preview requires a typed next operation.")
        if self.operation.mission_id != self.mission_id:
            raise ValueError("Mission preview operation identity does not match.")
        if isinstance(self.progress, bool) or not isinstance(self.progress, int):
            raise TypeError("Mission preview progress must be an integer.")
        if not 0 <= self.progress <= 100:
            raise ValueError("Mission preview progress must be between 0 and 100.")
        token = _text(self.advance_token, "Mission advance token", 64).lower()
        if token and not ADVANCE_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Mission advance token has an invalid format.")
        if self.operation.blocked and token:
            raise ValueError("Blocked Mission previews cannot issue advance tokens.")
        if not self.operation.blocked and not token:
            raise ValueError("Eligible Mission previews require an advance token.")
        object.__setattr__(self, "status", _text(
            self.status, "Mission preview status", 40, required=True
        ))
        object.__setattr__(self, "stage", _text(
            self.stage, "Mission preview stage", 40, required=True
        ))
        object.__setattr__(self, "mission_updated_at", _timestamp(
            self.mission_updated_at,
            "Mission preview update timestamp",
            required=True,
        ))
        last_event_id = _text(
            self.last_event_id, "Mission preview last event ID", 38
        ).lower()
        if last_event_id and not EVENT_ID_PATTERN.fullmatch(last_event_id):
            raise ValueError("Mission preview last event ID has an invalid format.")
        object.__setattr__(self, "last_event_id", last_event_id)
        object.__setattr__(self, "advance_token", token)
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(
            _text(item, "Mission preview warning", 500, required=True)
            for item in self.warnings
        )))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "mission_updated_at": self.mission_updated_at,
            "last_event_id": self.last_event_id,
            **self.operation.to_dict(),
            "advance_token": self.advance_token,
            "warnings": list(self.warnings),
            "preview_only": True,
            "operation_dispatched": False,
        }


@dataclass(frozen=True)
class MissionAdvanceRequest:
    mission_id: str
    advance_token: str = ""
    confirmed: bool = False
    actor: str = "user"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        token = _text(self.advance_token, "Mission advance token", 64).lower()
        if token and not ADVANCE_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Mission advance token has an invalid format.")
        if not isinstance(self.confirmed, bool):
            raise TypeError("Mission advance confirmation must be a boolean.")
        object.__setattr__(self, "advance_token", token)
        object.__setattr__(self, "actor", _text(
            self.actor, "Mission advance actor", 100, required=True
        ))


@dataclass(frozen=True)
class MissionAdvanceResult:
    mission_id: str
    operation_dispatched: bool
    capability_id: str
    downstream_result: Mapping[str, object]
    previous_status: str
    new_status: str
    new_stage: str
    new_progress: int
    reconciled: bool
    next_action: str
    audit_state: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        if not isinstance(self.operation_dispatched, bool):
            raise TypeError("Mission operation dispatch flag must be a boolean.")
        if not isinstance(self.reconciled, bool):
            raise TypeError("Mission reconciliation flag must be a boolean.")
        if isinstance(self.new_progress, bool) or not isinstance(self.new_progress, int):
            raise TypeError("Mission result progress must be an integer.")
        if not 0 <= self.new_progress <= 100:
            raise ValueError("Mission result progress must be between 0 and 100.")
        object.__setattr__(self, "capability_id", _text(
            self.capability_id, "Mission result capability", 100, required=True
        ))
        object.__setattr__(self, "downstream_result", _freeze_json(
            dict(self.downstream_result), "Mission downstream result"
        ))
        for name, label in (
            ("previous_status", "Mission previous status"),
            ("new_status", "Mission new status"),
            ("new_stage", "Mission new stage"),
            ("next_action", "Mission result next action"),
            ("audit_state", "Mission audit state"),
        ):
            object.__setattr__(self, name, _text(
                getattr(self, name), label, 500, required=name != "next_action"
            ))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(
            _text(item, "Mission result warning", 500, required=True)
            for item in self.warnings
        )))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "operation_dispatched": self.operation_dispatched,
            "capability_id": self.capability_id,
            "downstream_result": _thaw_json(self.downstream_result),
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "new_stage": self.new_stage,
            "new_progress": self.new_progress,
            "reconciled": self.reconciled,
            "next_action": self.next_action,
            "audit_state": self.audit_state,
            "warnings": list(self.warnings),
            "stopped_after_one_operation": True,
        }


@dataclass(frozen=True)
class MissionAdvanceAudit:
    schema_version: int
    mission_id: str
    attempt_id: str
    advance_token: str
    capability_id: str
    subject_id: str
    actor: str
    state: str
    started_at: str
    finished_at: str = ""
    result_status: str = ""
    downstream_reference: str = ""
    last_advance_event_id: str = ""
    safe_message: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                f"Unsupported Mission coordination audit schema: {self.schema_version}."
            )
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        attempt_id = _text(self.attempt_id, "Mission attempt ID", 48, required=True)
        if not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise ValueError("Mission attempt ID has an invalid format.")
        token = _text(self.advance_token, "Mission advance token", 64, required=True)
        if not ADVANCE_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Mission advance token has an invalid format.")
        capability = _text(self.capability_id, "Mission audit capability", 100, required=True)
        if not CAPABILITY_ID_PATTERN.fullmatch(capability):
            raise ValueError("Mission audit capability has an invalid format.")
        subject = _text(self.subject_id, "Mission audit subject", 160, required=True)
        if not LINK_ID_PATTERN.fullmatch(subject):
            raise ValueError("Mission audit subject has an invalid format.")
        state = _text(self.state, "Mission audit state", 20, required=True)
        if state not in AUDIT_STATES:
            raise ValueError(f"Mission audit state is not supported: {state}")
        finished = _timestamp(self.finished_at, "Mission audit finish timestamp")
        if state == "reserved" and finished:
            raise ValueError("Reserved Mission attempts cannot be finished.")
        if state != "reserved" and not finished:
            raise ValueError("Terminal Mission attempts require a finish timestamp.")
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "advance_token", token)
        object.__setattr__(self, "capability_id", capability)
        object.__setattr__(self, "subject_id", subject)
        object.__setattr__(self, "actor", _text(
            self.actor, "Mission audit actor", 100, required=True
        ))
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "started_at", _timestamp(
            self.started_at, "Mission audit start timestamp", required=True
        ))
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "result_status", _text(
            self.result_status, "Mission audit result status", 40
        ))
        object.__setattr__(self, "downstream_reference", _text(
            self.downstream_reference, "Mission audit downstream reference", 160
        ))
        last_event_id = _text(
            self.last_advance_event_id, "Mission audit event ID", 38
        ).lower()
        if last_event_id and not EVENT_ID_PATTERN.fullmatch(last_event_id):
            raise ValueError("Mission audit event ID has an invalid format.")
        object.__setattr__(self, "last_advance_event_id", last_event_id)
        object.__setattr__(self, "safe_message", _text(
            self.safe_message, "Mission audit message", 300
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "attempt_id": self.attempt_id,
            "advance_token": self.advance_token,
            "capability_id": self.capability_id,
            "subject_id": self.subject_id,
            "actor": self.actor,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_status": self.result_status,
            "downstream_reference": self.downstream_reference,
            "last_advance_event_id": self.last_advance_event_id,
            "safe_message": self.safe_message,
        }

    @classmethod
    def from_value(cls, value: object) -> "MissionAdvanceAudit":
        if not isinstance(value, Mapping):
            raise TypeError("Mission coordination audit must be a JSON object.")
        fields = {
            "schema_version", "mission_id", "attempt_id", "advance_token",
            "capability_id", "subject_id", "actor", "state", "started_at",
            "finished_at", "result_status", "downstream_reference",
            "last_advance_event_id", "safe_message",
        }
        if set(value) != fields:
            raise ValueError("Mission coordination audit fields are invalid.")
        return cls(**dict(value))
