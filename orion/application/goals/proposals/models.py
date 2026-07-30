"""Immutable, JSON-safe Goal Proposal lifecycle models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import math
import re
from typing import Any, Mapping

from orion.application.results import _freeze_json, _thaw_json


GOAL_PROPOSAL_SCHEMA_VERSION = 1
PROPOSAL_ID_PATTERN = re.compile(r"proposal-[a-f0-9]{32}")
HASH_PATTERN = re.compile(r"[a-f0-9]{64}")
STEP_ID_PATTERN = re.compile(r"proposal-[a-f0-9]{32}-step-[0-9]{3}")
SECRET_KEYS = frozenset({
    "api_key",
    "authorization",
    "credential",
    "oauth",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
})


class GoalProposalStatus(str, Enum):
    """Stable proposal lifecycle states."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALID = "invalid"
    SUPERSEDED = "superseded"
    CONSUMED = "consumed"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: object) -> "GoalProposalStatus":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Goal Proposal status must be one of: {choices}.") from exc


class GoalProposalStepStatus(str, Enum):
    """Stable per-step state; only one step may be dispatched."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    CONSUMED = "consumed"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: object) -> "GoalProposalStepStatus":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(
                f"Goal Proposal step status must be one of: {choices}."
            ) from exc


def _text(
    value: object,
    label: str,
    maximum: int,
    *,
    required: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{label} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _strings(
    value: object,
    label: str,
    *,
    maximum_items: int = 100,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list of strings.")
    result = tuple(
        _text(item, label, 1_000, required=True)
        for item in value
    )
    if len(result) > maximum_items:
        raise ValueError(f"{label} cannot contain more than {maximum_items} items.")
    return result


def _timestamp(value: object, label: str, *, optional: bool = False) -> str:
    text = _text(value, label, 80, required=not optional)
    if optional and not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text


def _hash(value: object, label: str) -> str:
    normalized = _text(value, label, 64, required=True).lower()
    if not HASH_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return normalized


def _proposal_id(value: object) -> str:
    normalized = _text(value, "Proposal ID", 41, required=True).lower()
    if not PROPOSAL_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Proposal ID has an invalid format.")
    return normalized


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings.")
    return dict(value)


def _safe_metadata(value: object, label: str) -> Mapping[str, object]:
    data = _mapping(value, label)

    def inspect(item: object, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if any(secret in normalized for secret in SECRET_KEYS):
                    raise ValueError(f"{path} cannot contain secret-bearing fields.")
                inspect(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                inspect(child, f"{path}[{index}]")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{path} cannot contain non-finite numbers.")
        elif item is not None and not isinstance(item, (str, int, bool)):
            raise ValueError(f"{path} contains a non-JSON value.")

    inspect(data, label)
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > 16_000:
        raise ValueError(f"{label} exceeds the 16,000-byte limit.")
    return _freeze_json(data)


@dataclass(frozen=True)
class GoalProposalStep:
    """One ordered capability snapshot in an immutable proposal."""

    step_id: str
    step_number: int
    capability_id: str
    reason: str
    requires_approval: bool
    mutates_state: bool
    required_inputs: tuple[str, ...]
    resolved_inputs: Mapping[str, object]
    expected_outputs: tuple[str, ...]
    required_permissions: tuple[str, ...]
    status: GoalProposalStepStatus = GoalProposalStepStatus.PENDING
    application_request_type: str = ""

    def __post_init__(self) -> None:
        step_id = _text(self.step_id, "Proposal step ID", 60, required=True).lower()
        if not STEP_ID_PATTERN.fullmatch(step_id):
            raise ValueError("Proposal step ID has an invalid format.")
        object.__setattr__(self, "step_id", step_id)
        if (
            isinstance(self.step_number, bool)
            or not isinstance(self.step_number, int)
            or self.step_number < 1
        ):
            raise ValueError("Proposal step number must be a positive integer.")
        for name, label, maximum in (
            ("capability_id", "Capability ID", 200),
            ("reason", "Capability reason", 1_000),
            ("application_request_type", "Application request type", 200),
        ):
            object.__setattr__(
                self,
                name,
                _text(
                    getattr(self, name),
                    label,
                    maximum,
                    required=name != "application_request_type",
                ),
            )
        if not isinstance(self.requires_approval, bool):
            raise ValueError("Step approval metadata must be true or false.")
        if not isinstance(self.mutates_state, bool):
            raise ValueError("Step mutation metadata must be true or false.")
        object.__setattr__(
            self,
            "required_inputs",
            _strings(self.required_inputs, "Required inputs"),
        )
        object.__setattr__(
            self,
            "resolved_inputs",
            _safe_metadata(self.resolved_inputs, "Resolved inputs"),
        )
        object.__setattr__(
            self,
            "expected_outputs",
            _strings(self.expected_outputs, "Expected outputs"),
        )
        object.__setattr__(
            self,
            "required_permissions",
            _strings(self.required_permissions, "Required permissions"),
        )
        object.__setattr__(
            self,
            "status",
            GoalProposalStepStatus.parse(self.status),
        )

    def immutable_dict(self) -> dict[str, object]:
        """Return safety-relevant fields covered by the proposal plan hash."""
        return {
            "step_id": self.step_id,
            "step_number": self.step_number,
            "capability_id": self.capability_id,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "mutates_state": self.mutates_state,
            "required_inputs": list(self.required_inputs),
            "resolved_inputs": _thaw_json(self.resolved_inputs),
            "expected_outputs": list(self.expected_outputs),
            "required_permissions": list(self.required_permissions),
            "application_request_type": self.application_request_type,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.immutable_dict(),
            "status": self.status.value,
        }

    @classmethod
    def from_value(cls, value: object) -> "GoalProposalStep":
        data = _mapping(value, "Goal Proposal step")
        allowed = {
            "step_id", "step_number", "capability_id", "reason",
            "requires_approval", "mutates_state", "required_inputs",
            "resolved_inputs", "expected_outputs", "required_permissions",
            "status", "application_request_type",
        }
        unknown = set(data) - allowed
        missing = allowed - set(data)
        if missing or unknown:
            raise ValueError(
                f"Goal Proposal step fields are invalid; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}."
            )
        return cls(**data)


@dataclass(frozen=True)
class GoalProposalSnapshot:
    """Canonical immutable proposal content covered by ``plan_hash``."""

    proposal_id: str
    goal_id: str
    version: int
    goal_text: str
    classification: str
    workspace: str
    department_id: str
    department_name: str
    priority: str
    created_at: str
    expires_at: str
    registry_fingerprint: str
    capability_fingerprint: str
    steps: tuple[GoalProposalStep, ...]
    source: str
    supersedes: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_id",
            _proposal_id(self.proposal_id),
        )
        object.__setattr__(self, "goal_id", _text(
            self.goal_id, "Goal ID", 120, required=True
        ))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Goal Proposal version must be a positive integer.")
        for name, label, maximum, required in (
            ("goal_text", "Goal text", 4_000, True),
            ("classification", "Classification", 120, True),
            ("workspace", "Workspace", 1_000, True),
            ("department_id", "Department ID", 120, False),
            ("department_name", "Department name", 120, False),
            ("priority", "Priority", 20, True),
            ("source", "Proposal source", 120, True),
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), label, maximum, required=required),
            )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "Proposal creation"),
        )
        object.__setattr__(
            self,
            "expires_at",
            _timestamp(self.expires_at, "Proposal expiry"),
        )
        object.__setattr__(
            self,
            "registry_fingerprint",
            _hash(self.registry_fingerprint, "Registry fingerprint"),
        )
        object.__setattr__(
            self,
            "capability_fingerprint",
            _hash(self.capability_fingerprint, "Capability fingerprint"),
        )
        steps = tuple(self.steps)
        if not steps or any(not isinstance(item, GoalProposalStep) for item in steps):
            raise ValueError("Goal Proposal snapshot requires capability steps.")
        object.__setattr__(self, "steps", steps)
        supersedes = str(self.supersedes).strip()
        if supersedes:
            supersedes = _proposal_id(supersedes)
        object.__setattr__(self, "supersedes", supersedes)
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "Proposal metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "version": self.version,
            "goal_text": self.goal_text,
            "classification": self.classification,
            "workspace": self.workspace,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "priority": self.priority,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "registry_fingerprint": self.registry_fingerprint,
            "capability_fingerprint": self.capability_fingerprint,
            "steps": [item.immutable_dict() for item in self.steps],
            "source": self.source,
            "supersedes": self.supersedes,
            "metadata": _thaw_json(self.metadata),
        }


@dataclass(frozen=True)
class GoalProposal:
    """Persisted, versioned, expiring representation of a reviewed Goal Plan."""

    schema_version: int
    proposal_id: str
    goal_id: str
    version: int
    status: GoalProposalStatus
    goal_text: str
    classification: str
    workspace: str
    department_id: str
    department_name: str
    priority: str
    created_at: str
    updated_at: str
    expires_at: str
    plan_hash: str
    registry_fingerprint: str
    capability_fingerprint: str
    steps: tuple[GoalProposalStep, ...]
    current_step: int
    accepted_at: str = ""
    accepted_by: str = ""
    rejected_at: str = ""
    rejected_by: str = ""
    rejection_reason: str = ""
    consumed_at: str = ""
    failed_at: str = ""
    failure_code: str = ""
    failure_message: str = ""
    attempted_capability_id: str = ""
    retry_eligible: bool = False
    source: str = "goal_engine"
    supersedes: str = ""
    superseded_by: str = ""
    dispatch_summary: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != GOAL_PROPOSAL_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported Goal Proposal schema version: {self.schema_version}."
            )
        object.__setattr__(self, "proposal_id", _proposal_id(self.proposal_id))
        object.__setattr__(
            self,
            "status",
            GoalProposalStatus.parse(self.status),
        )
        for name in ("created_at", "updated_at", "expires_at"):
            object.__setattr__(
                self,
                name,
                _timestamp(getattr(self, name), name.replace("_", " ").title()),
            )
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        updated = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if updated < created:
            raise ValueError("Proposal updated_at cannot precede created_at.")
        if expires <= created:
            raise ValueError("Proposal expiry must follow creation.")
        object.__setattr__(self, "plan_hash", _hash(self.plan_hash, "Plan hash"))
        object.__setattr__(
            self,
            "registry_fingerprint",
            _hash(self.registry_fingerprint, "Registry fingerprint"),
        )
        object.__setattr__(
            self,
            "capability_fingerprint",
            _hash(self.capability_fingerprint, "Capability fingerprint"),
        )
        steps = tuple(self.steps)
        if not steps or any(not isinstance(item, GoalProposalStep) for item in steps):
            raise ValueError("Goal Proposal requires ordered capability steps.")
        numbers = tuple(item.step_number for item in steps)
        if numbers != tuple(range(1, len(steps) + 1)):
            raise ValueError("Goal Proposal steps must preserve consecutive plan order.")
        if len({item.step_id for item in steps}) != len(steps):
            raise ValueError("Goal Proposal step IDs must be unique.")
        object.__setattr__(self, "steps", steps)
        if (
            isinstance(self.current_step, bool)
            or not isinstance(self.current_step, int)
            or self.current_step not in numbers
        ):
            raise ValueError("Goal Proposal current_step must identify an existing step.")
        for name, label, maximum in (
            ("accepted_at", "Accepted at", 80),
            ("rejected_at", "Rejected at", 80),
            ("consumed_at", "Consumed at", 80),
            ("failed_at", "Failed at", 80),
        ):
            value = getattr(self, name)
            object.__setattr__(
                self,
                name,
                _timestamp(value, label, optional=True),
            )
        for name, label, maximum in (
            ("accepted_by", "Accepted by", 100),
            ("rejected_by", "Rejected by", 100),
            ("rejection_reason", "Rejection reason", 500),
            ("failure_code", "Failure code", 120),
            ("failure_message", "Failure message", 500),
            ("attempted_capability_id", "Attempted capability ID", 200),
            ("superseded_by", "Superseded by", 41),
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), label, maximum),
            )
        if self.superseded_by:
            object.__setattr__(
                self,
                "superseded_by",
                _proposal_id(self.superseded_by),
            )
        if not isinstance(self.retry_eligible, bool):
            raise ValueError("Proposal retry eligibility must be true or false.")
        if self.retry_eligible:
            raise ValueError("Goal Proposal retries are not supported in v0.8.3.")
        object.__setattr__(
            self,
            "dispatch_summary",
            _safe_metadata(self.dispatch_summary, "Dispatch summary"),
        )
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "Proposal metadata"),
        )
        self._validate_status_fields()
        # Reuse snapshot validation for immutable fields.
        self.snapshot()

    @property
    def department(self) -> str:
        return self.department_name

    @property
    def current(self) -> GoalProposalStep:
        return next(
            item for item in self.steps if item.step_number == self.current_step
        )

    def snapshot(self) -> GoalProposalSnapshot:
        return GoalProposalSnapshot(
            proposal_id=self.proposal_id,
            goal_id=self.goal_id,
            version=self.version,
            goal_text=self.goal_text,
            classification=self.classification,
            workspace=self.workspace,
            department_id=self.department_id,
            department_name=self.department_name,
            priority=self.priority,
            created_at=self.created_at,
            expires_at=self.expires_at,
            registry_fingerprint=self.registry_fingerprint,
            capability_fingerprint=self.capability_fingerprint,
            steps=self.steps,
            source=self.source,
            supersedes=self.supersedes,
            metadata=self.metadata,
        )

    def _validate_status_fields(self) -> None:
        status = self.status
        if status in {
            GoalProposalStatus.ACCEPTED,
            GoalProposalStatus.CONSUMED,
            GoalProposalStatus.FAILED,
        } and not self.accepted_at:
            raise ValueError(f"{status.value} proposals require accepted_at.")
        if status is GoalProposalStatus.REJECTED and not self.rejected_at:
            raise ValueError("Rejected proposals require rejected_at.")
        if status is GoalProposalStatus.CONSUMED and not self.consumed_at:
            raise ValueError("Consumed proposals require consumed_at.")
        if status is GoalProposalStatus.FAILED and (
            not self.failed_at or not self.failure_code or not self.failure_message
        ):
            raise ValueError(
                "Failed proposals require safe failure time, code, and message."
            )
        if status is GoalProposalStatus.SUPERSEDED and not self.superseded_by:
            raise ValueError("Superseded proposals require superseded_by.")
        current_status = self.current.status
        expected = {
            GoalProposalStatus.PENDING: GoalProposalStepStatus.PENDING,
            GoalProposalStatus.ACCEPTED: GoalProposalStepStatus.ACCEPTED,
            GoalProposalStatus.CONSUMED: GoalProposalStepStatus.CONSUMED,
            GoalProposalStatus.FAILED: GoalProposalStepStatus.FAILED,
        }.get(status)
        if expected is not None and current_status is not expected:
            raise ValueError(
                f"{status.value} proposal current step must be {expected.value}."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "version": self.version,
            "status": self.status.value,
            "goal_text": self.goal_text,
            "classification": self.classification,
            "workspace": self.workspace,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "plan_hash": self.plan_hash,
            "registry_fingerprint": self.registry_fingerprint,
            "capability_fingerprint": self.capability_fingerprint,
            "steps": [item.to_dict() for item in self.steps],
            "current_step": self.current_step,
            "accepted_at": self.accepted_at,
            "accepted_by": self.accepted_by,
            "rejected_at": self.rejected_at,
            "rejected_by": self.rejected_by,
            "rejection_reason": self.rejection_reason,
            "consumed_at": self.consumed_at,
            "failed_at": self.failed_at,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "attempted_capability_id": self.attempted_capability_id,
            "retry_eligible": self.retry_eligible,
            "source": self.source,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "dispatch_summary": _thaw_json(self.dispatch_summary),
            "metadata": _thaw_json(self.metadata),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_value(cls, value: object) -> "GoalProposal":
        data = _mapping(value, "Goal Proposal")
        fields = {
            "schema_version", "proposal_id", "goal_id", "version", "status",
            "goal_text", "classification", "workspace", "department_id",
            "department_name", "priority", "created_at", "updated_at",
            "expires_at", "plan_hash", "registry_fingerprint",
            "capability_fingerprint", "steps", "current_step", "accepted_at",
            "accepted_by", "rejected_at", "rejected_by", "rejection_reason",
            "consumed_at", "failed_at", "failure_code", "failure_message",
            "attempted_capability_id", "retry_eligible", "source", "supersedes",
            "superseded_by", "dispatch_summary", "metadata",
        }
        missing = fields - set(data)
        unknown = set(data) - fields
        if missing or unknown:
            raise ValueError(
                f"Goal Proposal fields are invalid; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}."
            )
        raw_steps = data["steps"]
        if not isinstance(raw_steps, list):
            raise ValueError("Goal Proposal steps must be a JSON list.")
        data["steps"] = tuple(GoalProposalStep.from_value(item) for item in raw_steps)
        return cls(**data)


@dataclass(frozen=True)
class GoalProposalAcceptance:
    """Explicit, hash-bound request to accept and dispatch one proposal step."""

    proposal_id: str
    proposal_hash: str
    confirmed: bool
    accepted_by: str = "user"

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _proposal_id(self.proposal_id))
        object.__setattr__(
            self,
            "proposal_hash",
            _hash(self.proposal_hash, "Proposal acceptance hash"),
        )
        if not isinstance(self.confirmed, bool):
            raise ValueError("Proposal acceptance confirmation must be true or false.")
        object.__setattr__(
            self,
            "accepted_by",
            _text(self.accepted_by, "Accepted by", 100, required=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "confirmed": self.confirmed,
            "accepted_by": self.accepted_by,
        }


@dataclass(frozen=True)
class GoalProposalRejection:
    """Explicit request to reject one pending proposal."""

    proposal_id: str
    rejected_by: str = "user"
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _proposal_id(self.proposal_id))
        object.__setattr__(
            self,
            "rejected_by",
            _text(self.rejected_by, "Rejected by", 100, required=True),
        )
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, "Rejection reason", 500),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "rejected_by": self.rejected_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GoalProposalValidation:
    """Structured, read-only proposal validation evidence."""

    proposal_id: str
    valid: bool
    validation_status: str
    checked_at: str
    plan_hash_valid: bool
    capability_fingerprint_valid: bool
    registry_fingerprint_changed: bool
    workspace_valid: bool
    department_valid: bool
    inputs_valid: bool
    translation_supported: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _proposal_id(self.proposal_id))
        object.__setattr__(
            self,
            "validation_status",
            _text(
                self.validation_status,
                "Validation status",
                40,
                required=True,
            ),
        )
        object.__setattr__(
            self,
            "checked_at",
            _timestamp(self.checked_at, "Validation checked_at"),
        )
        for name in (
            "valid",
            "plan_hash_valid",
            "capability_fingerprint_valid",
            "registry_fingerprint_changed",
            "workspace_valid",
            "department_valid",
            "inputs_valid",
            "translation_supported",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be true or false.")
        object.__setattr__(
            self,
            "warnings",
            _strings(self.warnings, "Validation warnings"),
        )
        object.__setattr__(
            self,
            "errors",
            _strings(self.errors, "Validation errors"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "valid": self.valid,
            "validation_status": self.validation_status,
            "checked_at": self.checked_at,
            "plan_hash_valid": self.plan_hash_valid,
            "capability_fingerprint_valid": self.capability_fingerprint_valid,
            "registry_fingerprint_changed": self.registry_fingerprint_changed,
            "workspace_valid": self.workspace_valid,
            "department_valid": self.department_valid,
            "inputs_valid": self.inputs_valid,
            "translation_supported": self.translation_supported,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }
