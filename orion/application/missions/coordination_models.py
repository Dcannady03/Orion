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
ATTEMPT_HISTORY_KINDS = frozenset({"attempt", "resolution"})
RESOLUTION_FINDINGS = frozenset({"ran", "did_not_run", "cannot_determine"})
RESOLUTION_AUDIT_OUTCOMES = frozenset({"succeeded", "cleared", "none"})
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
    decision: str = ""
    note: str = ""

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
        decision = _text(
            self.decision, "Mission final-review decision", 20
        ).lower()
        if decision and decision not in {"accepted", "changes_requested", "rejected"}:
            raise ValueError(
                "Mission final-review decision is not accepted, changes "
                "requested, or rejected."
            )
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "note", _text(
            self.note, "Mission final-review note", 500
        ))


@dataclass(frozen=True)
class MissionAdvanceDispatch:
    """A reserved Mission advance: the durable audit plus the frozen operation.

    Returned by MissionCoordinator.dispatch() after the audit has been
    written in state "reserved". The caller hands it to execute() to run
    the operation; execute() re-checks that the audit is still reserved
    with the same attempt ID before dispatching, so an operator
    resolution between dispatch and execute wins and the operation never
    runs twice. The preview is frozen at dispatch time: the operation,
    its resolved inputs, and the advance token cannot drift while the
    dispatch waits to execute.
    """

    mission_id: str
    audit: MissionAdvanceAudit
    preview: MissionAdvancePreview
    actor: str
    correlation_id: str
    causation_id: str
    previous_status: str
    decision: str = ""
    note: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        if not isinstance(self.audit, MissionAdvanceAudit):
            raise TypeError("Mission dispatch requires a typed advance audit.")
        if not isinstance(self.preview, MissionAdvancePreview):
            raise TypeError("Mission dispatch requires a typed advance preview.")
        if self.audit.mission_id != self.mission_id:
            raise ValueError("Mission dispatch audit identity does not match.")
        if self.preview.mission_id != self.mission_id:
            raise ValueError("Mission dispatch preview identity does not match.")
        if self.audit.state != "reserved":
            raise ValueError("Mission dispatch requires a reserved audit.")
        if self.audit.advance_token != self.preview.advance_token:
            raise ValueError("Mission dispatch audit and preview tokens differ.")
        object.__setattr__(self, "actor", _text(
            self.actor, "Mission dispatch actor", 100, required=True
        ))
        object.__setattr__(self, "correlation_id", _text(
            self.correlation_id, "Mission dispatch correlation ID", 160
        ))
        causation_id = _text(
            self.causation_id, "Mission dispatch causation ID", 38
        ).lower()
        if causation_id and not EVENT_ID_PATTERN.fullmatch(causation_id):
            raise ValueError("Mission dispatch causation ID has an invalid format.")
        object.__setattr__(self, "causation_id", causation_id)
        object.__setattr__(self, "previous_status", _text(
            self.previous_status, "Mission dispatch previous status", 40,
            required=True,
        ))
        decision = _text(
            self.decision, "Mission final-review decision", 20
        ).lower()
        if decision and decision not in {"accepted", "changes_requested", "rejected"}:
            raise ValueError(
                "Mission final-review decision is not accepted, changes "
                "requested, or rejected."
            )
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "note", _text(
            self.note, "Mission final-review note", 500
        ))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(
            _text(item, "Mission dispatch warning", 500, required=True)
            for item in self.warnings
        )))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "attempt_id": self.audit.attempt_id,
            "advance_token": self.audit.advance_token,
            "capability_id": self.audit.capability_id,
            "subject_id": self.audit.subject_id,
            "actor": self.actor,
            "state": self.audit.state,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "previous_status": self.previous_status,
            "decision": self.decision,
            "note": self.note,
            "warnings": list(self.warnings),
            "audit": self.audit.to_dict(),
            "preview": self.preview.to_dict(),
        }


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


