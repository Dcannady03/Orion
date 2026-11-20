"""Approval-based shell execution for Orion.

Runs one shell command at a time through the platform shell — PowerShell
on Windows, ``sh`` on POSIX — with an explicit argument list and no
``shell=True``. The service itself only runs; approval and the
``shell.arbitrary_commands`` permission gate live in the application
handler, which calls this service only after both have passed.

Every execution is bounded: a timeout (default 30 seconds, hard cap 300)
kills the process and reports the timeout as the result, and each output
stream is truncated to the last 8,192 characters (line-preserving, like
``docker logs``). Standard input is closed; there is no interactive
session.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 300.0
MAX_OUTPUT_CHARS = 8_192
SHELL_CONFIG_KEY = "shell.arbitrary_commands"

_WINDOWS_NAMES = frozenset({"nt", "windows", "win32"})


class ShellUnavailableError(Exception):
    """The shell could not be started. Fail closed; never guess at a result."""


def _is_windows(platform_name: str | None) -> bool:
    return (platform_name or os.name).strip().lower() in _WINDOWS_NAMES


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate(text: str) -> tuple[str, bool]:
    """Keep the last MAX_OUTPUT_CHARS of text, preserving whole lines.

    Follows the docker logs precedent: the newest output survives. When a
    single line alone exceeds the budget, its tail is kept so the result
    is never silently empty.
    """
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    lines = text.splitlines()
    kept: list[str] = []
    budget = MAX_OUTPUT_CHARS
    for line in reversed(lines):
        if budget - (len(line) + 1) < 0:
            break
        kept.append(line)
        budget -= len(line) + 1
    if not kept and lines:
        return lines[-1][-MAX_OUTPUT_CHARS:], True
    return "\n".join(reversed(kept)), True


def _default_runner(
    argv: list[str], timeout: float, cwd: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@dataclass(frozen=True)
class ShellPlan:
    """One resolved shell command, not yet run.

    The plan is the approval preview: it shows the exact command, the
    shell that will run it, the working directory, and the effective
    timeout, so approval is informed.
    """

    command: str
    argv: tuple[str, ...]
    shell: str
    shell_binary: str
    cwd: str
    timeout: float

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "argv": list(self.argv),
            "shell": self.shell,
            "shell_binary": self.shell_binary,
            "cwd": self.cwd,
            "timeout": self.timeout,
        }


@dataclass(frozen=True)
class ShellExecution:
    """The completed outcome of one shell command."""

    command: str
    argv: tuple[str, ...]
    shell: str
    shell_binary: str
    cwd: str
    timeout: float
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "argv": list(self.argv),
            "shell": self.shell,
            "shell_binary": self.shell_binary,
            "cwd": self.cwd,
            "timeout": self.timeout,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "duration_seconds": round(self.duration_seconds, 3),
        }


class ShellService:
    """Run one shell command through the platform shell.

    The runner is injectable so tests can run without a live shell. The
    platform is injectable too, so the Windows and POSIX argument lists
    are both covered by tests on any host.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., Any] | None = None,
        default_cwd: str | Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self._run = runner or _default_runner
        self._default_cwd = Path(default_cwd) if default_cwd is not None else None
        self._platform_name = platform_name

    def plan(
        self,
        command: str,
        *,
        timeout: float | str | None = None,
        cwd: str | Path | None = None,
    ) -> ShellPlan:
        """Resolve a command into an exact, runnable plan without running it."""
        normalized = str(command or "").strip()
        if not normalized:
            raise ValueError("A shell command is required.")
        effective_timeout = self._resolve_timeout(timeout)
        directory = self._resolve_cwd(cwd)
        shell_binary, argv = self._shell_argv(normalized)
        return ShellPlan(
            command=normalized,
            argv=argv,
            shell="powershell" if _is_windows(self._platform_name) else "sh",
            shell_binary=shell_binary,
            cwd=str(directory),
            timeout=effective_timeout,
        )

    def execute(
        self,
        command: str,
        *,
        timeout: float | str | None = None,
        cwd: str | Path | None = None,
    ) -> ShellExecution:
        """Run one command and return its outcome. Never raises for a bad exit.

        A timeout kills the process and comes back as a timed-out result;
        a missing shell binary raises ShellUnavailableError.
        """
        plan = self.plan(command, timeout=timeout, cwd=cwd)
        started = time.monotonic()
        try:
            result = self._run(list(plan.argv), plan.timeout, plan.cwd)
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            stdout, stdout_truncated = _truncate(_coerce_text(exc.stdout))
            stderr, stderr_truncated = _truncate(_coerce_text(exc.stderr))
            return ShellExecution(
                command=plan.command,
                argv=plan.argv,
                shell=plan.shell,
                shell_binary=plan.shell_binary,
                cwd=plan.cwd,
                timeout=plan.timeout,
                exit_code=None,
                timed_out=True,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                duration_seconds=duration,
            )
        except FileNotFoundError:
            raise ShellUnavailableError(
                f"The {plan.shell} shell ({plan.shell_binary}) was not "
                "found on this machine."
            ) from None
        except OSError as exc:
            raise ShellUnavailableError(
                f"The shell could not be started: {exc}"
            ) from None
        duration = time.monotonic() - started
        stdout, stdout_truncated = _truncate(_coerce_text(result.stdout))
        stderr, stderr_truncated = _truncate(_coerce_text(result.stderr))
        return ShellExecution(
            command=plan.command,
            argv=plan.argv,
            shell=plan.shell,
            shell_binary=plan.shell_binary,
            cwd=plan.cwd,
            timeout=plan.timeout,
            exit_code=result.returncode,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_seconds=duration,
        )

    def _shell_argv(self, command: str) -> tuple[str, tuple[str, ...]]:
        if _is_windows(self._platform_name):
            # Honor the same ORION_POWERSHELL override the execution
            # engines use for PowerShell scripts (PowerShell 7 vs the
            # inbox Windows PowerShell).
            binary = os.environ.get("ORION_POWERSHELL", "").strip() or "powershell.exe"
            return binary, (
                binary,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            )
        return "sh", ("sh", "-c", command)

    @staticmethod
    def _resolve_timeout(timeout: float | str | None) -> float:
        if timeout is None:
            return DEFAULT_TIMEOUT_SECONDS
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            raise ValueError(
                "shell --timeout must be a number of seconds."
            ) from None
        if value <= 0:
            raise ValueError("shell --timeout must be greater than zero.")
        return min(value, MAX_TIMEOUT_SECONDS)

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is not None:
            candidate = Path(cwd)
        elif self._default_cwd is not None:
            candidate = self._default_cwd
        else:
            candidate = Path.cwd()
        try:
            resolved = candidate.expanduser().resolve()
        except OSError as exc:
            raise ValueError(
                f"Working directory could not be resolved: {candidate} ({exc})"
            ) from None
        if not resolved.is_dir():
            raise ValueError(
                f"Working directory is not a directory: {resolved}"
            )
        return resolved
