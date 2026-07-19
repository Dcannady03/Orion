"""Read-only host capability detection for Orion execution engines."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


ENGINE_STATUS_INSTALLED = "installed"
ENGINE_STATUS_NOT_INSTALLED = "not_installed"
ENGINE_STATUS_READY = "ready"
ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE = "installed_not_executable"
ENGINE_STATUS_DETECTION_ERROR = "detection_error"
ENGINE_STATUS_UNSUPPORTED_CLI = "unsupported_as_cli"
# Backward-compatible name used by Python runtime callers.
ENGINE_STATUS_UNAVAILABLE = ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE

WINDOWS_NAMES = frozenset({"nt", "windows", "win32"})
WINDOWS_WRAPPER_PRIORITY = {".cmd": 0, ".exe": 1, "": 2, ".ps1": 3}
FAILED_VERSION_MARKERS = (
    "not recognized as the name",
    "is not recognized as an internal or external command",
    "command not found",
    "no such file or directory",
    "cannot be loaded because",
)


def _is_windows(platform_name: str | None) -> bool:
    return (platform_name or os.name).strip().lower() in WINDOWS_NAMES


def safe_executable_command(
    executable: str | Path,
    arguments: Sequence[str],
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Build a fixed-argument command for native and Windows script wrappers.

    Batch and PowerShell shims cannot be passed to ``CreateProcess`` as native
    executables on every supported Python/Windows combination. Orion invokes the
    appropriate interpreter directly, with an argument list and ``shell=False``.
    """

    path = Path(executable).expanduser()
    args = tuple(str(value) for value in arguments)
    if not _is_windows(platform_name):
        return (str(path), *args)
    suffix = path.suffix.lower()
    values = os.environ if environment is None else environment
    if suffix == ".cmd":
        comspec = str(values.get("COMSPEC", "")).strip()
        if not comspec:
            system_root = str(values.get("SYSTEMROOT", values.get("WINDIR", ""))).strip()
            comspec = str(Path(system_root) / "System32" / "cmd.exe") if system_root else "cmd.exe"
        return (comspec, "/d", "/s", "/c", str(path), *args)
    if suffix == ".ps1":
        powershell = str(values.get("ORION_POWERSHELL", "")).strip() or "powershell.exe"
        return (
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            *args,
        )
    return (str(path), *args)


def executable_environment(
    executable: str | Path,
    environment: Mapping[str, str],
    *,
    platform_name: str | None = None,
) -> dict[str, str]:
    """Return a copy with a conventional Node runtime visible to npm shims."""

    result = dict(environment)
    if not _is_windows(platform_name):
        return result
    path = Path(executable).expanduser()
    app_data = str(result.get("APPDATA", "")).strip()
    if not app_data or os.path.normcase(str(path.parent)) != os.path.normcase(str(Path(app_data) / "npm")):
        return result
    program_files = str(result.get("PROGRAMFILES", "")).strip()
    if not program_files:
        system_drive = str(result.get("SYSTEMDRIVE", "C:")).strip() or "C:"
        program_files = str(Path(f"{system_drive}\\") / "Program Files")
    node_directory = Path(program_files) / "nodejs"
    try:
        node_exists = (node_directory / "node.exe").is_file()
    except OSError:
        node_exists = False
    if not node_exists:
        return result
    path_parts = [item for item in str(result.get("PATH", "")).split(os.pathsep) if item]
    if not any(os.path.normcase(item) == os.path.normcase(str(node_directory)) for item in path_parts):
        path_parts.append(str(node_directory))
        result["PATH"] = os.pathsep.join(path_parts)
    return result


@dataclass(frozen=True)
class ExecutableResolution:
    executable: str = ""
    source: str = ""
    path_visible: bool = False
    runnable: bool = False
    version: str = ""
    diagnostic: str = "command_not_found"
    detection_error: bool = False