@dataclass(frozen=True)
class MissionAttemptHistoryEntry:
    """One append-only attempt snapshot in a Mission's attempt history.

    The entry wraps a full MissionAdvanceAudit snapshot plus the time the
    snapshot was appended. Attempts are recorded at every audit transition
    (reserved -> uncertain/succeeded/failed), so the log holds the full
    trail while the blocking logic keeps reading only the single latest
    audit record.
    """

    schema_version: int
    kind: str
    recorded_at: str
    audit: MissionAdvanceAudit

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                "Unsupported Mission attempt history schema: "
                f"{self.schema_version}."
            )
        kind = _text(
            self.kind, "Mission attempt history kind", 20, required=True
        )
        if kind != "attempt":
            raise ValueError(
                f"Mission attempt history kind is not supported: {kind}"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "recorded_at", _timestamp(
            self.recorded_at,
            "Mission attempt history timestamp",
            required=True,
        ))
        if not isinstance(self.audit, MissionAdvanceAudit):
            raise TypeError(
                "Mission attempt history requires a Mission advance audit."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "recorded_at": self.recorded_at,
            "audit": self.audit.to_dict(),
        }

    @classmethod
    def from_value(cls, value: object) -> "MissionAttemptHistoryEntry":
        if not isinstance(value, Mapping):
            raise TypeError("Mission attempt history entry must be a JSON object.")
        fields = {"schema_version", "kind", "recorded_at", "audit"}
        if set(value) != fields:
            raise ValueError("Mission attempt history entry fields are invalid.")
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            recorded_at=value["recorded_at"],
            audit=MissionAdvanceAudit.from_value(value["audit"]),
        )


