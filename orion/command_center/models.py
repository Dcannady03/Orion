"""Provider-neutral domain models for Orion Command Center."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from orion.agents.models import normalize_agent_id


COMMAND_CENTER_SCHEMA_VERSION = 1
DEFAULT_ORGANIZATION_ID = "orion-organization"
DEFAULT_ORGANIZATION_NAME = "Orion Organization"
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")
EVENT_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+")
SECRET_KEY_PARTS = frozenset({
    "api_key", "apikey", "authorization", "credential", "oauth", "password",
    "private_key", "refresh_token", "secret", "token",
})
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[abprs])_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}\b", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_id(value: Any, label: str) -> str:
    normalized = re.sub(r"[-_\s]+", "-", str(value).strip().lower())
    if not ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} must be 2-80 lowercase letters, numbers, or hyphens."
        )
    return normalized


def parse_timestamp(value: Any, label: str, *, optional: bool = False) -> str:
    if optional and value in {None, ""}:
        return ""
    text = _text(value, label, 80, multiline=False)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text


def _text(
    value: Any,
    label: str,
    maximum: int,
    *,
    required: bool = True,
    multiline: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{label} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    if not multiline and any(character in normalized for character in "\r\n\t"):
        raise ValueError(f"{label} cannot contain control characters.")
    return normalized


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping.")
    return dict(value)


def _schema_version(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} schema_version must be an integer.")
    if value != COMMAND_CENTER_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported {label.lower()} schema version {value}; "
            f"this Orion version supports {COMMAND_CENTER_SCHEMA_VERSION}."
        )
    return value


def _enabled(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} enabled state must be true or false.")
    return value


def _looks_like_secret(value: Any) -> bool:
    return isinstance(value, str) and any(
        pattern.search(value.strip()) for pattern in SECRET_VALUE_PATTERNS
    )


def validate_safe_value(
    value: Any,
    path: str = "metadata",
    *,
    max_serialized_bytes: int = 16_000,
) -> Any:
    """Validate display-safe structured metadata without accepting credentials."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path} keys must be non-empty strings.")
            normalized = key.strip().lower().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                raise ValueError(f"{path} cannot store secret-bearing field: {key}")
            validate_safe_value(
                item,
                f"{path}.{key}",
                max_serialized_bytes=max_serialized_bytes,
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_safe_value(
                item,
                f"{path}[{index}]",
                max_serialized_bytes=max_serialized_bytes,
            )
    elif isinstance(value, str):
        if _looks_like_secret(value):
            raise ValueError(f"{path} cannot contain credential-shaped values.")
        if len(value) > 4_000:
            raise ValueError(f"{path} string values must be 4,000 characters or fewer.")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite.")
    elif value is not None and not isinstance(value, (bool, int)):
        raise ValueError(f"{path} contains an unsupported value type.")
    try:
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be JSON serializable.") from exc
    if size > max_serialized_bytes:
        raise ValueError(
            f"{path} must be {max_serialized_bytes:,} serialized bytes or fewer."
        )
    return value


def _extensions(value: dict[str, Any], known: frozenset[str], label: str) -> dict[str, Any]:
    result = {key: item for key, item in value.items() if key not in known}
    validate_safe_value(result, f"{label} extensions")
    return result


def _validate_time_order(
    created_at: str,
    updated_at: str,
    *,
    completed_at: str = "",
) -> None:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if updated < created:
        raise ValueError("updated_at cannot precede created_at.")
    if completed_at:
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if completed < created:
            raise ValueError("completed_at cannot precede created_at.")


class JobStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    @classmethod
    def parse(cls, value: Any) -> "JobStatus":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Job status must be one of: {choices}") from exc


class JobPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @classmethod
    def parse(cls, value: Any) -> "JobPriority":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Job priority must be one of: {choices}") from exc


class ApprovalState(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"

    @classmethod
    def parse(cls, value: Any) -> "ApprovalState":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Approval state must be one of: {choices}") from exc


class ActivitySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @classmethod
    def parse(cls, value: Any) -> "ActivitySeverity":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError("Activity severity must be info, warning, or error.") from exc


class ActivitySourceType(str, Enum):
    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    JOB = "job"
    AGENT = "agent"
    APPROVAL = "approval"
    TEAM = "team"
    SYSTEM = "system"
    USER = "user"

    @classmethod
    def parse(cls, value: Any) -> "ActivitySourceType":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"Activity source_type must be one of: {choices}") from exc


JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DRAFT: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.QUEUED: frozenset({
        JobStatus.PLANNING, JobStatus.AWAITING_APPROVAL, JobStatus.RUNNING,
        JobStatus.PAUSED, JobStatus.CANCELLED, JobStatus.FAILED,
    }),
    JobStatus.PLANNING: frozenset({
        JobStatus.AWAITING_APPROVAL, JobStatus.RUNNING, JobStatus.PAUSED,
        JobStatus.CANCELLED, JobStatus.FAILED,
    }),
    JobStatus.AWAITING_APPROVAL: frozenset({
        JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED,
        JobStatus.CANCELLED, JobStatus.FAILED,
    }),
    JobStatus.RUNNING: frozenset({
        JobStatus.AWAITING_REVIEW, JobStatus.COMPLETED, JobStatus.PAUSED,
        JobStatus.CANCELLED, JobStatus.FAILED,
    }),
    JobStatus.PAUSED: frozenset({
        JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLED,
        JobStatus.FAILED,
    }),
    JobStatus.AWAITING_REVIEW: frozenset({
        JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.CANCELLED,
        JobStatus.FAILED,
    }),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}
