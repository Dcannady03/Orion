"""Application boundary for Orion observability commands."""
from __future__ import annotations

from orion.application.observability.health import (
    HEALTH_SECTIONS,
    HealthChecker,
    HealthReport,
    HealthState,
)
from orion.application.observability.logs import (
    LogSearchRequest,
    LogSearchResult,
    LogViewer,
)
from orion.application.results import ApplicationResult


class ObservabilityApplicationHandler:
    """Expose read-only observability reports without mutating runtime state."""

    def __init__(
        self,
        *,
        paths,
        config_manager=None,
        provider_manager=None,
        services=None,
        event_store=None,
        clock=None,
    ) -> None:
        self._paths = paths
        self._event_store = event_store
        self.checker = HealthChecker(
            paths,
            config_manager=config_manager,
            provider_manager=provider_manager,
            services=services,
            event_store=event_store,
            clock=clock,
        )

    def health(self, section: str | None = None) -> ApplicationResult:
        """Run the unified health check, optionally limited to one section."""
        normalized = (section or "").strip().lower() or None
        if normalized is not None and normalized not in HEALTH_SECTIONS:
            return ApplicationResult.failure(
                f"Unknown health section: {section}",
                data={"read_only": True, "sections": list(HEALTH_SECTIONS)},
                errors=(f"Unknown health section: {section}",),
                next_actions=("health",),
            )
        report = self.checker.check(normalized)
        return ApplicationResult.success(
            self._render_health(report),
            data={**report.to_dict(), "read_only": True},
            warnings=tuple(
                f"{check.name}: {check.detail}"
                for check in report.checks
                if check.state in {HealthState.WARNING, HealthState.ERROR}
            ),
            next_actions=tuple(f"health {name}" for name in HEALTH_SECTIONS),
        )

    @staticmethod
    def _render_health(report: HealthReport) -> str:
        lines = [
            "Orion Health Check",
            "-" * 68,
            f"Overall     : {report.overall.value.upper()}",
            f"Generated   : {report.generated_at}",
        ]
        for check in report.checks:
            lines.append(
                f"{check.name.title():<12}: {check.state.value.upper()} - {check.detail}"
            )
        return "\n".join(lines)

    def logs(self, request: LogSearchRequest | None = None) -> ApplicationResult:
        """Search the runtime logs (Event Store plus plain log files)."""
        search = request or LogSearchRequest()
        viewer = LogViewer(self._paths.logs, self._event_store)
        try:
            result = viewer.search(search)
        except ValueError as exc:
            return ApplicationResult.failure(
                f"Log search failed: {exc}",
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("logs",),
            )
        return ApplicationResult.success(
            self._render_logs(result),
            data={
                **result.to_dict(),
                "read_only": True,
                "filters": {
                    "severity": search.severity or "",
                    "event_type": search.event_type or "",
                    "search": search.search or "",
                    "start_time": search.start_time or "",
                    "end_time": search.end_time or "",
                    "source": search.source,
                },
            },
            warnings=result.warnings,
            next_actions=("logs --severity error",),
        )

    @staticmethod
    def _render_logs(result: LogSearchResult) -> str:
        lines = ["Orion Log Search", "-" * 68]
        if not result.entries:
            lines.append("No log entries matched the filters.")
            return "\n".join(lines)
        lines.append(f"Found {len(result.entries)} log entries (newest first).")
        for entry in result.entries:
            timestamp = entry.occurred_at or "unknown time"
            lines.append(
                f"{timestamp} [{entry.severity.upper()}] "
                f"({entry.source}) {entry.message}"
            )
        return "\n".join(lines)
