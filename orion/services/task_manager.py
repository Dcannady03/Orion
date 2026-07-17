"""Strict, project-local work tracking for Orion Task Manager Phase 1."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable
from uuid import uuid4


TASK_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,95}")
EVENT_ID_PATTERN = re.compile(r"event-[a-z0-9-]{6,95}")
ARTIFACT_ID_PATTERN = re.compile(r"artifact-[a-z0-9-]{6,95}")
REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,95}")

TASK_STATUS_PROPOSED = "proposed"
TASK_STATUS_READY = "ready"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"
TASK_STATUSES = frozenset({
    TASK_STATUS_PROPOSED,
    TASK_STATUS_READY,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CANCELLED,
})
TERMINAL_TASK_STATUSES = frozenset({
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CANCELLED,
})

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_CANCELLED = "cancelled"
APPROVAL_STATES = frozenset({APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_CANCELLED})

EVENT_TYPES = frozenset({"created", "approved", "cancelled", "team_plan_linked"})
ARTIFACT_KINDS = frozenset({"ai_team_plan"})


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    keys = set(value)
    missing = sorted(fields - keys)
    unknown = sorted(keys - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _required_string(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _optional_string(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _optional_identity(value: Any, label: str) -> str:
    normalized = _optional_string(value, label, maximum=100).lower()
    if normalized and not REFERENCE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} has an invalid format.")
    return normalized


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = _required_string(value, label, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _task_id(value: Any, label: str = "Task ID") -> str:
    normalized = _required_string(value, label, maximum=96).lower()
    if not TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} has an invalid format.")
    return normalized


def _reference(value: Any, label: str) -> str:
    normalized = _required_string(value, label, maximum=96)
    if not REFERENCE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} has an invalid format.")
    return normalized


@dataclass(frozen=True)
class TaskArtifact:
    artifact_id: str
    kind: str
    reference: str
    summary: str
    created_at: str

    @classmethod
    def from_value(cls, value: Any) -> "TaskArtifact":
        value = _exact_mapping(
            value,
            {"artifact_id", "kind", "reference", "summary", "created_at"},
            "Task artifact",
        )
        artifact_id = _required_string(
            value["artifact_id"], "Task artifact ID", maximum=105
        ).lower()
        if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise ValueError("Task artifact ID has an invalid format.")
        kind = _required_string(value["kind"], "Task artifact kind", maximum=64).lower()
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"Task artifact kind is not supported: {kind}")
        created_at, _ = _timestamp(value["created_at"], "Task artifact created_at")
        return cls(
            artifact_id=artifact_id,
            kind=kind,
            reference=_reference(value["reference"], "Task artifact reference"),
            summary=_required_string(value["summary"], "Task artifact summary", maximum=500),
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "reference": self.reference,
            "summary": self.summary,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ProjectTask:
    task_id: str
    goal: str
    status: str
    approval: str
    assigned_role: str
    assigned_agent: str
    dependencies: tuple[str, ...]
    artifacts: tuple[TaskArtifact, ...]
    created_at: str
    updated_at: str

    @classmethod
    def from_value(cls, value: Any) -> "ProjectTask":
        value = _exact_mapping(
            value,
            {
                "task_id", "goal", "status", "approval", "assigned_role",
                "assigned_agent", "dependencies", "artifacts", "created_at",
                "updated_at",
            },
            "Project task",
        )
        task_id = _task_id(value["task_id"])
        goal = _required_string(value["goal"], "Task goal", maximum=4_000)
        status = _required_string(value["status"], "Task status", maximum=32).lower()
        if status not in TASK_STATUSES:
            raise ValueError(f"Task status is not recognized: {status}")
        approval = _required_string(
            value["approval"], "Task approval state", maximum=32
        ).lower()
        if approval not in APPROVAL_STATES:
            raise ValueError(f"Task approval state is not recognized: {approval}")
        if status == TASK_STATUS_PROPOSED and approval != APPROVAL_PENDING:
            raise ValueError("Proposed tasks require pending approval.")
        if status == TASK_STATUS_CANCELLED and approval != APPROVAL_CANCELLED:
            raise ValueError("Cancelled tasks require cancelled approval.")
        if status not in {TASK_STATUS_PROPOSED, TASK_STATUS_CANCELLED} and approval != APPROVAL_APPROVED:
            raise ValueError(f"Task status {status} requires approved approval state.")
        dependencies = value["dependencies"]
        if not isinstance(dependencies, list):
            raise ValueError("Task dependencies must be a JSON array.")
        normalized_dependencies = tuple(
            _task_id(item, "Task dependency") for item in dependencies
        )
        if len(set(normalized_dependencies)) != len(normalized_dependencies):
            raise ValueError("Task dependencies cannot contain duplicates.")
        if task_id in normalized_dependencies:
            raise ValueError("A task cannot depend on itself.")
        artifacts = value["artifacts"]
        if not isinstance(artifacts, list):
            raise ValueError("Task artifacts must be a JSON array.")
        normalized_artifacts = tuple(TaskArtifact.from_value(item) for item in artifacts)
        artifact_ids = [item.artifact_id for item in normalized_artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("Task artifact IDs must be unique.")
        artifact_references = [(item.kind, item.reference) for item in normalized_artifacts]
        if len(set(artifact_references)) != len(artifact_references):
            raise ValueError("Task artifact references must be unique by kind.")
        created_at, created = _timestamp(value["created_at"], "Task created_at")
        updated_at, updated = _timestamp(value["updated_at"], "Task updated_at")
        if updated < created:
            raise ValueError("Task updated_at cannot precede created_at.")
        return cls(
            task_id=task_id,
            goal=goal,
            status=status,
            approval=approval,
            assigned_role=_optional_identity(value["assigned_role"], "Task assigned role"),
            assigned_agent=_optional_identity(value["assigned_agent"], "Task assigned agent"),
            dependencies=normalized_dependencies,
            artifacts=normalized_artifacts,
            created_at=created_at,
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "approval": self.approval,
            "assigned_role": self.assigned_role,
            "assigned_agent": self.assigned_agent,
            "dependencies": list(self.dependencies),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    event_type: str
    actor: str
    previous_status: str
    status: str
    detail: str
    timestamp: str

    @classmethod
    def from_value(cls, value: Any) -> "TaskEvent":
        value = _exact_mapping(
            value,
            {
                "event_id", "task_id", "event_type", "actor", "previous_status",
                "status", "detail", "timestamp",
            },
            "Task event",
        )
        event_id = _required_string(value["event_id"], "Task event ID", maximum=105).lower()
        if not EVENT_ID_PATTERN.fullmatch(event_id):
            raise ValueError("Task event ID has an invalid format.")
        event_type = _required_string(
            value["event_type"], "Task event type", maximum=64
        ).lower()
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Task event type is not recognized: {event_type}")
        previous_status = _optional_string(
            value["previous_status"], "Task event previous status", maximum=32
        ).lower()
        if previous_status and previous_status not in TASK_STATUSES:
            raise ValueError("Task event previous status is not recognized.")
        status = _required_string(value["status"], "Task event status", maximum=32).lower()
        if status not in TASK_STATUSES:
            raise ValueError("Task event status is not recognized.")
        if event_type == "created" and (
            previous_status or status != TASK_STATUS_PROPOSED
        ):
            raise ValueError("Created events must enter proposed status from no prior status.")
        if event_type == "approved" and (
            previous_status != TASK_STATUS_PROPOSED or status != TASK_STATUS_READY
        ):
            raise ValueError("Approved events must transition proposed tasks to ready.")
        if event_type == "cancelled" and (
            not previous_status
            or previous_status in TERMINAL_TASK_STATUSES
            or status != TASK_STATUS_CANCELLED
        ):
            raise ValueError("Cancelled events require a non-terminal prior status.")
        if event_type == "team_plan_linked" and (
            previous_status != status or status in TERMINAL_TASK_STATUSES
        ):
            raise ValueError("Team-plan events cannot change or target terminal task status.")
        timestamp, _ = _timestamp(value["timestamp"], "Task event timestamp")
        return cls(
            event_id=event_id,
            task_id=_task_id(value["task_id"]),
            event_type=event_type,
            actor=_required_string(value["actor"], "Task event actor", maximum=100),
            previous_status=previous_status,
            status=status,
            detail=_required_string(value["detail"], "Task event detail", maximum=1_000),
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "previous_status": self.previous_status,
            "status": self.status,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


class TaskManager:
    """Own project tasks and their append-only progress event stream."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        artifact_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._lock = RLock()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or self._new_task_id
        self._event_id_factory = event_id_factory or self._new_event_id
        self._artifact_id_factory = artifact_id_factory or self._new_artifact_id
        self.bind(workspace_root)

    def bind(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        with self._lock:
            self._root = root
            self._context_dir = root / ".orion"
            self._tasks_path = self._context_dir / "tasks.json"
            self._events_path = self._context_dir / "task-events.jsonl"

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def tasks_path(self) -> Path:
        return self._tasks_path

    @property
    def events_path(self) -> Path:
        return self._events_path

    def create(
        self,
        goal: str,
        *,
        assigned_role: str = "",
        assigned_agent: str = "",
        dependencies: Iterable[str] = (),
        actor: str = "user",
    ) -> ProjectTask:
        self._require_initialized()
        normalized_goal = " ".join(str(goal).split()).strip()
        normalized_goal = _required_string(normalized_goal, "Task goal", maximum=4_000)
        task_id = _task_id(self._id_factory())
        normalized_dependencies = tuple(
            _task_id(item, "Task dependency") for item in dependencies
        )
        timestamp = self._timestamp()
        task = ProjectTask.from_value({
            "task_id": task_id,
            "goal": normalized_goal,
            "status": TASK_STATUS_PROPOSED,
            "approval": APPROVAL_PENDING,
            "assigned_role": assigned_role,
            "assigned_agent": assigned_agent,
            "dependencies": list(normalized_dependencies),
            "artifacts": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        })
        event = TaskEvent.from_value({
            "event_id": self._event_id_factory(),
            "task_id": task.task_id,
            "event_type": "created",
            "actor": actor,
            "previous_status": "",
            "status": task.status,
            "detail": task.goal[:1_000],
            "timestamp": timestamp,
        })
        with self._lock:
            tasks = self._read_tasks()
            if any(item.task_id == task.task_id for item in tasks):
                raise ValueError(f"Task ID already exists: {task.task_id}")
            known_ids = {item.task_id for item in tasks}
            missing = sorted(set(task.dependencies) - known_ids)
            if missing:
                raise ValueError(f"Task dependencies do not exist: {missing}")
            self._validate_new_event(event)
            tasks.append(task)
            self._write_tasks(tasks)
            self._append_event(event)
        return task

    def all(self, *, include_cancelled: bool = True) -> tuple[ProjectTask, ...]:
        self._require_initialized()
        with self._lock:
            tasks = self._read_tasks()
        if not include_cancelled:
            tasks = [item for item in tasks if item.status != TASK_STATUS_CANCELLED]
        return tuple(tasks)

    def get(self, task_id: str) -> ProjectTask:
        normalized_id = _task_id(task_id)
        for task in self.all():
            if task.task_id == normalized_id:
                return task
        raise FileNotFoundError(f"Task not found: {normalized_id}")

    def approve(self, task_id: str, *, actor: str = "user") -> ProjectTask:
        return self._transition(
            task_id,
            required_status=TASK_STATUS_PROPOSED,
            status=TASK_STATUS_READY,
            approval=APPROVAL_APPROVED,
            event_type="approved",
            detail="Task approved; no implementation has started.",
            actor=actor,
        )

    def cancel(self, task_id: str, *, actor: str = "user") -> ProjectTask:
        normalized_id = _task_id(task_id)
        with self._lock:
            tasks = self._read_tasks()
            index = self._find_index(tasks, normalized_id)
            current = tasks[index]
            if current.status in TERMINAL_TASK_STATUSES:
                raise ValueError(f"Task is already terminal: {current.status}")
            timestamp = self._timestamp()
            updated = replace(
                current,
                status=TASK_STATUS_CANCELLED,
                approval=APPROVAL_CANCELLED,
                updated_at=timestamp,
            )
            ProjectTask.from_value(updated.to_dict())
            event = self._event(
                updated,
                "cancelled",
                actor,
                current.status,
                "Task cancelled; no implementation was performed.",
                timestamp,
            )
            self._validate_new_event(event)
            tasks[index] = updated
            self._write_tasks(tasks)
            self._append_event(event)
            return updated

    def link_team_plan(
        self,
        task_id: str,
        team_task_id: str,
        *,
        summary: str,
        actor: str = "user",
    ) -> ProjectTask:
        normalized_id = _task_id(task_id)
        reference = _reference(team_task_id, "AI Team task ID")
        normalized_summary = _required_string(summary, "AI Team plan summary", maximum=500)
        with self._lock:
            tasks = self._read_tasks()
            index = self._find_index(tasks, normalized_id)
            current = tasks[index]
            if current.status in TERMINAL_TASK_STATUSES:
                raise ValueError("Cannot link a plan to a terminal task.")
            if any(
                item.kind == "ai_team_plan" and item.reference == reference
                for item in current.artifacts
            ):
                raise ValueError(f"AI Team plan is already linked: {reference}")
            timestamp = self._timestamp()
            artifact = TaskArtifact.from_value({
                "artifact_id": self._artifact_id_factory(),
                "kind": "ai_team_plan",
                "reference": reference,
                "summary": normalized_summary,
                "created_at": timestamp,
            })
            updated = replace(
                current,
                artifacts=current.artifacts + (artifact,),
                updated_at=timestamp,
            )
            ProjectTask.from_value(updated.to_dict())
            event = self._event(
                updated,
                "team_plan_linked",
                actor,
                current.status,
                f"Linked AI Team plan {reference}.",
                timestamp,
            )
            self._validate_new_event(event)
            tasks[index] = updated
            self._write_tasks(tasks)
            self._append_event(event)
            return updated

    def events(self, task_id: str | None = None, *, limit: int | None = None) -> tuple[TaskEvent, ...]:
        self._require_initialized()
        normalized_id = None
        if task_id is not None:
            normalized_id = self.get(task_id).task_id
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("Task event limit must be a positive integer.")
        with self._lock:
            events = self._read_events(normalized_id)
        if limit is not None:
            events = events[-limit:]
        return tuple(events)

    def _transition(
        self,
        task_id: str,
        *,
        required_status: str,
        status: str,
        approval: str,
        event_type: str,
        detail: str,
        actor: str,
    ) -> ProjectTask:
        normalized_id = _task_id(task_id)
        with self._lock:
            tasks = self._read_tasks()
            index = self._find_index(tasks, normalized_id)
            current = tasks[index]
            if current.status != required_status:
                raise ValueError(
                    f"Task must be {required_status} before this transition; "
                    f"current status is {current.status}."
                )
            timestamp = self._timestamp()
            updated = replace(
                current,
                status=status,
                approval=approval,
                updated_at=timestamp,
            )
            ProjectTask.from_value(updated.to_dict())
            event = self._event(
                updated, event_type, actor, current.status, detail, timestamp
            )
            self._validate_new_event(event)
            tasks[index] = updated
            self._write_tasks(tasks)
            self._append_event(event)
            return updated

    def _read_tasks(self) -> list[ProjectTask]:
        if not self._tasks_path.exists():
            return []
        try:
            value = json.loads(self._tasks_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Project tasks could not be read as valid JSON.") from exc
        if not isinstance(value, list):
            raise ValueError("tasks.json must contain a JSON array.")
        try:
            tasks = [ProjectTask.from_value(item) for item in value]
        except (TypeError, ValueError) as exc:
            raise ValueError("tasks.json contains an invalid project task.") from exc
        ids = [item.task_id for item in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("tasks.json contains duplicate task IDs.")
        known_ids = set(ids)
        for task in tasks:
            missing = sorted(set(task.dependencies) - known_ids)
            if missing:
                raise ValueError(
                    f"Task {task.task_id} has missing dependencies: {missing}"
                )
        self._validate_dependency_graph(tasks)
        return tasks

    def _write_tasks(self, tasks: list[ProjectTask]) -> None:
        payload = [ProjectTask.from_value(item.to_dict()).to_dict() for item in tasks]
        ids = [item["task_id"] for item in payload]
        if len(set(ids)) != len(ids):
            raise ValueError("Task IDs must be unique.")
        self._context_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._tasks_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._tasks_path)

    def _append_event(self, event: TaskEvent) -> None:
        TaskEvent.from_value(event.to_dict())
        self._context_dir.mkdir(parents=True, exist_ok=True)
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def _read_events(self, task_id: str | None = None) -> list[TaskEvent]:
        if not self._events_path.exists():
            return []
        events: list[TaskEvent] = []
        seen_ids: set[str] = set()
        known_task_ids = {task.task_id for task in self._read_tasks()}
        last_status: dict[str, str] = {}
        last_timestamp: dict[str, datetime] = {}
        try:
            lines = self._events_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ValueError("Task event history could not be read.") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = TaskEvent.from_value(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"Task event history is invalid at line {line_number}.") from exc
            if event.event_id in seen_ids:
                raise ValueError(f"Task event ID is duplicated: {event.event_id}")
            if event.task_id not in known_task_ids:
                raise ValueError(
                    f"Task event references an unknown task: {event.task_id}"
                )
            _, parsed_timestamp = _timestamp(event.timestamp, "Task event timestamp")
            if event.task_id not in last_status:
                if event.event_type != "created":
                    raise ValueError(
                        f"Task event history does not begin with creation: {event.task_id}"
                    )
            else:
                if event.event_type == "created":
                    raise ValueError(
                        f"Task event history repeats creation: {event.task_id}"
                    )
                if event.previous_status != last_status[event.task_id]:
                    raise ValueError(
                        f"Task event history has a broken status chain: {event.task_id}"
                    )
                if parsed_timestamp < last_timestamp[event.task_id]:
                    raise ValueError(
                        f"Task event history has reversed timestamps: {event.task_id}"
                    )
            last_status[event.task_id] = event.status
            last_timestamp[event.task_id] = parsed_timestamp
            seen_ids.add(event.event_id)
            if task_id is None or event.task_id == task_id:
                events.append(event)
        return events

    def _validate_new_event(self, event: TaskEvent) -> None:
        if any(item.event_id == event.event_id for item in self._read_events()):
            raise ValueError(f"Task event ID already exists: {event.event_id}")

    @staticmethod
    def _validate_dependency_graph(tasks: list[ProjectTask]) -> None:
        dependencies = {task.task_id: task.dependencies for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("Task dependencies contain a cycle.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)

    def _event(
        self,
        task: ProjectTask,
        event_type: str,
        actor: str,
        previous_status: str,
        detail: str,
        timestamp: str,
    ) -> TaskEvent:
        return TaskEvent.from_value({
            "event_id": self._event_id_factory(),
            "task_id": task.task_id,
            "event_type": event_type,
            "actor": actor,
            "previous_status": previous_status,
            "status": task.status,
            "detail": detail,
            "timestamp": timestamp,
        })

    @staticmethod
    def _find_index(tasks: list[ProjectTask], task_id: str) -> int:
        for index, task in enumerate(tasks):
            if task.task_id == task_id:
                return index
        raise FileNotFoundError(f"Task not found: {task_id}")

    def _require_initialized(self) -> None:
        if not (self._context_dir / "project.json").is_file():
            raise FileNotFoundError("Project context is not initialized. Run 'project init'.")

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")

    def _new_task_id(self) -> str:
        stamp = self._now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()
        return f"task-{stamp}-{uuid4().hex[:6]}"

    @staticmethod
    def _new_event_id() -> str:
        return f"event-{uuid4().hex}"

    @staticmethod
    def _new_artifact_id() -> str:
        return f"artifact-{uuid4().hex}"