TERMINAL_JOB_STATUSES = frozenset({
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
})
ACTIVE_JOB_STATUSES = frozenset({
    JobStatus.PLANNING, JobStatus.RUNNING, JobStatus.PAUSED,
})


@dataclass(frozen=True)
class Organization:
    schema_version: int
    organization_id: str
    name: str
    description: str
    owner_profile_reference: str
    created_at: str
    updated_at: str
    enabled: bool
    extensions: dict[str, Any] = field(default_factory=dict)

    FIELDS = frozenset({
        "schema_version", "id", "name", "description", "owner_profile_reference",
        "created_at", "updated_at", "enabled",
    })

    @classmethod
    def create(
        cls,
        *,
        name: str = DEFAULT_ORGANIZATION_NAME,
        description: str = "Personal AI organization coordinated by Orion.",
        owner_profile_reference: str = "default",
        now: str | None = None,
    ) -> "Organization":
        timestamp = now or utc_now()
        return cls.from_value({
            "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
            "id": DEFAULT_ORGANIZATION_ID,
            "name": name,
            "description": description,
            "owner_profile_reference": owner_profile_reference,
            "created_at": timestamp,
            "updated_at": timestamp,
            "enabled": True,
        })

    @classmethod
    def from_value(cls, value: Any) -> "Organization":
        value = _mapping(value, "Organization")
        schema_version = _schema_version(
            value.get("schema_version"), "Organization"
        )
        created_at = parse_timestamp(value.get("created_at"), "Organization created_at")
        updated_at = parse_timestamp(value.get("updated_at"), "Organization updated_at")
        _validate_time_order(created_at, updated_at)
        result = cls(
            schema_version=schema_version,
            organization_id=normalize_id(value.get("id"), "Organization ID"),
            name=_text(value.get("name"), "Organization name", 120),
            description=_text(
                value.get("description", ""),
                "Organization description",
                2_000,
                required=False,
            ),
            owner_profile_reference=_text(
                value.get("owner_profile_reference", ""),
                "Organization owner profile reference",
                200,
                required=False,
                multiline=False,
            ),
            created_at=created_at,
            updated_at=updated_at,
            enabled=_enabled(value.get("enabled"), "Organization"),
            extensions=_extensions(value, cls.FIELDS, "Organization"),
        )
        validate_safe_value(result.to_dict(), "Organization record")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.organization_id,
            "name": self.name,
            "description": self.description,
            "owner_profile_reference": self.owner_profile_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "enabled": self.enabled,
            **self.extensions,
        }


