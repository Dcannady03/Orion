"""Approval-bound local Codex execution for Orion Codex Bridge Phase 1."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from orion.services.team import (
    TASK_ID_PATTERN as TEAM_TASK_ID_PATTERN,
    TEAM_STATUS_AWAITING_APPROVAL,
    TeamArtifact,
    TeamTask,
    TeamTaskStore,
)
from orion.services.execution_engines import ExecutionEngineUnavailable


BRIDGE_SCHEMA_VERSION = 1
APPROVAL_ID_PATTERN = re.compile(r"approval-[a-z0-9-]{6,95}")
RUN_ID_PATTERN = re.compile(r"run-[a-z0-9-]{6,95}")
PLAN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")

RUN_STATUS_EXECUTING = "executing"
RUN_STATUS_AWAITING_REVIEW = "awaiting_review"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = frozenset({
    RUN_STATUS_EXECUTING,
    RUN_STATUS_AWAITING_REVIEW,
    RUN_STATUS_FAILED,
})
TEST_STATUSES = frozenset({"passed", "failed", "not_run"})
RUN_ERROR_CATEGORIES = frozenset({
    "codex_cli_unavailable",
    "codex_timeout",
    "codex_process_failed",
    "codex_output_too_large",
    "invalid_codex_output",
})
RUN_ARTIFACT_NAMES = frozenset({
    "approved-plan.json",
    "result-schema.json",
    "events.jsonl",
    "implementation-result.json",
})
PROTECTED_WORKSPACE_PARTS = frozenset({".git", ".agents", ".codex"})


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


def _bounded_strings(
    value: Any,
    label: str,
    *,
    maximum_items: int = 200,
    maximum_length: int = 2_000,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array.")
    if len(value) > maximum_items:
        raise ValueError(f"{label} cannot contain more than {maximum_items} items.")
    items = tuple(
        _required_string(item, f"{label} item", maximum=maximum_length)
        for item in value
    )
    if not allow_empty and not items:
        raise ValueError(f"{label} cannot be empty.")
    return items


def _timestamp(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, datetime | None]:
    if allow_empty and value == "":
        return "", None
    text = _required_string(value, label, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _team_task_id(value: Any) -> str:
    normalized = _required_string(value, "AI Team task ID", maximum=81)
    if not TEAM_TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError("AI Team task ID has an invalid format.")
    return normalized


def _approval_id(value: Any) -> str:
    normalized = _required_string(value, "Plan approval ID", maximum=105).lower()
    if not APPROVAL_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Plan approval ID has an invalid format.")
    return normalized


def _run_id(value: Any) -> str:
    normalized = _required_string(value, "Codex run ID", maximum=100).lower()
    if not RUN_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Codex run ID has an invalid format.")
    return normalized


def _plan_hash(value: Any) -> str:
    normalized = _required_string(value, "Plan hash", maximum=64).lower()
    if not PLAN_HASH_PATTERN.fullmatch(normalized):
        raise ValueError("Plan hash must be a SHA-256 hex digest.")
    return normalized


def _strict_json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"Non-finite JSON number is not supported: {constant}")

    return json.loads(value, parse_constant=reject_constant)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _same_path(first: str | Path, second: str | Path) -> bool:
    first_value = os.path.normcase(str(Path(first).expanduser().resolve()))
    second_value = os.path.normcase(str(Path(second).expanduser().resolve()))
    return first_value == second_value


def _owner_only(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _artifact_to_dict(artifact: TeamArtifact) -> dict[str, Any]:
    return {
        "role": artifact.role,
        "kind": artifact.kind,
        "output": artifact.output.to_dict(),
        "created_at": artifact.created_at,
    }


@dataclass(frozen=True)
class PlanSnapshot:
    """Approval-relevant fields from a persisted AI Team plan."""

    schema_version: int
    team_task_id: str
    goal: str
    final_plan: tuple[str, ...]
    artifacts: tuple[TeamArtifact, ...]

    @classmethod
    def from_team_task(cls, task: TeamTask) -> "PlanSnapshot":
        validated = TeamTask.from_dict(task.to_dict())
        if validated.status != TEAM_STATUS_AWAITING_APPROVAL:
            raise ValueError("Only an AI Team plan awaiting approval can be approved.")
        return cls.from_value({
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "team_task_id": validated.task_id,
            "goal": validated.goal,
            "final_plan": list(validated.final_plan),
            "artifacts": [_artifact_to_dict(item) for item in validated.artifacts],
        })

    @classmethod
    def from_value(cls, value: Any) -> "PlanSnapshot":
        value = _exact_mapping(
            value,
            {"schema_version", "team_task_id", "goal", "final_plan", "artifacts"},
            "Approved plan snapshot",
        )
        if value["schema_version"] != BRIDGE_SCHEMA_VERSION:
            raise ValueError("Approved plan snapshot schema version is not supported.")
        artifacts_value = value["artifacts"]
        if not isinstance(artifacts_value, list):
            raise ValueError("Approved plan artifacts must be a JSON array.")
        if len(artifacts_value) > 20:
            raise ValueError("Approved plan cannot contain more than 20 artifacts.")
        artifacts = tuple(TeamArtifact.from_value(item) for item in artifacts_value)
        final_plan = _bounded_strings(
            value["final_plan"],
            "Approved final plan",
            maximum_items=100,
            maximum_length=4_000,
            allow_empty=False,
        )
        return cls(
            schema_version=BRIDGE_SCHEMA_VERSION,
            team_task_id=_team_task_id(value["team_task_id"]),
            goal=_required_string(value["goal"], "Approved plan goal", maximum=4_000),
            final_plan=final_plan,
            artifacts=artifacts,
        )

    @property
    def hash(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "team_task_id": self.team_task_id,
            "goal": self.goal,
            "final_plan": list(self.final_plan),
            "artifacts": [_artifact_to_dict(item) for item in self.artifacts],
        }


@dataclass(frozen=True)
class PlanApproval:
    schema_version: int
    approval_id: str
    team_task_id: str
    plan_hash: str
    workspace_root: str
    approved_by: str
    approved_at: str
    plan: PlanSnapshot

    @classmethod
    def from_value(cls, value: Any) -> "PlanApproval":
        value = _exact_mapping(
            value,
            {
                "schema_version", "approval_id", "team_task_id", "plan_hash",
                "workspace_root", "approved_by", "approved_at", "plan",
            },
            "Plan approval",
        )
        if value["schema_version"] != BRIDGE_SCHEMA_VERSION:
            raise ValueError("Plan approval schema version is not supported.")
        workspace = Path(
            _required_string(value["workspace_root"], "Approved workspace", maximum=2_000)
        ).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Approved workspace must be an absolute path.")
        approved_at, _ = _timestamp(value["approved_at"], "Plan approval approved_at")
        plan = PlanSnapshot.from_value(value["plan"])
        team_task_id = _team_task_id(value["team_task_id"])
        plan_hash = _plan_hash(value["plan_hash"])
        if plan.team_task_id != team_task_id:
            raise ValueError("Plan approval task identity does not match its snapshot.")
        if plan.hash != plan_hash:
            raise ValueError("Plan approval hash does not match its immutable snapshot.")
        return cls(
            schema_version=BRIDGE_SCHEMA_VERSION,
            approval_id=_approval_id(value["approval_id"]),
            team_task_id=team_task_id,
            plan_hash=plan_hash,
            workspace_root=str(workspace.resolve()),
            approved_by=_required_string(value["approved_by"], "Plan approval actor", maximum=100),
            approved_at=approved_at,
            plan=plan,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "approval_id": self.approval_id,
            "team_task_id": self.team_task_id,
            "plan_hash": self.plan_hash,
            "workspace_root": self.workspace_root,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "plan": self.plan.to_dict(),
        }


@dataclass(frozen=True)
class FileChange:
    path: str
    summary: str

    @classmethod
    def from_value(cls, value: Any, workspace_root: str | Path) -> "FileChange":
        value = _exact_mapping(value, {"path", "summary"}, "Implementation file change")
        raw_path = _required_string(value["path"], "Changed file path", maximum=1_000)
        relative = Path(raw_path)
        if relative.is_absolute():
            raise ValueError("Changed file paths must be relative to the active workspace.")
        if any(part.lower() in PROTECTED_WORKSPACE_PARTS for part in relative.parts):
            raise ValueError("Changed file paths cannot target protected workspace metadata.")
        workspace = Path(workspace_root).resolve()
        candidate = (workspace / relative).resolve()
        try:
            normalized = candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("Changed file path escapes the active workspace.") from exc
        if not normalized.parts:
            raise ValueError("Changed file path must identify a file inside the workspace.")
        return cls(
            path=normalized.as_posix(),
            summary=_required_string(value["summary"], "File change summary", maximum=2_000),
        )

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "summary": self.summary}


@dataclass(frozen=True)
class TestResult:
    command: str
    status: str
    summary: str

    @classmethod
    def from_value(cls, value: Any) -> "TestResult":
        value = _exact_mapping(value, {"command", "status", "summary"}, "Implementation test result")
        status_value = _required_string(value["status"], "Test status", maximum=32).lower()
        if status_value not in TEST_STATUSES:
            raise ValueError(f"Test status is not supported: {status_value}")
        return cls(
            command=_required_string(value["command"], "Test command", maximum=2_000),
            status=status_value,
            summary=_required_string(value["summary"], "Test summary", maximum=4_000),
        )

    def to_dict(self) -> dict[str, str]:
        return {"command": self.command, "status": self.status, "summary": self.summary}


@dataclass(frozen=True)
class ImplementationResult:
    summary: str
    files_changed: tuple[FileChange, ...]
    tests: tuple[TestResult, ...]
    risks: tuple[str, ...]
    remaining_work: tuple[str, ...]
    review_notes: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any, workspace_root: str | Path) -> "ImplementationResult":
        value = _exact_mapping(
            value,
            {"summary", "files_changed", "tests", "risks", "remaining_work", "review_notes"},
            "Codex implementation result",
        )
        file_values = value["files_changed"]
        test_values = value["tests"]
        if not isinstance(file_values, list):
            raise ValueError("Implementation files_changed must be a JSON array.")
        if not isinstance(test_values, list):
            raise ValueError("Implementation tests must be a JSON array.")
        if len(file_values) > 500:
            raise ValueError("Implementation cannot report more than 500 changed files.")
        if not test_values or len(test_values) > 200:
            raise ValueError("Implementation must report between 1 and 200 test results.")
        files = tuple(FileChange.from_value(item, workspace_root) for item in file_values)
        paths = [item.path.lower() for item in files]
        if len(set(paths)) != len(paths):
            raise ValueError("Implementation changed file paths must be unique.")
        tests = tuple(TestResult.from_value(item) for item in test_values)
        return cls(
            summary=_required_string(value["summary"], "Implementation summary", maximum=8_000),
            files_changed=files,
            tests=tests,
            risks=_bounded_strings(value["risks"], "Implementation risks"),
            remaining_work=_bounded_strings(value["remaining_work"], "Implementation remaining work"),
            review_notes=_bounded_strings(value["review_notes"], "Implementation review notes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "files_changed": [item.to_dict() for item in self.files_changed],
            "tests": [item.to_dict() for item in self.tests],
            "risks": list(self.risks),
            "remaining_work": list(self.remaining_work),
            "review_notes": list(self.review_notes),
        }


@dataclass(frozen=True)
class CodexRun:
    schema_version: int
    run_id: str
    approval_id: str
    team_task_id: str
    plan_hash: str
    workspace_root: str
    status: str
    command: tuple[str, ...]
    artifacts: tuple[str, ...]
    started_at: str
    completed_at: str
    result: ImplementationResult | None
    error: str

    @classmethod
    def from_value(cls, value: Any) -> "CodexRun":
        value = _exact_mapping(
            value,
            {
                "schema_version", "run_id", "approval_id", "team_task_id",
                "plan_hash", "workspace_root", "status", "command", "artifacts",
                "started_at", "completed_at", "result", "error",
            },
            "Codex run",
        )
        if value["schema_version"] != BRIDGE_SCHEMA_VERSION:
            raise ValueError("Codex run schema version is not supported.")
        workspace = Path(
            _required_string(value["workspace_root"], "Codex run workspace", maximum=2_000)
        ).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Codex run workspace must be an absolute path.")
        status_value = _required_string(value["status"], "Codex run status", maximum=32).lower()
        if status_value not in RUN_STATUSES:
            raise ValueError(f"Codex run status is not recognized: {status_value}")
        command = _bounded_strings(
            value["command"], "Codex run command", maximum_items=40,
            maximum_length=4_000, allow_empty=False,
        )
        executable_name = Path(command[0]).name.lower()
        if executable_name not in {"codex", "codex.exe"}:
            raise ValueError("Codex run command must invoke the local Codex CLI.")
        artifacts = _bounded_strings(
            value["artifacts"], "Codex run artifacts", maximum_items=4,
            maximum_length=100,
        )
        if len(set(artifacts)) != len(artifacts) or not set(artifacts).issubset(RUN_ARTIFACT_NAMES):
            raise ValueError("Codex run artifact names are invalid or duplicated.")
        started_at, started = _timestamp(value["started_at"], "Codex run started_at")
        completed_at, completed = _timestamp(
            value["completed_at"], "Codex run completed_at", allow_empty=True
        )
        if completed is not None and started is not None and completed < started:
            raise ValueError("Codex run completed_at cannot precede started_at.")
        error = _optional_string(value["error"], "Codex run error", maximum=100)
        result_value = value["result"]
        result = None
        if result_value is not None:
            result = ImplementationResult.from_value(result_value, workspace)
        if status_value == RUN_STATUS_EXECUTING:
            if completed_at or result is not None or error:
                raise ValueError("Executing Codex runs cannot contain completion fields.")
        elif status_value == RUN_STATUS_AWAITING_REVIEW:
            if not completed_at or result is None or error:
                raise ValueError("Awaiting-review Codex runs require a result and completion time.")
            required = {"approved-plan.json", "result-schema.json", "events.jsonl", "implementation-result.json"}
            if set(artifacts) != required:
                raise ValueError("Awaiting-review Codex runs require all structured artifacts.")
        elif status_value == RUN_STATUS_FAILED:
            if not completed_at or result is not None or error not in RUN_ERROR_CATEGORIES:
                raise ValueError("Failed Codex runs require a sanitized error category.")
        return cls(
            schema_version=BRIDGE_SCHEMA_VERSION,
            run_id=_run_id(value["run_id"]),
            approval_id=_approval_id(value["approval_id"]),
            team_task_id=_team_task_id(value["team_task_id"]),
            plan_hash=_plan_hash(value["plan_hash"]),
            workspace_root=str(workspace.resolve()),
            status=status_value,
            command=command,
            artifacts=artifacts,
            started_at=started_at,
            completed_at=completed_at,
            result=result,
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "approval_id": self.approval_id,
            "team_task_id": self.team_task_id,
            "plan_hash": self.plan_hash,
            "workspace_root": self.workspace_root,
            "status": self.status,
            "command": list(self.command),
            "artifacts": list(self.artifacts),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": None if self.result is None else self.result.to_dict(),
            "error": self.error,
        }


class CodexBridgeStore:
    """Persist immutable approvals and bounded execution artifacts externally."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def save_approval(self, approval: PlanApproval) -> Path:
        validated = PlanApproval.from_value(approval.to_dict())
        path = self.approval_path(validated.team_task_id, validated.approval_id)
        self._write_immutable(path, json.dumps(validated.to_dict(), indent=2, ensure_ascii=False) + "\n")
        return path

    def load_approval(self, team_task_id: str, approval_id: str) -> PlanApproval:
        path = self.approval_path(team_task_id, approval_id)
        value = self._read_json(path, "Plan approval")
        try:
            approval = PlanApproval.from_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Plan approval is invalid: {approval_id}") from exc
        if approval.team_task_id != _team_task_id(team_task_id) or approval.approval_id != _approval_id(approval_id):
            raise ValueError("Plan approval identity does not match its path.")
        return approval

    def save_run(self, run: CodexRun) -> Path:
        validated = CodexRun.from_value(run.to_dict())
        path = self.run_path(validated.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(validated.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _owner_only(temporary)
        temporary.replace(path)
        _owner_only(path)
        return path

    def load_run(self, run_id: str) -> CodexRun:
        path = self.run_path(run_id)
        value = self._read_json(path, "Codex run")
        try:
            run = CodexRun.from_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Codex run is invalid: {run_id}") from exc
        if run.run_id != _run_id(run_id):
            raise ValueError("Codex run identity does not match its path.")
        return run

    def claim_approval(
        self,
        team_task_id: str,
        approval_id: str,
        run_id: str,
        claimed_at: str,
    ) -> Path:
        timestamp, _ = _timestamp(claimed_at, "Approval claim claimed_at")
        payload = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "team_task_id": _team_task_id(team_task_id),
            "approval_id": _approval_id(approval_id),
            "run_id": _run_id(run_id),
            "claimed_at": timestamp,
        }
        return self._write_immutable(
            self.approval_claim_path(payload["team_task_id"], payload["approval_id"]),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )

    def approval_used(self, team_task_id: str, approval_id: str) -> bool:
        normalized_task = _team_task_id(team_task_id)
        normalized_approval = _approval_id(approval_id)
        path = self.approval_claim_path(normalized_task, normalized_approval)
        if not path.exists():
            return False
        value = self._read_json(path, "Approval claim")
        value = _exact_mapping(
            value,
            {"schema_version", "team_task_id", "approval_id", "run_id", "claimed_at"},
            "Approval claim",
        )
        if value["schema_version"] != BRIDGE_SCHEMA_VERSION:
            raise ValueError("Approval claim schema version is not supported.")
        if _team_task_id(value["team_task_id"]) != normalized_task:
            raise ValueError("Approval claim task identity does not match its path.")
        if _approval_id(value["approval_id"]) != normalized_approval:
            raise ValueError("Approval claim identity does not match its path.")
        _run_id(value["run_id"])
        _timestamp(value["claimed_at"], "Approval claim claimed_at")
        return True

    def recent_runs(self, limit: int = 10) -> tuple[CodexRun, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Codex run limit must be a positive integer.")
        runs: list[CodexRun] = []
        for path in (self.root / "runs").glob("*/run.json"):
            try:
                runs.append(self.load_run(path.parent.name))
            except (OSError, ValueError):
                continue
        runs.sort(key=lambda item: (item.started_at, item.run_id), reverse=True)
        return tuple(runs[:limit])

    def write_run_artifact(self, run_id: str, name: str, content: str) -> Path:
        normalized_run = _run_id(run_id)
        if name not in RUN_ARTIFACT_NAMES:
            raise ValueError(f"Codex run artifact name is not supported: {name}")
        return self._write_immutable(self.root / "runs" / normalized_run / name, content)

    def approval_path(self, team_task_id: str, approval_id: str) -> Path:
        return self.root / "approvals" / _team_task_id(team_task_id) / f"{_approval_id(approval_id)}.json"

    def approval_claim_path(self, team_task_id: str, approval_id: str) -> Path:
        return self.root / "claims" / _team_task_id(team_task_id) / f"{_approval_id(approval_id)}.json"

    def run_path(self, run_id: str) -> Path:
        return self.root / "runs" / _run_id(run_id) / "run.json"

    def run_directory(self, run_id: str) -> Path:
        return self.run_path(run_id).parent

    @staticmethod
    def _write_immutable(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
        except FileExistsError as exc:
            raise FileExistsError(f"Immutable Codex artifact already exists: {path.name}") from exc
        _owner_only(path)
        return path

    @staticmethod
    def _read_json(path: Path, label: str) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path.stem}")
        try:
            value = _strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{label} could not be read as strict JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} must contain a JSON object.")
        return value


@dataclass(frozen=True)
class CodexProcessResult:
    returncode: int
    stdout: str
    stderr: str


class LocalCodexRunner:
    """Invoke the local Codex CLI without a shell or inherited secret variables."""

    SAFE_ENVIRONMENT_NAMES = frozenset({
        "APPDATA", "CODEX_CA_CERTIFICATE", "CODEX_HOME", "COMSPEC", "HOME", "HOMEDRIVE",
        "HOMEPATH", "LANG", "LC_ALL", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS",
        "OS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE", "SSL_CERT_FILE",
        "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TERM", "TMP", "USERDOMAIN",
        "USERNAME", "USERPROFILE", "VIRTUAL_ENV", "WINDIR",
    })

    def run(
        self,
        command: Iterable[str],
        *,
        cwd: Path,
        prompt: str,
        timeout: int,
    ) -> CodexProcessResult:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in self.SAFE_ENVIRONMENT_NAMES
        }
        environment["RUST_LOG"] = "error"
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            env=environment,
        )
        return CodexProcessResult(completed.returncode, completed.stdout, completed.stderr)