@dataclass(frozen=True)
class MissionResolutionHistoryEntry:
    """One append-only resolution record in a Mission's attempt history.

    A resolution is a distinct operator action, never an edit of the audit
    it resolves. Lock-only resolutions carry no attempt or capability: the
    finding is empty and the audit outcome is ``none``.
    """

    schema_version: int
    kind: str
    mission_id: str
    actor: str
    resolved_at: str
    attempt_id: str = ""
    capability_id: str = ""
    finding: str = ""
    audit_outcome: str = "none"
    downstream_reference: str = ""
    lock_cleared: bool = False
    recovery_record_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                "Unsupported Mission attempt history schema: "
                f"{self.schema_version}."
            )
        kind = _text(
            self.kind, "Mission attempt history kind", 20, required=True
        )
        if kind != "resolution":
            raise ValueError(
                f"Mission attempt history kind is not supported: {kind}"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        object.__setattr__(self, "actor", _text(
            self.actor, "Mission resolution actor", 100, required=True
        ))
        object.__setattr__(self, "resolved_at", _timestamp(
            self.resolved_at,
            "Mission resolution timestamp",
            required=True,
        ))
        attempt_id = _text(self.attempt_id, "Mission attempt ID", 48)
        if attempt_id and not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise ValueError("Mission attempt ID has an invalid format.")
        capability = _text(
            self.capability_id, "Mission resolution capability", 100
        )
        if capability and not CAPABILITY_ID_PATTERN.fullmatch(capability):
            raise ValueError("Mission resolution capability has an invalid format.")
        finding = _text(self.finding, "Mission resolution finding", 20)
        if finding and finding not in RESOLUTION_FINDINGS:
            raise ValueError(
                f"Mission resolution finding is not supported: {finding}"
            )
        outcome = _text(
            self.audit_outcome, "Mission resolution audit outcome", 20,
            required=True,
        )
        if outcome not in RESOLUTION_AUDIT_OUTCOMES:
            raise ValueError(
                f"Mission resolution audit outcome is not supported: {outcome}"
            )
        if finding:
            if not attempt_id:
                raise ValueError(
                    "Mission resolution with a finding requires an attempt ID."
                )
            if not capability:
                raise ValueError(
                    "Mission resolution with a finding requires a capability."
                )
            if outcome == "none":
                raise ValueError(
                    "Mission resolution with a finding requires an audit outcome."
                )
        else:
            if attempt_id or capability or outcome != "none":
                raise ValueError(
                    "Mission resolution without a finding must be lock-only."
                )
        if not isinstance(self.lock_cleared, bool):
            raise TypeError("Mission resolution lock flag must be a boolean.")
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "capability_id", capability)
        object.__setattr__(self, "finding", finding)
        object.__setattr__(self, "audit_outcome", outcome)
        object.__setattr__(self, "downstream_reference", _text(
            self.downstream_reference,
            "Mission resolution downstream reference",
            160,
        ))
        object.__setattr__(self, "recovery_record_id", _text(
            self.recovery_record_id,
            "Mission resolution recovery record ID",
            64,
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "mission_id": self.mission_id,
            "actor": self.actor,
            "resolved_at": self.resolved_at,
            "attempt_id": self.attempt_id,
            "capability_id": self.capability_id,
            "finding": self.finding,
            "audit_outcome": self.audit_outcome,
            "downstream_reference": self.downstream_reference,
            "lock_cleared": self.lock_cleared,
            "recovery_record_id": self.recovery_record_id,
        }

    @classmethod
    def from_value(cls, value: object) -> "MissionResolutionHistoryEntry":
        if not isinstance(value, Mapping):
            raise TypeError("Mission attempt history entry must be a JSON object.")
        fields = {
            "schema_version", "kind", "mission_id", "actor", "resolved_at",
            "attempt_id", "capability_id", "finding", "audit_outcome",
            "downstream_reference", "lock_cleared", "recovery_record_id",
        }
        if set(value) != fields:
            raise ValueError("Mission attempt history entry fields are invalid.")
        return cls(**dict(value))


def parse_attempt_history_entry(
    value: object,
) -> MissionAttemptHistoryEntry | MissionResolutionHistoryEntry:
    """Parse one attempt-history log line by its kind discriminator."""
    if not isinstance(value, Mapping):
        raise TypeError("Mission attempt history entry must be a JSON object.")
    kind = str(value.get("kind") or "").strip()
    if kind == "attempt":
        return MissionAttemptHistoryEntry.from_value(value)
    if kind == "resolution":
        return MissionResolutionHistoryEntry.from_value(value)
    raise ValueError("Mission attempt history kind is not supported.")


@dataclass(frozen=True)
class MissionLockInfo:
    """Bounded facts read from one per-Mission advance lock file.

    A lock file written by the current advance lock records the holder PID
    and the UTC time the lock was taken. Older or malformed lock files carry
    no usable identity: ``pid`` is 0 and ``started_at`` is empty, and the
    holder cannot be confirmed either way.
    """

    mission_id: str
    pid: int = 0
    started_at: str = ""
    lock_path: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        if (
            isinstance(self.pid, bool)
            or not isinstance(self.pid, int)
            or self.pid < 0
        ):
            raise TypeError("Mission lock PID must be a non-negative integer.")
        object.__setattr__(self, "started_at", _timestamp(
            self.started_at,
            "Mission lock start timestamp",
        ))
        object.__setattr__(self, "lock_path", _text(
            self.lock_path, "Mission lock path", 2_000
        ))

    @property
    def known(self) -> bool:
        """Whether the lock file identifies its holder process."""
        return self.pid > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "pid": self.pid,
            "started_at": self.started_at,
            "lock_path": self.lock_path,
            "known": self.known,
        }


