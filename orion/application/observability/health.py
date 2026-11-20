"""Unified health checks for Orion services, providers, and storage."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from orion.core.paths import OrionPaths


class HealthState(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


_SEVERITY = {
    HealthState.OK: 0,
    HealthState.UNKNOWN: 1,
    HealthState.WARNING: 2,
    HealthState.ERROR: 3,
}

_SERVICE_STATE_MAP = {
    "available": HealthState.OK,
    "degraded": HealthState.WARNING,
    "unavailable": HealthState.ERROR,
}

# Disk-space thresholds for the user-data volume.
DISK_WARNING_FREE_BYTES = 1 * 1024 ** 3  # 1 GiB
DISK_WARNING_FREE_PERCENT = 10.0
DISK_ERROR_FREE_BYTES = 256 * 1024 ** 2  # 256 MiB
DISK_ERROR_FREE_PERCENT = 5.0

HEALTH_SECTIONS = ("disk", "providers", "services", "config", "workspace", "events")


def _plain(value: Any) -> Any:
    """Return a plain JSON-compatible copy of a nested value."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _format_bytes(size: int) -> str:
    """Render a byte count with a human-scale unit."""
    value = float(max(int(size), 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size)} B"


@dataclass(frozen=True)
class HealthCheck:
    """One named health observation with a severity and detail."""

    name: str
    state: HealthState
    detail: str
    data: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "data": _plain(dict(self.data)),
        }


@dataclass(frozen=True)
class HealthReport:
    """An immutable snapshot of every health check that ran."""

    generated_at: str
    checks: tuple[HealthCheck, ...] = ()

    @property
    def overall(self) -> HealthState:
        worst = HealthState.OK
        for check in self.checks:
            if _SEVERITY[check.state] > _SEVERITY[worst]:
                worst = check.state
        return worst

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "overall": self.overall.value,
            "checks": [check.to_dict() for check in self.checks],
        }


