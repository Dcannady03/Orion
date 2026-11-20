"""Application boundary for Orion observability commands."""
from __future__ import annotations

import json
import zipfile

from orion.application.observability.backup import (
    EXCLUDED_PATHS,
    BackupService,
)
from orion.application.observability.diagnostics import (
    DiagnosticsCollector,
    DiagnosticsReport,
)
from orion.application.observability.graphs import (
    HealthGraphs,
    HealthSnapshotStore,
    build_dependency_graph,
    build_timelines,
)
from orion.application.observability.recovery import (
    DEFAULT_RECOVERY_LIMIT,
    RecoveryHistoryStore,
)
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
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._services = services
        self._event_store = event_store
        self._clock = clock
        self.checker = HealthChecker(
            paths,
            config_manager=config_manager,
            provider_manager=provider_manager,
            services=services,
            event_store=event_store,
            clock=clock,
        )

    def health(
        self, section: str | None = None, *, record: bool = False
    ) -> ApplicationResult:
        """Run the unified health check, optionally limited to one section.

        With record=True the report is also saved as a health snapshot
        for the graphs command; without it the check stays read-only.
        """
        normalized = (section or "").strip().lower() or None
        if normalized is not None and normalized not in HEALTH_SECTIONS:
            return ApplicationResult.failure(
                f"Unknown health section: {section}",
                data={"read_only": True, "sections": list(HEALTH_SECTIONS)},
                errors=(f"Unknown health section: {section}",),
                next_actions=("health",),
            )
        report = self.checker.check(normalized)
        message = self._render_health(report)
        data: dict[str, object] = {**report.to_dict(), "read_only": not record}
        if record:
            try:
                path = HealthSnapshotStore(self._paths.observability).append(report)
            except OSError as exc:
                return ApplicationResult.failure(
                    f"Health check ran, but the snapshot could not be saved: {exc}",
                    data=data,
                    errors=(str(exc),),
                    next_actions=("health",),
                )
            message += f"\nSnapshot recorded to {path}."
            data["recorded"] = True
            data["snapshot_path"] = str(path)
        return ApplicationResult.success(
            message,
            data=data,
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

    def diagnostics(self) -> ApplicationResult:
        """Build an environment report suitable for issue reports."""
        collector = DiagnosticsCollector(
            self._paths,
            config_manager=self._config_manager,
            provider_manager=self._provider_manager,
            services=self._services,
            event_store=self._event_store,
            clock=self._clock,
        )
        report = collector.collect()
        return ApplicationResult.success(
            self._render_diagnostics(report),
            data={**report.to_dict(), "read_only": True},
            next_actions=("health", "logs --severity error"),
        )

    @staticmethod
    def _render_diagnostics(report: DiagnosticsReport) -> str:
        lines = [
            "Orion Diagnostics",
            "-" * 68,
            f"Generated   : {report.generated_at}",
        ]
        for name, section in report.sections.items():
            lines.append(f"{name.title()}:")
            for key, value in section.items():
                if isinstance(value, (dict, list)):
                    rendered = json.dumps(value, sort_keys=True, default=str)
                else:
                    rendered = str(value)
                lines.append(f"  {key}: {rendered}")
        return "\n".join(lines)

    def graphs(self, section: str | None = None) -> ApplicationResult:
        """Show health graphs: timelines from snapshots, or the live tree."""
        normalized = (section or "").strip().lower() or None
        if normalized == "dependencies":
            report = self.checker.check()
            tree = build_dependency_graph(report)
            return ApplicationResult.success(
                self._render_dependency_graph(tree),
                data={
                    "read_only": True,
                    "kind": "dependencies",
                    "tree": tree.to_dict(),
                    "generated_at": report.generated_at,
                },
                next_actions=("graphs", "health"),
            )
        if normalized is not None and normalized not in HEALTH_SECTIONS:
            return ApplicationResult.failure(
                f"Unknown graphs section: {section}",
                data={
                    "read_only": True,
                    "sections": [*HEALTH_SECTIONS, "dependencies"],
                },
                errors=(f"Unknown graphs section: {section}",),
                next_actions=("graphs",),
            )
        history = HealthSnapshotStore(self._paths.observability).load()
        if not history.snapshots:
            return ApplicationResult.success(
                "No health snapshots recorded yet.\n"
                "Run `health --record` to record one, then `graphs` to view it.",
                data={"read_only": True, "kind": "timelines", "snapshots": 0},
                next_actions=("health --record",),
            )
        graphs = build_timelines(history)
        return ApplicationResult.success(
            self._render_timelines(graphs, normalized),
            data={
                **graphs.to_dict(),
                "read_only": True,
                "kind": "timelines",
                "snapshots": len(history.snapshots),
            },
            next_actions=("graphs dependencies", "health --record"),
        )

    @staticmethod
    def _render_timelines(graphs: HealthGraphs, section: str | None) -> str:
        lines = [
            "Orion Health Graphs",
            "-" * 68,
            "Legend: = ok, ~ warning, X error, ? unknown (oldest to newest)",
        ]
        timelines = [graphs.overall, *graphs.sections]
        if section is not None:
            timelines = [item for item in timelines if item.section == section]
        for timeline in timelines:
            counts = ", ".join(
                f"{state} {count}"
                for state, count in timeline.counts.items()
                if count
            )
            lines.append("")
            lines.append(
                f"{timeline.section.title()} "
                f"({timeline.snapshots} snapshots, {timeline.transitions} changes)"
            )
            lines.append(f"  {timeline.sparkline}")
            lines.append(f"  {timeline.first_at} .. {timeline.last_at}")
            lines.append(f"  {counts}")
        if graphs.skipped:
            lines.append("")
            lines.append(f"{graphs.skipped} malformed snapshot line(s) ignored.")
        return "\n".join(lines)

    @staticmethod
    def _render_dependency_graph(tree) -> str:
        lines = ["Orion Dependency Graph", "-" * 68]
        lines.append(f"{tree.name} [{tree.state.value.upper()}]")
        for index, child in enumerate(tree.children):
            lines.extend(
                child.render("", last=index == len(tree.children) - 1)
            )
        return "\n".join(lines)

    def backup_export(self, name: str | None = None) -> ApplicationResult:
        """Export config and runtime data to a verified backup archive."""
        service = BackupService(self._paths, clock=self._clock)
        try:
            export = service.export(name)
        except (ValueError, OSError) as exc:
            return ApplicationResult.failure(
                f"Backup export failed: {exc}",
                data={"read_only": False},
                errors=(str(exc),),
                next_actions=("backup list",),
            )
        return ApplicationResult.success(
            f"Backup exported: {export.name}\n"
            f"  Files: {export.file_count}\n"
            f"  Size: {export.total_bytes} bytes\n"
            f"  Path: {export.path}\n"
            "Vault and tokens were excluded.",
            data={
                **export.to_dict(),
                "read_only": False,
                "excluded": list(EXCLUDED_PATHS),
            },
            next_actions=("backup list", f"backup verify {export.path}"),
        )

    def backup_list(self) -> ApplicationResult:
        """List backup archives in the backups directory."""
        service = BackupService(self._paths, clock=self._clock)
        backups = service.list_backups()
        if not backups:
            return ApplicationResult.success(
                "No backups found.",
                data={"read_only": True, "backups": []},
                next_actions=("backup export",),
            )
        lines = ["Orion Backups", "-" * 68]
        for info in backups:
            lines.append(
                f"{info.name} ({info.size_bytes} bytes, {info.modified_at})"
            )
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "read_only": True,
                "backups": [info.to_dict() for info in backups],
            },
            next_actions=("backup export",),
        )

    def backup_verify(self, path: str) -> ApplicationResult:
        """Verify a backup archive against its manifest (read-only)."""
        service = BackupService(self._paths, clock=self._clock)
        verification = service.verify(path)
        if not verification.valid:
            detail = "\n".join(f"  - {error}" for error in verification.errors)
            return ApplicationResult.failure(
                f"Backup verification failed:\n{detail}",
                data={**verification.to_dict(), "read_only": True},
                errors=verification.errors,
                next_actions=("backup list",),
            )
        return ApplicationResult.success(
            f"Backup verified: {verification.file_count} file(s), checksums OK.",
            data={**verification.to_dict(), "read_only": True},
            warnings=verification.warnings,
            next_actions=(f"backup import {path}",),
        )

    def backup_import_plan(self, path: str) -> ApplicationResult:
        """Describe what importing a backup would change (read-only)."""
        service = BackupService(self._paths, clock=self._clock)
        try:
            plan = service.plan_import(path)
        except ValueError as exc:
            return ApplicationResult.failure(
                f"Import plan failed: {exc}",
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=(f"backup verify {path}",),
            )
        lines = [
            "Backup Import Plan",
            "-" * 68,
            f"Archive : {plan.path}",
            f"Files   : {len(plan.files)} ({plan.total_bytes} bytes)",
            f"Replace : {len(plan.overwrite)} existing file(s)",
            f"Add     : {len(plan.new)} new file(s)",
            f"Safety backup: {plan.safety_backup_name} (written before any change)",
        ]
        if plan.warnings:
            lines.append("Warnings:")
            for warning in plan.warnings:
                lines.append(f"  - {warning}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={**plan.to_dict(), "read_only": True},
            warnings=plan.warnings,
            next_actions=(f"backup import {path}",),
        )

    def backup_import(self, path: str, *, confirm: bool = False) -> ApplicationResult:
        """Restore a verified backup. Requires explicit confirmation."""
        if not confirm:
            return ApplicationResult.failure(
                "Backup import requires explicit confirmation.",
                data={"read_only": True},
                errors=("Backup import requires explicit confirmation.",),
                next_actions=(f"backup import {path}",),
            )
        service = BackupService(self._paths, clock=self._clock)
        try:
            result = service.import_backup(path, confirm=True)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            self.record_recovery(
                "backup-import", path, status="failed", detail=str(exc)
            )
            return ApplicationResult.failure(
                f"Backup import failed: {exc}",
                data={"read_only": False},
                errors=(str(exc),),
                next_actions=(f"backup verify {path}",),
            )
        recorded = self.record_recovery(
            "backup-import",
            path,
            status="success",
            detail=f"{result.restored} file(s) restored.",
            safety_backup=str(result.safety_backup or ""),
        )
        warnings: tuple[str, ...] = ()
        if recorded is None:
            warnings = ("Recovery history could not be updated.",)
        lines = [f"Backup imported: {result.restored} file(s) restored."]
        if result.safety_backup:
            lines.append(f"Safety backup: {result.safety_backup}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={**result.to_dict(), "read_only": False},
            warnings=warnings,
            next_actions=("health", "recovery"),
        )

    def record_recovery(
        self,
        kind: str,
        target: str,
        *,
        status: str = "success",
        detail: str = "",
        safety_backup: str = "",
    ):
        """Append a recovery record. Returns it, or None when the write failed.

        Raises ValueError for an unknown kind or status (a programming error).
        Callers in recovery paths must never let a recording failure break
        the recovery itself; this method only raises for bad arguments.
        """
        store = RecoveryHistoryStore(self._paths.observability, clock=self._clock)
        try:
            return store.record(
                kind,
                target,
                status=status,
                detail=detail,
                safety_backup=safety_backup,
            )
        except OSError:
            return None

    def recovery_list(
        self,
        kind: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_RECOVERY_LIMIT,
    ) -> ApplicationResult:
        """List recorded recoveries, newest first, optionally filtered."""
        store = RecoveryHistoryStore(self._paths.observability, clock=self._clock)
        try:
            result = store.query(kind=kind, status=status, limit=limit)
        except ValueError as exc:
            return ApplicationResult.failure(
                f"Recovery list failed: {exc}",
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("recovery",),
            )
        if not result.records:
            return ApplicationResult.success(
                "No recoveries recorded yet.",
                data={"read_only": True, "records": [], "total": 0},
                next_actions=("backup export",),
            )
        lines = ["Orion Recovery History", "-" * 68]
        for record in result.records:
            lines.append(
                f"{record.occurred_at} [{record.status.upper()}] "
                f"{record.kind} - {record.target} ({record.record_id})"
            )
        if result.skipped:
            lines.append(f"{result.skipped} malformed record(s) ignored.")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "read_only": True,
                "records": [record.to_dict() for record in result.records],
                "skipped": result.skipped,
                "total": result.total,
            },
            next_actions=("recovery <id>",),
        )

    def recovery_show(self, record_id: str) -> ApplicationResult:
        """Show one recovery record in detail."""
        store = RecoveryHistoryStore(self._paths.observability, clock=self._clock)
        record = store.get((record_id or "").strip())
        if record is None:
            return ApplicationResult.failure(
                f"No recovery record: {record_id}",
                data={"read_only": True},
                errors=(f"No recovery record: {record_id}",),
                next_actions=("recovery",),
            )
        lines = [
            f"Recovery {record.record_id}",
            "-" * 68,
            f"When    : {record.occurred_at}",
            f"Kind    : {record.kind}",
            f"Target  : {record.target}",
            f"Status  : {record.status}",
            f"Detail  : {record.detail}",
        ]
        if record.safety_backup:
            lines.append(f"Safety backup: {record.safety_backup}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={**record.to_dict(), "read_only": True},
            next_actions=("recovery",),
        )