@dataclass(frozen=True)
class MissionResolvePreview:
    """Read-only uncertainty inspection; resolving nothing by itself."""

    mission_id: str
    status: str
    stage: str
    progress: int
    mission_updated_at: str
    last_event_id: str
    audit: MissionAdvanceAudit | None = None
    audit_blocked: bool = False
    lock: MissionLockInfo | None = None
    lock_blocked: bool = False
    lock_holder_alive: bool | None = None
    downstream_observed: bool = False
    downstream_reference: str = ""
    downstream_evidence: str = ""
    downstream_unknown: bool = False
    suggested_finding: str = ""
    resolvable: bool = False
    blocked_reason: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        if self.audit is not None and not isinstance(
            self.audit, MissionAdvanceAudit
        ):
            raise TypeError("Mission resolve preview audit must be typed.")
        if self.lock is not None and not isinstance(self.lock, MissionLockInfo):
            raise TypeError("Mission resolve preview lock must be typed.")
        if self.audit is not None and self.audit.mission_id != self.mission_id:
            raise ValueError("Mission resolve preview audit identity does not match.")
        if self.lock is not None and self.lock.mission_id != self.mission_id:
            raise ValueError("Mission resolve preview lock identity does not match.")
        for name in (
            "audit_blocked",
            "lock_blocked",
            "downstream_observed",
            "downstream_unknown",
            "resolvable",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean.")
        if self.lock_holder_alive is not None and not isinstance(
            self.lock_holder_alive, bool
        ):
            raise TypeError("Mission lock holder liveness must be a boolean or null.")
        if isinstance(self.progress, bool) or not isinstance(self.progress, int):
            raise TypeError("Mission resolve preview progress must be an integer.")
        if not 0 <= self.progress <= 100:
            raise ValueError("Mission resolve preview progress must be between 0 and 100.")
        if self.audit_blocked and self.audit is None:
            raise ValueError("A blocked Mission resolve preview requires an audit.")
        if self.lock_blocked and self.lock is None:
            raise ValueError("A lock-blocked Mission resolve preview requires lock info.")
        if self.lock_holder_alive is not None and not self.lock_blocked:
            raise ValueError("Lock holder liveness requires a lock.")
        suggested = _text(
            self.suggested_finding, "Mission resolve suggested finding", 20
        ).lower()
        if suggested and suggested not in {"ran", "did_not_run"}:
            raise ValueError(
                "Mission resolve suggested finding is not ran or did not run."
            )
        blocked_reason = _text(
            self.blocked_reason,
            "Mission resolve blocked reason",
            500,
        )
        if self.resolvable and blocked_reason:
            raise ValueError("Resolvable Mission previews cannot carry a blocked reason.")
        if not self.resolvable and not blocked_reason:
            raise ValueError("Unresolvable Mission previews require a blocked reason.")
        object.__setattr__(self, "status", _text(
            self.status, "Mission resolve preview status", 40, required=True
        ))
        object.__setattr__(self, "stage", _text(
            self.stage, "Mission resolve preview stage", 40, required=True
        ))
        object.__setattr__(self, "mission_updated_at", _timestamp(
            self.mission_updated_at,
            "Mission resolve preview update timestamp",
            required=True,
        ))
        last_event_id = _text(
            self.last_event_id, "Mission resolve preview last event ID", 38
        ).lower()
        if last_event_id and not EVENT_ID_PATTERN.fullmatch(last_event_id):
            raise ValueError("Mission resolve preview last event ID has an invalid format.")
        object.__setattr__(self, "last_event_id", last_event_id)
        object.__setattr__(self, "downstream_reference", _text(
            self.downstream_reference,
            "Mission resolve downstream reference",
            160,
        ))
        object.__setattr__(self, "downstream_evidence", _text(
            self.downstream_evidence,
            "Mission resolve downstream evidence",
            500,
        ))
        object.__setattr__(self, "suggested_finding", suggested)
        object.__setattr__(self, "blocked_reason", blocked_reason)
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(
            _text(item, "Mission resolve preview warning", 500, required=True)
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
            "audit": self.audit.to_dict() if self.audit is not None else None,
            "audit_blocked": self.audit_blocked,
            "lock": self.lock.to_dict() if self.lock is not None else None,
            "lock_blocked": self.lock_blocked,
            "lock_holder_alive": self.lock_holder_alive,
            "downstream_observed": self.downstream_observed,
            "downstream_reference": self.downstream_reference,
            "downstream_evidence": self.downstream_evidence,
            "downstream_unknown": self.downstream_unknown,
            "suggested_finding": self.suggested_finding,
            "resolvable": self.resolvable,
            "blocked_reason": self.blocked_reason,
            "warnings": list(self.warnings),
            "preview_only": True,
            "operation_dispatched": False,
        }


@dataclass(frozen=True)
class MissionResolveRequest:
    mission_id: str
    finding: str = ""
    confirmed: bool = False
    actor: str = "user"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        finding = _text(
            self.finding, "Mission resolve finding", 20
        ).lower()
        if finding and finding not in RESOLUTION_FINDINGS:
            raise ValueError(
                "Mission resolve finding is not ran, did not run, or cannot "
                "determine."
            )
        if not isinstance(self.confirmed, bool):
            raise TypeError("Mission resolve confirmation must be a boolean.")
        object.__setattr__(self, "finding", finding)
        object.__setattr__(self, "actor", _text(
            self.actor, "Mission resolve actor", 100, required=True
        ))


@dataclass(frozen=True)
class MissionResolveResult:
    mission_id: str
    finding: str
    audit_outcome: str
    lock_cleared: bool
    downstream_reference: str
    previous_status: str
    new_status: str
    new_stage: str
    new_progress: int
    reconciled: bool
    next_action: str
    recovery_record_id: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        finding = _text(self.finding, "Mission resolve finding", 20).lower()
        if finding and finding not in {"ran", "did_not_run"}:
            raise ValueError(
                "Mission resolve result finding is not ran or did not run."
            )
        audit_outcome = _text(
            self.audit_outcome, "Mission resolve audit outcome", 20
        ).lower()
        if audit_outcome not in RESOLUTION_AUDIT_OUTCOMES:
            raise ValueError(
                "Mission resolve audit outcome is not succeeded, cleared, or none."
            )
        if not isinstance(self.lock_cleared, bool):
            raise TypeError("Mission resolve lock flag must be a boolean.")
        if not isinstance(self.reconciled, bool):
            raise TypeError("Mission resolve reconciliation flag must be a boolean.")
        if isinstance(self.new_progress, bool) or not isinstance(self.new_progress, int):
            raise TypeError("Mission resolve progress must be an integer.")
        if not 0 <= self.new_progress <= 100:
            raise ValueError("Mission resolve progress must be between 0 and 100.")
        object.__setattr__(self, "finding", finding)
        object.__setattr__(self, "audit_outcome", audit_outcome)
        object.__setattr__(self, "downstream_reference", _text(
            self.downstream_reference,
            "Mission resolve downstream reference",
            160,
        ))
        for name, label in (
            ("previous_status", "Mission previous status"),
            ("new_status", "Mission new status"),
            ("new_stage", "Mission new stage"),
            ("next_action", "Mission resolve next action"),
        ):
            object.__setattr__(self, name, _text(
                getattr(self, name), label, 500, required=name != "next_action"
            ))
        object.__setattr__(self, "recovery_record_id", _text(
            self.recovery_record_id, "Mission resolve recovery record ID", 40
        ))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(
            _text(item, "Mission resolve warning", 500, required=True)
            for item in self.warnings
        )))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "finding": self.finding,
            "audit_outcome": self.audit_outcome,
            "lock_cleared": self.lock_cleared,
            "downstream_reference": self.downstream_reference,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "new_stage": self.new_stage,
            "new_progress": self.new_progress,
            "reconciled": self.reconciled,
            "next_action": self.next_action,
            "recovery_record_id": self.recovery_record_id,
            "warnings": list(self.warnings),
            "resolution_applied": True,
            "operation_dispatched": False,
        }