@dataclass(frozen=True)
class Department:
    schema_version: int
    department_id: str
    name: str
    description: str
    icon: str
    agent_ids: tuple[str, ...]
    workflow_policy_reference: str
    created_at: str
    updated_at: str
    enabled: bool
    extensions: dict[str, Any] = field(default_factory=dict)

    FIELDS = frozenset({
        "schema_version", "id", "name", "description", "icon", "agent_ids",
        "workflow_policy_reference", "created_at", "updated_at", "enabled",
    })

    @classmethod
    def create(
        cls,
        *,
        department_id: str,
        name: str,
        description: str = "",
        icon: str = "",
        workflow_policy_reference: str = "",
        now: str | None = None,
    ) -> "Department":
        timestamp = now or utc_now()
        return cls.from_value({
            "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
            "id": department_id,
            "name": name,
            "description": description,
            "icon": icon,
            "agent_ids": [],
            "workflow_policy_reference": workflow_policy_reference,
            "created_at": timestamp,
            "updated_at": timestamp,
            "enabled": True,
        })

    @classmethod
    def from_value(cls, value: Any) -> "Department":
        value = _mapping(value, "Department")
        schema_version = _schema_version(value.get("schema_version"), "Department")
        raw_agents = value.get("agent_ids", [])
        if not isinstance(raw_agents, list) or any(
            not isinstance(item, str) for item in raw_agents
        ):
            raise ValueError("Department agent_ids must be a list of strings.")
        agent_ids = tuple(normalize_agent_id(item) for item in raw_agents)
        if len(agent_ids) > 256:
            raise ValueError("Department cannot reference more than 256 agents.")
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("Department agent_ids cannot contain duplicates.")
        created_at = parse_timestamp(value.get("created_at"), "Department created_at")
        updated_at = parse_timestamp(value.get("updated_at"), "Department updated_at")
        _validate_time_order(created_at, updated_at)
        result = cls(
            schema_version=schema_version,
            department_id=normalize_id(value.get("id"), "Department ID"),
            name=_text(value.get("name"), "Department name", 120),
            description=_text(
                value.get("description", ""),
                "Department description",
                2_000,
                required=False,
            ),
            icon=_text(
                value.get("icon", ""),
                "Department icon",
                32,
                required=False,
                multiline=False,
            ),
            agent_ids=agent_ids,
            workflow_policy_reference=_text(
                value.get("workflow_policy_reference", ""),
                "Department workflow policy reference",
                200,
                required=False,
                multiline=False,
            ),
            created_at=created_at,
            updated_at=updated_at,
            enabled=_enabled(value.get("enabled"), "Department"),
            extensions=_extensions(value, cls.FIELDS, "Department"),
        )
        validate_safe_value(result.to_dict(), "Department record")
        return result

    def with_agent(self, agent_id: str, timestamp: str) -> "Department":
        normalized = normalize_agent_id(agent_id)
        if normalized in self.agent_ids:
            return self
        return replace(
            self,
            agent_ids=(*self.agent_ids, normalized),
            updated_at=parse_timestamp(timestamp, "Department updated_at"),
        )

    def without_agent(self, agent_id: str, timestamp: str) -> "Department":
        normalized = normalize_agent_id(agent_id)
        if normalized not in self.agent_ids:
            raise ValueError(
                f"Agent {normalized} is not a member of department {self.department_id}."
            )
        return replace(
            self,
            agent_ids=tuple(item for item in self.agent_ids if item != normalized),
            updated_at=parse_timestamp(timestamp, "Department updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.department_id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "agent_ids": list(self.agent_ids),
            "workflow_policy_reference": self.workflow_policy_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "enabled": self.enabled,
            **self.extensions,
        }


@dataclass(frozen=True)
class Job:
    schema_version: int
    job_id: str
    title: str
    goal: str
    status: JobStatus
    priority: JobPriority
    department_id: str
    assigned_agent_ids: tuple[str, ...]
    workspace_reference: str
    created_at: str
    updated_at: str
    started_at: str
    completed_at: str
    created_by: str
    approval_state: ApprovalState
    current_stage: str
    progress: int
    result_summary: str
    error_summary: str
    metadata: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)

    FIELDS = frozenset({
        "schema_version", "id", "title", "goal", "status", "priority",
        "department_id", "assigned_agent_ids", "workspace_reference", "created_at",
        "updated_at", "started_at", "completed_at", "created_by",
        "approval_state", "current_stage", "progress", "result_summary",
        "error_summary", "metadata",
    })

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        title: str,
        goal: str,
        priority: JobPriority | str = JobPriority.NORMAL,
        department_id: str = "",
        assigned_agent_ids: tuple[str, ...] | list[str] = (),
        workspace_reference: str = "",
        created_by: str = "user",
        metadata: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> "Job":
        timestamp = now or utc_now()
        return cls.from_value({
            "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
            "id": job_id,
            "title": title,
            "goal": goal,
            "status": JobStatus.DRAFT.value,
            "priority": JobPriority.parse(priority).value,
            "department_id": department_id,
            "assigned_agent_ids": list(assigned_agent_ids),
            "workspace_reference": workspace_reference,
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": "",
            "completed_at": "",
            "created_by": created_by,
            "approval_state": ApprovalState.NOT_REQUIRED.value,
            "current_stage": "",
            "progress": 0,
            "result_summary": "",
            "error_summary": "",
            "metadata": metadata or {},
        })

    @classmethod
    def from_value(cls, value: Any) -> "Job":
        value = _mapping(value, "Job")
        schema_version = _schema_version(value.get("schema_version"), "Job")
        raw_agents = value.get("assigned_agent_ids", [])
        if not isinstance(raw_agents, list) or any(
            not isinstance(item, str) for item in raw_agents
        ):
            raise ValueError("Job assigned_agent_ids must be a list of strings.")
        agents = tuple(normalize_agent_id(item) for item in raw_agents)
        if len(agents) > 64:
            raise ValueError("Job cannot reference more than 64 assigned agents.")
        if len(set(agents)) != len(agents):
            raise ValueError("Job assigned_agent_ids cannot contain duplicates.")
        progress = value.get("progress")
        if isinstance(progress, bool) or not isinstance(progress, int):
            raise ValueError("Job progress must be an integer.")
        if not 0 <= progress <= 100:
            raise ValueError("Job progress must be between 0 and 100.")
        metadata = _mapping(value.get("metadata", {}), "Job metadata")
        validate_safe_value(metadata, "Job metadata")
        created_at = parse_timestamp(value.get("created_at"), "Job created_at")
        updated_at = parse_timestamp(value.get("updated_at"), "Job updated_at")
        started_at = parse_timestamp(
            value.get("started_at", ""), "Job started_at", optional=True
        )
        completed_at = parse_timestamp(
            value.get("completed_at", ""), "Job completed_at", optional=True
        )
        _validate_time_order(
            created_at, updated_at, completed_at=completed_at
        )
        if started_at:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if started < created:
                raise ValueError("started_at cannot precede created_at.")
        status = JobStatus.parse(value.get("status"))
        if status in TERMINAL_JOB_STATUSES and not completed_at:
            raise ValueError("Terminal jobs require completed_at.")
        if status not in TERMINAL_JOB_STATUSES and completed_at:
            raise ValueError("Non-terminal jobs cannot have completed_at.")
        result_summary = _text(
            value.get("result_summary", ""),
            "Job result summary",
            4_000,
            required=False,
        )
        error_summary = _text(
            value.get("error_summary", ""),
            "Job error summary",
            2_000,
            required=False,
        )
        validate_safe_value(
            {"result_summary": result_summary, "error_summary": error_summary},
            "Job summaries",
        )
        department = value.get("department_id", "")
        department_id = (
            normalize_id(department, "Department ID") if str(department).strip() else ""
        )
        result = cls(
            schema_version=schema_version,
            job_id=normalize_id(value.get("id"), "Job ID"),
            title=_text(value.get("title"), "Job title", 200),
            goal=_text(value.get("goal"), "Job goal", 10_000),
            status=status,
            priority=JobPriority.parse(value.get("priority")),
            department_id=department_id,
            assigned_agent_ids=agents,
            workspace_reference=_text(
                value.get("workspace_reference", ""),
                "Job workspace reference",
                1_000,
                required=False,
                multiline=False,
            ),
            created_at=created_at,
            updated_at=updated_at,
            started_at=started_at,
            completed_at=completed_at,
            created_by=_text(
                value.get("created_by"), "Job created_by", 120, multiline=False
            ),
            approval_state=ApprovalState.parse(value.get("approval_state")),
            current_stage=_text(
                value.get("current_stage", ""),
                "Job current stage",
                200,
                required=False,
                multiline=False,
            ),
            progress=progress,
            result_summary=result_summary,
            error_summary=error_summary,
            metadata=metadata,
            extensions=_extensions(value, cls.FIELDS, "Job"),
        )
        validate_safe_value(result.to_dict(), "Job record", max_serialized_bytes=32_000)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.job_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status.value,
            "priority": self.priority.value,
            "department_id": self.department_id,
            "assigned_agent_ids": list(self.assigned_agent_ids),
            "workspace_reference": self.workspace_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_by": self.created_by,
            "approval_state": self.approval_state.value,
            "current_stage": self.current_stage,
            "progress": self.progress,
            "result_summary": self.result_summary,
            "error_summary": self.error_summary,
            "metadata": dict(self.metadata),
            **self.extensions,
        }


