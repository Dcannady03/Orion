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
from typing import Any, Callable, ClassVar, Iterable, Mapping
from uuid import uuid4

from orion.services.team import (
    TASK_ID_PATTERN as TEAM_TASK_ID_PATTERN,
    TEAM_STATUS_AWAITING_APPROVAL,
    TeamArtifact,
    TeamTask,
    TeamTaskStore,
)
from orion.services.execution_engines import (
    ExecutionEngine,
    ExecutionEngineUnavailable,
    executable_environment,
    safe_executable_command,
)
from orion.services.workspace import (
    WORKSPACE_MODE_STANDARD,
    WorkspaceCapabilities,
)
from orion.services.workspace_snapshot import (
    SnapshotLimits,
    WorkspaceBaseline,
    WorkspaceChangeSet,
    WorkspaceRollbackError,
    WorkspaceSnapshotError,
    WorkspaceSnapshotService,
    baseline_json,
    changes_json,
)


BRIDGE_SCHEMA_VERSION = 2
APPROVAL_ID_PATTERN = re.compile(r"approval-[a-z0-9-]{6,95}")
RUN_ID_PATTERN = re.compile(r"run-[a-z0-9-]{6,95}")
PLAN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")

RUN_STATUS_EXECUTING = "executing"
RUN_STATUS_AWAITING_REVIEW = "awaiting_review"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_ROLLED_BACK = "rolled_back"
RUN_STATUSES = frozenset({
    RUN_STATUS_EXECUTING,
    RUN_STATUS_AWAITING_REVIEW,
    RUN_STATUS_FAILED,
    RUN_STATUS_ROLLED_BACK,
})
TEST_STATUSES = frozenset({"passed", "failed", "not_run"})
RUN_ERROR_CATEGORIES = frozenset({
    "codex_cli_unavailable",
    "codex_cli_argument_error",
    "codex_timeout",
    "codex_process_failed",
    "codex_output_too_large",
    "invalid_codex_output",
    "workspace_snapshot_failed",
    "workspace_change_mismatch",
})
RUN_ARTIFACT_NAMES = frozenset({
    "approved-plan.json",
    "result-schema.json",
    "events.jsonl",
    "implementation-result.json",
    "workspace-baseline.json",
    "workspace-changes.json",
    "workspace.diff",
    "rollback.json",
})
PROTECTED_WORKSPACE_PARTS = frozenset({".git", ".agents", ".codex"})
CODEX_REQUIRED_SECURITY_ARGUMENTS = frozenset({
    "--sandbox",
    "--config",
    "--ignore-user-config",
    "--strict-config",
    "--cd",
})
CODEX_REQUIRED_PROTOCOL_ARGUMENTS = frozenset({"--json", "--output-schema"})
CODEX_OPTIONAL_COMPATIBILITY_ARGUMENTS = frozenset({
    "--ask-for-approval",
    "--ephemeral",
    "--skip-git-repo-check",
})
CODEX_HELP_OPTION_PATTERN = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
CODEX_UNSUPPORTED_ARGUMENT_PATTERN = re.compile(
    r"(?:unexpected\s+argument|unknown\s+(?:argument|option)|"
    r"unrecognized\s+(?:argument|option))\s+['\"]?(--[a-z0-9][a-z0-9-]*)",
    re.IGNORECASE,
)
SAFE_STDERR_SUMMARIES = frozenset({
    "",
    "Codex could not be started.",
    "Codex execution exceeded Orion's timeout.",
    "Codex output exceeded Orion's capture limit.",
    "Codex rejected an unsupported command-line argument.",
    "Codex exited without a structured result.",
})
UTF8_MOJIBAKE_REPLACEMENTS = {
    "â€™": "’",
    "â€˜": "‘",
    "â€œ": "“",
    "â€": "”",
    "â€“": "–",
    "â€”": "—",
    "â€¦": "…",
}


def _native_windows(platform_name: str) -> bool:
    return str(platform_name).strip().casefold() in {"nt", "win32", "windows"}


def _isolated_codex_config_values(platform_name: str) -> tuple[str, ...]:
    """Return the strict static config shared by capability probes and execution."""

    values = [
        'approval_policy="never"',
        'sandbox_mode="workspace-write"',
    ]
    if _native_windows(platform_name):
        # Orion ignores user config, so the preferred native Windows sandbox must
        # be selected again inside the isolated child configuration.
        values.append('windows.sandbox="elevated"')
    values.extend((
        'web_search="disabled"',
        "mcp_servers={}",
        "features.apps=false",
        "features.hooks=false",
        "features.multi_agent=false",
        "features.remote_plugin=false",
        "sandbox_workspace_write.network_access=false",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.exclude_slash_tmp=true",
    ))
    return tuple(values)


