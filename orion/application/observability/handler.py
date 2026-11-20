"""Application boundary for Orion observability commands."""
from __future__ import annotations

import json
import zipfile

from orion.application.observability.audit import (
    AuditReport,
    SecurityAudit,
)
from orion.application.observability.backup import (
    EXCLUDED_PATHS,
    BackupService,
)
from orion.application.observability.diagnostics import (
    DiagnosticsCollector,
    DiagnosticsReport,
)
from orion.application.observability.docker import (
    AmbiguousContainerError,
    DockerService,
    DockerUnavailableError,
    UnknownContainerError,
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
from orion.application.observability.remediation import (
    RemediationEngine,
    RemediationOutcome,
)
from orion.application.observability.retry import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_LIMIT,
    RetryOutcome,
    RetryPolicy,
    RetryResult,
    RetryRunner,
)
from orion.application.observability.runbooks import (
    RUNBOOKS,
    find_runbook,
    match_runbooks,
    suggest_runbooks,
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
        plugin_manager=None,
        docker_service=None,
    ) -> None:
        self._paths = paths
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._services = services
        self._event_store = event_store
        self._clock = clock
        self._plugin_manager = plugin_manager
        self._docker_service = docker_service
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

    def audit(self) -> ApplicationResult:
        """Run the read-only security audit."""
        report = SecurityAudit(
            self._paths,
            config_manager=self._config_manager,
            provider_manager=self._provider_manager,
            services=self._services,
            plugin_manager=self._plugin_manager,
            clock=self._clock,
        ).run()
        return ApplicationResult.success(
            self._render_audit(report),
            data={**report.to_dict(), "read_only": True},
            warnings=tuple(
                f"{finding.check}: {finding.detail}"
                for finding in report.findings
                if finding.state in {HealthState.WARNING, HealthState.ERROR}
            ),
            next_actions=("diagnostics", "health"),
        )

    @staticmethod
    def _render_audit(report: AuditReport) -> str:
        lines = [
            "Orion Security Audit",
            "-" * 68,
            f"Overall     : {report.overall.value.upper()}",
            f"Generated   : {report.generated_at}",
        ]
        for finding in report.findings:
            lines.append(
                f"{finding.check:<20}: {finding.state.value.upper()} - {finding.detail}"
            )
        return "\n".join(lines)

    def runbooks_list(self) -> ApplicationResult:
        """List the available diagnostic runbooks."""
        lines = ["Orion Diagnostic Runbooks", "-" * 68]
        for runbook in RUNBOOKS:
            lines.append(f"{runbook.runbook_id:<20} {runbook.title}")
            lines.append(f"{'':<20} {runbook.symptom}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "read_only": True,
                "runbooks": [runbook.to_dict() for runbook in RUNBOOKS],
            },
            next_actions=("runbooks diagnose", "runbooks <id>"),
        )

    def runbooks_show(self, runbook_id: str) -> ApplicationResult:
        """Show one diagnostic runbook in full."""
        runbook = find_runbook(runbook_id)
        if runbook is None:
            return ApplicationResult.failure(
                f"No runbook: {runbook_id}",
                data={
                    "read_only": True,
                    "runbooks": [item.runbook_id for item in RUNBOOKS],
                },
                errors=(f"No runbook: {runbook_id}",),
                next_actions=("runbooks",),
            )
        lines = [
            f"Runbook: {runbook.title} ({runbook.runbook_id})",
            "-" * 68,
            f"Symptom: {runbook.symptom}",
            "",
            "Checks:",
        ]
        for index, step in enumerate(runbook.steps, start=1):
            lines.append(f"  {index}. {step.title}")
            lines.append(f"     Run: {step.command}")
            lines.append(f"     Look for: {step.look_for}")
        lines.append("")
        lines.append("Likely causes:")
        for cause in runbook.causes:
            lines.append(f"  - {cause.cause}")
            lines.append(f"    Evidence: {cause.evidence}")
            lines.append(f"    Fix: {cause.fix}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={**runbook.to_dict(), "read_only": True},
            next_actions=("runbooks diagnose",),
        )

    def runbooks_match(self, text: str) -> ApplicationResult:
        """Find runbooks matching a symptom description."""
        matched = match_runbooks(text)
        if not matched:
            return ApplicationResult.success(
                f"No runbook matches: {text}",
                data={"read_only": True, "runbooks": [], "query": text},
                next_actions=("runbooks",),
            )
        lines = [f"Runbooks matching: {text}", "-" * 68]
        for runbook in matched:
            lines.append(f"{runbook.runbook_id:<20} {runbook.title}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "read_only": True,
                "runbooks": [runbook.to_dict() for runbook in matched],
                "query": text,
            },
            next_actions=tuple(f"runbooks {runbook.runbook_id}" for runbook in matched),
        )

    def runbooks_diagnose(self) -> ApplicationResult:
        """Run the health check and suggest runbooks for live problems."""
        report = self.checker.check()
        suggestions = suggest_runbooks(report)
        if not suggestions:
            return ApplicationResult.success(
                "Health check found no problems; no runbook is suggested.",
                data={**report.to_dict(), "read_only": True, "suggestions": []},
                next_actions=("runbooks",),
            )
        lines = [
            "Suggested runbooks",
            "-" * 68,
            f"Health check overall: {report.overall.value.upper()}",
        ]
        for runbook, reason in suggestions:
            lines.append("")
            lines.append(f"{runbook.runbook_id} - {runbook.title}")
            lines.append(f"  Why: {reason}")
            lines.append(f"  See: runbooks {runbook.runbook_id}")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                **report.to_dict(),
                "read_only": True,
                "suggestions": [
                    {"id": runbook.runbook_id, "title": runbook.title, "reason": reason}
                    for runbook, reason in suggestions
                ],
            },
            next_actions=tuple(
                f"runbooks {runbook.runbook_id}" for runbook, _ in suggestions
            ),
        )

    def retry_provider(
        self, name: str, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
    ) -> ApplicationResult:
        """Retry a provider connection test, then verify it end to end.

        The attempt connects and lists models; the verification confirms
        the provider reports configured and the configured model (if any)
        is in the returned model list.
        """
        key = (name or "").strip().lower()
        if key not in {"ollama", "openai", "gemini"}:
            return ApplicationResult.failure(
                f"Unknown provider: {name}",
                data={"read_only": True, "providers": ["ollama", "openai", "gemini"]},
                errors=(f"Unknown provider: {name}",),
                next_actions=("ai providers",),
            )
        try:
            policy = RetryPolicy(max_attempts=max_attempts)
        except ValueError as exc:
            return ApplicationResult.failure(
                str(exc),
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("retry provider ollama",),
            )

        def _attempt() -> RetryOutcome:
            models = self._provider_manager.test_connection(key)
            return RetryOutcome(
                True,
                f"{len(models)} models visible",
                {"models": list(models)},
            )

        def _verify(outcome: RetryOutcome) -> RetryOutcome:
            configured_model = ""
            if self._config_manager is not None:
                configured_model = (
                    self._config_manager.get(f"providers.{key}.model", "") or ""
                )
            models = list(outcome.evidence.get("models", ()))
            if configured_model and configured_model not in models:
                return RetryOutcome(
                    False,
                    f"Connected, but configured model {configured_model} "
                    "is not in the model list.",
                )
            for status in self._provider_manager.statuses():
                if status.key == key and not status.configured:
                    return RetryOutcome(
                        False,
                        f"Connected, but {key} still reports not configured.",
                    )
            if configured_model:
                return RetryOutcome(
                    True,
                    f"Connection verified; configured model {configured_model} "
                    "is available.",
                )
            return RetryOutcome(
                True, "Connection verified; no default model configured."
            )

        result = RetryRunner(policy=policy).run(
            "provider", key, attempt_fn=_attempt, verify_fn=_verify
        )
        return self._retry_result(result)

    def retry_health(
        self, section: str | None = None, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
    ) -> ApplicationResult:
        """Retry a health check until it is OK, then verify it holds.

        The attempt runs the health check; the verification re-runs it and
        requires the same section to still be OK.
        """
        normalized = (section or "").strip().lower() or None
        if normalized is not None and normalized not in HEALTH_SECTIONS:
            return ApplicationResult.failure(
                f"Unknown health section: {section}",
                data={"read_only": True, "sections": list(HEALTH_SECTIONS)},
                errors=(f"Unknown health section: {section}",),
                next_actions=("health",),
            )
        try:
            policy = RetryPolicy(max_attempts=max_attempts)
        except ValueError as exc:
            return ApplicationResult.failure(
                str(exc),
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("retry health",),
            )

        def _attempt() -> RetryOutcome:
            report = self.checker.check(normalized)
            if report.overall is HealthState.OK:
                return RetryOutcome(True, "Health check is OK.")
            problems = "; ".join(
                f"{check.name}: {check.detail}"
                for check in report.checks
                if check.state in {HealthState.WARNING, HealthState.ERROR}
            )
            return RetryOutcome(
                False, f"Health check is {report.overall.value}: {problems}"
            )

        def _verify(_outcome: RetryOutcome) -> RetryOutcome:
            report = self.checker.check(normalized)
            if report.overall is HealthState.OK:
                return RetryOutcome(
                    True, "Re-check confirms the health check is still OK."
                )
            return RetryOutcome(
                False,
                f"Re-check shows {report.overall.value}; the recovery did not hold.",
            )

        result = RetryRunner(policy=policy).run(
            "health",
            normalized or "all",
            attempt_fn=_attempt,
            verify_fn=_verify,
        )
        return self._retry_result(result)

    def retry_backup(
        self, path: str, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
    ) -> ApplicationResult:
        """Retry a backup verification, then verify it with a fresh read.

        The attempt verifies the archive against its manifest; the
        verification re-reads the archive independently and verifies again.
        """
        if not (path or "").strip():
            return ApplicationResult.failure(
                "retry backup needs an archive path.",
                data={"read_only": True},
                errors=("retry backup needs an archive path.",),
                next_actions=("backup list",),
            )
        try:
            policy = RetryPolicy(max_attempts=max_attempts)
        except ValueError as exc:
            return ApplicationResult.failure(
                str(exc),
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("backup list",),
            )
        service = BackupService(self._paths, clock=self._clock)

        def _attempt() -> RetryOutcome:
            verification = service.verify(path)
            if verification.valid:
                return RetryOutcome(
                    True,
                    f"Backup verified: {verification.file_count} files, "
                    "manifest intact.",
                )
            detail = "; ".join(verification.errors) or "Backup verification failed."
            return RetryOutcome(False, detail)

        def _verify(_outcome: RetryOutcome) -> RetryOutcome:
            verification = service.verify(path)
            if verification.valid:
                return RetryOutcome(
                    True, "Independent re-read confirms the archive and manifest."
                )
            detail = "; ".join(verification.errors) or "Re-read failed."
            return RetryOutcome(False, detail)

        result = RetryRunner(policy=policy).run(
            "backup", path, attempt_fn=_attempt, verify_fn=_verify
        )
        return self._retry_result(result)

    @staticmethod
    def _retry_result(result: RetryResult) -> ApplicationResult:
        message = ObservabilityApplicationHandler._render_retry(result)
        data: dict[str, object] = {**result.to_dict(), "read_only": True}
        if result.ok:
            return ApplicationResult.success(
                message,
                data=data,
                next_actions=("health",),
            )
        if result.succeeded:
            error = f"Verification failed: {result.verification}"
        else:
            error = result.attempts[-1].detail if result.attempts else "Retry failed."
        return ApplicationResult.failure(
            message,
            data=data,
            errors=(error,),
            next_actions=("runbooks diagnose", "runbooks"),
        )

    @staticmethod
    def _render_retry(result: RetryResult) -> str:
        lines = [
            f"Retry: {result.operation} {result.target}",
            "-" * 68,
        ]
        for attempt in result.attempts:
            state = "OK" if attempt.ok else "FAILED"
            lines.append(
                f"Attempt {attempt.attempt}: {state} "
                f"({attempt.duration_seconds:.2f}s) - {attempt.detail}"
            )
        lines.append(
            f"Verified  : {'yes' if result.verified else 'no'} - {result.verification}"
        )
        if result.ok:
            lines.append("Result    : succeeded and verified")
        elif result.succeeded:
            lines.append("Result    : operation succeeded but verification failed")
        else:
            lines.append("Result    : failed after all attempts")
        return "\n".join(lines)

    def _remediation_engine(self) -> RemediationEngine:
        return RemediationEngine(
            paths=self._paths,
            checker=self.checker,
            config_manager=self._config_manager,
            provider_manager=self._provider_manager,
            clock=self._clock,
        )

    def remediate_plan(self) -> ApplicationResult:
        """Propose fixes for current health problems. Applies nothing."""
        plan = self._remediation_engine().propose()
        if not plan.proposals:
            return ApplicationResult.success(
                "No problems found; nothing to remediate.",
                data={**plan.to_dict(), "read_only": True},
                next_actions=("health",),
            )
        lines = [
            "Proposed remediations (nothing has been applied)",
            "-" * 68,
        ]
        for proposal in plan.proposals:
            lines.append(f"{proposal.proposal_id} - {proposal.title}")
            lines.append(f"  Problem   : {proposal.problem}")
            lines.append(f"  Fix       : {proposal.fix}")
            lines.append(
                "  Risk      : "
                f"{proposal.risk}; reversible: "
                f"{'yes' if proposal.reversible else 'no'}"
            )
            files = proposal.details.get("files")
            if files:
                lines.append(f"  Files     : {', '.join(str(item) for item in files)}")
        lines.append("")
        lines.append("Apply one with: remediate apply <id>  (asks for approval first)")
        return ApplicationResult.success(
            "\n".join(lines),
            data={**plan.to_dict(), "read_only": True},
            next_actions=tuple(
                f"remediate apply {proposal.proposal_id}"
                for proposal in plan.proposals
            ),
        )

    def remediate_apply(
        self, proposal_id: str, *, confirm: bool = False
    ) -> ApplicationResult:
        """Apply one approved remediation, verify it, and record it.

        Without confirm=True nothing is applied. Applied fixes are
        recorded in the recovery history, verified or not.
        """
        engine = self._remediation_engine()
        outcome = engine.apply(proposal_id, confirm=confirm)
        return self._remediation_outcome(outcome)

    def remediate_apply_all(self, *, confirm: bool = False) -> ApplicationResult:
        """Apply every proposed remediation, with one approval for the plan."""
        engine = self._remediation_engine()
        plan = engine.propose()
        if not plan.proposals:
            return ApplicationResult.success(
                "No problems found; nothing to remediate.",
                data={**plan.to_dict(), "read_only": True},
                next_actions=("health",),
            )
        outcomes = [
            engine.apply(proposal.proposal_id, confirm=confirm)
            for proposal in plan.proposals
        ]
        for outcome in outcomes:
            if outcome.applied:
                self.record_recovery(
                    "remediation",
                    outcome.proposal_id,
                    status="success" if outcome.verified else "failed",
                    detail=outcome.detail,
                )
        lines = ["Remediation results", "-" * 68]
        for outcome in outcomes:
            state = (
                "applied and verified"
                if outcome.applied and outcome.verified
                else "applied, not verified"
                if outcome.applied
                else "not applied"
            )
            lines.append(f"{outcome.proposal_id}: {state} - {outcome.detail}")
        message = "\n".join(lines)
        data: dict[str, object] = {
            "read_only": False,
            "outcomes": [outcome.to_dict() for outcome in outcomes],
        }
        if all(outcome.applied and outcome.verified for outcome in outcomes):
            return ApplicationResult.success(
                message,
                data=data,
                next_actions=("health", "recovery"),
            )
        return ApplicationResult.failure(
            message,
            data=data,
            errors=tuple(
                outcome.detail for outcome in outcomes if not outcome.verified
            ),
            next_actions=("runbooks diagnose", "health"),
        )

    def _remediation_outcome(self, outcome: RemediationOutcome) -> ApplicationResult:
        if outcome.applied:
            self.record_recovery(
                "remediation",
                outcome.proposal_id,
                status="success" if outcome.verified else "failed",
                detail=outcome.detail,
            )
        lines = [
            f"Remediation: {outcome.proposal_id}",
            "-" * 68,
            f"Applied   : {'yes' if outcome.applied else 'no'}",
            f"Verified  : {'yes' if outcome.verified else 'no'}",
            f"Detail    : {outcome.detail}",
        ]
        message = "\n".join(lines)
        data: dict[str, object] = {**outcome.to_dict(), "read_only": not outcome.applied}
        if outcome.applied and outcome.verified:
            return ApplicationResult.success(
                message,
                data=data,
                next_actions=("health", "recovery"),
            )
        if not outcome.applied and outcome.detail.startswith("No longer needed"):
            return ApplicationResult.success(
                message,
                data=data,
                next_actions=("health",),
            )
        return ApplicationResult.failure(
            message,
            data=data,
            errors=(outcome.detail,),
            next_actions=("remediate", "runbooks diagnose"),
        )

    def _docker(self) -> DockerService:
        if self._docker_service is not None:
            return self._docker_service
        return DockerService()

    @staticmethod
    def _docker_unavailable(exc: DockerUnavailableError) -> ApplicationResult:
        if exc.reason == "cli_missing":
            message = (
                "Docker is not available: the `docker` command was not "
                "found on this machine."
            )
        elif exc.reason == "timeout":
            message = (
                "Docker did not answer in time; the daemon may be busy "
                "or unreachable."
            )
        else:
            message = f"Docker is not available: {exc.detail}"
        return ApplicationResult.failure(
            message,
            data={"read_only": True, "docker_available": False},
            errors=(message,),
            next_actions=("diagnostics",),
        )

    @staticmethod
    def _docker_unknown(exc: Exception) -> ApplicationResult:
        message = str(exc)
        return ApplicationResult.failure(
            message,
            data={"read_only": True},
            errors=(message,),
            next_actions=("docker list",),
        )

    def docker_list(self) -> ApplicationResult:
        """List every Docker container, running or stopped."""
        try:
            listing = self._docker().list_containers()
        except DockerUnavailableError as exc:
            return self._docker_unavailable(exc)
        return ApplicationResult.success(
            self._render_docker_list(listing),
            data={**listing.to_dict(), "read_only": True},
            next_actions=("docker inspect <name>", "docker health"),
        )

    @staticmethod
    def _render_docker_list(listing) -> str:
        lines = [
            f"Docker Containers ({len(listing.containers)})",
            "-" * 68,
        ]
        if not listing.containers:
            lines.append("No containers found.")
        for item in listing.containers:
            ports = ", ".join(item.ports) if item.ports else "-"
            lines.append(
                f"{item.name:<18} {item.state:<10} {item.health:<10} "
                f"{item.image:<22} {ports}"
            )
        if listing.skipped:
            lines.append(
                f"{listing.skipped} malformed container line(s) ignored."
            )
        return "\n".join(lines)

    def docker_inspect(self, name: str) -> ApplicationResult:
        """Show full detail for one Docker container."""
        try:
            detail = self._docker().inspect_container(name)
        except DockerUnavailableError as exc:
            return self._docker_unavailable(exc)
        except (UnknownContainerError, AmbiguousContainerError) as exc:
            return self._docker_unknown(exc)
        return ApplicationResult.success(
            self._render_docker_inspect(detail),
            data={**detail.to_dict(), "read_only": True},
            next_actions=("docker list", "docker logs " + detail.name),
        )

    @staticmethod
    def _render_docker_inspect(detail) -> str:
        lines = [
            f"Container: {detail.name} ({detail.container_id[:12]})",
            "-" * 68,
            f"State     : {detail.state}",
            f"Health    : {detail.health}",
            f"Image     : {detail.image}",
            f"Created   : {detail.created}",
        ]
        if detail.started_at:
            lines.append(f"Started   : {detail.started_at}")
        if detail.finished_at and not detail.finished_at.startswith("0001-"):
            lines.append(
                f"Finished  : {detail.finished_at} (exit {detail.exit_code})"
            )
        lines.append("Ports     :")
        if detail.ports:
            for port in detail.ports:
                lines.append(f"  {port}")
        else:
            lines.append("  -")
        lines.append("Mounts    :")
        if detail.mounts:
            for mount in detail.mounts:
                lines.append(f"  {mount}")
        else:
            lines.append("  -")
        return "\n".join(lines)

    def docker_control(
        self, action: str, name: str, *, confirm: bool = False
    ) -> ApplicationResult:
        """Start, stop, or restart one container (needs confirm=True).

        Without confirm=True nothing is applied. Applied actions are
        recorded in the recovery history, verified or not.
        """
        normalized = (action or "").strip().lower()
        if not confirm:
            message = (
                f"docker {normalized} needs interactive approval; "
                "nothing was changed."
            )
            return ApplicationResult.failure(
                message,
                data={"read_only": True, "action": normalized, "name": name},
                errors=(message,),
                next_actions=(f"docker {normalized} {name}",),
            )
        try:
            outcome = self._docker().control(normalized, name)
        except DockerUnavailableError as exc:
            return self._docker_unavailable(exc)
        except (UnknownContainerError, AmbiguousContainerError) as exc:
            return self._docker_unknown(exc)
        except ValueError as exc:
            return ApplicationResult.failure(
                str(exc),
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("docker list",),
            )
        if outcome.applied:
            self.record_recovery(
                "docker",
                outcome.name,
                status="success" if outcome.verified else "failed",
                detail=outcome.detail,
            )
        lines = [
            f"Docker {outcome.action}: {outcome.name}",
            "-" * 68,
            f"Applied   : {'yes' if outcome.applied else 'no'}",
            f"Verified  : {'yes' if outcome.verified else 'no'}",
            f"State     : {outcome.state_before} -> {outcome.state_after}",
            f"Detail    : {outcome.detail}",
        ]
        message = "\n".join(lines)
        data: dict[str, object] = {
            **outcome.to_dict(),
            "read_only": not outcome.applied,
        }
        if outcome.applied and outcome.verified:
            return ApplicationResult.success(
                message,
                data=data,
                next_actions=("docker list", "recovery --kind docker"),
            )
        return ApplicationResult.failure(
            message,
            data=data,
            errors=(outcome.detail,),
            next_actions=(f"docker inspect {outcome.name}", "docker list"),
        )

    def docker_health(self) -> ApplicationResult:
        """Summarize Docker container health across every container."""
        try:
            summary = self._docker().health_summary()
        except DockerUnavailableError as exc:
            return self._docker_unavailable(exc)
        lines = [
            f"Docker Health: {summary.overall.upper()}",
            "-" * 68,
            f"Containers : {summary.total} "
            f"({summary.running} running, {summary.exited} exited)",
            f"Healthy    : {summary.healthy}",
            f"Unhealthy  : {summary.unhealthy}",
            f"Starting   : {summary.starting}",
            f"No healthcheck: {summary.no_healthcheck}",
        ]
        if summary.unhealthy:
            lines.append("")
            lines.append("Unhealthy containers:")
            for item in summary.containers:
                if item.health == "unhealthy":
                    lines.append(f"  {item.name} ({item.image})")
        message = "\n".join(lines)
        data: dict[str, object] = {
            **summary.to_dict(),
            "read_only": True,
        }
        if summary.overall == "error":
            return ApplicationResult.failure(
                message,
                data=data,
                errors=(
                    f"{summary.unhealthy} container(s) unhealthy.",
                ),
                next_actions=("docker list", "docker logs <name>"),
            )
        return ApplicationResult.success(
            message,
            data=data,
            next_actions=("docker list",),
        )

    def docker_logs(self, name: str, tail: int = 50) -> ApplicationResult:
        """Show recent log lines for one container (read-only, capped)."""
        try:
            result = self._docker().container_logs(name, tail=tail)
        except DockerUnavailableError as exc:
            return self._docker_unavailable(exc)
        except (UnknownContainerError, AmbiguousContainerError) as exc:
            return self._docker_unknown(exc)
        except ValueError as exc:
            return ApplicationResult.failure(
                str(exc),
                data={"read_only": True},
                errors=(str(exc),),
                next_actions=("docker list",),
            )
        lines = [
            f"Logs: {result.name} (last {len(result.lines)} line(s))",
            "-" * 68,
        ]
        if result.lines:
            lines.extend(result.lines)
        else:
            lines.append("(no log output)")
        if result.truncated:
            lines.append(
                "(output capped; oldest lines dropped — "
                "use --tail to narrow the window)"
            )
        return ApplicationResult.success(
            "\n".join(lines),
            data={**result.to_dict(), "read_only": True},
            next_actions=("docker list", "docker inspect " + result.name),
        )
