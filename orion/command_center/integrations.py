"""Provider-neutral AI Team integration for Orion Command Center."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from orion.command_center.models import (
    ActivitySeverity,
    ActivitySourceType,
    ApprovalState,
    EXTERNAL_REFERENCE_PATTERN,
    Job,
    JobStatus,
    JobTeamIntegration,
    TeamIntegrationLink,
    WorkflowAgentAssignment,
    WorkflowStage,
    normalize_id,
    parse_timestamp,
    utc_now,
)
from orion.command_center.repository import RepositoryDiagnostic


ENGINEERING_WORKFLOW_ID = "engineering"
ENGINEERING_STAGE_SPECS = (
    (WorkflowStage.PLANNING, ("planner",), ("planner", "planning")),
    (WorkflowStage.ARCHITECTURE, ("architect",), ("architect", "architecture")),
    (
        WorkflowStage.IMPLEMENTATION,
        ("software-engineer",),
        ("software engineer", "implementation", "developer"),
    ),
    (
        WorkflowStage.ENGINEERING_REVIEW,
        ("engineer",),
        ("engineering reviewer", "engineer", "validation"),
    ),
    (
        WorkflowStage.FINAL_REVIEW,
        ("reviewer",),
        ("code reviewer", "reviewer", "final review"),
    ),
)
WORKFLOW_EXPECTED_STAGES = (
    WorkflowStage.PLANNING,
    WorkflowStage.ARCHITECTURE,
    WorkflowStage.AWAITING_APPROVAL,
    WorkflowStage.IMPLEMENTATION,
    WorkflowStage.TESTING,
    WorkflowStage.ENGINEERING_REVIEW,
    WorkflowStage.DOCUMENTATION,
    WorkflowStage.FINAL_REVIEW,
    WorkflowStage.AWAITING_REVIEW,
    WorkflowStage.COMPLETED,
)
TERMINAL_EXTERNAL_STATUSES = frozenset({
    "completed", "failed", "cancelled", "rolled_back",
})
ACTIVE_EXTERNAL_STATUSES = frozenset({
    "planning", "executing", "implementation", "testing", "running",
})


def _safe_text(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


def _same_path(first: str | Path, second: str | Path) -> bool:
    return os.path.normcase(str(Path(first).expanduser().resolve())) == os.path.normcase(
        str(Path(second).expanduser().resolve())
    )


@dataclass(frozen=True)
class ProviderRouteSummary:
    agent_id: str
    provider: str
    model: str
    source: str
    fallback: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class ResolvedCommandCenterWorkflow:
    workflow_id: str
    source: str
    department_id: str
    agent_ids: tuple[str, ...]
    role_assignments: tuple[WorkflowAgentAssignment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.workflow_id,
            "source": self.source,
            "department_id": self.department_id,
            "agent_ids": list(self.agent_ids),
            "role_assignments": [
                item.to_dict() for item in self.role_assignments
            ],
        }


@dataclass(frozen=True)
class LaunchPreview:
    job_id: str
    allowed: bool
    workflow: ResolvedCommandCenterWorkflow | None
    department_name: str
    workspace_root: str
    provider_routes: tuple[ProviderRouteSummary, ...]
    approval_required: bool
    intended_team_task_type: str
    expected_stages: tuple[str, ...]
    execution_engine: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "allowed": self.allowed,
            "workflow": None if self.workflow is None else self.workflow.to_dict(),
            "department": self.department_name,
            "workspace": (
                Path(self.workspace_root).name if self.workspace_root else ""
            ),
            "provider_routes": [
                item.to_dict() for item in self.provider_routes
            ],
            "approval_required": self.approval_required,
            "intended_team_task_type": self.intended_team_task_type,
            "expected_stages": list(self.expected_stages),
            "execution_engine": self.execution_engine,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class LaunchResult:
    job: Job
    team_task_id: str
    preview: LaunchPreview
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        integration = JobTeamIntegration.from_job(self.job)
        link = integration.active_link
        return {
            "job_id": self.job.job_id,
            "status": self.job.status.value,
            "stage": self.job.current_stage,
            "progress": self.job.progress,
            "team_task_id": self.team_task_id,
            "team_run_id": link.team_run_id if link else "",
            "approval_state": self.job.approval_state.value,
            "next_action": link.next_action if link else "",
            "warnings": list(self.warnings),
            "preview": self.preview.to_dict(),
        }


@dataclass(frozen=True)
class SyncResult:
    job: Job
    changed: bool
    warnings: tuple[str, ...]
    activity_events: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        integration = JobTeamIntegration.from_job(self.job)
        link = integration.active_link
        if link is None and integration.links:
            link = integration.links[-1]
        return {
            "job_id": self.job.job_id,
            "changed": self.changed,
            "status": self.job.status.value,
            "stage": self.job.current_stage,
            "progress": self.job.progress,
            "approval_state": self.job.approval_state.value,
            "active_agent_id": link.active_agent_id if link else "",
            "team_task_id": link.team_task_id if link else "",
            "team_run_id": link.team_run_id if link else "",
            "external_status": link.external_status if link else "",
            "next_action": link.next_action if link else "",
            "warnings": list(self.warnings),
            "activity_events": list(self.activity_events),
        }


@dataclass(frozen=True)
class _RunReference:
    run_id: str
    started_at: str = ""


@dataclass(frozen=True)
class _RunInspection:
    runs: tuple[Any, ...] = ()
    unresolved: tuple[_RunReference, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _WorkflowProjection:
    status: JobStatus
    stage: WorkflowStage
    progress: int
    approval_state: ApprovalState
    active_agent_id: str
    external_status: str
    next_action: str
    team_run_id: str = ""
    approval_id: str = ""
    execution_engine: str = ""
    result_summary: str = ""
    error_summary: str = ""
    warnings: tuple[str, ...] = ()
    active_link: bool = True


class LaunchValidationError(ValueError):
    """Raised with a complete, read-only launch preview."""

    def __init__(self, preview: LaunchPreview) -> None:
        self.preview = preview
        super().__init__("; ".join(preview.errors) or "Command Center launch is invalid.")


class CommandCenterTeamIntegrationService:
    """Launch planning through Team and mirror authoritative workflow state."""

    def __init__(
        self,
        command_center,
        team_orchestrator,
        agent_manager,
        *,
        workspace_manager=None,
        service_registry=None,
        external_state_source=None,
        execution_engines=None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.command_center = command_center
        self.team = team_orchestrator
        self.agent_manager = agent_manager
        self.workspace_manager = workspace_manager
        self.service_registry = service_registry
        self.external_state_source = external_state_source
        self.execution_engines = execution_engines
        self._now = now or utc_now

    def validate_launch(
        self,
        job_id: str,
        *,
        workflow: str = "",
        workspace: str = "",
    ) -> LaunchPreview:
        return self.preview_launch(
            job_id,
            workflow=workflow,
            workspace=workspace,
        )

    def preview_launch(
        self,
        job_id: str,
        *,
        workflow: str = "",
        workspace: str = "",
    ) -> LaunchPreview:
        errors: list[str] = []
        warnings: list[str] = []
        routes: tuple[ProviderRouteSummary, ...] = ()
        resolved: ResolvedCommandCenterWorkflow | None = None
        department_name = ""
        workspace_root = ""

        try:
            job = self.command_center.job(job_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return LaunchPreview(
                str(job_id).strip(),
                False,
                None,
                "",
                "",
                (),
                True,
                "ai_team_plan",
                tuple(item.value for item in WORKFLOW_EXPECTED_STAGES),
                self._execution_engine_summary(),
                (),
                (_safe_text(exc),),
            )

        if job.status not in {JobStatus.DRAFT, JobStatus.QUEUED}:
            errors.append(
                f"Job status {job.status.value} cannot be launched."
            )
        try:
            integration = JobTeamIntegration.from_job(job)
            if integration.active_link is not None:
                errors.append(
                    "Job already has an active AI Team link; synchronize it instead."
                )
        except ValueError as exc:
            errors.append(_safe_text(exc))

        department = None
        if job.department_id:
            try:
                department = self.command_center.department(job.department_id)
                department_name = department.name
                if not department.enabled:
                    errors.append(
                        f"Department is disabled: {department.department_id}"
                    )
            except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
                errors.append(_safe_text(exc))
        elif not job.assigned_agent_ids:
            errors.append(
                "Launch requires a department or explicit job agent assignments."
            )

        try:
            resolved = self._resolve_workflow(job, department, workflow)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            errors.append(_safe_text(exc))

        try:
            workspace_root = self._resolve_workspace(job, workspace)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            errors.append(_safe_text(exc))

        errors.extend(self._required_service_errors())
        if len(job.goal) > 4_000:
            errors.append(
                "AI Team planning supports goals of 4,000 characters or fewer."
            )
        if resolved is not None:
            try:
                routes = self._provider_routes(job, resolved)
            except (
                ConnectionError,
                FileNotFoundError,
                OSError,
                PermissionError,
                RuntimeError,
                ValueError,
            ) as exc:
                errors.append(_safe_text(exc))
        if not routes and resolved is not None and not errors:
            errors.append("No viable provider route is available for planning.")

        if self.execution_engines is None:
            warnings.append(
                "No execution engine service is registered; planning may launch, "
                "but implementation remains unavailable."
            )

        return LaunchPreview(
            job.job_id,
            not errors,
            resolved,
            department_name,
            workspace_root,
            routes,
            True,
            "ai_team_plan",
            tuple(item.value for item in WORKFLOW_EXPECTED_STAGES),
            self._execution_engine_summary(),
            tuple(dict.fromkeys(warnings)),
            tuple(dict.fromkeys(errors)),
        )

    def launch(
        self,
        job_id: str,
        *,
        workflow: str = "",
        workspace: str = "",
    ) -> LaunchResult:
        job = self.command_center.job(job_id)
        self._activity(
            "job.launch_requested",
            job,
            "Command Center job launch was explicitly requested.",
        )
        preview = self.preview_launch(
            job_id,
            workflow=workflow,
            workspace=workspace,
        )
        if not preview.allowed or preview.workflow is None:
            self._activity(
                "job.launch_failed",
                job,
                "Command Center job launch validation failed.",
                severity=ActivitySeverity.ERROR,
                metadata={"errors": list(preview.errors)[:10]},
            )
            raise LaunchValidationError(preview)
        self._activity(
            "job.launch_validated",
            job,
            "Command Center job launch validation passed.",
            metadata={
                "workflow_id": preview.workflow.workflow_id,
                "agent_count": len(preview.workflow.agent_ids),
            },
        )

        team_task_id = self._reserve_team_task_id()
        timestamp = self._timestamp()
        link = TeamIntegrationLink.create(
            team_task_id=team_task_id,
            workflow_id=preview.workflow.workflow_id,
            linked_at=timestamp,
            role_assignments=preview.workflow.role_assignments,
        )
        link = replace(
            link,
            external_status="planning",
            active_agent_id=preview.workflow.agent_ids[0],
            next_action="AI Team planning is in progress.",
        )
        job = self._save_linked_job(
            job,
            link,
            status=JobStatus.PLANNING,
            stage=WorkflowStage.PLANNING,
            progress=5,
            workspace_root=preview.workspace_root,
            approval_state=ApprovalState.NOT_REQUIRED,
        )
        self._activity(
            "job.linked_to_team_task",
            job,
            f"Linked job to AI Team task {team_task_id}.",
            metadata={
                "team_task_id": team_task_id,
                "workflow_id": preview.workflow.workflow_id,
            },
        )
        self._activity(
            "workflow.stage_started",
            job,
            "Workflow stage started: planning.",
            agent_id=link.active_agent_id,
            metadata={"stage": WorkflowStage.PLANNING.value},
        )
        self._activity(
            "workflow.agent_started",
            job,
            f"Agent {link.active_agent_id} started planning work.",
            agent_id=link.active_agent_id,
            metadata={"stage": WorkflowStage.PLANNING.value},
        )

        try:
            task = self.team.plan(
                job.goal,
                selected_agents=list(preview.workflow.agent_ids),
                provider="auto",
                model="auto",
                task_id=team_task_id,
            )
        except Exception as exc:
            try:
                task = self.team.task(team_task_id)
            except (FileNotFoundError, OSError, ValueError):
                task = None
            if task is not None:
                self.sync_from_team_task(task.task_id)
            else:
                self._apply_projection(
                    job.job_id,
                    link,
                    _WorkflowProjection(
                        JobStatus.FAILED,
                        WorkflowStage.FAILED,
                        job.progress,
                        ApprovalState.CANCELLED,
                        "",
                        "failed",
                        "Inspect the safe Team planning failure and correct configuration.",
                        error_summary=(
                            f"AI Team planning stopped safely ({type(exc).__name__})."
                        ),
                        active_link=False,
                    ),
                )
            raise

        synced = self.sync_from_team_task(task.task_id)
        return LaunchResult(
            synced.job,
            task.task_id,
            preview,
            synced.warnings,
        )

    def link_team_task(
        self,
        job_id: str,
        team_task_id: str,
        *,
        workflow_id: str,
        role_assignments: tuple[WorkflowAgentAssignment, ...] = (),
    ) -> Job:
        job = self.command_center.job(job_id)
        integration = JobTeamIntegration.from_job(job)
        if integration.active_link is not None:
            if integration.active_link.team_task_id == team_task_id:
                return job
            raise ValueError("Job already has an active AI Team link.")
        link = TeamIntegrationLink.create(
            team_task_id=team_task_id,
            workflow_id=workflow_id,
            linked_at=self._timestamp(),
            role_assignments=role_assignments,
        )
        return self._save_linked_job(
            job,
            link,
            status=JobStatus.QUEUED,
            stage=WorkflowStage.QUEUED,
            progress=0,
            workspace_root=job.workspace_reference,
            approval_state=job.approval_state,
        )

    def link_team_run(self, job_id: str, team_run_id: str) -> Job:
        job = self.command_center.job(job_id)
        integration = JobTeamIntegration.from_job(job)
        link = integration.active_link
        if link is None:
            raise ValueError("Job does not have an active AI Team link.")
        if link.team_run_id == team_run_id:
            return job
        if link.team_run_id:
            raise ValueError("Job already links a different AI Team run.")
        updated_link = replace(link, team_run_id=team_run_id)
        updated = self._save_link_only(job, integration.with_link(updated_link))
        self._activity(
            "job.linked_to_team_run",
            updated,
            f"Linked job to AI Team run {team_run_id}.",
            metadata={"team_run_id": team_run_id},
        )
        return updated

    def sync_from_team_task(self, team_task_id: str) -> SyncResult:
        job = self._job_for_team_task(team_task_id)
        if job is None:
            raise FileNotFoundError(
                f"No Command Center job links AI Team task {team_task_id}."
            )
        return self.sync_job(job.job_id)

    def sync_from_team_run(self, team_run_id: str) -> SyncResult:
        job = self._job_for_team_run(team_run_id)
        if job is None and self.external_state_source is not None:
            run = self.external_state_source.run(team_run_id)
            job = self._job_for_team_task(getattr(run, "team_task_id", ""))
        if job is None:
            raise FileNotFoundError(
                f"No Command Center job links AI Team run {team_run_id}."
            )
        return self.sync_job(job.job_id)

    def sync_job(self, job_id: str) -> SyncResult:
        original = self.command_center.job(job_id)
        integration = JobTeamIntegration.from_job(original)
        link = integration.active_link
        if link is None:
            raise ValueError("Job does not have an active AI Team link.")
        try:
            task = self.team.task(link.team_task_id)
        except FileNotFoundError:
            warning = "Linked AI Team task is missing."
            projection = _WorkflowProjection(
                original.status,
                WorkflowStage.parse(
                    original.current_stage or WorkflowStage.QUEUED.value
                ),
                original.progress,
                original.approval_state,
                "",
                "missing_team_task",
                "Restore or inspect the missing AI Team task record.",
                warnings=(warning,),
            )
            return self._apply_projection(original.job_id, link, projection)

        approvals = self._approvals_for_task(link.team_task_id)
        inspection = self._inspect_runs_for_task(
            link.team_task_id,
            linked_run_id=link.team_run_id,
        )
        run = self._select_run(link, inspection.runs)
        approval = self._select_approval(link, approvals, run)
        if link.approval_id and approval is None:
            return self._apply_projection(
                original.job_id,
                link,
                _WorkflowProjection(
                    original.status,
                    WorkflowStage.parse(
                        original.current_stage or WorkflowStage.QUEUED.value
                    ),
                    original.progress,
                    original.approval_state,
                    link.active_agent_id,
                    "missing_approval",
                    "Restore or inspect the missing authoritative approval.",
                    team_run_id=link.team_run_id,
                    approval_id=link.approval_id,
                    execution_engine=link.execution_engine,
                    warnings=("Linked authoritative approval is missing.",),
                ),
            )
        projection = self._project(task, approval, run, link)
        run_warnings = list(inspection.warnings)
        if run is None:
            run_warnings.extend(
                self._unresolved_run_warning(item.run_id)
                for item in inspection.unresolved
            )
        if run_warnings:
            projection = replace(
                projection,
                warnings=tuple(dict.fromkeys(
                    (*projection.warnings, *run_warnings)
                )),
            )
        return self._apply_projection(original.job_id, link, projection)

    def handle_team_event(self, event: Any) -> SyncResult | None:
        team_task_id = _safe_text(
            getattr(event, "team_task_id", "")
            or getattr(event, "task_id", ""),
            100,
        )
        team_run_id = _safe_text(
            getattr(event, "team_run_id", "")
            or getattr(event, "run_id", ""),
            100,
        )
        try:
            if team_run_id:
                return self.sync_from_team_run(team_run_id)
            if team_task_id:
                return self.sync_from_team_task(team_task_id)
        except FileNotFoundError:
            return None
        return None

    def describe_next_action(self, job_id: str) -> str:
        integration = JobTeamIntegration.from_job(self.command_center.job(job_id))
        link = integration.active_link
        if link is None and integration.links:
            link = integration.links[-1]
        return link.next_action if link is not None else "Launch the job when ready."

    def cancel(self, job_id: str) -> Job:
        job = self.command_center.job(job_id)
        integration = JobTeamIntegration.from_job(job)
        link = integration.active_link
        if link is None:
            return self.command_center.cancel_job(job_id)
        synced = self.sync_job(job_id)
        job = synced.job
        integration = JobTeamIntegration.from_job(job)
        link = integration.active_link
        if link is None:
            raise ValueError("Linked job is already terminal.")

        task = self.team.task(link.team_task_id)
        run = None
        if link.team_run_id and self.external_state_source is not None:
            run = self.external_state_source.run(link.team_run_id)
        task_status = _safe_text(getattr(task, "status", ""), 100).lower()
        run_status = _safe_text(getattr(run, "status", ""), 100).lower()
        if task_status == "planning" or run_status in ACTIVE_EXTERNAL_STATUSES:
            cancellation = getattr(self.external_state_source, "cancel", None)
            if callable(cancellation):
                cancellation(
                    team_task_id=link.team_task_id,
                    team_run_id=link.team_run_id,
                )
                return self.sync_job(job_id).job
            raise ValueError(
                "The linked AI Team workflow is active and has no safe cancellation "
                "operation; Command Center did not change its status."
            )

        projection = _WorkflowProjection(
            JobStatus.CANCELLED,
            WorkflowStage.CANCELLED,
            job.progress,
            (
                ApprovalState.CANCELLED
                if job.approval_state == ApprovalState.PENDING
                else job.approval_state
            ),
            "",
            "cancelled",
            "No further action is required.",
            team_run_id=link.team_run_id,
            approval_id=link.approval_id,
            execution_engine=link.execution_engine,
            result_summary="Job cancelled; linked Team records were retained.",
            active_link=False,
        )
        return self._apply_projection(job_id, link, projection).job

    def doctor_issues(self, jobs: Iterable[Job]) -> tuple[RepositoryDiagnostic, ...]:
        issues: list[RepositoryDiagnostic] = []
        task_links: dict[str, list[str]] = {}
        now = datetime.fromisoformat(self._timestamp().replace("Z", "+00:00"))
        for job in jobs:
            try:
                integration = JobTeamIntegration.from_job(job)
            except ValueError as exc:
                issues.append(RepositoryDiagnostic(
                    "error",
                    "integration.unsupported_or_invalid_schema",
                    _safe_text(exc),
                    f"jobs/{job.job_id}.yaml",
                ))
                continue
            for link in integration.links:
                task_links.setdefault(link.team_task_id, []).append(job.job_id)
                if link.workflow_id == ENGINEERING_WORKFLOW_ID:
                    mapped = {item.stage for item in link.role_assignments}
                    required = {
                        WorkflowStage.PLANNING,
                        WorkflowStage.ARCHITECTURE,
                        WorkflowStage.IMPLEMENTATION,
                        WorkflowStage.TESTING,
                        WorkflowStage.ENGINEERING_REVIEW,
                        WorkflowStage.DOCUMENTATION,
                        WorkflowStage.FINAL_REVIEW,
                    }
                    if not required.issubset(mapped):
                        issues.append(RepositoryDiagnostic(
                            "error",
                            "integration.invalid_workflow_mapping",
                            f"Job {job.job_id} has an incomplete Engineering "
                            "workflow mapping.",
                            f"jobs/{job.job_id}.yaml",
                        ))
                try:
                    task = self.team.task(link.team_task_id)
                except FileNotFoundError:
                    issues.append(RepositoryDiagnostic(
                        "error",
                        "integration.missing_team_task",
                        f"Job {job.job_id} links missing AI Team task "
                        f"{link.team_task_id}.",
                        f"jobs/{job.job_id}.yaml",
                    ))
                    task = None
                except (OSError, ValueError) as exc:
                    issues.append(RepositoryDiagnostic(
                        "error",
                        "integration.invalid_team_task",
                        _safe_text(exc),
                        f"jobs/{job.job_id}.yaml",
                    ))
                    task = None

                inspection = self._inspect_runs_for_task(
                    link.team_task_id,
                    linked_run_id=link.team_run_id,
                )
                run = self._select_run(link, inspection.runs)
                for warning in inspection.warnings:
                    issues.append(RepositoryDiagnostic(
                        "warning",
                        "integration.run_inspection_unavailable",
                        warning,
                        f"jobs/{job.job_id}.yaml",
                    ))
                if run is None:
                    for reference in inspection.unresolved:
                        issues.append(RepositoryDiagnostic(
                            "error",
                            "integration.unresolved_team_run",
                            self._unresolved_run_warning(reference.run_id),
                            f"jobs/{job.job_id}.yaml",
                        ))
                task_status = _safe_text(getattr(task, "status", "")).lower()
                run_status = _safe_text(getattr(run, "status", "")).lower()
                if job.status == JobStatus.COMPLETED and (
                    task_status == "planning" or run_status in ACTIVE_EXTERNAL_STATUSES
                ):
                    issues.append(RepositoryDiagnostic(
                        "error",
                        "integration.completed_job_active_team",
                        f"Completed job {job.job_id} has active Team work.",
                        f"jobs/{job.job_id}.yaml",
                    ))
                if job.status == JobStatus.CANCELLED and run_status in ACTIVE_EXTERNAL_STATUSES:
                    issues.append(RepositoryDiagnostic(
                        "error",
                        "integration.cancelled_job_active_run",
                        f"Cancelled job {job.job_id} has an active Team run.",
                        f"jobs/{job.job_id}.yaml",
                    ))
                if link.approval_id and not any(
                    _safe_text(getattr(item, "approval_id", "")) == link.approval_id
                    for item in self._approvals_for_task(link.team_task_id)
                ):
                    issues.append(RepositoryDiagnostic(
                        "error",
                        "integration.missing_approval",
                        f"Job {job.job_id} links a missing authoritative approval.",
                        f"jobs/{job.job_id}.yaml",
                    ))
                if link.last_synced_at and link.active:
                    synced = datetime.fromisoformat(
                        link.last_synced_at.replace("Z", "+00:00")
                    )
                    if (now - synced).total_seconds() > 86_400:
                        issues.append(RepositoryDiagnostic(
                            "warning",
                            "integration.stale_sync",
                            f"Job {job.job_id} has not synchronized in over 24 hours.",
                            f"jobs/{job.job_id}.yaml",
                        ))
                for assignment in link.role_assignments:
                    try:
                        agent = self.agent_manager.load(assignment.agent_id)
                    except FileNotFoundError:
                        issues.append(RepositoryDiagnostic(
                            "error",
                            "integration.missing_required_agent",
                            f"Workflow stage {assignment.stage.value} references "
                            f"missing agent {assignment.agent_id}.",
                            f"jobs/{job.job_id}.yaml",
                        ))
                    else:
                        if not bool(getattr(agent, "enabled", False)):
                            issues.append(RepositoryDiagnostic(
                                "error",
                                "integration.disabled_required_agent",
                                f"Workflow stage {assignment.stage.value} references "
                                f"disabled agent {assignment.agent_id}.",
                                f"jobs/{job.job_id}.yaml",
                            ))
        for task_id, job_ids in task_links.items():
            active_jobs = [
                item for item in jobs
                if item.job_id in job_ids
                and JobTeamIntegration.from_job(item).active_link is not None
            ]
            if len(active_jobs) > 1:
                issues.append(RepositoryDiagnostic(
                    "error",
                    "integration.duplicate_active_team_link",
                    f"AI Team task {task_id} is active in multiple Command Center jobs.",
                ))
        return tuple(issues)

    def _resolve_workflow(
        self,
        job: Job,
        department,
        requested_workflow: str,
    ) -> ResolvedCommandCenterWorkflow:
        metadata_workflow = _safe_text(job.metadata.get("workflow_id", ""), 80)
        policy_workflow = _safe_text(
            getattr(department, "workflow_policy_reference", ""),
            80,
        )
        selected = requested_workflow or metadata_workflow or policy_workflow
        source = (
            "explicit-launch"
            if requested_workflow
            else "explicit-job"
            if metadata_workflow
            else "department-policy"
            if policy_workflow
            else "team-mapping"
        )
        if selected:
            try:
                workflow_id = normalize_id(selected, "Workflow ID")
            except ValueError as exc:
                raise ValueError(f"Invalid workflow: {selected}") from exc
            if workflow_id not in {ENGINEERING_WORKFLOW_ID, "engineering-default"}:
                raise ValueError(f"Invalid workflow: {selected}")
            workflow_id = ENGINEERING_WORKFLOW_ID
        else:
            if (
                department is not None
                and str(department.name).strip().casefold() == "engineering"
            ):
                workflow_id = ENGINEERING_WORKFLOW_ID
                source = "engineering-default"
            elif job.assigned_agent_ids:
                workflow_id = ENGINEERING_WORKFLOW_ID
                source = "explicit-assignments"
            else:
                raise ValueError(
                    "No Command Center workflow policy can be resolved."
                )

        candidate_ids = (
            job.assigned_agent_ids
            if job.assigned_agent_ids
            else tuple(getattr(department, "agent_ids", ()))
        )
        if not candidate_ids:
            raise ValueError("Workflow has no assigned department agents.")
        candidates = []
        for agent_id in candidate_ids:
            try:
                agent = self.agent_manager.load(agent_id)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"Required workflow agent is missing: {agent_id}"
                ) from exc
            if not bool(getattr(agent, "enabled", False)):
                raise ValueError(f"Required workflow agent is disabled: {agent_id}")
            candidates.append(agent)

        used: set[str] = set()
        core_assignments: list[WorkflowAgentAssignment] = []
        for stage, preferred_ids, terms in ENGINEERING_STAGE_SPECS:
            agent = self._select_stage_agent(
                stage,
                candidates,
                preferred_ids,
                terms,
                used,
            )
            used.add(str(agent.agent_id))
            role = _safe_text(
                getattr(getattr(agent, "role", None), "job", "")
                or getattr(agent, "name", "")
                or agent.agent_id,
                200,
            )
            core_assignments.append(
                WorkflowAgentAssignment(stage, str(agent.agent_id), role)
            )
        by_stage = {item.stage: item for item in core_assignments}
        role_assignments = (
            by_stage[WorkflowStage.PLANNING],
            by_stage[WorkflowStage.ARCHITECTURE],
            by_stage[WorkflowStage.IMPLEMENTATION],
            WorkflowAgentAssignment(
                WorkflowStage.TESTING,
                by_stage[WorkflowStage.ENGINEERING_REVIEW].agent_id,
                "Tester / validation",
            ),
            by_stage[WorkflowStage.ENGINEERING_REVIEW],
            WorkflowAgentAssignment(
                WorkflowStage.DOCUMENTATION,
                by_stage[WorkflowStage.FINAL_REVIEW].agent_id,
                "Documentation review",
            ),
            by_stage[WorkflowStage.FINAL_REVIEW],
        )
        ordered_agents = tuple(
            dict.fromkeys(item.agent_id for item in core_assignments)
        )
        return ResolvedCommandCenterWorkflow(
            workflow_id,
            source,
            getattr(department, "department_id", job.department_id),
            ordered_agents,
            role_assignments,
        )

    @staticmethod
    def _select_stage_agent(
        stage: WorkflowStage,
        agents: list[Any],
        preferred_ids: tuple[str, ...],
        terms: tuple[str, ...],
        used: set[str],
    ):
        scored: list[tuple[int, str, Any]] = []
        for agent in agents:
            agent_id = str(getattr(agent, "agent_id", ""))
            if agent_id in used:
                continue
            role = getattr(agent, "role", None)
            haystack = " ".join((
                agent_id,
                str(getattr(agent, "name", "")),
                str(getattr(role, "job", "")),
                str(getattr(role, "specialty", "")),
            )).casefold().replace("-", " ")
            score = 0
            if agent_id in preferred_ids:
                score = 100
            else:
                score = sum(10 for term in terms if term in haystack)
            if score:
                scored.append((score, agent_id, agent))
        if not scored:
            raise ValueError(
                f"Required workflow agent cannot be resolved for stage "
                f"{stage.value}."
            )
        scored.sort(key=lambda item: (-item[0], item[1]))
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            raise ValueError(
                f"Required workflow agent is ambiguous for stage {stage.value}: "
                f"{scored[0][1]}, {scored[1][1]}."
            )
        return scored[0][2]

    def _provider_routes(
        self,
        job: Job,
        workflow: ResolvedCommandCenterWorkflow,
    ) -> tuple[ProviderRouteSummary, ...]:
        resolver = getattr(
            self.agent_manager,
            "preview_resolution_candidates",
            None,
        ) or getattr(self.agent_manager, "resolution_candidates", None)
        if not callable(resolver):
            raise ValueError(
                "Agent provider routing is unavailable for AI Team launch."
            )
        routes = []
        for agent_id in workflow.agent_ids:
            agent = self.agent_manager.load(agent_id)
            candidates = resolver(
                agent,
                goal=job.goal,
                provider="auto",
                model="auto",
            )
            if not candidates:
                raise ValueError(
                    f"No provider route is available for agent {agent_id}."
                )
            selected = candidates[0]
            routes.append(ProviderRouteSummary(
                agent_id,
                _safe_text(getattr(selected, "provider", ""), 64),
                _safe_text(getattr(selected, "model", ""), 200),
                _safe_text(getattr(selected, "source", "routing"), 64),
                bool(getattr(selected, "fallback_reason", "")),
            ))
        return tuple(routes)

    def _resolve_workspace(self, job: Job, workspace: str) -> str:
        if self.workspace_manager is None:
            raise ValueError("Workspace service is not registered.")
        active = Path(self.workspace_manager.root).expanduser().resolve()
        requested_text = str(workspace or job.workspace_reference).strip()
        requested = (
            Path(requested_text).expanduser().resolve()
            if requested_text
            else active
        )
        if not requested.exists():
            raise FileNotFoundError("Command Center job workspace does not exist.")
        if not requested.is_dir():
            raise NotADirectoryError(
                "Command Center job workspace is not a directory."
            )
        if not _same_path(requested, active):
            raise PermissionError(
                "Command Center launch workspace must match Orion's active workspace."
            )
        return str(active)

    def _required_service_errors(self) -> list[str]:
        if self.service_registry is None:
            return []
        required = ("command_center", "team", "agents", "workspace")
        return [
            f"Required service is not registered: {name}"
            for name in required
            if not self.service_registry.contains(name)
        ]

    def _execution_engine_summary(self) -> str:
        if self.execution_engines is None:
            return "optional; no execution engine service"
        return "optional; validated at the approval-bound implementation stage"

    def _reserve_team_task_id(self) -> str:
        reserve = getattr(self.team, "reserve_task_id", None)
        if not callable(reserve):
            raise ValueError("AI Team cannot reserve a durable task ID.")
        return _safe_text(reserve(), 100)

    def _save_linked_job(
        self,
        job: Job,
        link: TeamIntegrationLink,
        *,
        status: JobStatus,
        stage: WorkflowStage,
        progress: int,
        workspace_root: str,
        approval_state: ApprovalState,
    ) -> Job:
        integration = JobTeamIntegration.from_job(job).with_link(link)
        metadata = dict(job.metadata)
        metadata["team_integration"] = integration.to_dict()
        return self.command_center.synchronize_job(
            job.job_id,
            status=status,
            current_stage=stage.value,
            progress=progress,
            approval_state=approval_state,
            metadata=metadata,
            workspace_reference=workspace_root,
        )

    def _save_link_only(
        self,
        job: Job,
        integration: JobTeamIntegration,
    ) -> Job:
        metadata = dict(job.metadata)
        metadata["team_integration"] = integration.to_dict()
        return self.command_center.synchronize_job(
            job.job_id,
            status=job.status,
            current_stage=job.current_stage,
            progress=job.progress,
            approval_state=job.approval_state,
            metadata=metadata,
        )

    def _project(
        self,
        task: Any,
        approval: Any | None,
        run: Any | None,
        link: TeamIntegrationLink,
    ) -> _WorkflowProjection:
        task_status = _safe_text(getattr(task, "status", "")).lower()
        if task_status == "failed":
            return _WorkflowProjection(
                JobStatus.FAILED,
                WorkflowStage.FAILED,
                5,
                ApprovalState.CANCELLED,
                "",
                "failed",
                "Inspect the safe AI Team planning failure and retry with a new job.",
                error_summary=_safe_text(
                    getattr(task, "error", "")
                    or "The linked AI Team planning task failed.",
                    1_000,
                ),
                active_link=False,
            )
        if task_status == "planning":
            artifacts = tuple(getattr(task, "artifacts", ()) or ())
            selected = tuple(getattr(task, "selected_agents", ()) or ())
            next_index = min(len(artifacts), max(0, len(selected) - 1))
            active_agent = selected[next_index] if selected else (
                link.active_agent_id
            )
            stage = (
                WorkflowStage.PLANNING
                if len(artifacts) == 0
                else WorkflowStage.ARCHITECTURE
            )
            return _WorkflowProjection(
                JobStatus.PLANNING,
                stage,
                min(24, 5 + len(artifacts) * 4),
                ApprovalState.NOT_REQUIRED,
                active_agent,
                "planning",
                "Wait for AI Team planning to finish.",
            )
        if task_status == "cancelled":
            return _WorkflowProjection(
                JobStatus.CANCELLED,
                WorkflowStage.CANCELLED,
                25,
                ApprovalState.CANCELLED,
                "",
                "cancelled",
                "No further action is required.",
                result_summary="The authoritative AI Team task was cancelled.",
                active_link=False,
            )
        if task_status not in {"awaiting_approval", "completed"}:
            raise ValueError(
                f"Unsupported AI Team lifecycle status: {task_status or 'missing'}"
            )

        approval_state = self._approval_state(approval)
        if run is None:
            if approval_state == ApprovalState.APPROVED:
                approval_id = _safe_text(getattr(approval, "approval_id", ""), 100)
                return _WorkflowProjection(
                    JobStatus.QUEUED,
                    WorkflowStage.QUEUED,
                    30,
                    approval_state,
                    "",
                    "approved",
                    (
                        f"Run: team implement {link.team_task_id} {approval_id}"
                        if approval_id
                        else "Start implementation through the existing Team command."
                    ),
                    approval_id=approval_id,
                    execution_engine=_safe_text(
                        getattr(approval, "execution_engine", ""),
                        100,
                    ),
                )
            if approval_state == ApprovalState.DENIED:
                return _WorkflowProjection(
                    JobStatus.FAILED,
                    WorkflowStage.FAILED,
                    25,
                    ApprovalState.DENIED,
                    "",
                    "approval_denied",
                    "Create a new job if a revised plan is required.",
                    error_summary="The authoritative AI Team approval was denied.",
                    active_link=False,
                )
            return _WorkflowProjection(
                JobStatus.AWAITING_APPROVAL,
                WorkflowStage.AWAITING_APPROVAL,
                25,
                ApprovalState.PENDING,
                "",
                "awaiting_approval",
                f"Review with: team status {link.team_task_id}; approve with: "
                f"team approve {link.team_task_id}",
            )

        return self._project_run(task, approval, run, link)

    def _project_run(
        self,
        task: Any,
        approval: Any | None,
        run: Any,
        link: TeamIntegrationLink,
    ) -> _WorkflowProjection:
        status = _safe_text(getattr(run, "status", "")).lower()
        run_id = _safe_text(getattr(run, "run_id", ""), 100)
        approval_id = _safe_text(
            getattr(run, "approval_id", "")
            or getattr(approval, "approval_id", ""),
            100,
        )
        engine = _safe_text(
            getattr(approval, "execution_engine", "")
            or link.execution_engine,
            100,
        )
        implementation_agent = self._agent_for_stage(
            link, WorkflowStage.IMPLEMENTATION
        )
        if status in {"executing", "implementation", "running"}:
            return _WorkflowProjection(
                JobStatus.RUNNING,
                WorkflowStage.IMPLEMENTATION,
                35,
                ApprovalState.APPROVED,
                implementation_agent,
                "executing",
                f"Inspect implementation with: team run {run_id}",
                run_id,
                approval_id,
                engine,
            )
        if status == "testing":
            return _WorkflowProjection(
                JobStatus.RUNNING,
                WorkflowStage.TESTING,
                70,
                ApprovalState.APPROVED,
                self._agent_for_stage(link, WorkflowStage.TESTING),
                "testing",
                f"Inspect validation with: team run {run_id}",
                run_id,
                approval_id,
                engine,
            )
        if status == "failed":
            return _WorkflowProjection(
                JobStatus.FAILED,
                WorkflowStage.FAILED,
                35,
                ApprovalState.APPROVED,
                "",
                "failed",
                f"Inspect the safe run status with: team run {run_id}",
                run_id,
                approval_id,
                engine,
                error_summary=(
                    "AI Team implementation failed safely "
                    f"({_safe_text(getattr(run, 'error', 'execution_failed'), 100)})."
                ),
                active_link=False,
            )
        if status in {"cancelled", "rolled_back"}:
            return _WorkflowProjection(
                JobStatus.CANCELLED,
                WorkflowStage.CANCELLED,
                65,
                ApprovalState.APPROVED,
                "",
                status,
                "No further action is required.",
                run_id,
                approval_id,
                engine,
                result_summary=(
                    "AI Team implementation changes were rolled back."
                    if status == "rolled_back"
                    else "AI Team run was cancelled."
                ),
                active_link=False,
            )
        if status == "completed":
            return _WorkflowProjection(
                JobStatus.COMPLETED,
                WorkflowStage.COMPLETED,
                100,
                ApprovalState.APPROVED,
                "",
                "completed",
                "No further action is required.",
                run_id,
                approval_id,
                engine,
                result_summary=self._run_result_summary(run),
                active_link=False,
            )
        if status not in {
            "awaiting_review", "engineering_review", "final_review",
            "documentation",
        }:
            raise ValueError(
                f"Unsupported AI Team run lifecycle status: {status or 'missing'}"
            )

        validation = getattr(run, "validation", None)
        documentation = getattr(run, "documentation", None)
        warnings: list[str] = []
        if validation is None:
            return _WorkflowProjection(
                JobStatus.RUNNING,
                WorkflowStage.TESTING,
                65,
                ApprovalState.APPROVED,
                self._agent_for_stage(link, WorkflowStage.TESTING),
                "awaiting_validation",
                f"Run validation with: team test {run_id}",
                run_id,
                approval_id,
                engine,
                result_summary=self._run_result_summary(run),
            )
        validation_status = _safe_text(
            getattr(validation, "status", ""),
            40,
        ).lower()
        if validation_status not in {"passed", "warnings"}:
            warnings.append(
                f"Validation requires attention: {validation_status or 'unknown'}."
            )
        if documentation is None:
            return _WorkflowProjection(
                JobStatus.AWAITING_REVIEW,
                WorkflowStage.DOCUMENTATION,
                85 if validation_status in {"passed", "warnings"} else 80,
                ApprovalState.APPROVED,
                self._agent_for_stage(link, WorkflowStage.DOCUMENTATION),
                "awaiting_documentation_review",
                f"Run documentation review with: team docs {run_id}",
                run_id,
                approval_id,
                engine,
                result_summary=self._run_result_summary(run),
                warnings=tuple(warnings),
            )
        documentation_status = _safe_text(
            getattr(documentation, "status", ""),
            40,
        ).lower()
        if documentation_status not in {
            "passed", "warnings", "not_required",
        }:
            warnings.append(
                "Documentation review requires attention: "
                f"{documentation_status or 'unknown'}."
            )
        return _WorkflowProjection(
            JobStatus.AWAITING_REVIEW,
            WorkflowStage.FINAL_REVIEW,
            95,
            ApprovalState.APPROVED,
            self._agent_for_stage(link, WorkflowStage.FINAL_REVIEW),
            "awaiting_review",
            "Perform final human review; then mark the Command Center job completed.",
            run_id,
            approval_id,
            engine,
            result_summary=self._run_result_summary(run),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _run_result_summary(run: Any) -> str:
        result = getattr(run, "result", None)
        return _safe_text(
            getattr(result, "summary", "")
            or "AI Team implementation finished and awaits final review.",
            2_000,
        )

    @staticmethod
    def _approval_state(approval: Any | None) -> ApprovalState:
        if approval is None:
            return ApprovalState.PENDING
        state = _safe_text(
            getattr(approval, "state", "")
            or getattr(approval, "status", "")
            or "approved",
            40,
        ).lower()
        if state in {"approved", "granted"}:
            return ApprovalState.APPROVED
        if state in {"denied", "rejected"}:
            return ApprovalState.DENIED
        if state == "cancelled":
            return ApprovalState.CANCELLED
        return ApprovalState.PENDING

    @staticmethod
    def _agent_for_stage(
        link: TeamIntegrationLink,
        stage: WorkflowStage,
    ) -> str:
        assignment = next(
            (item for item in link.role_assignments if item.stage == stage),
            None,
        )
        return assignment.agent_id if assignment is not None else ""

    def _apply_projection(
        self,
        job_id: str,
        prior_link: TeamIntegrationLink,
        projection: _WorkflowProjection,
    ) -> SyncResult:
        before = self.command_center.job(job_id)
        projected_link = replace(
            prior_link,
            team_run_id=projection.team_run_id or prior_link.team_run_id,
            execution_engine=(
                projection.execution_engine or prior_link.execution_engine
            ),
            external_status=projection.external_status,
            approval_id=projection.approval_id or prior_link.approval_id,
            active_agent_id=projection.active_agent_id,
            next_action=projection.next_action,
            synchronization_warnings=projection.warnings,
            active=projection.active_link,
        )
        projected_progress = max(before.progress, min(100, projection.progress))
        projected_result = projection.result_summary or before.result_summary
        projected_error = projection.error_summary or before.error_summary
        if (
            before.status == projection.status
            and before.current_stage == projection.stage.value
            and before.progress == projected_progress
            and before.approval_state == projection.approval_state
            and before.result_summary == projected_result
            and before.error_summary == projected_error
            and self._link_semantic(prior_link)
            == self._link_semantic(projected_link)
        ):
            return SyncResult(before, False, projection.warnings, ())
        link = replace(projected_link, last_synced_at=self._timestamp())
        integration = JobTeamIntegration.from_job(before).with_link(link)
        metadata = dict(before.metadata)
        metadata["team_integration"] = integration.to_dict()
        after = self.command_center.synchronize_job(
            job_id,
            status=projection.status,
            current_stage=projection.stage.value,
            progress=projected_progress,
            approval_state=projection.approval_state,
            metadata=metadata,
            result_summary=projected_result,
            error_summary=projected_error,
        )
        changed = self._semantic_job_state(before) != self._semantic_job_state(after)
        events: list[str] = []
        if prior_link.team_run_id != link.team_run_id and link.team_run_id:
            self._activity(
                "job.linked_to_team_run",
                after,
                f"Linked job to AI Team run {link.team_run_id}.",
                metadata={"team_run_id": link.team_run_id},
            )
            events.append("job.linked_to_team_run")
        new_sync_warnings = tuple(
            item for item in link.synchronization_warnings
            if item not in prior_link.synchronization_warnings
        )
        if new_sync_warnings:
            self._activity(
                "job.sync_warning",
                after,
                new_sync_warnings[0],
                severity=ActivitySeverity.WARNING,
                metadata={"warning_count": len(link.synchronization_warnings)},
            )
            events.append("job.sync_warning")
        elif (
            prior_link.synchronization_warnings
            and not link.synchronization_warnings
        ):
            self._activity(
                "job.sync_warning_cleared",
                after,
                "Team workflow synchronization warning cleared.",
            )
            events.append("job.sync_warning_cleared")
        if before.current_stage != after.current_stage:
            if before.current_stage:
                self._activity(
                    "workflow.stage_completed",
                    after,
                    f"Workflow stage completed: {before.current_stage}.",
                    metadata={"stage": before.current_stage},
                )
                events.append("workflow.stage_completed")
                completion_event = {
                    WorkflowStage.IMPLEMENTATION.value: "execution.completed",
                    WorkflowStage.TESTING.value: "testing.completed",
                    WorkflowStage.ENGINEERING_REVIEW.value: "review.completed",
                    WorkflowStage.DOCUMENTATION.value: "review.completed",
                }.get(before.current_stage)
                if completion_event:
                    self._activity(
                        completion_event,
                        after,
                        f"{before.current_stage.replace('_', ' ').title()} completed.",
                        metadata={"stage": before.current_stage},
                    )
                    events.append(completion_event)
            self._activity(
                "workflow.stage_started",
                after,
                f"Workflow stage started: {after.current_stage}.",
                agent_id=link.active_agent_id,
                metadata={"stage": after.current_stage},
            )
            events.append("workflow.stage_started")
        if prior_link.active_agent_id != link.active_agent_id:
            if prior_link.active_agent_id:
                self._activity(
                    "workflow.agent_completed",
                    after,
                    f"Agent {prior_link.active_agent_id} completed its active stage.",
                    agent_id=prior_link.active_agent_id,
                )
                events.append("workflow.agent_completed")
            if link.active_agent_id:
                self._activity(
                    "workflow.agent_started",
                    after,
                    f"Agent {link.active_agent_id} started its active stage.",
                    agent_id=link.active_agent_id,
                )
                events.append("workflow.agent_started")
        if before.approval_state != after.approval_state:
            event = {
                ApprovalState.PENDING: "approval.requested",
                ApprovalState.APPROVED: "approval.granted",
                ApprovalState.DENIED: "approval.denied",
            }.get(after.approval_state)
            if event:
                self._activity(
                    event,
                    after,
                    f"Authoritative approval state is {after.approval_state.value}.",
                    metadata={"approval_state": after.approval_state.value},
                )
                events.append(event)
        if before.status != after.status:
            event = {
                JobStatus.COMPLETED: "job.completed",
                JobStatus.FAILED: "job.failed",
                JobStatus.CANCELLED: "job.cancelled",
            }.get(after.status)
            if event:
                self._activity(
                    event,
                    after,
                    f"Linked workflow is {after.status.value}.",
                    severity=(
                        ActivitySeverity.ERROR
                        if after.status == JobStatus.FAILED
                        else ActivitySeverity.INFO
                    ),
                )
                events.append(event)
        stage_event = {
            WorkflowStage.IMPLEMENTATION: "execution.started",
            WorkflowStage.TESTING: "testing.started",
            WorkflowStage.ENGINEERING_REVIEW: "review.started",
            WorkflowStage.DOCUMENTATION: "review.started",
            WorkflowStage.FINAL_REVIEW: "review.started",
        }.get(projection.stage)
        if stage_event and before.current_stage != after.current_stage:
            self._activity(
                stage_event,
                after,
                f"{projection.stage.value.replace('_', ' ').title()} started.",
                agent_id=link.active_agent_id,
            )
            events.append(stage_event)
        if changed:
            self._activity(
                "job.sync_completed",
                after,
                "Command Center synchronized authoritative AI Team state.",
                metadata={
                    "external_status": link.external_status,
                    "status": after.status.value,
                    "stage": after.current_stage,
                },
            )
            events.append("job.sync_completed")
        return SyncResult(after, changed, projection.warnings, tuple(events))

    @staticmethod
    def _semantic_job_state(job: Job) -> tuple[Any, ...]:
        return (
            job.status,
            job.current_stage,
            job.progress,
            job.approval_state,
            job.result_summary,
            job.error_summary,
            job.workspace_reference,
            job.metadata,
        )

    @staticmethod
    def _link_semantic(link: TeamIntegrationLink) -> tuple[Any, ...]:
        return (
            link.team_task_id,
            link.team_run_id,
            link.workflow_id,
            link.execution_engine,
            link.external_status,
            link.approval_id,
            link.active_agent_id,
            link.next_action,
            link.synchronization_warnings,
            link.role_assignments,
            link.active,
        )

    def _approvals_for_task(self, team_task_id: str) -> tuple[Any, ...]:
        source = self.external_state_source
        if source is None:
            return ()
        method = getattr(source, "approvals_for_task", None)
        if not callable(method):
            return ()
        try:
            return tuple(method(team_task_id))
        except FileNotFoundError:
            return ()

    def _inspect_runs_for_task(
        self,
        team_task_id: str,
        *,
        linked_run_id: str = "",
    ) -> _RunInspection:
        source = self.external_state_source
        if source is None:
            unresolved = (
                (_RunReference(linked_run_id),) if linked_run_id else ()
            )
            return _RunInspection(unresolved=unresolved)

        runs: tuple[Any, ...] = ()
        unresolved: list[_RunReference] = []
        warnings: list[str] = []
        inspect_method = getattr(source, "inspect_runs_for_task", None)
        if callable(inspect_method):
            try:
                result = inspect_method(team_task_id)
                runs = tuple(getattr(result, "runs", ()) or ())
                for item in tuple(getattr(result, "unresolved", ()) or ()):
                    run_id = _safe_text(
                        item if isinstance(item, str) else getattr(item, "run_id", ""),
                        100,
                    )
                    if run_id and EXTERNAL_REFERENCE_PATTERN.fullmatch(run_id):
                        unresolved.append(_RunReference(
                            run_id,
                            _safe_text(getattr(item, "started_at", ""), 80),
                        ))
            except (FileNotFoundError, OSError, TypeError, ValueError, RuntimeError):
                warnings.append(
                    "Team workflow run inspection could not be completed."
                )
        else:
            method = getattr(source, "runs_for_task", None)
            if callable(method):
                try:
                    runs = tuple(method(team_task_id))
                except FileNotFoundError:
                    runs = ()
                except (OSError, TypeError, ValueError, RuntimeError):
                    warnings.append(
                        "Team workflow run inspection could not be completed."
                    )

        scoped_runs: list[Any] = []
        for item in runs:
            if _safe_text(getattr(item, "team_task_id", ""), 100) == team_task_id:
                scoped_runs.append(item)
            else:
                warnings.append(
                    "A run for a different Team task was ignored during synchronization."
                )

        known_ids = {
            _safe_text(getattr(item, "run_id", ""), 100)
            for item in scoped_runs
        }
        unresolved_ids = {item.run_id for item in unresolved}
        if linked_run_id and linked_run_id not in known_ids | unresolved_ids:
            load_method = getattr(source, "run", None)
            if callable(load_method):
                try:
                    linked_run = load_method(linked_run_id)
                except (FileNotFoundError, OSError, TypeError, ValueError, RuntimeError):
                    unresolved.append(_RunReference(linked_run_id))
                else:
                    if (
                        _safe_text(
                            getattr(linked_run, "team_task_id", ""),
                            100,
                        )
                        == team_task_id
                    ):
                        scoped_runs.append(linked_run)
                    else:
                        unresolved.append(_RunReference(linked_run_id))
                        warnings.append(
                            "The linked run belongs to a different Team task and "
                            "was ignored."
                        )
            else:
                unresolved.append(_RunReference(linked_run_id))

        unique_unresolved = {
            item.run_id: item for item in unresolved
        }
        return _RunInspection(
            tuple(scoped_runs),
            tuple(unique_unresolved.values()),
            tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _select_run(
        link: TeamIntegrationLink,
        runs: tuple[Any, ...],
    ) -> Any | None:
        if link.team_run_id:
            linked = next(
                (
                    item for item in runs
                    if _safe_text(getattr(item, "run_id", ""), 100)
                    == link.team_run_id
                ),
                None,
            )
            if linked is not None:
                return linked
        if not runs:
            return None
        return sorted(
            runs,
            key=lambda item: (
                _safe_text(getattr(item, "started_at", ""), 80),
                _safe_text(getattr(item, "run_id", ""), 100),
            ),
            reverse=True,
        )[0]

    @staticmethod
    def _unresolved_run_warning(run_id: str) -> str:
        safe_id = (
            run_id
            if EXTERNAL_REFERENCE_PATTERN.fullmatch(run_id)
            else "unknown"
        )
        return (
            f"Team workflow run reference {safe_id} could not be inspected."
        )

    @staticmethod
    def _select_approval(
        link: TeamIntegrationLink,
        approvals: tuple[Any, ...],
        run: Any | None,
    ) -> Any | None:
        wanted = (
            _safe_text(getattr(run, "approval_id", ""), 100)
            if run is not None
            else link.approval_id
        )
        if wanted:
            selected = next(
                (
                    item for item in approvals
                    if _safe_text(getattr(item, "approval_id", ""), 100)
                    == wanted
                ),
                None,
            )
            if selected is not None:
                return selected
        if not approvals:
            return None
        return sorted(
            approvals,
            key=lambda item: (
                _safe_text(getattr(item, "approved_at", ""), 80),
                _safe_text(getattr(item, "approval_id", ""), 100),
            ),
            reverse=True,
        )[0]

    def _job_for_team_task(self, team_task_id: str) -> Job | None:
        for job in self.command_center.jobs():
            try:
                integration = JobTeamIntegration.from_job(job)
            except ValueError:
                continue
            if any(
                link.team_task_id == team_task_id
                for link in integration.links
            ):
                return job
        return None

    def _job_for_team_run(self, team_run_id: str) -> Job | None:
        for job in self.command_center.jobs():
            try:
                integration = JobTeamIntegration.from_job(job)
            except ValueError:
                continue
            if any(link.team_run_id == team_run_id for link in integration.links):
                return job
        return None

    def _activity(
        self,
        event_type: str,
        job: Job,
        message: str,
        *,
        severity: ActivitySeverity = ActivitySeverity.INFO,
        agent_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.command_center.record_activity(
            event_type=event_type,
            source_type=(
                ActivitySourceType.APPROVAL
                if event_type.startswith("approval.")
                else ActivitySourceType.TEAM
                if event_type.startswith(("workflow.", "execution.", "testing.", "review."))
                else ActivitySourceType.JOB
            ),
            source_id=job.job_id,
            job_id=job.job_id,
            department_id=job.department_id,
            agent_id=agent_id,
            message=message,
            severity=severity,
            metadata=metadata,
        )

    def _timestamp(self) -> str:
        return parse_timestamp(self._now(), "Command Center integration timestamp")


class CommandCenterJobUpdateAdapter:
    """Compatibility lifecycle adapter that delegates to validated job state."""

    def __init__(self, command_center) -> None:
        self.command_center = command_center

    def queued(self, job_id: str):
        current = self.command_center.job(job_id)
        if current.status == JobStatus.DRAFT:
            return self.command_center.update_job_status(job_id, JobStatus.QUEUED)
        return current

    def planning(self, job_id: str, *, stage: str = "planning"):
        current = self.queued(job_id)
        if current.status == JobStatus.QUEUED:
            return self.command_center.update_job_status(
                job_id, JobStatus.PLANNING, current_stage=stage
            )
        return self.command_center.update_job_progress(
            job_id, current.progress, current_stage=stage
        )

    def request_approval(self, job_id: str, *, stage: str = "awaiting_approval"):
        current = self.command_center.job(job_id)
        if current.status == JobStatus.DRAFT:
            current = self.queued(job_id)
        if current.status == JobStatus.QUEUED:
            current = self.planning(job_id)
        if current.status != JobStatus.AWAITING_APPROVAL:
            current = self.command_center.update_job_status(
                job_id,
                JobStatus.AWAITING_APPROVAL,
                current_stage=stage,
            )
        return current

    def resolve_approval(self, job_id: str, *, approved: bool):
        return self.command_center.resolve_job_approval(
            job_id,
            ApprovalState.APPROVED if approved else ApprovalState.DENIED,
        )

    def running(self, job_id: str, *, stage: str = "running"):
        current = self.command_center.job(job_id)
        if current.status == JobStatus.DRAFT:
            current = self.queued(job_id)
        if current.status == JobStatus.AWAITING_APPROVAL:
            if current.approval_state != ApprovalState.APPROVED:
                raise PermissionError(
                    "The existing Orion approval must resolve before this job can run."
                )
        return self.command_center.update_job_status(
            job_id, JobStatus.RUNNING, current_stage=stage
        )

    def progress(self, job_id: str, progress: int, *, stage: str | None = None):
        return self.command_center.update_job_progress(
            job_id, progress, current_stage=stage
        )

    def awaiting_review(self, job_id: str, *, stage: str = "awaiting_review"):
        return self.command_center.update_job_status(
            job_id, JobStatus.AWAITING_REVIEW, current_stage=stage
        )

    def completed(self, job_id: str, *, result_summary: str = ""):
        return self.command_center.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            result_summary=result_summary,
            current_stage="completed",
        )

    def failed(self, job_id: str, *, error_summary: str = ""):
        return self.command_center.update_job_status(
            job_id,
            JobStatus.FAILED,
            error_summary=error_summary,
            current_stage="failed",
        )

    def sync_team_task(self, job_id: str, team_task: Any):
        status = str(getattr(team_task, "status", "")).strip().lower()
        if status == "planning":
            return self.planning(job_id)
        if status == "awaiting_approval":
            return self.request_approval(job_id)
        if status == "failed":
            return self.failed(
                job_id,
                error_summary="The linked AI Team planning task failed.",
            )
        raise ValueError(f"Unsupported AI Team lifecycle status: {status or 'missing'}")