@dataclass(frozen=True)
class ActivityEvent:
    schema_version: int
    event_id: str
    timestamp: str
    event_type: str
    severity: ActivitySeverity
    source_type: ActivitySourceType
    source_id: str
    job_id: str
    department_id: str
    agent_id: str
    message: str
    metadata: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)

    FIELDS = frozenset({
        "schema_version", "id", "timestamp", "event_type", "severity",
        "source_type", "source_id", "job_id", "department_id", "agent_id",
        "message", "metadata",
    })

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        event_type: str,
        source_type: ActivitySourceType | str,
        source_id: str,
        message: str,
        timestamp: str | None = None,
        severity: ActivitySeverity | str = ActivitySeverity.INFO,
        job_id: str = "",
        department_id: str = "",
        agent_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "ActivityEvent":
        return cls.from_value({
            "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
            "id": event_id,
            "timestamp": timestamp or utc_now(),
            "event_type": event_type,
            "severity": ActivitySeverity.parse(severity).value,
            "source_type": ActivitySourceType.parse(source_type).value,
            "source_id": source_id,
            "job_id": job_id,
            "department_id": department_id,
            "agent_id": agent_id,
            "message": message,
            "metadata": metadata or {},
        })

    @classmethod
    def from_value(cls, value: Any) -> "ActivityEvent":
        value = _mapping(value, "Activity event")
        schema_version = _schema_version(
            value.get("schema_version"), "Activity event"
        )
        event_type = _text(
            value.get("event_type"),
            "Activity event_type",
            120,
            multiline=False,
        ).lower()
        if not EVENT_TYPE_PATTERN.fullmatch(event_type):
            raise ValueError(
                "Activity event_type must be a dotted lowercase identifier."
            )
        metadata = _mapping(value.get("metadata", {}), "Activity metadata")
        message = _text(
            value.get("message"), "Activity message", 1_000, multiline=False
        )
        validate_safe_value(
            {"message": message, "metadata": metadata}, "Activity event"
        )
        raw_job_id = str(value.get("job_id", "")).strip()
        raw_department_id = str(value.get("department_id", "")).strip()
        raw_agent_id = str(value.get("agent_id", "")).strip()
        result = cls(
            schema_version=schema_version,
            event_id=normalize_id(value.get("id"), "Activity event ID"),
            timestamp=parse_timestamp(
                value.get("timestamp"), "Activity timestamp"
            ),
            event_type=event_type,
            severity=ActivitySeverity.parse(value.get("severity")),
            source_type=ActivitySourceType.parse(value.get("source_type")),
            source_id=_text(
                value.get("source_id"), "Activity source_id", 200, multiline=False
            ),
            job_id=normalize_id(raw_job_id, "Job ID") if raw_job_id else "",
            department_id=(
                normalize_id(raw_department_id, "Department ID")
                if raw_department_id
                else ""
            ),
            agent_id=normalize_agent_id(raw_agent_id) if raw_agent_id else "",
            message=message,
            metadata=metadata,
            extensions=_extensions(value, cls.FIELDS, "Activity event"),
        )
        validate_safe_value(result.to_dict(), "Activity event")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "severity": self.severity.value,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "job_id": self.job_id,
            "department_id": self.department_id,
            "agent_id": self.agent_id,
            "message": self.message,
            "metadata": dict(self.metadata),
            **self.extensions,
        }
