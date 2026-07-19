"""Bounded, read-only automatic validation for completed AI Team runs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable, Iterable, Mapping

import yaml

from orion.services.execution_engines import ExecutionEngine
from orion.services.team_roles import TeamRoleSnapshot
from orion.services.workspace import WorkspaceCapabilities
from orion.services.workspace_snapshot import (
    SnapshotLimits,
    WorkspaceBaseline,
    WorkspaceChangeSet,
    WorkspaceSnapshotService,
)


VALIDATION_SCHEMA_VERSION = 1
VALIDATION_ID_PATTERN = re.compile(r"validation-[0-9]{4}")
VALIDATION_STATUSES = frozenset({"passed", "warnings", "failed", "unavailable", "error"})
VALIDATION_CHECK_STATUSES = frozenset({"passed", "warning", "failed", "skipped", "error"})
PROTECTED_WORKSPACE_PARTS = frozenset({".git", ".codex", ".agents"})
TESTER_DENIED_WORKSPACE_PARTS = frozenset({".git", ".codex", ".agents", ".orion", "vault"})
TESTER_DENIED_FILE_NAMES = frozenset({
    ".env", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "vault.yaml", "vault.yml", "google-gmail-token.json", "google-calendar-token.json",
    "microsoft-mail-token.json", "microsoft-calendar-token.json",
})
TESTER_DENIED_FILE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks"})
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[abprs])_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
)
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
MARKDOWN_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
TEST_COUNT_PATTERN = re.compile(r"\bRan\s+(\d+)\s+tests?\b", re.IGNORECASE)
BROAD_PYTHON_PATHS = frozenset({
    "orion/core/orion.py",
    "orion/core/router.py",
    "orion/core/config.py",
    "orion/services/codex_bridge.py",
    "orion/services/team.py",
    "orion/services/team_roles.py",
    "orion/services/team_validation.py",
})


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _safe_text(value: Any, *, maximum: int = 1_000, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("Validation text fields must be strings.")
    text = value.strip()
    if required and not text:
        raise ValueError("Validation text fields cannot be empty.")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = " ".join(text.split())
    return text[:maximum]


def _bounded_strings(
    value: Any,
    label: str,
    *,
    maximum_items: int = 500,
    maximum_length: int = 1_000,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{label} must be a bounded JSON array.")
    return tuple(_safe_text(item, maximum=maximum_length, required=True) for item in value)


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = _safe_text(value, maximum=80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _duration(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Validation duration must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 86_400:
        raise ValueError("Validation duration is outside its safe range.")
    return result


def _same_path(first: str | Path, second: str | Path) -> bool:
    return os.path.normcase(str(Path(first).expanduser().resolve())) == os.path.normcase(
        str(Path(second).expanduser().resolve())
    )


def _relative_path(workspace: Path, value: str | Path) -> str:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Validation file paths must remain inside the approved workspace.")
    if (
        any(part.casefold() in TESTER_DENIED_WORKSPACE_PARTS for part in relative.parts)
        or relative.name.casefold() in TESTER_DENIED_FILE_NAMES
        or relative.suffix.casefold() in TESTER_DENIED_FILE_SUFFIXES
    ):
        raise ValueError("Validation cannot inspect Vault, OAuth token, or credential files.")
    candidate = (workspace / relative).resolve()
    try:
        normalized = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Validation file path escapes the approved workspace.") from exc
    return normalized.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ValidationCommand:
    command: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    safe_summary: str

    @classmethod
    def from_value(cls, value: Any) -> "ValidationCommand":
        value = _exact_mapping(
            value,
            {"command", "exit_code", "timed_out", "duration_seconds", "safe_summary"},
            "Validation command",
        )
        exit_code = value["exit_code"]
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise ValueError("Validation command exit_code must be an integer or null.")
        if not isinstance(value["timed_out"], bool):
            raise ValueError("Validation command timed_out must be true or false.")
        if value["timed_out"] and exit_code is not None:
            raise ValueError("Timed-out validation commands cannot have an exit code.")
        return cls(
            _safe_text(value["command"], maximum=4_000, required=True),
            exit_code,
            value["timed_out"],
            _duration(value["duration_seconds"]),
            _safe_text(value["safe_summary"], maximum=1_000, required=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
            "safe_summary": self.safe_summary,
        }


@dataclass(frozen=True)
class ValidationCheck:
    check_id: str
    name: str
    status: str
    summary: str
    files: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "ValidationCheck":
        value = _exact_mapping(value, {"check_id", "name", "status", "summary", "files"}, "Validation check")
        check_id = _safe_text(value["check_id"], maximum=80, required=True).lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,79}", check_id):
            raise ValueError("Validation check ID has an invalid format.")
        status = _safe_text(value["status"], maximum=20, required=True).lower()
        if status not in VALIDATION_CHECK_STATUSES:
            raise ValueError(f"Validation check status is not supported: {status}")
        return cls(
            check_id,
            _safe_text(value["name"], maximum=150, required=True),
            status,
            _safe_text(value["summary"], maximum=1_000, required=True),
            _bounded_strings(value["files"], "Validation check files", maximum_items=500, maximum_length=1_000),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class ValidationAttempt:
    schema_version: int
    attempt_id: str
    run_id: str
    team_task_id: str
    approval_id: str
    workspace_root: str
    tester_requested: str
    tester_resolved: str
    execution_engine: str
    fallback: str
    fallback_reason: str
    status: str
    checks: tuple[ValidationCheck, ...]
    commands: tuple[ValidationCommand, ...]
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    warnings: tuple[str, ...]
    skipped_checks: tuple[str, ...]
    files_inspected: tuple[str, ...]
    started_at: str
    completed_at: str
    duration_seconds: float
    safe_diagnostics: tuple[str, ...]
    artifact_paths: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> "ValidationAttempt":
        fields = {
            "schema_version", "attempt_id", "run_id", "team_task_id", "approval_id",
            "workspace_root", "tester_requested", "tester_resolved", "execution_engine",
            "fallback", "fallback_reason", "status", "checks", "commands",
            "checks_passed", "checks_failed", "warnings", "skipped_checks",
            "files_inspected", "started_at", "completed_at", "duration_seconds",
            "safe_diagnostics", "artifact_paths",
        }
        value = _exact_mapping(value, fields, "Validation attempt")
        if value["schema_version"] != VALIDATION_SCHEMA_VERSION:
            raise ValueError("Validation schema version is not supported.")
        attempt_id = _safe_text(value["attempt_id"], maximum=15, required=True).lower()
        if not VALIDATION_ID_PATTERN.fullmatch(attempt_id):
            raise ValueError("Validation attempt ID has an invalid format.")
        workspace = Path(_safe_text(value["workspace_root"], maximum=2_000, required=True)).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Validation workspace must be absolute.")
        status = _safe_text(value["status"], maximum=20, required=True).lower()
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"Validation status is not supported: {status}")
        checks_value = value["checks"]
        commands_value = value["commands"]
        if not isinstance(checks_value, list) or len(checks_value) > 100:
            raise ValueError("Validation checks must be a bounded JSON array.")
        if not isinstance(commands_value, list) or len(commands_value) > 50:
            raise ValueError("Validation commands must be a bounded JSON array.")
        started_at, started = _timestamp(value["started_at"], "Validation started_at")
        completed_at, completed = _timestamp(value["completed_at"], "Validation completed_at")
        if completed < started:
            raise ValueError("Validation completion cannot precede its start.")
        artifact_paths = _bounded_strings(
            value["artifact_paths"], "Validation artifact paths", maximum_items=2, maximum_length=100
        )
        expected_prefix = f"validation/{attempt_id}."
        if artifact_paths != (f"{expected_prefix}json", f"{expected_prefix}log"):
            raise ValueError("Validation artifact paths do not match the attempt identity.")
        checks = tuple(ValidationCheck.from_value(item) for item in checks_value)
        commands = tuple(ValidationCommand.from_value(item) for item in commands_value)
        checks_passed = _bounded_strings(value["checks_passed"], "Passed validation checks")
        checks_failed = _bounded_strings(value["checks_failed"], "Failed validation checks")
        warnings = _bounded_strings(value["warnings"], "Validation warnings")
        skipped_checks = _bounded_strings(value["skipped_checks"], "Skipped validation checks")
        if status in {"passed", "warnings", "failed"} or (status == "error" and checks):
            expected_passed = tuple(item.name for item in checks if item.status == "passed")
            expected_failed = tuple(item.name for item in checks if item.status in {"failed", "error"})
            expected_warnings = tuple(item.summary for item in checks if item.status == "warning")
            expected_skipped = tuple(
                f"{item.name} — {item.summary}" for item in checks if item.status == "skipped"
            )
            if (
                checks_passed != expected_passed
                or checks_failed != expected_failed
                or warnings != expected_warnings
                or skipped_checks != expected_skipped
            ):
                raise ValueError("Validation summary fields do not match their checks.")
            derived = (
                "error" if any(item.status == "error" for item in checks)
                else "failed" if expected_failed
                else "warnings" if expected_warnings
                else "passed"
            )
            if status != derived:
                raise ValueError("Validation status does not match its check results.")
        elif status == "unavailable" and (checks or commands or checks_passed or checks_failed or warnings):
            raise ValueError("Unavailable validation attempts cannot contain executed checks.")
        return cls(
            VALIDATION_SCHEMA_VERSION,
            attempt_id,
            _safe_text(value["run_id"], maximum=100, required=True),
            _safe_text(value["team_task_id"], maximum=100, required=True),
            _safe_text(value["approval_id"], maximum=110, required=True),
            str(workspace.resolve()),
            _safe_text(value["tester_requested"], maximum=300, required=True),
            _safe_text(value["tester_resolved"], maximum=300),
            _safe_text(value["execution_engine"], maximum=100),
            _safe_text(value["fallback"], maximum=300),
            _safe_text(value["fallback_reason"], maximum=500),
            status,
            checks,
            commands,
            checks_passed,
            checks_failed,
            warnings,
            skipped_checks,
            _bounded_strings(value["files_inspected"], "Validation files inspected", maximum_length=1_000),
            started_at,
            completed_at,
            _duration(value["duration_seconds"]),
            _bounded_strings(value["safe_diagnostics"], "Validation diagnostics"),
            artifact_paths,
        )

    @property
    def review_status(self) -> str:
        return {
            "passed": "Awaiting Review — Validation Passed",
            "warnings": "Awaiting Review — Validation Warnings",
            "failed": "Awaiting Review — Validation Failed",
            "unavailable": "Validation Unavailable",
            "error": "Validation Error",
        }[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "team_task_id": self.team_task_id,
            "approval_id": self.approval_id,
            "workspace_root": self.workspace_root,
            "tester_requested": self.tester_requested,
            "tester_resolved": self.tester_resolved,
            "execution_engine": self.execution_engine,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "status": self.status,
            "checks": [item.to_dict() for item in self.checks],
            "commands": [item.to_dict() for item in self.commands],
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
            "warnings": list(self.warnings),
            "skipped_checks": list(self.skipped_checks),
            "files_inspected": list(self.files_inspected),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "safe_diagnostics": list(self.safe_diagnostics),
            "artifact_paths": list(self.artifact_paths),
        }


@dataclass(frozen=True)
class ValidationRequest:
    attempt_id: str
    run_id: str
    team_task_id: str
    approval_id: str
    workspace: WorkspaceCapabilities
    active_workspace: str
    changes: WorkspaceChangeSet
    implementation_result: Mapping[str, Any]
    plan_goal: str
    plan_steps: tuple[str, ...]
    tester: TeamRoleSnapshot
    execution_engine: ExecutionEngine | None
    baseline: WorkspaceBaseline
    blob_root: Path
    protected_baseline: Mapping[str, Any] | None
    artifact_paths: tuple[str, str]


@dataclass(frozen=True)
class ValidationProcessResult:
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    output_bytes: int
    test_count: int | None = None


class BoundedValidationRunner:
    """Run only Orion-selected Python validation commands in an isolated temp home."""

    SAFE_ENVIRONMENT_NAMES = frozenset({
        "COMSPEC", "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS", "OS", "PATH", "PATHEXT",
        "PROCESSOR_ARCHITECTURE", "PROGRAMFILES", "SYSTEMDRIVE", "SYSTEMROOT", "WINDIR",
    })

    def run(
        self,
        command: Iterable[str],
        *,
        cwd: Path,
        temp_root: Path,
        timeout: int,
        max_output_bytes: int,
    ) -> ValidationProcessResult:
        argv = tuple(str(item) for item in command)
        if len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve() or argv[1] != "-m":
            raise PermissionError("Tester rejected a command outside the deterministic Python allowlist.")
        if argv[2] not in {"py_compile", "unittest"}:
            raise PermissionError("Tester rejected an unsupported validation module.")
        environment = {
            name: value for name, value in os.environ.items()
            if name.upper() in self.SAFE_ENVIRONMENT_NAMES
        }
        guard = temp_root / "sitecustomize.py"
        guard.write_text(_VALIDATION_GUARD, encoding="utf-8")
        cache = temp_root / "pycache"
        cache.mkdir(parents=True, exist_ok=True)
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
            environment[name] = str(temp_root)
        environment.update({
            "PYTHONPATH": str(temp_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(cache),
            "ORION_VALIDATION_WORKSPACE": str(cwd),
            "ORION_VALIDATION_TEMP": str(temp_root),
            "NO_PROXY": "",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        })
        started = time.monotonic()
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=environment,
        )
        captured = bytearray()
        total = 0
        capture_lock = Lock()

        def drain(stream) -> None:
            nonlocal total
            try:
                for chunk in iter(lambda: stream.read(8_192), b""):
                    with capture_lock:
                        total += len(chunk)
                        remaining = max_output_bytes - len(captured)
                        if remaining > 0:
                            captured.extend(chunk[:remaining])
            finally:
                stream.close()

        threads = (
            Thread(target=drain, args=(process.stdout,), daemon=True),
            Thread(target=drain, args=(process.stderr,), daemon=True),
        )
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=5)
        if timed_out:
            return ValidationProcessResult(None, True, time.monotonic() - started, min(total, max_output_bytes + 1))
        match = TEST_COUNT_PATTERN.search(bytes(captured).decode("utf-8", errors="replace"))
        return ValidationProcessResult(
            process.returncode,
            False,
            time.monotonic() - started,
            min(total, max_output_bytes + 1),
            int(match.group(1)) if match else None,
        )


_VALIDATION_GUARD = r'''# Orion automatic-validation child guard.
import builtins
import io
import os
import pathlib
import socket
import subprocess
import sys

_TEMP = pathlib.Path(os.environ["ORION_VALIDATION_TEMP"]).resolve()
_WORKSPACE = pathlib.Path(os.environ["ORION_VALIDATION_WORKSPACE"]).resolve()
_READ_ROOTS = (_TEMP, _WORKSPACE, pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve())
_DENIED_PARTS = {".git", ".codex", ".agents", ".orion", "vault"}
_DENIED_NAMES = {
    ".env", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "vault.yaml", "vault.yml", "google-gmail-token.json", "google-calendar-token.json",
    "microsoft-mail-token.json", "microsoft-calendar-token.json",
}
_DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}

def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def _allowed(value):
    if isinstance(value, int) or os.path.normcase(str(value)) == os.path.normcase(os.devnull):
        return True
    try:
        return _inside(pathlib.Path(value).resolve(), _TEMP)
    except (TypeError, ValueError, OSError):
        return False

def _read_allowed(value):
    if isinstance(value, int) or os.path.normcase(str(value)) == os.path.normcase(os.devnull):
        return True
    try:
        path = pathlib.Path(value).resolve()
    except (TypeError, ValueError, OSError):
        return False
    if not any(_inside(path, root) for root in _READ_ROOTS):
        return False
    if _inside(path, _WORKSPACE):
        relative = path.relative_to(_WORKSPACE)
        if any(part.casefold() in _DENIED_PARTS for part in relative.parts[:-1]):
            return False
        if path.name.casefold() in _DENIED_NAMES or path.suffix.casefold() in _DENIED_SUFFIXES:
            return False
    return True

def _writing(mode):
    return any(flag in str(mode) for flag in ("w", "a", "x", "+"))

_open = builtins.open
def guarded_open(file, mode="r", *args, **kwargs):
    if _writing(mode) and not _allowed(file):
        raise PermissionError("Orion Tester is read-only outside its validation directory.")
    if not _writing(mode) and not _read_allowed(file):
        raise PermissionError("Orion Tester cannot read outside its approved workspace boundary.")
    return _open(file, mode, *args, **kwargs)
builtins.open = guarded_open
io.open = guarded_open

_os_open = os.open
def guarded_os_open(file, flags, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    if flags & write_flags and not _allowed(file):
        raise PermissionError("Orion Tester blocked a workspace write.")
    if not flags & write_flags and not _read_allowed(file):
        raise PermissionError("Orion Tester blocked an unrelated file read.")
    return _os_open(file, flags, *args, **kwargs)
os.open = guarded_os_open

def _guard_path(function):
    def guarded(path, *args, **kwargs):
        if not _allowed(path):
            raise PermissionError("Orion Tester blocked a filesystem mutation.")
        return function(path, *args, **kwargs)
    return guarded
for _name in ("mkdir", "makedirs", "remove", "unlink", "rmdir", "removedirs"):
    if hasattr(os, _name):
        setattr(os, _name, _guard_path(getattr(os, _name)))
for _name in ("chmod", "chown", "truncate"):
    if hasattr(os, _name):
        setattr(os, _name, _guard_path(getattr(os, _name)))

def _guard_two_paths(function):
    def guarded(source, destination, *args, **kwargs):
        if not _allowed(source) or not _allowed(destination):
            raise PermissionError("Orion Tester blocked a filesystem mutation.")
        return function(source, destination, *args, **kwargs)
    return guarded
for _name in ("rename", "replace"):
    if hasattr(os, _name):
        setattr(os, _name, _guard_two_paths(getattr(os, _name)))

def blocked_process(*args, **kwargs):
    raise PermissionError("Orion Tester blocked a nested command.")
subprocess.Popen = blocked_process
subprocess.run = blocked_process
subprocess.call = blocked_process
subprocess.check_call = blocked_process
subprocess.check_output = blocked_process
os.system = blocked_process

def blocked_network(*args, **kwargs):
    raise PermissionError("Orion Tester network access is disabled.")
socket.create_connection = blocked_network
def guarded_connect(self, *args, **kwargs):
    return blocked_network(*args, **kwargs)
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect
socket.socket.sendto = guarded_connect
'''


class AutomaticValidationService:
    """Plan and execute deterministic checks without allowing implementation edits."""

    def __init__(
        self,
        config_manager,
        *,
        snapshot_service: WorkspaceSnapshotService | None = None,
        runner: BoundedValidationRunner | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config_manager
        self.snapshots = snapshot_service or WorkspaceSnapshotService()
        self.runner = runner or BoundedValidationRunner()
        self._now = now or (lambda: datetime.now(timezone.utc))

    def protected_state(self, workspace: str | Path) -> dict[str, Any]:
        """Hash protected metadata without reading contents or touching Vault data."""
        root = Path(workspace).expanduser().resolve()
        directories: dict[str, Any] = {}
        for name in sorted(PROTECTED_WORKSPACE_PARTS):
            target = root / name
            digest = hashlib.sha256()
            count = 0
            if target.exists():
                root_details = target.lstat()
                digest.update(
                    f".\0{root_details.st_size}\0{root_details.st_mtime_ns}\0"
                    f"{stat.S_IFMT(root_details.st_mode)}\n".encode("utf-8")
                )
                for path in sorted(target.rglob("*"), key=lambda item: str(item).casefold()):
                    count += 1
                    if count > 50_000:
                        raise ValueError(f"Protected workspace metadata is too large to validate: {name}")
                    relative = path.relative_to(target).as_posix()
                    details = path.lstat()
                    digest.update(
                        f"{relative}\0{details.st_size}\0{details.st_mtime_ns}\0{stat.S_IFMT(details.st_mode)}\n".encode(
                            "utf-8", errors="surrogatepass"
                        )
                    )
            directories[name] = {
                "exists": target.exists(),
                "entries": count,
                "metadata_sha256": digest.hexdigest(),
            }
        return {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "workspace_root": str(root),
            "directories": directories,
        }

    def validate(self, request: ValidationRequest) -> ValidationAttempt:
        started_wall = self._timestamp()
        started = time.monotonic()
        checks: list[ValidationCheck] = []
        commands: list[ValidationCommand] = []
        diagnostics: list[str] = []
        workspace = Path(request.workspace.root).resolve()
        files_inspected: set[str] = set()

        if not _same_path(workspace, request.active_workspace):
            raise PermissionError("Validation workspace no longer matches the active approved workspace.")
        if not _same_path(request.changes.workspace_root, workspace):
            raise PermissionError("Validation change artifacts belong to a different workspace.")
        if not request.tester.available:
            return self.unavailable(request, request.tester.fallback_reason or "Tester assignment is unavailable.")
        engine = request.execution_engine
        if (
            not isinstance(engine, ExecutionEngine)
            or not engine.ready_for_implementation
            or engine.engine_id.casefold() != request.tester.actual_assignment.casefold()
        ):
            return self.unavailable(request, "Configured Tester execution engine is unavailable.")

        expected = {item.path: item for item in request.changes.changes}
        reported = {
            _relative_path(workspace, item.get("path", ""))
            for item in request.implementation_result.get("files_changed", [])
            if isinstance(item, dict)
        }
        files_inspected.update(expected)
        integrity_failures: list[str] = []
        if reported != set(expected):
            integrity_failures.append("Implementation result file list does not match the recorded snapshot.")
        for relative, change in expected.items():
            normalized = _relative_path(workspace, relative)
            path = workspace / normalized
            if change.kind == "deleted":
                if path.exists():
                    integrity_failures.append(f"Expected deleted file still exists: {normalized}")
            elif not path.is_file():
                integrity_failures.append(f"Expected {change.kind} file is missing: {normalized}")
            elif _sha256(path) != change.after_sha256:
                integrity_failures.append(f"Recorded implementation file changed before validation: {normalized}")
        current_changes, _ = self.snapshots.compare(
            request.baseline,
            request.blob_root,
            SnapshotLimits.from_config(self.config),
        )
        if current_changes.to_dict() != request.changes.to_dict():
            integrity_failures.append("Current workspace no longer matches the recorded implementation snapshot.")
        checks.append(ValidationCheck(
            "implementation_integrity",
            "Implementation artifact integrity",
            "failed" if integrity_failures else "passed",
            integrity_failures[0] if integrity_failures else f"Verified {len(expected)} recorded workspace change(s).",
            tuple(sorted(expected, key=str.casefold)),
        ))

        protected_problem = self._protected_problem(request.protected_baseline, workspace)
        checks.append(ValidationCheck(
            "protected_workspace",
            "Protected workspace metadata",
            "failed" if protected_problem else ("warning" if request.protected_baseline is None else "passed"),
            protected_problem or (
                "Protected metadata baseline is unavailable for this older run."
                if request.protected_baseline is None
                else "No writes were detected in .git, .codex, or .agents."
            ),
        ))

        changed_existing = [
            path for path, item in expected.items() if item.kind != "deleted" and (workspace / path).is_file()
        ]
        suffixes = {Path(path).suffix.casefold() for path in changed_existing}

        if ".json" in suffixes:
            checks.append(self._parse_json(workspace, changed_existing, files_inspected))
        if suffixes & {".yaml", ".yml"}:
            checks.append(self._parse_yaml(workspace, changed_existing, files_inspected))
        if ".toml" in suffixes:
            checks.append(self._parse_toml(workspace, changed_existing, files_inspected))
        if ".md" in suffixes:
            checks.extend(self._markdown(workspace, changed_existing, files_inspected))

        plan_text = "\n".join((request.plan_goal, *request.plan_steps)).casefold()
        if any(term in plan_text for term in ("documentation", "readme", ".md")):
            documentation_changed = any(Path(path).suffix.casefold() == ".md" for path in changed_existing)
            checks.append(ValidationCheck(
                "documentation_expected",
                "Required documentation change",
                "passed" if documentation_changed else "warning",
                "The approved plan's documentation requirement was reflected in the change set."
                if documentation_changed else "The approved plan named documentation, but no Markdown file changed.",
                tuple(sorted(path for path in changed_existing if Path(path).suffix.casefold() == ".md")),
            ))

        python_files = sorted(
            (path for path in changed_existing if Path(path).suffix.casefold() == ".py"),
            key=str.casefold,
        )
        with tempfile.TemporaryDirectory(prefix="orion-validation-") as temporary:
            temp_root = Path(temporary).resolve()
            if python_files:
                files_inspected.update(python_files)
                compile_results = []
                for offset in range(0, len(python_files), 25):
                    chunk = python_files[offset:offset + 25]
                    process = self._run_command(
                        (sys.executable, "-m", "py_compile", *chunk),
                        f"python -m py_compile <changed-python-files {offset + 1}-{offset + len(chunk)}>",
                        workspace,
                        temp_root,
                    )
                    commands.append(process[0])
                    compile_results.append(process)
                compile_status = (
                    "error" if any(item[1] == "error" for item in compile_results)
                    else "failed" if any(item[1] == "failed" for item in compile_results)
                    else "passed"
                )
                checks.append(ValidationCheck(
                    "python_compile",
                    "Python compile",
                    compile_status,
                    (
                        f"Compiled {len(python_files)} changed Python file(s) in "
                        f"{len(compile_results)} bounded command(s)."
                        if compile_status == "passed"
                        else next(item[2] for item in compile_results if item[1] != "passed")
                    ),
                    tuple(python_files),
                ))
                test_files, full_suite, selection_reason = self._python_test_plan(workspace, python_files)
                if test_files or full_suite:
                    if full_suite:
                        argv = (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")
                        display = "python -m unittest discover -s tests -p test_*.py"
                        check_id = "python_full_tests"
                        name = "Full Python test discovery"
                    else:
                        modules = tuple(Path(item).with_suffix("").as_posix().replace("/", ".") for item in test_files)
                        argv = (sys.executable, "-m", "unittest", *modules)
                        display = "python -m unittest " + " ".join(modules)
                        check_id = "python_targeted_tests"
                        name = "Targeted Python tests"
                        files_inspected.update(test_files)
                    process = self._run_command(argv, display, workspace, temp_root)
                    commands.append(process[0])
                    summary = process[2]
                    if process[3] is not None:
                        summary = f"{process[3]} test(s); {summary}"
                    checks.append(ValidationCheck(check_id, name, process[1], summary, tuple(test_files)))
                    if not full_suite:
                        checks.append(ValidationCheck(
                            "python_full_suite",
                            "Full Python test suite",
                            "skipped",
                            "Targeted tests were sufficient for the changed modules.",
                        ))
                    elif selection_reason:
                        diagnostics.append(selection_reason)
                else:
                    checks.append(ValidationCheck(
                        "python_tests",
                        "Python tests",
                        "warning",
                        selection_reason or "No relevant Python tests were discovered.",
                    ))

        final_changes, _ = self.snapshots.compare(
            request.baseline,
            request.blob_root,
            SnapshotLimits.from_config(self.config),
        )
        mutation_problem = final_changes.to_dict() != request.changes.to_dict()
        final_protected_problem = self._protected_problem(request.protected_baseline, workspace)
        checks.append(ValidationCheck(
            "tester_read_only",
            "Tester read-only boundary",
            "failed" if mutation_problem or final_protected_problem else "passed",
            "Tester commands altered workspace state."
            if mutation_problem or final_protected_problem
            else "Validation left implementation files and protected metadata unchanged.",
        ))

        statuses = {item.status for item in checks}
        if "error" in statuses:
            status = "error"
        elif "failed" in statuses:
            status = "failed"
        elif "warning" in statuses:
            status = "warnings"
        else:
            status = "passed"
        return self._attempt(
            request,
            status,
            checks,
            commands,
            files_inspected,
            diagnostics,
            started_wall,
            started,
        )

    def unavailable(self, request: ValidationRequest, reason: str) -> ValidationAttempt:
        started = self._timestamp()
        return ValidationAttempt.from_value({
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "team_task_id": request.team_task_id,
            "approval_id": request.approval_id,
            "workspace_root": request.workspace.root,
            "tester_requested": request.tester.requested_assignment,
            "tester_resolved": request.tester.actual_assignment,
            "execution_engine": "" if request.execution_engine is None else request.execution_engine.engine_id,
            "fallback": request.tester.fallback,
            "fallback_reason": request.tester.fallback_reason,
            "status": "unavailable",
            "checks": [],
            "commands": [],
            "checks_passed": [],
            "checks_failed": [],
            "warnings": [],
            "skipped_checks": ["Automatic validation — Tester unavailable"],
            "files_inspected": [],
            "started_at": started,
            "completed_at": started,
            "duration_seconds": 0.0,
            "safe_diagnostics": [_safe_text(reason, maximum=1_000, required=True)],
            "artifact_paths": list(request.artifact_paths),
        })

    def error(self, request: ValidationRequest, reason: str) -> ValidationAttempt:
        started = self._timestamp()
        return ValidationAttempt.from_value({
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "team_task_id": request.team_task_id,
            "approval_id": request.approval_id,
            "workspace_root": request.workspace.root,
            "tester_requested": request.tester.requested_assignment,
            "tester_resolved": request.tester.actual_assignment,
            "execution_engine": "" if request.execution_engine is None else request.execution_engine.engine_id,
            "fallback": request.tester.fallback,
            "fallback_reason": request.tester.fallback_reason,
            "status": "error",
            "checks": [],
            "commands": [],
            "checks_passed": [],
            "checks_failed": ["Automatic validation error"],
            "warnings": [],
            "skipped_checks": [],
            "files_inspected": [],
            "started_at": started,
            "completed_at": started,
            "duration_seconds": 0.0,
            "safe_diagnostics": [_safe_text(reason, maximum=1_000, required=True)],
            "artifact_paths": list(request.artifact_paths),
        })

    def _attempt(
        self,
        request: ValidationRequest,
        status: str,
        checks: list[ValidationCheck],
        commands: list[ValidationCommand],
        files_inspected: set[str],
        diagnostics: list[str],
        started_wall: str,
        started: float,
    ) -> ValidationAttempt:
        return ValidationAttempt.from_value({
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "team_task_id": request.team_task_id,
            "approval_id": request.approval_id,
            "workspace_root": request.workspace.root,
            "tester_requested": request.tester.requested_assignment,
            "tester_resolved": request.tester.actual_assignment,
            "execution_engine": request.execution_engine.engine_id if request.execution_engine else "",
            "fallback": request.tester.fallback,
            "fallback_reason": request.tester.fallback_reason,
            "status": status,
            "checks": [item.to_dict() for item in checks],
            "commands": [item.to_dict() for item in commands],
            "checks_passed": [item.name for item in checks if item.status == "passed"],
            "checks_failed": [item.name for item in checks if item.status in {"failed", "error"}],
            "warnings": [item.summary for item in checks if item.status == "warning"],
            "skipped_checks": [f"{item.name} — {item.summary}" for item in checks if item.status == "skipped"],
            "files_inspected": sorted(files_inspected, key=str.casefold),
            "started_at": started_wall,
            "completed_at": self._timestamp(),
            "duration_seconds": round(time.monotonic() - started, 6),
            "safe_diagnostics": diagnostics,
            "artifact_paths": list(request.artifact_paths),
        })

    def _run_command(
        self,
        argv: tuple[str, ...],
        display: str,
        workspace: Path,
        temp_root: Path,
    ) -> tuple[ValidationCommand, str, str, int | None]:
        try:
            result = self.runner.run(
                argv,
                cwd=workspace,
                temp_root=temp_root,
                timeout=self._command_timeout(),
                max_output_bytes=self._max_output_bytes(),
            )
        except (OSError, PermissionError, ValueError) as exc:
            command = ValidationCommand(display, None, False, 0.0, "Validation command could not start safely.")
            return command, "error", _safe_text(type(exc).__name__, required=True), None
        if result.timed_out:
            command = ValidationCommand(display, None, True, result.duration_seconds, "Validation command reached its timeout.")
            return command, "error", "Validation command timed out.", result.test_count
        if result.output_bytes > self._max_output_bytes():
            command = ValidationCommand(
                display,
                result.exit_code,
                False,
                result.duration_seconds,
                "Validation output exceeded Orion's capture limit and was discarded.",
            )
            return command, "error", "Validation output exceeded the bounded capture limit.", result.test_count
        success = result.exit_code == 0
        command = ValidationCommand(
            display,
            result.exit_code,
            False,
            result.duration_seconds,
            f"Command {'passed' if success else 'failed'} with exit code {result.exit_code}; raw output was discarded.",
        )
        return command, "passed" if success else "failed", command.safe_summary, result.test_count

    @staticmethod
    def _parse_json(workspace: Path, paths: list[str], inspected: set[str]) -> ValidationCheck:
        selected = [item for item in paths if Path(item).suffix.casefold() == ".json"]
        failures = []
        for relative in selected:
            inspected.add(relative)
            try:
                json.loads((workspace / relative).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                failures.append(f"{relative} line {exc.lineno}, column {exc.colno}")
            except (OSError, UnicodeError, ValueError):
                failures.append(f"{relative} could not be parsed safely")
        return ValidationCheck(
            "json_syntax", "JSON syntax", "failed" if failures else "passed",
            failures[0] if failures else f"Parsed {len(selected)} changed JSON file(s).", tuple(selected)
        )

    @staticmethod
    def _parse_yaml(workspace: Path, paths: list[str], inspected: set[str]) -> ValidationCheck:
        selected = [item for item in paths if Path(item).suffix.casefold() in {".yaml", ".yml"}]
        failures = []
        for relative in selected:
            inspected.add(relative)
            try:
                yaml.safe_load((workspace / relative).read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                location = f" line {mark.line + 1}, column {mark.column + 1}" if mark else ""
                failures.append(f"{relative}{location}")
            except (OSError, UnicodeError, ValueError):
                failures.append(f"{relative} could not be parsed safely")
        return ValidationCheck(
            "yaml_syntax", "YAML syntax", "failed" if failures else "passed",
            failures[0] if failures else f"Parsed {len(selected)} changed YAML file(s).", tuple(selected)
        )

    @staticmethod
    def _parse_toml(workspace: Path, paths: list[str], inspected: set[str]) -> ValidationCheck:
        selected = [item for item in paths if Path(item).suffix.casefold() == ".toml"]
        try:
            import tomllib
        except ImportError:
            return ValidationCheck("toml_syntax", "TOML syntax", "warning", "TOML parser is unavailable.", tuple(selected))
        failures = []
        for relative in selected:
            inspected.add(relative)
            try:
                tomllib.loads((workspace / relative).read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                line = re.search(r"line\s+(\d+)", str(exc), re.IGNORECASE)
                failures.append(f"{relative}{' line ' + line.group(1) if line else ''}")
            except (OSError, UnicodeError, ValueError):
                failures.append(f"{relative} could not be parsed safely")
        return ValidationCheck(
            "toml_syntax", "TOML syntax", "failed" if failures else "passed",
            failures[0] if failures else f"Parsed {len(selected)} changed TOML file(s).", tuple(selected)
        )

    @staticmethod
    def _markdown(workspace: Path, paths: list[str], inspected: set[str]) -> list[ValidationCheck]:
        selected = [item for item in paths if Path(item).suffix.casefold() == ".md"]
        structure_failures: list[str] = []
        missing_links: list[str] = []
        for relative in selected:
            inspected.add(relative)
            try:
                text = (workspace / relative).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                structure_failures.append(f"{relative} could not be read safely")
                continue
            fences: list[str] = []
            previous_level = 0
            for line_number, line in enumerate(text.splitlines(), start=1):
                fence = MARKDOWN_FENCE_PATTERN.match(line)
                if fence:
                    marker = fence.group(1)[0]
                    if fences and fences[-1] == marker:
                        fences.pop()
                    elif not fences:
                        fences.append(marker)
                    continue
                if fences:
                    continue
                heading = re.match(r"^(#{1,6})\s+(.*)$", line)
                if heading:
                    level = len(heading.group(1))
                    if not heading.group(2).strip():
                        structure_failures.append(f"{relative} line {line_number} has an empty heading")
                    if previous_level and level > previous_level + 1:
                        structure_failures.append(f"{relative} line {line_number} skips a heading level")
                    previous_level = level
            if fences:
                structure_failures.append(f"{relative} has an unclosed fenced code block")
            for target in MARKDOWN_LINK_PATTERN.findall(text):
                value = target.strip().split(maxsplit=1)[0].strip("<>")
                if not value or value.startswith(("#", "http://", "https://", "mailto:", "data:")):
                    continue
                link_path = value.split("#", 1)[0]
                if not link_path:
                    continue
                candidate = ((workspace / relative).parent / link_path).resolve()
                try:
                    candidate.relative_to(workspace)
                except ValueError:
                    missing_links.append(f"{relative} -> outside workspace")
                    continue
                if not candidate.exists():
                    missing_links.append(f"{relative} -> {link_path}")
        return [
            ValidationCheck(
                "markdown_structure", "Markdown structure", "failed" if structure_failures else "passed",
                structure_failures[0] if structure_failures else f"Validated {len(selected)} Markdown file(s).",
                tuple(selected),
            ),
            ValidationCheck(
                "markdown_links", "Markdown local links", "warning" if missing_links else "passed",
                missing_links[0] if missing_links else "Local relative links resolve inside the workspace.",
                tuple(selected),
            ),
        ]

    @staticmethod
    def _python_test_plan(workspace: Path, python_files: list[str]) -> tuple[list[str], bool, str]:
        tests_root = workspace / "tests"
        if not tests_root.is_dir():
            return [], False, "No tests directory is available for changed Python files."
        all_tests = sorted(
            (path.relative_to(workspace).as_posix() for path in tests_root.rglob("test_*.py") if path.is_file()),
            key=str.casefold,
        )
        if not all_tests:
            return [], False, "No Python test files were discovered."
        if any(path.casefold() in BROAD_PYTHON_PATHS for path in python_files):
            return all_tests, True, "Broad shared infrastructure changed; selected full test discovery."
        targets: set[str] = set()
        for relative in python_files:
            path = Path(relative)
            if path.name.startswith("test_") and "tests" in {part.casefold() for part in path.parts}:
                targets.add(relative)
            stem = path.stem.casefold()
            for test_path in all_tests:
                test_stem = Path(test_path).stem.casefold()
                if test_stem == f"test_{stem}" or stem in test_stem:
                    targets.add(test_path)
        if targets:
            return sorted(targets, key=str.casefold), False, ""
        return all_tests, True, "No targeted tests matched; selected full test discovery."

    def _protected_problem(self, baseline: Mapping[str, Any] | None, workspace: Path) -> str:
        if baseline is None:
            return ""
        try:
            if baseline.get("schema_version") != VALIDATION_SCHEMA_VERSION:
                return "Protected metadata baseline has an unsupported schema."
            if not _same_path(baseline.get("workspace_root", ""), workspace):
                return "Protected metadata baseline belongs to another workspace."
            current = self.protected_state(workspace)
            if current.get("directories") != baseline.get("directories"):
                return "Unexpected write detected in .git, .codex, or .agents."
        except (OSError, TypeError, ValueError):
            return "Protected workspace metadata could not be validated safely."
        return ""

    def validation_log(self, attempt: ValidationAttempt) -> str:
        lines = [
            f"Validation {attempt.attempt_id}",
            f"Status: {attempt.status.upper()}",
            f"Tester: {attempt.tester_resolved or attempt.tester_requested}",
        ]
        for check in attempt.checks:
            lines.append(f"{check.status.upper():7} {check.name}: {check.summary}")
        for diagnostic in attempt.safe_diagnostics:
            lines.append(f"INFO    {diagnostic}")
        return "\n".join(_safe_text(line, maximum=1_500, required=True) for line in lines) + "\n"

    def _command_timeout(self) -> int:
        value = self.config.get("team.validation.command_timeout_seconds", 120)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("Validation command timeout must be finite.")
        result = int(value)
        if result < 1 or result > 900:
            raise ValueError("Validation command timeout must be between 1 and 900 seconds.")
        return result

    def _max_output_bytes(self) -> int:
        value = self.config.get("team.validation.max_output_bytes", 250_000)
        if isinstance(value, bool) or not isinstance(value, int) or not 1_000 <= value <= 5_000_000:
            raise ValueError("Validation output limit must be between 1,000 and 5,000,000 bytes.")
        return value

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")