class ExecutableResolver:
    """Resolve and probe CLI entry points with isolated Windows behavior."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] | None = None,
        run: Callable[..., object] | None = None,
        probe: Callable[[str], bool] | None = None,
        environment: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        path_exists: Callable[[Path], bool] | None = None,
        probe_timeout: float = 3.0,
    ) -> None:
        self._which = which or shutil.which
        self._run = run or subprocess.run
        self._probe_override = probe
        self._environment = dict(os.environ if environment is None else environment)
        self._platform_name = platform_name or platform.system()
        self._path_exists = path_exists or Path.is_file
        self._probe_timeout = max(0.1, min(float(probe_timeout), 10.0))

    def resolve(self, command: str) -> ExecutableResolution:
        normalized = str(command).strip()
        if not normalized or any(character.isspace() for character in normalized):
            return ExecutableResolution(diagnostic="invalid_command", detection_error=True)

        candidates, lookup_error = self._path_candidates(normalized)
        path_result: ExecutableResolution | None = None
        if candidates:
            path_result = self._probe_candidates(candidates, source="PATH", path_visible=True)
            if path_result.runnable:
                return path_result

        if _is_windows(self._platform_name):
            appdata_candidates = self._directory_candidates(
                normalized,
                Path(self._environment["APPDATA"]) / "npm"
                if self._environment.get("APPDATA")
                else None,
            )
            if appdata_candidates:
                return self._probe_candidates(
                    appdata_candidates,
                    source="npm global directory (%APPDATA%\\npm)",
                    path_visible=False,
                )

            npm_candidates, npm_error = self._npm_prefix_candidates(normalized)
            if npm_candidates:
                return self._probe_candidates(
                    npm_candidates,
                    source="npm global directory (npm prefix -g)",
                    path_visible=False,
                )
            if npm_error:
                return path_result or ExecutableResolution(
                    diagnostic=npm_error,
                    detection_error=True,
                )

        if path_result is not None:
            return path_result
        if lookup_error:
            return ExecutableResolution(
                diagnostic="path_lookup_failed",
                detection_error=True,
            )
        return ExecutableResolution()

    def resolve_path(self, command: str) -> Path | None:
        resolution = self.resolve(command)
        return Path(resolution.executable) if resolution.executable else None

    def _candidate_names(self, command: str) -> tuple[str, ...]:
        if _is_windows(self._platform_name):
            # Prefer directly executable npm wrappers over PowerShell shims.
            return (f"{command}.cmd", f"{command}.exe", command, f"{command}.ps1")
        return (command,)

    def _path_candidates(self, command: str) -> tuple[list[Path], bool]:
        found: list[Path] = []
        had_error = False
        for candidate in self._candidate_names(command):
            try:
                resolved = self._which(candidate)
            except (OSError, TypeError, ValueError):
                had_error = True
                continue
            if resolved:
                found.append(Path(resolved).expanduser())
        return self._ordered_unique(found), had_error

    def _directory_candidates(self, command: str, directory: Path | None) -> list[Path]:
        if directory is None:
            return []
        candidates: list[Path] = []
        for name in self._candidate_names(command):
            candidate = directory / name
            try:
                exists = bool(self._path_exists(candidate))
            except (OSError, TypeError, ValueError):
                exists = False
            if exists:
                candidates.append(candidate)
        return self._ordered_unique(candidates)

    def _npm_prefix_candidates(self, command: str) -> tuple[list[Path], str]:
        npm_paths, lookup_error = self._path_candidates("npm")
        if not npm_paths:
            return [], "npm_path_lookup_failed" if lookup_error else ""
        npm = npm_paths[0]
        invocation = safe_executable_command(
            npm,
            ("prefix", "-g"),
            platform_name=self._platform_name,
            environment=self._environment,
        )
        environment = executable_environment(
            npm,
            self._environment,
            platform_name=self._platform_name,
        )
        try:
            completed = self._run(
                list(invocation),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._probe_timeout,
                check=False,
                shell=False,
                env=environment or None,
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return [], "npm_prefix_probe_failed"
        if int(getattr(completed, "returncode", 1)) != 0:
            return [], "npm_prefix_probe_failed"
        prefix = str(getattr(completed, "stdout", "") or "").strip().splitlines()
        if not prefix:
            return [], "npm_prefix_empty"
        root = Path(prefix[-1].strip()).expanduser()
        candidates = self._directory_candidates(command, root)
        if not candidates:
            candidates = self._directory_candidates(command, root / "bin")
        return candidates, ""

    def _probe_candidates(
        self,
        candidates: Sequence[Path],
        *,
        source: str,
        path_visible: bool,
    ) -> ExecutableResolution:
        first_failure: ExecutableResolution | None = None
        for candidate in candidates:
            result = self._probe_candidate(candidate, source=source, path_visible=path_visible)
            if result.runnable:
                return result
            if first_failure is None:
                first_failure = result
        return first_failure or ExecutableResolution()

    def _probe_candidate(
        self,
        candidate: Path,
        *,
        source: str,
        path_visible: bool,
    ) -> ExecutableResolution:
        executable = str(candidate)
        if self._probe_override is not None:
            try:
                runnable = bool(self._probe_override(executable))
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                runnable = False
            return ExecutableResolution(
                executable=executable,
                source=source,
                path_visible=path_visible,
                runnable=runnable,
                diagnostic="version_probe_succeeded" if runnable else "launch_failed",
            )

        invocation = safe_executable_command(
            candidate,
            ("--version",),
            platform_name=self._platform_name,
            environment=self._environment,
        )
        environment = executable_environment(
            candidate,
            self._environment,
            platform_name=self._platform_name,
        )
        try:
            completed = self._run(
                list(invocation),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._probe_timeout,
                check=False,
                shell=False,
                env=environment or None,
            )
        except subprocess.TimeoutExpired:
            return ExecutableResolution(
                executable=executable,
                source=source,
                path_visible=path_visible,
                diagnostic="version_probe_timed_out",
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return ExecutableResolution(
                executable=executable,
                source=source,
                path_visible=path_visible,
                diagnostic="launch_failed",
            )
        output = str(getattr(completed, "stdout", "") or "").strip()
        error_output = str(getattr(completed, "stderr", "") or "").strip()
        version = (output or error_output).splitlines()[0][:300] if (output or error_output) else ""
        combined = f"{output}\n{error_output}".casefold()
        broken_wrapper = any(marker in combined for marker in FAILED_VERSION_MARKERS)
        runnable = int(getattr(completed, "returncode", 1)) == 0 and not broken_wrapper
        return ExecutableResolution(
            executable=executable,
            source=source,
            path_visible=path_visible,
            runnable=runnable,
            version=version,
            diagnostic="version_probe_succeeded" if runnable else "version_probe_failed",
        )

    @staticmethod
    def _ordered_unique(candidates: Sequence[Path]) -> list[Path]:
        unique: dict[str, Path] = {}
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            unique.setdefault(key, candidate)
        return sorted(
            unique.values(),
            key=lambda item: WINDOWS_WRAPPER_PRIORITY.get(item.suffix.lower(), 4),
        )


def resolve_codex_executable(
    *,
    which: Callable[[str], str | None] | None = None,
    platform_name: str | None = None,
) -> Path | None:
    """Resolve the exact local Codex CLI entry point without a launch probe."""

    resolver = ExecutableResolver(
        which=which,
        platform_name=platform_name,
        probe=lambda _executable: True,
    )
    return resolver.resolve_path("codex")


@dataclass(frozen=True)
class WindowsAppDetection:
    packages: tuple[str, ...] = ()
    available: bool = True
    source: str = "Store package"
    diagnostic: str = ""


class WindowsAppDetector:
    """Read registered Appx identities without conflating desktop and CLI apps."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] | None = None,
        run: Callable[..., object] | None = None,
        environment: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._which = which or shutil.which
        self._run = run or subprocess.run
        self._environment = dict(os.environ if environment is None else environment)
        self._platform_name = platform_name or platform.system()
        self._timeout = max(0.1, min(float(timeout), 10.0))

    def detect(self) -> WindowsAppDetection:
        if not _is_windows(self._platform_name):
            return WindowsAppDetection()
        powershell = None
        for candidate in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
            try:
                powershell = self._which(candidate)
            except (OSError, TypeError, ValueError):
                continue
            if powershell:
                break
        if not powershell:
            return WindowsAppDetection(available=False, diagnostic="appx_query_unavailable")
        command = [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-AppxPackage -ErrorAction Stop | Select-Object -ExpandProperty Name",
        ]
        try:
            completed = self._run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                check=False,
                shell=False,
                env=self._environment or None,
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return WindowsAppDetection(available=False, diagnostic="appx_query_failed")
        if int(getattr(completed, "returncode", 1)) != 0:
            return WindowsAppDetection(available=False, diagnostic="appx_query_failed")
        packages = tuple(
            line.strip()
            for line in str(getattr(completed, "stdout", "") or "").splitlines()
            if line.strip()
        )
        return WindowsAppDetection(packages=packages)


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
    discovery_source: str = ""
    version: str = ""
    path_visible: bool = False
    version_probe: str = ""

    @property
    def ready_for_implementation(self) -> bool:
        return (
            self.installed
            and self.cli_support
            and self.implementation_supported
            and self.status in {ENGINE_STATUS_READY, ENGINE_STATUS_INSTALLED}
        )


