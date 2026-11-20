"""Environment diagnostics for issue reports.

Collects a read-only snapshot of the running Orion instance: version,
platform, providers, the unified health check, storage, and the Event
Store. The report is safe to paste into an issue report: it contains
configuration flags only and never includes API keys, tokens, or
environment values.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from orion.application.observability.health import (
    HealthChecker,
    HealthReport,
    HealthState,
)
from orion.core.paths import OrionPaths

_GIT_TIMEOUT_SECONDS = 5.0


def _plain(value: Any) -> Any:
    """Return a plain JSON-compatible copy of a nested value."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _orion_version() -> str:
    try:
        from orion.version import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - version is best-effort in a report
        return "unknown"


def _git_info(install_root: Path) -> tuple[str, str]:
    """Return (short commit, dirty flag) for the install root, or unknowns."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(install_root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if commit.returncode != 0 or not commit.stdout.strip():
            return "unknown", "unknown"
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(install_root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        dirty = "unknown"
        if status.returncode == 0:
            dirty = "yes" if status.stdout.strip() else "no"
        return commit.stdout.strip(), dirty
    except Exception:  # noqa: BLE001 - git is best-effort in a report
        return "unknown", "unknown"


@dataclass(frozen=True)
class DiagnosticsReport:
    """An immutable snapshot of the environment Orion is running in."""

    generated_at: str
    sections: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "sections": _plain(dict(self.sections)),
        }


class DiagnosticsCollector:
    """Build an environment report, reusing the unified health check.

    Every section is isolated: a failing section reports an error
    instead of hiding the remaining sections. The report never reads
    secrets or environment values.
    """

    def __init__(
        self,
        paths: OrionPaths,
        *,
        config_manager: Any = None,
        provider_manager: Any = None,
        services: Any = None,
        event_store: Any = None,
        disk_usage: Callable[[str | Path], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        version: str | None = None,
        commit: str | None = None,
    ) -> None:
        if not isinstance(paths, OrionPaths):
            raise TypeError("Diagnostics require OrionPaths.")
        self._paths = paths
        self._event_store = event_store
        self._version = version
        self._commit = commit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._checker = HealthChecker(
            paths,
            config_manager=config_manager,
            provider_manager=provider_manager,
            services=services,
            event_store=event_store,
            disk_usage=disk_usage,
            clock=self._clock,
        )

    def collect(self) -> DiagnosticsReport:
        """Build the full environment report."""
        report = self._checker.check()
        checks = {check.name: check for check in report.checks}
        builders = (
            ("orion", lambda: self._section_orion()),
            ("platform", lambda: self._section_platform()),
            ("providers", lambda: self._section_providers(checks)),
            ("health", lambda: self._section_health(report)),
            ("storage", lambda: self._section_storage(checks)),
            ("services", lambda: self._section_services(checks)),
            ("events", lambda: self._section_events(checks)),
            ("config", lambda: self._section_config(checks)),
        )
        sections: dict[str, Mapping[str, object]] = {}
        for name, builder in builders:
            try:
                sections[name] = builder()
            except Exception as exc:  # noqa: BLE001 - one broken section must not hide the rest
                sections[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return DiagnosticsReport(
            generated_at=self._clock().isoformat(),
            sections=sections,
        )

    def _section_orion(self) -> dict[str, object]:
        version = self._version or _orion_version()
        if self._commit is not None:
            commit, dirty = self._commit, "unknown"
        else:
            commit, dirty = _git_info(self._paths.install_root)
        return {
            "version": version,
            "commit": commit,
            "dirty": dirty,
            "install_root": str(self._paths.install_root),
            "user_root": str(self._paths.user_root),
        }

    @staticmethod
    def _section_platform() -> dict[str, object]:
        return {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count() or 0,
        }

    @staticmethod
    def _section_providers(checks: Mapping[str, Any]) -> dict[str, object]:
        check = checks.get("providers")
        data = getattr(check, "data", {}) if check is not None else {}
        providers = data.get("providers") if isinstance(data, Mapping) else None
        if not providers:
            return {
                "available": False,
                "detail": getattr(check, "detail", "Provider manager is unavailable."),
            }
        return {"available": True, "providers": _plain(providers)}

    @staticmethod
    def _section_health(report: HealthReport) -> dict[str, object]:
        return {
            "overall": report.overall.value,
            "checks": {
                check.name: {
                    "state": check.state.value,
                    "detail": check.detail,
                }
                for check in report.checks
            },
        }

    @staticmethod
    def _section_storage(checks: Mapping[str, Any]) -> dict[str, object]:
        check = checks.get("disk")
        if check is None:
            return {"available": False}
        data = check.data if isinstance(check.data, Mapping) else {}
        return {
            "state": check.state.value,
            "path": str(data.get("path", "")),
            "total_bytes": int(data.get("total_bytes", 0)),
            "free_bytes": int(data.get("free_bytes", 0)),
            "free_percent": data.get("free_percent", 0.0),
        }

    @staticmethod
    def _section_services(checks: Mapping[str, Any]) -> dict[str, object]:
        check = checks.get("services")
        data = getattr(check, "data", {}) if check is not None else {}
        if not isinstance(data, Mapping) or not data:
            return {
                "available": False,
                "detail": getattr(check, "detail", "Service registry is unavailable."),
            }
        services = data.get("services", {})
        states = {
            name: str(entry.get("state", HealthState.UNKNOWN.value))
            for name, entry in services.items()
            if isinstance(entry, Mapping)
        }
        problems = [
            name
            for name, state in states.items()
            if state in {HealthState.WARNING.value, HealthState.ERROR.value}
        ]
        return {
            "available": True,
            "registered": int(data.get("registered", 0)),
            "reporting": int(data.get("reporting", 0)),
            "states": states,
            "problems": problems,
        }

    def _section_events(self, checks: Mapping[str, Any]) -> dict[str, object]:
        check = checks.get("events")
        data = getattr(check, "data", {}) if check is not None else {}
        latest = data.get("latest_event_at") if isinstance(data, Mapping) else None
        warnings = data.get("warnings") if isinstance(data, Mapping) else None
        files = 0
        try:
            events_root = self._paths.events
            if events_root.is_dir():
                files = sum(
                    1 for path in events_root.iterdir() if path.suffix == ".jsonl"
                )
        except OSError:
            files = 0
        return {
            "state": check.state.value if check is not None else "unknown",
            "latest_event_at": latest or "",
            "warnings": list(warnings) if isinstance(warnings, list) else [],
            "daily_files": files,
        }

    @staticmethod
    def _section_config(checks: Mapping[str, Any]) -> dict[str, object]:
        config = checks.get("config")
        workspace = checks.get("workspace")
        config_data = getattr(config, "data", {}) if config is not None else {}
        workspace_data = (
            getattr(workspace, "data", {}) if workspace is not None else {}
        )
        return {
            "state": config.state.value if config is not None else "unknown",
            "keys": int(config_data.get("keys", 0))
            if isinstance(config_data, Mapping)
            else 0,
            "path": str(config_data.get("path", ""))
            if isinstance(config_data, Mapping)
            else "",
            "workspace": str(workspace_data.get("path", ""))
            if isinstance(workspace_data, Mapping)
            else "",
            "workspace_state": workspace.state.value
            if workspace is not None
            else "unknown",
        }