class CodexBridgeError(RuntimeError):
    """A sanitized execution failure with an optional persisted run identity."""

    def __init__(self, message: str, *, run_id: str = "", category: str = "") -> None:
        super().__init__(message)
        self.run_id = run_id
        self.category = category


class CodexBridge:
    """Approve exact AI Team plan versions and execute them once through Codex."""

    RESULT_SCHEMA: Mapping[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "files_changed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "summary": {"type": "string", "minLength": 1},
                    },
                    "required": ["path", "summary"],
                    "additionalProperties": False,
                },
            },
            "tests": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": ["passed", "failed", "not_run"]},
                        "summary": {"type": "string", "minLength": 1},
                    },
                    "required": ["command", "status", "summary"],
                    "additionalProperties": False,
                },
            },
            "risks": {"type": "array", "items": {"type": "string"}},
            "remaining_work": {"type": "array", "items": {"type": "string"}},
            "review_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "files_changed", "tests", "risks", "remaining_work", "review_notes"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        config_manager,
        team_store: TeamTaskStore,
        store: CodexBridgeStore,
        workspace_root: str | Path,
        *,
        runner: LocalCodexRunner | None = None,
        execution_engines=None,
        now: Callable[[], datetime] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config_manager
        self.team_store = team_store
        self.store = store
        self.runner = runner or LocalCodexRunner()
        self.execution_engines = execution_engines
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._approval_id_factory = approval_id_factory or self._new_approval_id
        self._run_id_factory = run_id_factory or self._new_run_id
        self._lock = RLock()
        self.bind(workspace_root)

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def bind(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        with self._lock:
            self._workspace_root = root

    def approve(self, team_task_id: str, *, actor: str = "user") -> PlanApproval:
        self._require_enabled()
        with self._lock:
            self._require_workspace_root()
            task = self.team_store.load(team_task_id)
            plan = PlanSnapshot.from_team_task(task)
            approval = PlanApproval.from_value({
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "approval_id": self._approval_id_factory(),
                "team_task_id": plan.team_task_id,
                "plan_hash": plan.hash,
                "workspace_root": str(self._workspace_root),
                "approved_by": actor,
                "approved_at": self._timestamp(),
                "plan": plan.to_dict(),
            })
            self.store.save_approval(approval)
            return approval

    def execute(self, team_task_id: str, approval_id: str) -> CodexRun:
        self._require_enabled()
        with self._lock:
            self._require_workspace_root()
            task = self.team_store.load(team_task_id)
            plan = PlanSnapshot.from_team_task(task)
            approval = self.store.load_approval(plan.team_task_id, approval_id)
            if not _same_path(approval.workspace_root, self._workspace_root):
                raise PermissionError("Plan approval belongs to a different active workspace.")
            if approval.plan_hash != plan.hash or approval.plan.to_dict() != plan.to_dict():
                raise PermissionError("Persisted AI Team plan changed after approval; approve this version again.")
            if self.store.approval_used(plan.team_task_id, approval.approval_id):
                raise PermissionError("Plan approval has already been consumed by a Codex run.")
            if self.execution_engines is not None:
                self.execution_engines.require_codex()

            timeout_seconds = self._timeout_seconds()
            max_output_bytes = self._max_output_bytes()

            run_id = _run_id(self._run_id_factory())
            try:
                self.store.claim_approval(
                    plan.team_task_id,
                    approval.approval_id,
                    run_id,
                    self._timestamp(),
                )
            except FileExistsError as exc:
                raise PermissionError(
                    "Plan approval has already been consumed by a Codex run."
                ) from exc
            run_directory = self.store.run_directory(run_id)
            schema_path = run_directory / "result-schema.json"
            command = self._command(schema_path)
            run = CodexRun.from_value({
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "run_id": run_id,
                "approval_id": approval.approval_id,
                "team_task_id": plan.team_task_id,
                "plan_hash": plan.hash,
                "workspace_root": str(self._workspace_root),
                "status": RUN_STATUS_EXECUTING,
                "command": list(command),
                "artifacts": ["approved-plan.json", "result-schema.json"],
                "started_at": self._timestamp(),
                "completed_at": "",
                "result": None,
                "error": "",
            })
            self.store.write_run_artifact(
                run_id,
                "approved-plan.json",
                json.dumps(approval.to_dict(), indent=2, ensure_ascii=False) + "\n",
            )
            self.store.write_run_artifact(
                run_id,
                "result-schema.json",
                json.dumps(self.RESULT_SCHEMA, indent=2, ensure_ascii=False) + "\n",
            )
            self.store.save_run(run)

            try:
                process = self.runner.run(
                    command,
                    cwd=self._workspace_root,
                    prompt=self._prompt(approval),
                    timeout=timeout_seconds,
                )
            except FileNotFoundError as exc:
                self._fail(run, "codex_cli_unavailable")
                raise CodexBridgeError(
                    "Local Codex CLI was not found. Install or repair Codex, then approve a new run.",
                    run_id=run.run_id,
                    category="codex_cli_unavailable",
                ) from exc
            except (OSError, PermissionError) as exc:
                self._fail(run, "codex_cli_unavailable")
                raise CodexBridgeError(
                    "Local Codex CLI could not be started. Repair its installation, then approve a new run.",
                    run_id=run.run_id,
                    category="codex_cli_unavailable",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                self._fail(run, "codex_timeout")
                raise CodexBridgeError(
                    "Codex execution reached Orion's timeout and stopped.",
                    run_id=run.run_id,
                    category="codex_timeout",
                ) from exc

            if not isinstance(process, CodexProcessResult):
                self._fail(run, "invalid_codex_output")
                raise CodexBridgeError(
                    "Codex runner returned an invalid process result.",
                    run_id=run.run_id,
                    category="invalid_codex_output",
                )
            output_size = len(process.stdout.encode("utf-8")) + len(process.stderr.encode("utf-8"))
            if output_size > max_output_bytes:
                self._fail(run, "codex_output_too_large")
                raise CodexBridgeError(
                    "Codex output exceeded Orion's configured capture limit.",
                    run_id=run.run_id,
                    category="codex_output_too_large",
                )
            if process.returncode != 0:
                self._fail(run, "codex_process_failed")
                raise CodexBridgeError(
                    "Codex execution failed; raw process errors were not persisted.",
                    run_id=run.run_id,
                    category="codex_process_failed",
                )

            try:
                events, final_message = self._events(process.stdout)
                events_text = "".join(
                    json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n"
                    for item in events
                )
                self.store.write_run_artifact(run_id, "events.jsonl", events_text)
                result_value = _strict_json_loads(final_message)
                result = ImplementationResult.from_value(result_value, self._workspace_root)
                self.store.write_run_artifact(
                    run_id,
                    "implementation-result.json",
                    json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._fail(run, "invalid_codex_output", include_events=(self.store.run_directory(run_id) / "events.jsonl").is_file())
                raise CodexBridgeError(
                    "Codex did not return a valid structured implementation result.",
                    run_id=run.run_id,
                    category="invalid_codex_output",
                ) from exc

            completed = replace(
                run,
                status=RUN_STATUS_AWAITING_REVIEW,
                artifacts=(
                    "approved-plan.json", "result-schema.json", "events.jsonl",
                    "implementation-result.json",
                ),
                completed_at=self._timestamp(),
                result=result,
            )
            CodexRun.from_value(completed.to_dict())
            self.store.save_run(completed)
            return completed

    def run(self, run_id: str) -> CodexRun:
        return self.store.load_run(run_id)

    def recent(self, limit: int = 10) -> tuple[CodexRun, ...]:
        return self.store.recent_runs(limit)

    def _fail(self, run: CodexRun, category: str, *, include_events: bool = False) -> CodexRun:
        artifacts = ["approved-plan.json", "result-schema.json"]
        if include_events:
            artifacts.append("events.jsonl")
        failed = replace(
            run,
            status=RUN_STATUS_FAILED,
            artifacts=tuple(artifacts),
            completed_at=self._timestamp(),
            result=None,
            error=category,
        )
        CodexRun.from_value(failed.to_dict())
        self.store.save_run(failed)
        return failed

    def _command(self, schema_path: Path) -> tuple[str, ...]:
        workspace_key = json.dumps(str(self._workspace_root))
        return (
            "codex", "exec", "--json", "--ephemeral",
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
            "--ignore-user-config", "--strict-config",
            "--config", 'web_search="disabled"',
            "--config", "mcp_servers={}",
            "--config", "features.apps=false",
            "--config", "features.hooks=false",
            "--config", "features.multi_agent=false",
            "--config", "features.remote_plugin=false",
            "--config", "sandbox_workspace_write.network_access=false",
            "--config", "sandbox_workspace_write.writable_roots=[]",
            "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
            "--config", f'projects.{workspace_key}.trust_level="untrusted"',
            "--cd", str(self._workspace_root),
            "--output-schema", str(schema_path),
            "-",
        )

    @staticmethod
    def _prompt(approval: PlanApproval) -> str:
        steps = "\n".join(
            f"{index}. {item}" for index, item in enumerate(approval.plan.final_plan, start=1)
        )
        return f"""Implement this explicitly approved Orion AI Team plan.

Approval ID: {approval.approval_id}
Approved plan SHA-256: {approval.plan_hash}
Goal: {approval.plan.goal}

Approved implementation plan:
{steps}

Hard boundaries:
- Work only inside the current active workspace.
- Make the smallest changes needed to implement the approved plan.
- Do not create or switch branches, modify Git metadata, commit, push, merge, tag, or open pull requests.
- Do not access the network or request broader permissions.
- Do not invoke web search, MCP servers, apps, plugins, hooks, or sub-agents.
- Run relevant local tests. If tests cannot run, report one test entry with status not_run.
- Do not continue into review fixes or release work.
- Finish by returning only the structured result required by the supplied JSON schema.
"""

    @staticmethod
    def _events(stdout: str) -> tuple[tuple[dict[str, Any], ...], str]:
        if not stdout.strip():
            raise ValueError("Codex JSONL output is empty.")
        events: list[dict[str, Any]] = []
        final_message = ""
        for line_number, line in enumerate(stdout.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = _strict_json_loads(line)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Codex JSONL is invalid at line {line_number}.") from exc
            if not isinstance(value, dict) or not isinstance(value.get("type"), str) or not value["type"].strip():
                raise ValueError(f"Codex JSONL event is invalid at line {line_number}.")
            events.append(value)
            item = value.get("item")
            if (
                value.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            ):
                final_message = item["text"].strip()
        if not events or not final_message:
            raise ValueError("Codex JSONL does not contain a final agent message.")
        return tuple(events), final_message

    def _require_enabled(self) -> None:
        if not bool(self.config.get("codex_bridge.enabled", True)):
            raise ValueError("Codex Bridge is disabled in configuration.")

    def _require_workspace_root(self) -> None:
        if not self._workspace_root.is_dir():
            raise NotADirectoryError(f"Active workspace is not available: {self._workspace_root}")
        try:
            self.store.root.relative_to(self._workspace_root)
        except ValueError:
            pass
        else:
            raise ValueError("Codex Bridge artifacts must be stored outside the active workspace.")
        if not (self._workspace_root / ".git").exists():
            raise ValueError("Codex Bridge requires the active workspace to be a Git repository root.")

    def _timeout_seconds(self) -> int:
        value = self.config.get("codex_bridge.timeout_seconds", 1_800)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("Codex Bridge timeout must be a finite number.")
        normalized = int(value)
        if normalized < 1 or normalized > 7_200:
            raise ValueError("Codex Bridge timeout must be between 1 and 7,200 seconds.")
        return normalized

    def _max_output_bytes(self) -> int:
        value = self.config.get("codex_bridge.max_output_bytes", 5_000_000)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100_000_000:
            raise ValueError("Codex Bridge output limit must be between 1 and 100,000,000 bytes.")
        return value

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _new_approval_id() -> str:
        return f"approval-{uuid4().hex}"

    @staticmethod
    def _new_run_id() -> str:
        return f"run-{uuid4().hex}"