class ExecutionEngineUnavailable(RuntimeError):
    """Raised before execution when Orion has no usable implementation adapter."""


class ExecutionEngineService:
    """Detect desktop applications, runnable CLIs, and the Python runtime."""

    CLI_ENGINES = {
        "codex": ("codex", "Codex CLI", True),
        "claude_code": ("claude", "Claude Code", False),
        "gemini_cli": ("gemini", "Gemini CLI", False),
    }
    DESKTOP_ENGINES = {
        "codex_desktop": "Codex Desktop",
        "chatgpt_desktop": "ChatGPT Desktop",
    }
    ENGINE_ORDER = (
        "codex",
        "codex_desktop",
        "chatgpt_desktop",
        "claude_code",
        "gemini_cli",
        "python",
    )

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
        executable_resolver: ExecutableResolver | None = None,
        windows_app_detector: WindowsAppDetector | None = None,
    ) -> None:
        self.config = config_manager
        self.application_catalog = application_catalog
        self._which = which or shutil.which
        self._environment = dict(os.environ if environment is None else environment)
        self._platform_name = platform_name or platform.system()
        self._python_executable = python_executable or sys.executable
        self._resolver = executable_resolver or ExecutableResolver(
            which=self._which,
            probe=probe,
            environment=self._environment,
            platform_name=self._platform_name,
        )
        self._app_detector = windows_app_detector or WindowsAppDetector(
            which=self._which,
            environment=self._environment,
            platform_name=self._platform_name,
        )
        self._app_detection: WindowsAppDetection | None = None

    @property
    def selected_engine_id(self) -> str:
        value = str(self.config.get("execution.default_engine", "codex")).strip().lower()
        return value or "codex"

    def status(self) -> tuple[ExecutionEngine, ...]:
        engines = {
            engine_id: self._cli_engine(
                command,
                name,
                engine_id=engine_id,
                implementation_supported=implementation_supported,
            )
            for engine_id, (command, name, implementation_supported)
            in self.CLI_ENGINES.items()
        }
        engines.update({
            engine_id: self._desktop_engine(engine_id, name)
            for engine_id, name in self.DESKTOP_ENGINES.items()
        })
        engines["python"] = self._python_engine()
        return tuple(engines[engine_id] for engine_id in self.ENGINE_ORDER)

    def engine(self, engine_id: str) -> ExecutionEngine:
        normalized = str(engine_id).strip().lower()
        if normalized in self.CLI_ENGINES:
            command, name, implementation_supported = self.CLI_ENGINES[normalized]
            return self._cli_engine(
                command,
                name,
                engine_id=normalized,
                implementation_supported=implementation_supported,
            )
        if normalized in self.DESKTOP_ENGINES:
            return self._desktop_engine(normalized, self.DESKTOP_ENGINES[normalized])
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
        resolution = self._resolver.resolve("codex")
        return Path(resolution.executable) if resolution.executable else None

    def _cli_engine(
        self,
        command: str,
        name: str,
        *,
        engine_id: str | None = None,
        implementation_supported: bool,
    ) -> ExecutionEngine:
        resolution = self._resolver.resolve(command)
        selected_id = engine_id or command
        if not resolution.executable:
            status = (
                ENGINE_STATUS_DETECTION_ERROR
                if resolution.detection_error
                else ENGINE_STATUS_NOT_INSTALLED
            )
            return ExecutionEngine(
                selected_id,
                name,
                status,
                False,
                True,
                implementation_supported,
                reason=resolution.diagnostic,
                path_visible=resolution.path_visible,
                version_probe="Detection error" if resolution.detection_error else "Not run",
            )
        if not resolution.runnable:
            return ExecutionEngine(
                selected_id,
                name,
                ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE,
                True,
                True,
                implementation_supported,
                executable=resolution.executable,
                reason=resolution.diagnostic,
                discovery_source=resolution.source,
                version=resolution.version,
                path_visible=resolution.path_visible,
                version_probe=f"Failed ({resolution.diagnostic.replace('_', ' ')})",
            )
        return ExecutionEngine(
            selected_id,
            name,
            ENGINE_STATUS_READY,
            True,
            True,
            implementation_supported,
            executable=resolution.executable,
            discovery_source=resolution.source,
            version=resolution.version,
            path_visible=resolution.path_visible,
            version_probe="Succeeded",
        )

    def _desktop_engine(self, engine_id: str, name: str) -> ExecutionEngine:
        installed, source, diagnostic = self._desktop_status(engine_id)
        if installed:
            return ExecutionEngine(
                engine_id,
                name,
                ENGINE_STATUS_INSTALLED,
                True,
                False,
                False,
                reason="desktop_has_no_cli_adapter",
                discovery_source=source,
            )
        return ExecutionEngine(
            engine_id,
            name,
            ENGINE_STATUS_DETECTION_ERROR if diagnostic else ENGINE_STATUS_NOT_INSTALLED,
            False,
            False,
            False,
            reason=diagnostic or "application_not_found",
            discovery_source=source,
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
            ENGINE_STATUS_READY if ready else ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE,
            ready,
            True,
            False,
            executable=str(executable) if ready else "",
            reason="" if ready else "python_runtime_not_found",
            discovery_source="Current Orion runtime" if ready else "",
            path_visible=True if ready else False,
            version_probe="Not required",
        )

    def _desktop_status(self, engine_id: str) -> tuple[bool, str, str]:
        catalog_names = self._catalog_names()
        if engine_id == "codex_desktop":
            if any(name == "codex" or name.startswith("codex ") for name in catalog_names):
                return True, "Application catalog", ""
            paths = self._desktop_paths("Codex")
            package_match = lambda value: value == "openai.codex" or value.startswith("openai.codex_")
            try:
                codex_alias = self._which("codex")
            except (OSError, TypeError, ValueError):
                codex_alias = None
            if codex_alias:
                normalized_alias = str(codex_alias).replace("/", "\\").lower()
                if "\\windowsapps\\openai.codex_" in normalized_alias:
                    return True, "Known install location", ""
        else:
            if any(name == "chatgpt" or name.startswith("chatgpt ") for name in catalog_names):
                return True, "Application catalog", ""
            paths = self._desktop_paths("ChatGPT")
            package_match = lambda value: "chatgpt" in value
        try:
            if any(path.exists() for path in paths):
                return True, "Known install location", ""
        except OSError:
            pass
        if _is_windows(self._platform_name):
            detection = self._windows_app_detection()
            normalized = tuple(package.strip().lower() for package in detection.packages)
            if any(package_match(package) for package in normalized):
                return True, detection.source, ""
            if not detection.available:
                return False, detection.source, detection.diagnostic or "appx_query_failed"
        return False, "", ""

    def _catalog_names(self) -> set[str]:
        if self.application_catalog is None:
            return set()
        try:
            return {
                str(application.name).strip().lower()
                for application in self.application_catalog.applications()
            }
        except (AttributeError, OSError, TypeError, ValueError):
            return set()

    def _desktop_paths(self, product: str) -> tuple[Path, ...]:
        candidates: list[Path] = []
        local_app_data = self._environment.get("LOCALAPPDATA")
        app_data = self._environment.get("APPDATA")
        program_data = self._environment.get("PROGRAMDATA")
        if local_app_data:
            local = Path(local_app_data)
            candidates.extend((
                local / "Programs" / product / f"{product}.exe",
                local / "Microsoft" / "WindowsApps" / f"{product}.exe",
            ))
        if app_data:
            candidates.append(
                Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{product}.lnk"
            )
        if program_data:
            candidates.append(
                Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{product}.lnk"
            )
        if self._platform_name.strip().lower() == "darwin":
            candidates.extend((Path("/Applications") / f"{product}.app", Path.home() / "Applications" / f"{product}.app"))
        return tuple(candidates)

    def _windows_app_detection(self) -> WindowsAppDetection:
        if self._app_detection is None:
            try:
                self._app_detection = self._app_detector.detect()
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                self._app_detection = WindowsAppDetection(
                    available=False,
                    diagnostic="appx_query_failed",
                )
        return self._app_detection