class HealthChecker:
    """Run Orion health checks without mutating runtime state.

    Every check is isolated: a failing check reports UNKNOWN instead of
    hiding the remaining checks. Checks are read-only by construction.
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
    ) -> None:
        if not isinstance(paths, OrionPaths):
            raise TypeError("Health checks require OrionPaths.")
        self.paths = paths
        self.config_manager = config_manager
        self.provider_manager = provider_manager
        self.services = services
        self.event_store = event_store
        self._disk_usage = disk_usage or shutil.disk_usage
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, section: str | None = None) -> HealthReport:
        """Run every health check, or one named section."""
        normalized = (section or "").strip().lower()
        if normalized and normalized not in HEALTH_SECTIONS:
            raise ValueError(f"Unknown health section: {section}")
        selected = (normalized,) if normalized else HEALTH_SECTIONS
        checks: list[HealthCheck] = []
        for name in selected:
            try:
                checks.append(getattr(self, f"_check_{name}")())
            except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
                checks.append(HealthCheck(
                    name,
                    HealthState.UNKNOWN,
                    f"Check failed ({type(exc).__name__}): {exc}",
                ))
        return HealthReport(
            generated_at=self._clock().isoformat(),
            checks=tuple(checks),
        )

    def _check_disk(self) -> HealthCheck:
        target = self.paths.user_root
        usage = self._disk_usage(target)
        total = int(usage.total)
        free = int(usage.free)
        free_percent = (free / total * 100.0) if total > 0 else 0.0
        detail = (
            f"{_format_bytes(free)} free of {_format_bytes(total)} "
            f"({free_percent:.1f}%) on the user-data volume"
        )
        data = {
            "path": str(target),
            "total_bytes": total,
            "free_bytes": free,
            "free_percent": round(free_percent, 1),
        }
        if free < DISK_ERROR_FREE_BYTES or free_percent < DISK_ERROR_FREE_PERCENT:
            return HealthCheck("disk", HealthState.ERROR, detail, data)
        if free < DISK_WARNING_FREE_BYTES or free_percent < DISK_WARNING_FREE_PERCENT:
            return HealthCheck("disk", HealthState.WARNING, detail, data)
        return HealthCheck("disk", HealthState.OK, detail, data)

    def _check_providers(self) -> HealthCheck:
        if self.provider_manager is None:
            return HealthCheck(
                "providers",
                HealthState.UNKNOWN,
                "Provider manager is unavailable.",
            )
        statuses = list(self.provider_manager.statuses())
        active = next(
            (item for item in statuses if bool(getattr(item, "active", False))),
            None,
        )
        parts: list[str] = []
        rows: list[dict[str, object]] = []
        for item in statuses:
            key = str(getattr(item, "key", ""))
            configured = bool(getattr(item, "configured", False))
            label = key
            if bool(getattr(item, "active", False)):
                label += " (active)"
            label += " configured" if configured else " not configured"
            parts.append(label)
            rows.append({
                "key": key,
                "enabled": bool(getattr(item, "enabled", False)),
                "configured": configured,
                "active": bool(getattr(item, "active", False)),
                "model": str(getattr(item, "model", "") or ""),
            })
        detail = "; ".join(parts) if parts else "No providers registered."
        data: dict[str, object] = {"providers": rows}
        if active is None:
            return HealthCheck(
                "providers",
                HealthState.WARNING,
                f"No active provider. {detail}",
                data,
            )
        if not bool(getattr(active, "configured", False)):
            return HealthCheck(
                "providers",
                HealthState.ERROR,
                f"Active provider {active.key} is not configured. {detail}",
                data,
            )
        return HealthCheck("providers", HealthState.OK, detail, data)

    def _check_services(self) -> HealthCheck:
        if self.services is None:
            return HealthCheck(
                "services",
                HealthState.UNKNOWN,
                "Service registry is unavailable.",
            )
        snapshot = self.services.snapshot() if hasattr(self.services, "snapshot") else {}
        reporting: dict[str, dict[str, str]] = {}
        for name in sorted(snapshot):
            getter = getattr(snapshot[name], "get_status", None)
            if not callable(getter):
                continue
            try:
                status = getter()
            except Exception as exc:  # noqa: BLE001 - a broken service must not hide the rest
                reporting[name] = {
                    "state": HealthState.UNKNOWN.value,
                    "message": f"Status failed ({type(exc).__name__}).",
                }
                continue
            raw_state = getattr(status, "state", None)
            state_value = getattr(raw_state, "value", raw_state)
            mapped = _SERVICE_STATE_MAP.get(str(state_value), HealthState.UNKNOWN)
            reporting[name] = {
                "state": mapped.value,
                "message": str(getattr(status, "message", "") or ""),
            }
        data: dict[str, object] = {
            "registered": len(snapshot),
            "reporting": len(reporting),
            "services": reporting,
        }
        if not reporting:
            return HealthCheck(
                "services",
                HealthState.UNKNOWN,
                "No registered service reports a status.",
                data,
            )
        worst = HealthState.OK
        for entry in reporting.values():
            state = HealthState(entry["state"])
            if _SEVERITY[state] > _SEVERITY[worst]:
                worst = state
        summary = ", ".join(
            f"{name} ({entry['state']})" for name, entry in reporting.items()
        )
        problems = [
            f"{name}: {entry['message']}"
            for name, entry in reporting.items()
            if entry["state"] in {HealthState.WARNING.value, HealthState.ERROR.value}
            and entry["message"]
        ]
        detail = f"{len(reporting)} reporting service(s): {summary}"
        if problems:
            detail += ". " + "; ".join(problems)
        return HealthCheck("services", worst, detail, data)

    def _check_config(self) -> HealthCheck:
        if self.config_manager is None:
            return HealthCheck(
                "config",
                HealthState.UNKNOWN,
                "Configuration manager is unavailable.",
            )
        config = getattr(self.config_manager, "config", None)
        if not isinstance(config, dict) or not config:
            return HealthCheck(
                "config",
                HealthState.ERROR,
                "Configuration is missing or empty.",
            )
        path = (
            getattr(self.config_manager, "local_config_path", None)
            or getattr(self.config_manager, "config_path", None)
        )
        detail = f"Configuration loaded ({len(config)} top-level keys)"
        if path is not None:
            detail += f" from {path}"
        return HealthCheck(
            "config",
            HealthState.OK,
            detail,
            {"keys": len(config), "path": str(path) if path is not None else ""},
        )

    def _check_workspace(self) -> HealthCheck:
        if self.config_manager is None:
            return HealthCheck(
                "workspace",
                HealthState.UNKNOWN,
                "Configuration manager is unavailable.",
            )
        raw = str(self.config_manager.get("workspace.default_path", ".") or ".")
        path = Path(raw).expanduser()
        absolute = path.resolve()
        data: dict[str, object] = {"path": str(absolute)}
        if not absolute.exists():
            return HealthCheck(
                "workspace",
                HealthState.WARNING,
                f"Workspace path does not exist: {absolute}",
                data,
            )
        if not absolute.is_dir():
            return HealthCheck(
                "workspace",
                HealthState.ERROR,
                f"Workspace path is not a directory: {absolute}",
                data,
            )
        return HealthCheck(
            "workspace",
            HealthState.OK,
            f"Workspace exists: {absolute}",
            data,
        )

    def _check_events(self) -> HealthCheck:
        if self.event_store is None:
            return HealthCheck(
                "events",
                HealthState.WARNING,
                "Event Store is disabled; no event history is recorded.",
            )
        latest, warnings = self.event_store.latest_at()
        data: dict[str, object] = {
            "latest_event_at": latest,
            "warnings": list(warnings),
        }
        if warnings:
            return HealthCheck(
                "events",
                HealthState.WARNING,
                f"Event Store enabled with {len(warnings)} warning(s): {warnings[0]}",
                data,
            )
        if latest:
            return HealthCheck(
                "events",
                HealthState.OK,
                f"Event Store enabled; latest event {latest}.",
                data,
            )
        return HealthCheck(
            "events",
            HealthState.OK,
            "Event Store enabled; no events recorded yet.",
            data,
        )