def _normalize_utf8_text(value: str) -> str:
    normalized = value
    for broken, repaired in UTF8_MOJIBAKE_REPLACEMENTS.items():
        normalized = normalized.replace(broken, repaired)
    return normalized


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
    normalized = _normalize_utf8_text(value.strip())
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _optional_string(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = _normalize_utf8_text(value.strip())
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
    workspace: WorkspaceCapabilities
    execution_engine: str
    approved_scope: str
    expected_operation: str
    approved_by: str
    approved_at: str
    plan: PlanSnapshot

    @classmethod
    def from_value(cls, value: Any) -> "PlanApproval":
        value = _exact_mapping(
            value,
            {
                "schema_version", "approval_id", "team_task_id", "plan_hash",
                "workspace_root", "workspace", "execution_engine", "approved_scope",
                "expected_operation", "approved_by", "approved_at", "plan",
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
        capabilities = WorkspaceCapabilities.from_value(value["workspace"])
        if not _same_path(workspace, capabilities.root):
            raise ValueError("Approved workspace capability root does not match the approval.")
        engine = _required_string(
            value["execution_engine"], "Approved execution engine", maximum=50
        ).lower()
        if engine != "codex":
            raise ValueError("Codex Bridge approvals must bind the Codex execution engine.")
        approved_scope = _required_string(
            value["approved_scope"], "Approved execution scope", maximum=100
        ).lower()
        if approved_scope != "active_workspace":
            raise ValueError("Codex Bridge approval scope is not supported.")
        expected_operation = _required_string(
            value["expected_operation"], "Approved operation", maximum=100
        ).lower()
        if expected_operation != "implement":
            raise ValueError("Codex Bridge approval operation is not supported.")
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
            workspace=capabilities,
            execution_engine=engine,
            approved_scope=approved_scope,
            expected_operation=expected_operation,
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
            "workspace": self.workspace.to_dict(),
            "execution_engine": self.execution_engine,
            "approved_scope": self.approved_scope,
            "expected_operation": self.expected_operation,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "plan": self.plan.to_dict(),
        }


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable router-to-bridge handoff for one approved implementation."""

    team_task_id: str
    approval_id: str
    workspace: WorkspaceCapabilities
    execution_engine: ExecutionEngine
    approved_scope: str = "active_workspace"
    expected_operation: str = "implement"

    def __post_init__(self) -> None:
        _team_task_id(self.team_task_id)
        _approval_id(self.approval_id)
        WorkspaceCapabilities.from_value(self.workspace.to_dict())
        engine = self.execution_engine
        if (
            not isinstance(engine, ExecutionEngine)
            or engine.engine_id != "codex"
            or not engine.ready_for_implementation
            or not isinstance(engine.executable, str)
            or not engine.executable.strip()
        ):
            raise ExecutionEngineUnavailable("No execution engine is currently available.")
        if self.approved_scope != "active_workspace" or self.expected_operation != "implement":
            raise ValueError("Codex execution context does not match the approved operation.")


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
class CodexRunDiagnostics:
    """Strict, sanitized process diagnostics persisted without raw output."""

    exit_code: int | None
    timed_out: bool
    resolved_executable: str
    safe_stderr_summary: str = ""
    unsupported_argument: str = ""

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        default_executable: str,
        successful: bool = False,
    ) -> "CodexRunDiagnostics":
        if value is None:
            return cls(
                exit_code=0 if successful else None,
                timed_out=False,
                resolved_executable=_required_string(
                    default_executable,
                    "Codex diagnostic executable",
                    maximum=4_000,
                ),
            )
        value = _exact_mapping(
            value,
            {
                "exit_code",
                "timed_out",
                "resolved_executable",
                "safe_stderr_summary",
                "unsupported_argument",
            },
            "Codex run diagnostics",
        )
        exit_code = value["exit_code"]
        if (
            exit_code is not None
            and (
                isinstance(exit_code, bool)
                or not isinstance(exit_code, int)
                or not -(2 ** 31) <= exit_code <= (2 ** 32 - 1)
            )
        ):
            raise ValueError("Codex diagnostic exit_code must be a bounded integer or null.")
        if not isinstance(value["timed_out"], bool):
            raise ValueError("Codex diagnostic timed_out must be a boolean.")
        if value["timed_out"] and exit_code is not None:
            raise ValueError("Timed-out Codex diagnostics cannot contain an exit code.")
        executable = _required_string(
            value["resolved_executable"],
            "Codex diagnostic executable",
            maximum=4_000,
        )
        if os.path.normcase(executable) != os.path.normcase(default_executable):
            raise ValueError("Codex diagnostic executable does not match the run command.")
        summary = _optional_string(
            value["safe_stderr_summary"],
            "Codex safe stderr summary",
            maximum=200,
        )
        if summary not in SAFE_STDERR_SUMMARIES:
            raise ValueError("Codex safe stderr summary is not a recognized sanitized value.")
        unsupported = _optional_string(
            value["unsupported_argument"],
            "Codex unsupported argument",
            maximum=100,
        ).lower()
        if unsupported and not re.fullmatch(r"--[a-z0-9][a-z0-9-]*", unsupported):
            raise ValueError("Codex unsupported argument is not valid.")
        if unsupported and summary != "Codex rejected an unsupported command-line argument.":
            raise ValueError("Unsupported Codex arguments require the sanitized argument summary.")
        return cls(exit_code, value["timed_out"], executable, summary, unsupported)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "resolved_executable": self.resolved_executable,
            "safe_stderr_summary": self.safe_stderr_summary,
            "unsupported_argument": self.unsupported_argument,
        }


@dataclass(frozen=True)
class CodexRun:
    schema_version: int
    run_id: str
    approval_id: str
    team_task_id: str
    plan_hash: str
    workspace_root: str
    workspace: WorkspaceCapabilities
    status: str
    command: tuple[str, ...]
    artifacts: tuple[str, ...]
    started_at: str
    completed_at: str
    result: ImplementationResult | None
    changes: WorkspaceChangeSet | None
    error: str
    diagnostics: CodexRunDiagnostics

    @classmethod
    def from_value(cls, value: Any) -> "CodexRun":
        if isinstance(value, dict) and "diagnostics" not in value:
            # Existing v2 run records remain readable; new saves always include
            # the strict diagnostics envelope.
            value = {**value, "diagnostics": None}
        value = _exact_mapping(
            value,
            {
                "schema_version", "run_id", "approval_id", "team_task_id",
                "plan_hash", "workspace_root", "workspace", "status", "command",
                "artifacts", "started_at", "completed_at", "result", "changes", "error",
                "diagnostics",
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
        capabilities = WorkspaceCapabilities.from_value(value["workspace"])
        if not _same_path(workspace, capabilities.root):
            raise ValueError("Codex run workspace capability root does not match its path.")
        status_value = _required_string(value["status"], "Codex run status", maximum=32).lower()
        if status_value not in RUN_STATUSES:
            raise ValueError(f"Codex run status is not recognized: {status_value}")
        command = _bounded_strings(
            value["command"], "Codex run command", maximum_items=64,
            maximum_length=4_000, allow_empty=False,
        )
        executable_name = Path(command[0]).name.lower()
        if executable_name not in {"codex", "codex.cmd", "codex.exe", "codex.ps1"}:
            raise ValueError("Codex run command must invoke the local Codex CLI.")
        artifacts = _bounded_strings(
            value["artifacts"], "Codex run artifacts", maximum_items=8,
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
        diagnostics = CodexRunDiagnostics.from_value(
            value["diagnostics"],
            default_executable=command[0],
            successful=status_value in {RUN_STATUS_AWAITING_REVIEW, RUN_STATUS_ROLLED_BACK},
        )
        result_value = value["result"]
        result = None
        if result_value is not None:
            result = ImplementationResult.from_value(result_value, workspace)
        changes_value = value["changes"]
        changes = None
        if changes_value is not None:
            changes = WorkspaceChangeSet.from_value(changes_value)
            if not _same_path(changes.workspace_root, workspace):
                raise ValueError("Codex run changes belong to a different workspace.")
        if status_value == RUN_STATUS_EXECUTING:
            if completed_at or result is not None or changes is not None or error:
                raise ValueError("Executing Codex runs cannot contain completion fields.")
            if diagnostics.exit_code is not None or diagnostics.timed_out:
                raise ValueError("Executing Codex runs cannot contain completion diagnostics.")
        elif status_value == RUN_STATUS_AWAITING_REVIEW:
            if not completed_at or result is None or changes is None or error:
                raise ValueError("Awaiting-review Codex runs require a result and completion time.")
            if diagnostics.exit_code != 0 or diagnostics.timed_out:
                raise ValueError("Awaiting-review Codex runs require successful process diagnostics.")
            required = {
                "approved-plan.json", "result-schema.json", "events.jsonl",
                "implementation-result.json", "workspace-baseline.json",
                "workspace-changes.json", "workspace.diff",
            }
            if set(artifacts) != required:
                raise ValueError("Awaiting-review Codex runs require all structured artifacts.")
        elif status_value == RUN_STATUS_FAILED:
            if not completed_at or result is not None or error not in RUN_ERROR_CATEGORIES:
                raise ValueError("Failed Codex runs require a sanitized error category.")
        elif status_value == RUN_STATUS_ROLLED_BACK:
            if not completed_at or changes is None or error:
                raise ValueError("Rolled-back Codex runs require their recorded workspace changes.")
            if "rollback.json" not in artifacts:
                raise ValueError("Rolled-back Codex runs require a rollback artifact.")
        return cls(
            schema_version=BRIDGE_SCHEMA_VERSION,
            run_id=_run_id(value["run_id"]),
            approval_id=_approval_id(value["approval_id"]),
            team_task_id=_team_task_id(value["team_task_id"]),
            plan_hash=_plan_hash(value["plan_hash"]),
            workspace_root=str(workspace.resolve()),
            workspace=capabilities,
            status=status_value,
            command=command,
            artifacts=artifacts,
            started_at=started_at,
            completed_at=completed_at,
            result=result,
            changes=changes,
            error=error,
            diagnostics=diagnostics,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "approval_id": self.approval_id,
            "team_task_id": self.team_task_id,
            "plan_hash": self.plan_hash,
            "workspace_root": self.workspace_root,
            "workspace": self.workspace.to_dict(),
            "status": self.status,
            "command": list(self.command),
            "artifacts": list(self.artifacts),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": None if self.result is None else self.result.to_dict(),
            "changes": None if self.changes is None else self.changes.to_dict(),
            "error": self.error,
            "diagnostics": self.diagnostics.to_dict(),
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

    def read_run_artifact(self, run_id: str, name: str) -> str:
        normalized_run = _run_id(run_id)
        if name not in RUN_ARTIFACT_NAMES:
            raise ValueError(f"Codex run artifact name is not supported: {name}")
        path = self.root / "runs" / normalized_run / name
        if not path.is_file():
            raise FileNotFoundError(f"Codex run artifact not found: {name}")
        return path.read_text(encoding="utf-8")

    def approval_path(self, team_task_id: str, approval_id: str) -> Path:
        return self.root / "approvals" / _team_task_id(team_task_id) / f"{_approval_id(approval_id)}.json"

    def approval_claim_path(self, team_task_id: str, approval_id: str) -> Path:
        return self.root / "claims" / _team_task_id(team_task_id) / f"{_approval_id(approval_id)}.json"

    def run_path(self, run_id: str) -> Path:
        return self.root / "runs" / _run_id(run_id) / "run.json"

    def run_directory(self, run_id: str) -> Path:
        return self.run_path(run_id).parent

    def snapshot_blob_root(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "snapshot" / "blobs"

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
        "OS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROGRAMFILES", "SSL_CERT_FILE",
        "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TERM", "TMP", "USERDOMAIN",
        "USERNAME", "USERPROFILE", "VIRTUAL_ENV", "WINDIR",
    })

    def __init__(self, *, platform_name: str | None = None) -> None:
        self._platform_name = platform_name or os.name

    def run(
        self,
        command: Iterable[str],
        *,
        cwd: Path,
        prompt: str,
        timeout: int,
    ) -> CodexProcessResult:
        return self._run_process(
            command,
            cwd=cwd,
            input_text=prompt,
            timeout=timeout,
        )

    def run_help(self, executable: str | Path, *, timeout: float) -> CodexProcessResult:
        """Probe options and strict isolated config without starting an agent turn."""

        command = [
            str(executable),
            "exec",
            "--ignore-user-config",
            "--strict-config",
        ]
        for value in _isolated_codex_config_values(self._platform_name):
            command.extend(("--config", value))
        command.append("--help")

        return self._run_process(
            command,
            cwd=Path.cwd(),
            input_text=None,
            timeout=timeout,
        )

    def _run_process(
        self,
        command: Iterable[str],
        *,
        cwd: Path,
        input_text: str | None,
        timeout: float,
    ) -> CodexProcessResult:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in self.SAFE_ENVIRONMENT_NAMES
        }
        environment["RUST_LOG"] = "error"
        logical_command = list(command)
        if not logical_command:
            raise ValueError("Codex command cannot be empty.")
        environment = executable_environment(
            logical_command[0],
            environment,
            platform_name=self._platform_name,
        )
        launch_command = safe_executable_command(
            logical_command[0],
            logical_command[1:],
            platform_name=self._platform_name,
            environment=environment,
        )
        completed = subprocess.run(
            list(launch_command),
            cwd=str(cwd),
            input=input_text,
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


@dataclass(frozen=True)
class CodexCLICapabilities:
    executable: str
    supported_options: frozenset[str]

    def supports(self, option: str) -> bool:
        return str(option).strip().lower() in self.supported_options


class CodexCLICompatibilityError(ValueError):
    """Raised before approval claiming when the CLI lacks a required protection."""

    def __init__(self, message: str, *, missing_required: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.missing_required = tuple(sorted(set(missing_required)))


class CodexCLICapabilityDetector:
    """Discover ``codex exec`` options once per executable for this process."""

    _cache: ClassVar[dict[str, CodexCLICapabilities]] = {}
    _cache_lock: ClassVar[RLock] = RLock()

    def __init__(
        self,
        runner: LocalCodexRunner | None = None,
        *,
        timeout: float = 5.0,
    ) -> None:
        self.runner = runner or LocalCodexRunner()
        self.timeout = max(0.1, min(float(timeout), 10.0))

    def detect(self, executable: str | Path) -> CodexCLICapabilities:
        path = str(Path(executable).expanduser())
        key = os.path.normcase(path)
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            process = self.runner.run_help(path, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise CodexCLICompatibilityError(
                "Codex CLI capability detection timed out; no implementation was started."
            ) from exc
        except (FileNotFoundError, OSError, PermissionError) as exc:
            raise CodexCLICompatibilityError(
                "Codex CLI capability detection could not start the resolved executable."
            ) from exc
        if not isinstance(process, CodexProcessResult) or process.returncode != 0:
            raise CodexCLICompatibilityError(
                "Codex CLI capability detection failed; no implementation was started."
            )
        output_size = len(process.stdout.encode("utf-8")) + len(process.stderr.encode("utf-8"))
        if output_size > 256_000:
            raise CodexCLICompatibilityError(
                "Codex CLI capability help exceeded Orion's safe capture limit."
            )
        help_text = f"{process.stdout}\n{process.stderr}"
        options = frozenset(
            match.group(1).lower()
            for match in CODEX_HELP_OPTION_PATTERN.finditer(help_text)
        )
        if not options:
            raise CodexCLICompatibilityError(
                "Codex CLI capability help did not contain a supported option list."
            )
        capabilities = CodexCLICapabilities(path, options)
        with self._cache_lock:
            existing = self._cache.setdefault(key, capabilities)
        return existing

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()


@dataclass(frozen=True)
class CodexCommandPlan:
    command: tuple[str, ...]
    required_security_arguments: tuple[str, ...]
    optional_compatibility_arguments: tuple[str, ...]
    unsupported_arguments: tuple[str, ...]


def _unsupported_argument(stderr: str) -> str:
    match = CODEX_UNSUPPORTED_ARGUMENT_PATTERN.search(str(stderr)[:32_000])
    return match.group(1).lower() if match else ""


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
        workspace_capabilities: WorkspaceCapabilities | None = None,
        snapshot_service: WorkspaceSnapshotService | None = None,
        runner: LocalCodexRunner | None = None,
        capability_detector: CodexCLICapabilityDetector | None = None,
        execution_engines=None,
        default_execution_engine: ExecutionEngine | None = None,
        now: Callable[[], datetime] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.config = config_manager
        self.team_store = team_store
        self.store = store
        self._platform_name = platform_name or os.name
        self.runner = runner or LocalCodexRunner(platform_name=self._platform_name)
        self.capability_detector = capability_detector or CodexCLICapabilityDetector(
            LocalCodexRunner(platform_name=self._platform_name)
        )
        self.snapshots = snapshot_service or WorkspaceSnapshotService()
        # ``execution_engines`` remains accepted for constructor compatibility,
        # but execution never asks it to rediscover Codex. The router supplies the
        # validated immutable engine snapshot in ``ExecutionContext``.
        self.execution_engines = execution_engines
        self._default_execution_engine = default_execution_engine
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._approval_id_factory = approval_id_factory or self._new_approval_id
        self._run_id_factory = run_id_factory or self._new_run_id
        self._lock = RLock()
        self.bind(workspace_root, workspace_capabilities)

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def workspace_capabilities(self) -> WorkspaceCapabilities:
        return self._workspace_capabilities

    def bind(
        self,
        workspace_root: str | Path,
        capabilities: WorkspaceCapabilities | None = None,
    ) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        selected = capabilities or WorkspaceCapabilities.detect(root)
        if not _same_path(selected.root, root):
            raise ValueError("Workspace capabilities belong to a different active workspace.")
        with self._lock:
            self._workspace_root = root
            self._workspace_capabilities = selected

    def approve(self, team_task_id: str, *, actor: str = "user") -> PlanApproval:
        self._require_enabled()
        with self._lock:
            self._require_workspace()
            task = self.team_store.load(team_task_id)
            plan = PlanSnapshot.from_team_task(task)
            approval = PlanApproval.from_value({
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "approval_id": self._approval_id_factory(),
                "team_task_id": plan.team_task_id,
                "plan_hash": plan.hash,
                "workspace_root": str(self._workspace_root),
                "workspace": self._workspace_capabilities.to_dict(),
                "execution_engine": "codex",
                "approved_scope": "active_workspace",
                "expected_operation": "implement",
                "approved_by": actor,
                "approved_at": self._timestamp(),
                "plan": plan.to_dict(),
            })
            self.store.save_approval(approval)
            return approval

    def execution_context(
        self,
        team_task_id: str,
        approval_id: str,
        execution_engine: ExecutionEngine,
        workspace: WorkspaceCapabilities,
    ) -> ExecutionContext:
        """Create the one immutable router-to-bridge execution handoff."""
        if not _same_path(workspace.root, self._workspace_root):
            raise PermissionError("Execution context belongs to a different active workspace.")
        return ExecutionContext(
            team_task_id=_team_task_id(team_task_id),
            approval_id=_approval_id(approval_id),
            workspace=workspace,
            execution_engine=execution_engine,
        )

    def execute(
        self,
        team_task_id: str | ExecutionContext,
        approval_id: str | None = None,
        *,
        execution_engine: ExecutionEngine | None = None,
    ) -> CodexRun:
        self._require_enabled()
        with self._lock:
            self._require_workspace()
            if isinstance(team_task_id, ExecutionContext):
                if approval_id is not None or execution_engine is not None:
                    raise TypeError("Execution context cannot be combined with separate execution arguments.")
                context = team_task_id
            else:
                if approval_id is None:
                    raise TypeError("Approval ID is required for Codex execution.")
                supplied_engine = execution_engine or self._default_execution_engine
                self._resolve_codex_executable(supplied_engine)
                context = self.execution_context(
                    team_task_id,
                    approval_id,
                    supplied_engine,
                    self._workspace_capabilities,
                )
            self._validate_context(context)
            task = self.team_store.load(context.team_task_id)
            plan = PlanSnapshot.from_team_task(task)
            approval = self.store.load_approval(plan.team_task_id, context.approval_id)
            if not _same_path(approval.workspace_root, self._workspace_root):
                raise PermissionError("Plan approval belongs to a different active workspace.")
            if approval.plan_hash != plan.hash or approval.plan.to_dict() != plan.to_dict():
                raise PermissionError("Persisted AI Team plan changed after approval; approve this version again.")
            if self.store.approval_used(plan.team_task_id, approval.approval_id):
                raise PermissionError("Plan approval has already been consumed by a Codex run.")
            self._validate_approval_context(approval, context)
            execution_workspace = self._validated_execution_workspace(approval, context)
            codex_executable = Path(context.execution_engine.executable).expanduser()

            # Approval and workspace bindings are validated before the read-only
            # help probe. Required protections are checked before baseline storage,
            # approval claiming, or implementation execution.
            capabilities = self.capability_detector.detect(codex_executable)
            run_id = _run_id(self._run_id_factory())
            run_directory = self.store.run_directory(run_id)
            schema_path = run_directory / "result-schema.json"
            command_plan = self._command(
                codex_executable,
                schema_path,
                context.workspace,
                capabilities,
                execution_workspace,
            )
            command = command_plan.command

            timeout_seconds = self._timeout_seconds()
            max_output_bytes = self._max_output_bytes()
            snapshot_limits = SnapshotLimits.from_config(self.config)

            baseline = self.snapshots.capture(
                context.workspace,
                self.store.snapshot_blob_root(run_id),
                snapshot_limits,
                created_at=self._timestamp(),
            )
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
            run = CodexRun.from_value({
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "run_id": run_id,
                "approval_id": approval.approval_id,
                "team_task_id": plan.team_task_id,
                "plan_hash": plan.hash,
                "workspace_root": str(execution_workspace),
                "workspace": context.workspace.to_dict(),
                "status": RUN_STATUS_EXECUTING,
                "command": list(command),
                "artifacts": [
                    "approved-plan.json", "result-schema.json", "workspace-baseline.json",
                ],
                "started_at": self._timestamp(),
                "completed_at": "",
                "result": None,
                "changes": None,
                "error": "",
                "diagnostics": {
                    "exit_code": None,
                    "timed_out": False,
                    "resolved_executable": str(codex_executable),
                    "safe_stderr_summary": "",
                    "unsupported_argument": "",
                },
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
            self.store.write_run_artifact(
                run_id,
                "workspace-baseline.json",
                baseline_json(baseline),
            )
            self.store.save_run(run)

            try:
                process = self.runner.run(
                    command,
                    cwd=execution_workspace,
                    prompt=self._prompt(approval),
                    timeout=timeout_seconds,
                )
            except FileNotFoundError as exc:
                self._fail(
                    run,
                    "codex_cli_unavailable",
                    baseline,
                    snapshot_limits,
                    diagnostics=self._diagnostics(
                        run,
                        safe_stderr_summary="Codex could not be started.",
                    ),
                )
                raise CodexBridgeError(
                    "Local Codex CLI was not found. Install or repair Codex, then approve a new run.",
                    run_id=run.run_id,
                    category="codex_cli_unavailable",
                ) from exc
            except (OSError, PermissionError) as exc:
                self._fail(
                    run,
                    "codex_cli_unavailable",
                    baseline,
                    snapshot_limits,
                    diagnostics=self._diagnostics(
                        run,
                        safe_stderr_summary="Codex could not be started.",
                    ),
                )
                raise CodexBridgeError(
                    "Local Codex CLI could not be started. Repair its installation, then approve a new run.",
                    run_id=run.run_id,
                    category="codex_cli_unavailable",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                self._fail(
                    run,
                    "codex_timeout",
                    baseline,
                    snapshot_limits,
                    diagnostics=self._diagnostics(
                        run,
                        timed_out=True,
                        safe_stderr_summary="Codex execution exceeded Orion's timeout.",
                    ),
                )
                raise CodexBridgeError(
                    "Codex execution reached Orion's timeout and stopped.",
                    run_id=run.run_id,
                    category="codex_timeout",
                ) from exc

            if not isinstance(process, CodexProcessResult):
                self._fail(run, "invalid_codex_output", baseline, snapshot_limits)
                raise CodexBridgeError(
                    "Codex runner returned an invalid process result.",
                    run_id=run.run_id,
                    category="invalid_codex_output",
                )
            output_size = len(process.stdout.encode("utf-8")) + len(process.stderr.encode("utf-8"))
            if output_size > max_output_bytes:
                self._fail(
                    run,
                    "codex_output_too_large",
                    baseline,
                    snapshot_limits,
                    diagnostics=self._diagnostics(
                        run,
                        exit_code=process.returncode,
                        safe_stderr_summary="Codex output exceeded Orion's capture limit.",
                    ),
                )
                raise CodexBridgeError(
                    "Codex output exceeded Orion's configured capture limit.",
                    run_id=run.run_id,
                    category="codex_output_too_large",
                )
            if process.returncode != 0:
                unsupported_argument = _unsupported_argument(process.stderr)
                category = (
                    "codex_cli_argument_error"
                    if unsupported_argument
                    else "codex_process_failed"
                )
                safe_summary = (
                    "Codex rejected an unsupported command-line argument."
                    if unsupported_argument
                    else "Codex exited without a structured result."
                )
                self._fail(
                    run,
                    category,
                    baseline,
                    snapshot_limits,
                    diagnostics=self._diagnostics(
                        run,
                        exit_code=process.returncode,
                        safe_stderr_summary=safe_summary,
                        unsupported_argument=unsupported_argument,
                    ),
                )
                raise CodexBridgeError(
                    (
                        f"Codex rejected unsupported argument {unsupported_argument}; "
                        "the run stopped before implementation."
                        if unsupported_argument
                        else "Codex execution failed; raw process errors were not persisted."
                    ),
                    run_id=run.run_id,
                    category=category,
                )

            try:
                events, final_message = self._events(process.stdout)
                events_text = "".join(
                    json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n"
                    for item in events
                )
                self.store.write_run_artifact(run_id, "events.jsonl", events_text)
                result_value = _strict_json_loads(final_message)
                result = ImplementationResult.from_value(result_value, execution_workspace)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._fail(
                    run,
                    "invalid_codex_output",
                    baseline,
                    snapshot_limits,
                    include_events=(self.store.run_directory(run_id) / "events.jsonl").is_file(),
                    diagnostics=self._diagnostics(run, exit_code=process.returncode),
                )
                raise CodexBridgeError(
                    "Codex did not return a valid structured implementation result.",
                    run_id=run.run_id,
                    category="invalid_codex_output",
                ) from exc

            try:
                changes, _ = self._capture_changes(run, baseline, snapshot_limits)
                self._validate_reported_changes(result, changes, approval.plan)
            except WorkspaceSnapshotError as exc:
                self._fail(
                    run,
                    "workspace_snapshot_failed",
                    baseline,
                    snapshot_limits,
                    include_events=True,
                    diagnostics=self._diagnostics(run, exit_code=process.returncode),
                )
                raise CodexBridgeError(
                    "Orion could not safely capture the completed workspace state.",
                    run_id=run.run_id,
                    category="workspace_snapshot_failed",
                ) from exc
            except ValueError as exc:
                self._fail(
                    run,
                    "workspace_change_mismatch",
                    baseline,
                    snapshot_limits,
                    include_events=True,
                    diagnostics=self._diagnostics(run, exit_code=process.returncode),
                )
                raise CodexBridgeError(
                    "Codex reported file changes that did not match the bounded workspace review.",
                    run_id=run.run_id,
                    category="workspace_change_mismatch",
                ) from exc

            self.store.write_run_artifact(
                run_id,
                "implementation-result.json",
                json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
            )

            completed = replace(
                run,
                status=RUN_STATUS_AWAITING_REVIEW,
                artifacts=(
                    "approved-plan.json", "result-schema.json", "events.jsonl",
                    "implementation-result.json", "workspace-baseline.json",
                    "workspace-changes.json", "workspace.diff",
                ),
                completed_at=self._timestamp(),
                result=result,
                changes=changes,
                diagnostics=self._diagnostics(run, exit_code=process.returncode),
            )
            CodexRun.from_value(completed.to_dict())
            self.store.save_run(completed)
            return completed

    def run(self, run_id: str) -> CodexRun:
        return self.store.load_run(run_id)

    def recent(self, limit: int = 10) -> tuple[CodexRun, ...]:
        return self.store.recent_runs(limit)

    def rollback(self, run_id: str) -> CodexRun:
        """Restore one run after verifying no affected path changed again."""
        with self._lock:
            self._require_workspace()
            run = self.store.load_run(run_id)
            if run.status not in {RUN_STATUS_AWAITING_REVIEW, RUN_STATUS_FAILED}:
                raise ValueError("Only a completed, unrolled Codex run can be rolled back.")
            if not _same_path(run.workspace_root, self._workspace_root):
                raise PermissionError("Codex run belongs to a different active workspace.")
            if run.changes is None:
                raise ValueError("Codex run does not contain a safe workspace change record.")
            baseline = WorkspaceBaseline.from_value(
                _strict_json_loads(self.store.read_run_artifact(run.run_id, "workspace-baseline.json"))
            )
            self.snapshots.rollback(
                baseline,
                run.changes,
                self.store.snapshot_blob_root(run.run_id),
            )
            self.store.write_run_artifact(
                run.run_id,
                "rollback.json",
                json.dumps({
                    "schema_version": BRIDGE_SCHEMA_VERSION,
                    "run_id": run.run_id,
                    "workspace_root": run.workspace_root,
                    "rolled_back_at": self._timestamp(),
                    "change_count": len(run.changes.changes),
                }, indent=2) + "\n",
            )
            rolled_back = replace(
                run,
                status=RUN_STATUS_ROLLED_BACK,
                artifacts=tuple((*run.artifacts, "rollback.json")),
                error="",
            )
            CodexRun.from_value(rolled_back.to_dict())
            self.store.save_run(rolled_back)
            return rolled_back

    def _capture_changes(
        self,
        run: CodexRun,
        baseline: WorkspaceBaseline,
        limits: SnapshotLimits,
    ) -> tuple[WorkspaceChangeSet, str]:
        changes_path = self.store.run_directory(run.run_id) / "workspace-changes.json"
        diff_path = self.store.run_directory(run.run_id) / "workspace.diff"
        if changes_path.is_file() and diff_path.is_file():
            changes = WorkspaceChangeSet.from_value(
                _strict_json_loads(changes_path.read_text(encoding="utf-8"))
            )
            return changes, diff_path.read_text(encoding="utf-8")
        changes, diff = self.snapshots.compare(
            baseline,
            self.store.snapshot_blob_root(run.run_id),
            limits,
        )
        self.store.write_run_artifact(run.run_id, "workspace-changes.json", changes_json(changes))
        self.store.write_run_artifact(run.run_id, "workspace.diff", diff)
        return changes, diff

    def _fail(
        self,
        run: CodexRun,
        category: str,
        baseline: WorkspaceBaseline,
        limits: SnapshotLimits,
        *,
        include_events: bool = False,
        diagnostics: CodexRunDiagnostics | None = None,
    ) -> CodexRun:
        artifacts = ["approved-plan.json", "result-schema.json", "workspace-baseline.json"]
        if include_events:
            artifacts.append("events.jsonl")
        changes = None
        try:
            changes, _ = self._capture_changes(run, baseline, limits)
            artifacts.extend(("workspace-changes.json", "workspace.diff"))
        except (OSError, ValueError, WorkspaceSnapshotError, WorkspaceRollbackError):
            # Preserve the primary execution failure. A missing post-run change
            # record is visible in the artifact list without obscuring the cause.
            pass
        failed = replace(
            run,
            status=RUN_STATUS_FAILED,
            artifacts=tuple(artifacts),
            completed_at=self._timestamp(),
            result=None,
            changes=changes,
            error=category,
            diagnostics=diagnostics or run.diagnostics,
        )
        CodexRun.from_value(failed.to_dict())
        self.store.save_run(failed)
        return failed

    @staticmethod
    def _diagnostics(
        run: CodexRun,
        *,
        exit_code: int | None = None,
        timed_out: bool = False,
        safe_stderr_summary: str = "",
        unsupported_argument: str = "",
    ) -> CodexRunDiagnostics:
        return CodexRunDiagnostics.from_value({
            "exit_code": exit_code,
            "timed_out": timed_out,
            "resolved_executable": run.command[0],
            "safe_stderr_summary": safe_stderr_summary,
            "unsupported_argument": unsupported_argument,
        }, default_executable=run.command[0])

    def _validate_context(self, context: ExecutionContext) -> None:
        if not isinstance(context, ExecutionContext):
            raise TypeError("A validated Codex execution context is required.")
        if not _same_path(context.workspace.root, self._workspace_root):
            raise PermissionError("Execution context belongs to a different active workspace.")
        if context.workspace.to_dict() != self._workspace_capabilities.to_dict():
            raise PermissionError("Active workspace capabilities changed; approve the plan again.")

    @staticmethod
    def _validate_approval_context(
        approval: PlanApproval,
        context: ExecutionContext,
    ) -> None:
        if approval.team_task_id != context.team_task_id or approval.approval_id != context.approval_id:
            raise PermissionError("Execution context does not match the immutable approval.")
        if approval.workspace.to_dict() != context.workspace.to_dict():
            raise PermissionError("Workspace capabilities changed after approval; approve the plan again.")
        if (
            approval.execution_engine != context.execution_engine.engine_id
            or approval.approved_scope != context.approved_scope
            or approval.expected_operation != context.expected_operation
        ):
            raise PermissionError("Execution context does not match the approved engine, scope, or operation.")

    def _validated_execution_workspace(
        self,
        approval: PlanApproval,
        context: ExecutionContext,
    ) -> Path:
        """Return the one canonical workspace shared by approval, context, and launch."""
        approved_workspace = Path(approval.workspace_root).expanduser().resolve()
        execution_workspace = Path(context.workspace.root).expanduser().resolve()
        if not (
            _same_path(approved_workspace, execution_workspace)
            and _same_path(approved_workspace, self._workspace_root)
        ):
            raise PermissionError(
                "Approved, execution, and active workspace paths must match before Codex can start."
            )
        return approved_workspace

    @staticmethod
    def _validate_reported_changes(
        result: ImplementationResult,
        changes: WorkspaceChangeSet,
        plan: PlanSnapshot,
    ) -> None:
        reported = {item.path.casefold() for item in result.files_changed}
        observed = {item.path.casefold() for item in changes.changes}
        if reported != observed:
            raise ValueError("Structured file list does not match observed workspace changes.")
        artifact_text = json.dumps(
            [_artifact_to_dict(item) for item in plan.artifacts],
            ensure_ascii=False,
        )
        plan_text = "\n".join((plan.goal, *plan.final_plan, artifact_text)).casefold()
        for item in changes.by_kind("deleted"):
            path = item.path.casefold()
            name = Path(item.path).name.casefold()
            deletion_named = bool(re.search(r"\b(delete|deletes|deleted|remove|removes|removed)\b", plan_text))
            if not deletion_named or (path not in plan_text and name not in plan_text):
                raise ValueError(f"Deleted file was not explicitly named by the approved plan: {item.path}")

    def _resolve_codex_executable(
        self,
        execution_engine: ExecutionEngine | None = None,
    ) -> Path:
        if (
            not isinstance(execution_engine, ExecutionEngine)
            or execution_engine.engine_id != "codex"
            or not execution_engine.ready_for_implementation
        ):
            raise ExecutionEngineUnavailable(
                "No validated execution engine was supplied by the command router."
            )
        value = execution_engine.executable
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            raise ExecutionEngineUnavailable(
                "No validated execution engine was supplied by the command router."
            )
        executable = Path(value).expanduser()
        if (
            executable is None
            or executable.name.lower() not in {"codex", "codex.cmd", "codex.exe", "codex.ps1"}
        ):
            raise ExecutionEngineUnavailable(
                "No execution engine is currently available."
            )
        return executable

    def _command(
        self,
        codex_executable: Path,
        schema_path: Path,
        workspace: WorkspaceCapabilities,
        capabilities: CodexCLICapabilities,
        approved_workspace: Path,
    ) -> CodexCommandPlan:
        if os.path.normcase(capabilities.executable) != os.path.normcase(str(codex_executable)):
            raise CodexCLICompatibilityError(
                "Codex CLI capabilities belong to a different resolved executable."
            )
        required = CODEX_REQUIRED_SECURITY_ARGUMENTS | CODEX_REQUIRED_PROTOCOL_ARGUMENTS
        missing_required = tuple(sorted(
            option for option in required if not capabilities.supports(option)
        ))
        if missing_required:
            missing = ", ".join(missing_required)
            raise CodexCLICompatibilityError(
                f"Codex CLI is missing required execution protections: {missing}. "
                "No implementation was started and the approval remains unused.",
                missing_required=missing_required,
            )

        command_workspace = Path(approved_workspace).expanduser().resolve()
        if not (
            _same_path(command_workspace, workspace.root)
            and _same_path(command_workspace, self._workspace_root)
        ):
            raise PermissionError(
                "Codex --cd must match the immutable approved execution workspace."
            )

        workspace_key = json.dumps(str(command_workspace))
        command = [str(codex_executable), "exec"]
        included_optional: list[str] = []
        unsupported: list[str] = []
        if workspace.mode == WORKSPACE_MODE_STANDARD:
            if capabilities.supports("--skip-git-repo-check"):
                command.append("--skip-git-repo-check")
                included_optional.append("--skip-git-repo-check")
            else:
                unsupported.append("--skip-git-repo-check")
        command.append("--json")
        if capabilities.supports("--ephemeral"):
            command.append("--ephemeral")
            included_optional.append("--ephemeral")
        else:
            unsupported.append("--ephemeral")
        command.extend((
            "--sandbox", "workspace-write",
        ))
        if capabilities.supports("--ask-for-approval"):
            command.extend(("--ask-for-approval", "never"))
            included_optional.append("--ask-for-approval")
        else:
            unsupported.append("--ask-for-approval")
        command.extend((
            "--ignore-user-config",
            "--strict-config",
        ))
        for value in _isolated_codex_config_values(self._platform_name):
            command.extend(("--config", value))
        command.extend((
            "--config", f'projects.{workspace_key}.trust_level="untrusted"',
            "--cd", str(command_workspace),
            "--output-schema", str(schema_path),
            "-",
        ))
        return CodexCommandPlan(
            command=tuple(command),
            required_security_arguments=tuple(sorted(required)),
            optional_compatibility_arguments=tuple(included_optional),
            unsupported_arguments=tuple(unsupported),
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
Workspace mode: {approval.workspace.mode.title()}

Approved implementation plan:
{steps}

Hard boundaries:
- Work only inside the current active workspace.
- The active workspace is intentionally writable through Codex's workspace-write sandbox.
- Use Codex's file-edit/patch tool for file changes instead of shell redirection or shell write commands.
- If a complex shell preflight is declined by command policy, retry with simple fixed-argument read/test commands; do not infer that the workspace is read-only.
- Make the smallest changes needed to implement the approved plan.
- Do not modify ignored runtime, dependency, credential, or secret files.
- Delete a file only when that exact deletion is explicitly named in the approved plan.
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

    def _require_workspace(self) -> None:
        if not self._workspace_root.is_dir():
            raise NotADirectoryError(f"Active workspace is not available: {self._workspace_root}")
        try:
            self.store.root.relative_to(self._workspace_root)
        except ValueError:
            pass
        else:
            raise ValueError("Codex Bridge artifacts must be stored outside the active workspace.")

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
