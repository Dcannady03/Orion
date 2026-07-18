"""Read-only host capability detection for Orion execution engines."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


ENGINE_STATUS_INSTALLED = "installed"
ENGINE_STATUS_NOT_INSTALLED = "not_installed"
ENGINE_STATUS_READY = "ready"
ENGINE_STATUS_UNAVAILABLE = "unavailable"


def resolve_codex_executable(
    *,
    which: Callable[[str], str | None] | None = None,
    platform_name: str | None = None,
) -> Path | None:
    """Resolve the exact local Codex CLI entry point in platform-safe order."""

    resolver = which or shutil.which
    platform_value = (platform_name or os.name).strip().lower()
    candidates = (
        ("codex.cmd", "codex.exe", "codex")
        if platform_value in {"nt", "windows"}
        else ("codex",)
    )
    for candidate in candidates:
        try:
            resolved = resolver(candidate)
        except (OSError, TypeError, ValueError):
            continue
        if resolved:
            return Path(resolved).expanduser()
    return None


@dataclass(frozen=True)
class ExecutionEngine:
    engine_id: str
    name: str
    status: str
    installed: bool
    cli_support: bool
    implementation_supported: bool
    executable: str = ""
    reason: str = ""

    @property
    def ready_for_implementation(self) -> bool:
        return self.installed and self.cli_support and self.implementation_supported


class ExecutionEngineUnavailable(RuntimeError):
    """Raised before execution when Orion has no usable implementation adapter."""


class ExecutionEngineService:
    """Detect desktop applications, runnable CLIs, and the Python runtime."""

    ENGINE_ORDER = ("codex", "chatgpt_desktop", "claude_code", "gemini_cli", "python")

    def __init__(
        self,
        config_manager,
        application_catalog=None,
        *,
        which: Callable[[str], str | None] | None = None,
        probe: Callable[[str], bool] | None = None,
        environment: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.config = config_manager
        self.application_catalog = application_catalog
        self._which = which or shutil.which
        self._probe = probe or self._probe_cli
        self._environment = dict(os.environ if environment is None else environment)
        self._platform_name = platform_name or platform.system()
        self._python_executable = python_executable or sys.executable

    @property
    def selected_engine_id(self) -> str:
        value = str(self.config.get("execution.default_engine", "codex")).strip().lower()
        return value or "codex"

    def status(self) -> tuple[ExecutionEngine, ...]:
        engines = {
            "codex": self._codex_engine(),
            "chatgpt_desktop": self._desktop_engine(),
            "claude_code": self._cli_engine(
                "claude_code", "Claude Code", ("claude",), implementation_supported=False
            ),
            "gemini_cli": self._cli_engine(
                "gemini_cli", "Gemini CLI", ("gemini",), implementation_supported=False
            ),
            "python": self._python_engine(),
        }
        return tuple(engines[engine_id] for engine_id in self.ENGINE_ORDER)

    def engine(self, engine_id: str) -> ExecutionEngine:
        normalized = str(engine_id).strip().lower()
        if normalized == "codex":
            return self._codex_engine()
        if normalized == "chatgpt_desktop":
            return self._desktop_engine()
        if normalized == "claude_code":
            return self._cli_engine("claude_code", "Claude Code", ("claude",), implementation_supported=False)
        if normalized == "gemini_cli":
            return self._cli_engine("gemini_cli", "Gemini CLI", ("gemini",), implementation_supported=False)
        if normalized == "python":
            return self._python_engine()
        raise ValueError(f"Execution engine is not recognized: {engine_id}")

    def selected_engine(self) -> ExecutionEngine | None:
        try:
            engine = self.engine(self.selected_engine_id)
        except ValueError:
            return None
        return engine if engine.ready_for_implementation else None

    def codex_available(self) -> bool:
        return self.engine("codex").ready_for_implementation

    def require_codex(self) -> ExecutionEngine:
        engine = self.engine("codex")
        if not engine.ready_for_implementation:
            raise ExecutionEngineUnavailable("No execution engine is currently available.")
        return engine

    def resolve_codex_executable(self) -> Path | None:
        return resolve_codex_executable(
            which=self._which,
            platform_name=self._platform_name,
        )

    def _codex_engine(self) -> ExecutionEngine:
        resolved = self.resolve_codex_executable()
        return self._cli_engine(
            "codex",
            "Codex CLI",
            (),
            implementation_supported=True,
            resolved_executable=resolved,
        )

    def _cli_engine(
        self,
        engine_id: str,
        name: str,
        commands: tuple[str, ...],
        *,
        implementation_supported: bool,
        resolved_executable: str | Path | None = None,
    ) -> ExecutionEngine:
        executable = str(resolved_executable) if resolved_executable else ""
        for command in commands:
            try:
                candidate = self._which(command)
            except (OSError, TypeError, ValueError):
                continue
            if candidate:
                executable = str(Path(candidate).expanduser())
                break
        if not executable:
            return ExecutionEngine(
                engine_id, name, ENGINE_STATUS_NOT_INSTALLED, False, True,
                implementation_supported, reason="command_not_found",
            )
        try:
            runnable = bool(self._probe(executable))
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            runnable = False
        if not runnable:
            return ExecutionEngine(
                engine_id, name, ENGINE_STATUS_NOT_INSTALLED, False, True,
                implementation_supported, executable=executable,
                reason="command_not_runnable",
            )
        return ExecutionEngine(
            engine_id, name, ENGINE_STATUS_INSTALLED, True, True,
            implementation_supported, executable=executable,
        )

    def _desktop_engine(self) -> ExecutionEngine:
        installed = self._chatgpt_desktop_installed()
        return ExecutionEngine(
            "chatgpt_desktop",
            "ChatGPT Desktop",
            ENGINE_STATUS_INSTALLED if installed else ENGINE_STATUS_NOT_INSTALLED,
            installed,
            False,
            False,
            reason="desktop_has_no_cli_adapter" if installed else "application_not_found",
        )

    def _python_engine(self) -> ExecutionEngine:
        executable = Path(self._python_executable).expanduser()
        try:
            ready = executable.is_file()
        except OSError:
            ready = False
        return ExecutionEngine(
            "python",
            "Python Executor",
            ENGINE_STATUS_READY if ready else ENGINE_STATUS_UNAVAILABLE,
            ready,
            True,
            False,
            executable=str(executable) if ready else "",
            reason="" if ready else "python_runtime_not_found",
        )

    def _chatgpt_desktop_installed(self) -> bool:
        if self.application_catalog is not None:
            try:
                names = {
                    str(application.name).strip().lower()
                    for application in self.application_catalog.applications()
                }
            except (AttributeError, OSError, TypeError, ValueError):
                names = set()
            if any(name == "chatgpt" or name.startswith("chatgpt ") for name in names):
                return True

        candidates: list[Path] = []
        local_app_data = self._environment.get("LOCALAPPDATA")
        app_data = self._environment.get("APPDATA")
        program_data = self._environment.get("PROGRAMDATA")
        if local_app_data:
            local = Path(local_app_data)
            candidates.extend((
                local / "Programs" / "ChatGPT" / "ChatGPT.exe",
                local / "Microsoft" / "WindowsApps" / "ChatGPT.exe",
            ))
            packages = local / "Packages"
            try:
                if packages.is_dir() and any(
                    path.is_dir() and "chatgpt" in path.name.lower()
                    for path in packages.iterdir()
                ):
                    return True
            except OSError:
                pass
        if app_data:
            candidates.append(
                Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "ChatGPT.lnk"
            )
        if program_data:
            candidates.append(
                Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "ChatGPT.lnk"
            )
        if self._platform_name.lower() == "darwin":
            candidates.extend((Path("/Applications/ChatGPT.app"), Path.home() / "Applications/ChatGPT.app"))
        if any(path.exists() for path in candidates):
            return True

        # OpenAI's Windows desktop package may expose a bundled, non-runnable
        # codex.exe beneath WindowsApps without creating a ChatGPT shortcut.
        # That is evidence of the desktop application, not CLI support.
        try:
            bundled_alias = self._which("codex")
        except (OSError, TypeError, ValueError):
            bundled_alias = None
        if bundled_alias:
            alias = str(bundled_alias).replace("/", "\\").lower()
            if "\\windowsapps\\" in alias and (
                "\\openai.codex_" in alias or "\\openai.chatgpt" in alias
            ):
                return True
        return False

    @staticmethod
    def _probe_cli(executable: str) -> bool:
        try:
            completed = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0
