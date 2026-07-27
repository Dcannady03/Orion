"""Storage abstractions and file persistence for Orion Command Center."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

import yaml

from orion.command_center.models import (
    ActivityEvent,
    Department,
    Job,
    Organization,
    normalize_id,
)


MAX_RECORD_FILE_BYTES = 512_000
MAX_ACTIVITY_FILE_BYTES = 20_000_000
MAX_ACTIVITY_READ_LIMIT = 1_000


@dataclass(frozen=True)
class RepositoryDiagnostic:
    severity: str
    code: str
    message: str
    record: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "record": self.record,
        }


class CommandCenterRepository(Protocol):
    """Persistence contract consumed by CommandCenterService."""

    root: Path

    def save_organization(self, organization: Organization) -> Path: ...
    def load_organization(self) -> Organization: ...
    def save_department(
        self, department: Department, *, overwrite: bool = False
    ) -> Path: ...
    def load_department(self, department_id: str) -> Department: ...
    def list_departments(self) -> tuple[Department, ...]: ...
    def save_job(self, job: Job, *, overwrite: bool = False) -> Path: ...
    def load_job(self, job_id: str) -> Job: ...
    def list_jobs(self) -> tuple[Job, ...]: ...
    def append_activity(self, event: ActivityEvent) -> Path: ...
    def list_activity(self, limit: int = 50) -> tuple[ActivityEvent, ...]: ...
    def diagnostics(self) -> tuple[RepositoryDiagnostic, ...]: ...


class FileCommandCenterRepository:
    """Versioned YAML records plus append-only JSON Lines activity."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.organization_path = self.root / "organization.yaml"
        self.departments_root = self.root / "departments"
        self.jobs_root = self.root / "jobs"
        self.activity_path = self.root / "activity.jsonl"
        self._lock = RLock()

    def save_organization(self, organization: Organization) -> Path:
        validated = Organization.from_value(organization.to_dict())
        with self._lock:
            self._atomic_yaml(self.organization_path, validated.to_dict())
        return self.organization_path

    def load_organization(self) -> Organization:
        value = self._read_yaml(self.organization_path, "Organization")
        return Organization.from_value(value)

    def save_department(
        self,
        department: Department,
        *,
        overwrite: bool = False,
    ) -> Path:
        validated = Department.from_value(department.to_dict())
        path = self._record_path(
            self.departments_root,
            validated.department_id,
            "Department ID",
        )
        with self._lock:
            if path.exists() and not overwrite:
                raise FileExistsError(
                    f"Department already exists: {validated.department_id}"
                )
            self._atomic_yaml(path, validated.to_dict())
        return path

    def load_department(self, department_id: str) -> Department:
        normalized = normalize_id(department_id, "Department ID")
        path = self._record_path(self.departments_root, normalized, "Department ID")
        value = self._read_yaml(path, "Department")
        result = Department.from_value(value)
        if result.department_id != normalized:
            raise ValueError("Department ID does not match its filename.")
        return result

    def list_departments(self) -> tuple[Department, ...]:
        if not self.departments_root.exists():
            return ()
        self._validate_directory(self.departments_root)
        results = [
            self.load_department(path.stem)
            for path in sorted(
                self.departments_root.glob("*.yaml"),
                key=lambda item: item.name.casefold(),
            )
        ]
        return tuple(sorted(results, key=lambda item: item.department_id))

    def save_job(self, job: Job, *, overwrite: bool = False) -> Path:
        validated = Job.from_value(job.to_dict())
        path = self._record_path(self.jobs_root, validated.job_id, "Job ID")
        with self._lock:
            if path.exists() and not overwrite:
                raise FileExistsError(f"Job already exists: {validated.job_id}")
            self._atomic_yaml(path, validated.to_dict())
        return path

    def load_job(self, job_id: str) -> Job:
        normalized = normalize_id(job_id, "Job ID")
        path = self._record_path(self.jobs_root, normalized, "Job ID")
        value = self._read_yaml(path, "Job")
        result = Job.from_value(value)
        if result.job_id != normalized:
            raise ValueError("Job ID does not match its filename.")
        return result

    def list_jobs(self) -> tuple[Job, ...]:
        if not self.jobs_root.exists():
            return ()
        self._validate_directory(self.jobs_root)
        results = [
            self.load_job(path.stem)
            for path in sorted(
                self.jobs_root.glob("*.yaml"),
                key=lambda item: item.name.casefold(),
            )
        ]
        return tuple(sorted(results, key=lambda item: item.job_id))

    def append_activity(self, event: ActivityEvent) -> Path:
        validated = ActivityEvent.from_value(event.to_dict())
        with self._lock:
            prior = self._read_activity_all()
            if any(item.event_id == validated.event_id for item in prior):
                raise FileExistsError(
                    f"Activity event already exists: {validated.event_id}"
                )
            if prior:
                previous = datetime.fromisoformat(
                    prior[-1].timestamp.replace("Z", "+00:00")
                )
                current = datetime.fromisoformat(
                    validated.timestamp.replace("Z", "+00:00")
                )
                if current < previous:
                    raise ValueError(
                        "Activity events cannot be appended with a reversed timestamp."
                    )
            self._ensure_directory(self.root)
            self._validate_existing_file(self.activity_path, optional=True)
            line = json.dumps(
                validated.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with self.activity_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict_permissions(self.activity_path)
        return self.activity_path

    def list_activity(self, limit: int = 50) -> tuple[ActivityEvent, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Activity limit must be an integer.")
        if not 1 <= limit <= MAX_ACTIVITY_READ_LIMIT:
            raise ValueError(
                f"Activity limit must be between 1 and {MAX_ACTIVITY_READ_LIMIT}."
            )
        with self._lock:
            events = self._read_activity_all()
        return tuple(events[-limit:])

    def diagnostics(self) -> tuple[RepositoryDiagnostic, ...]:
        """Inspect storage without creating, rewriting, or deleting any record."""
        issues: list[RepositoryDiagnostic] = []
        issues.extend(self._writability_diagnostics())

        if not self.organization_path.exists():
            issues.append(RepositoryDiagnostic(
                "error",
                "organization.missing",
                "Default organization record is missing.",
                "organization.yaml",
            ))
        else:
            try:
                self.load_organization()
            except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
                issues.append(self._invalid_record("organization", exc))

        department_ids: list[str] = []
        if self.departments_root.exists():
            try:
                self._validate_directory(self.departments_root)
                department_files = sorted(self.departments_root.glob("*.yaml"))
            except (OSError, PermissionError, ValueError) as exc:
                issues.append(RepositoryDiagnostic(
                    "error",
                    "department.storage_invalid",
                    self._safe_error(exc, "Department storage is invalid."),
                    "departments/",
                ))
                department_files = []
            for path in department_files:
                try:
                    value = self._read_yaml(path, "Department")
                    record = Department.from_value(value)
                    department_ids.append(record.department_id)
                    expected = normalize_id(path.stem, "Department ID")
                    if record.department_id != expected:
                        raise ValueError(
                            "Department ID does not match its filename."
                        )
                except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
                    issues.append(
                        self._invalid_record(
                            "department",
                            exc,
                            f"departments/{path.name}",
                        )
                    )
        issues.extend(self._duplicate_diagnostics("department", department_ids))

        job_ids: list[str] = []
        if self.jobs_root.exists():
            try:
                self._validate_directory(self.jobs_root)
                job_files = sorted(self.jobs_root.glob("*.yaml"))
            except (OSError, PermissionError, ValueError) as exc:
                issues.append(RepositoryDiagnostic(
                    "error",
                    "job.storage_invalid",
                    self._safe_error(exc, "Job storage is invalid."),
                    "jobs/",
                ))
                job_files = []
            for path in job_files:
                try:
                    value = self._read_yaml(path, "Job")
                    record = Job.from_value(value)
                    job_ids.append(record.job_id)
                    expected = normalize_id(path.stem, "Job ID")
                    if record.job_id != expected:
                        raise ValueError("Job ID does not match its filename.")
                except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
                    issues.append(
                        self._invalid_record("job", exc, f"jobs/{path.name}")
                    )
        issues.extend(self._duplicate_diagnostics("job", job_ids))

        if self.activity_path.exists():
            try:
                self._read_activity_all()
            except (OSError, PermissionError, ValueError) as exc:
                issues.append(self._invalid_record(
                    "activity", exc, "activity.jsonl"
                ))
        return tuple(sorted(
            issues,
            key=lambda item: (
                {"error": 0, "warning": 1, "info": 2}.get(item.severity, 3),
                item.code,
                item.record,
            ),
        ))

    def _read_yaml(self, path: Path, label: str) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"{label} record not found.")
        self._validate_existing_file(path)
        if path.stat().st_size > MAX_RECORD_FILE_BYTES:
            raise ValueError(f"{label} record exceeds the size limit.")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"{label} record is malformed YAML.") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} record must contain a YAML mapping.")
        return value

    def _read_activity_all(self) -> list[ActivityEvent]:
        if not self.activity_path.exists():
            return []
        self._validate_existing_file(self.activity_path)
        if self.activity_path.stat().st_size > MAX_ACTIVITY_FILE_BYTES:
            raise ValueError("Activity history exceeds the supported size limit.")
        try:
            lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ValueError("Activity history could not be read.") from exc
        events: list[ActivityEvent] = []
        seen: set[str] = set()
        previous: datetime | None = None
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = ActivityEvent.from_value(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Activity history is malformed at line {line_number}."
                ) from exc
            if event.event_id in seen:
                raise ValueError(
                    f"Activity event ID is duplicated at line {line_number}."
                )
            timestamp = datetime.fromisoformat(
                event.timestamp.replace("Z", "+00:00")
            )
            if previous is not None and timestamp < previous:
                raise ValueError(
                    f"Activity timestamps are reversed at line {line_number}."
                )
            previous = timestamp
            seen.add(event.event_id)
            events.append(event)
        return events

    def _atomic_yaml(self, path: Path, value: dict[str, Any]) -> None:
        self._ensure_directory(path.parent)
        self._validate_existing_file(path, optional=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(
                    value,
                    handle,
                    sort_keys=False,
                    allow_unicode=True,
                    default_flow_style=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict_permissions(temporary)
            os.replace(temporary, path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def _record_path(self, root: Path, record_id: str, label: str) -> Path:
        normalized = normalize_id(record_id, label)
        return root / f"{normalized}.yaml"

    def _ensure_directory(self, path: Path) -> None:
        self._validate_root()
        path.mkdir(parents=True, exist_ok=True)
        self._validate_directory(path)

    def _validate_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise PermissionError("Command Center storage root cannot be a symlink.")

    @staticmethod
    def _validate_directory(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Command Center storage directories cannot be symlinks.")
        if path.exists() and not path.is_dir():
            raise NotADirectoryError("Command Center storage path is not a directory.")

    @staticmethod
    def _validate_existing_file(path: Path, *, optional: bool = False) -> None:
        if not path.exists():
            if optional:
                return
            raise FileNotFoundError("Command Center record not found.")
        if path.is_symlink():
            raise PermissionError("Command Center record symlinks are not allowed.")
        if not path.is_file():
            raise ValueError("Command Center record path is not a file.")

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _writability_diagnostics(self) -> list[RepositoryDiagnostic]:
        target = self.root
        while not target.exists() and target != target.parent:
            target = target.parent
        if not target.exists():
            return [RepositoryDiagnostic(
                "error",
                "storage.parent_missing",
                "No existing parent directory is available for Command Center storage.",
            )]
        if target.is_symlink():
            return [RepositoryDiagnostic(
                "error",
                "storage.symlink",
                "Command Center storage or its nearest parent is a symlink.",
            )]
        if not target.is_dir():
            return [RepositoryDiagnostic(
                "error",
                "storage.not_directory",
                "Command Center storage or its nearest parent is not a directory.",
            )]
        if not os.access(target, os.R_OK):
            return [RepositoryDiagnostic(
                "error",
                "storage.unreadable",
                "Command Center storage is not readable.",
            )]
        if not os.access(target, os.W_OK):
            return [RepositoryDiagnostic(
                "error",
                "storage.unwritable",
                "Command Center storage is not writable.",
            )]
        return []

    @staticmethod
    def _duplicate_diagnostics(
        kind: str,
        record_ids: list[str],
    ) -> list[RepositoryDiagnostic]:
        duplicates = sorted({
            record_id for record_id in record_ids if record_ids.count(record_id) > 1
        })
        return [
            RepositoryDiagnostic(
                "error",
                f"{kind}.duplicate_id",
                f"Duplicate {kind} ID: {record_id}",
            )
            for record_id in duplicates
        ]

    @classmethod
    def _invalid_record(
        cls,
        kind: str,
        error: Exception,
        record: str = "",
    ) -> RepositoryDiagnostic:
        text = str(error)
        suffix = "unsupported_schema" if "Unsupported" in text and "schema" in text else "invalid"
        return RepositoryDiagnostic(
            "error",
            f"{kind}.{suffix}",
            cls._safe_error(error, f"{kind.title()} record is invalid."),
            record,
        )

    @staticmethod
    def _safe_error(error: Exception, fallback: str) -> str:
        text = str(error).strip()
        if not text:
            return fallback
        return text[:500]
