"""Application service for Orion Command Center."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from orion.command_center.models import (
    ACTIVE_JOB_STATUSES,
    COMMAND_CENTER_SCHEMA_VERSION,
    COMMAND_CENTER_SNAPSHOT_SCHEMA_VERSION,
    JOB_TRANSITIONS,
    TERMINAL_JOB_STATUSES,
    ActivityEvent,
    ActivitySeverity,
    ActivitySourceType,
    ApprovalState,
    Department,
    Job,
    JobPriority,
    JobStatus,
    JobTeamIntegration,
    Organization,
    TeamIntegrationLink,
    WorkflowStage,
    normalize_id,
    parse_timestamp,
    utc_now,
    validate_safe_value,
)
from orion.command_center.repository import (
    CommandCenterRepository,
    RepositoryDiagnostic,
)
from orion.command_center.templates import (
    DepartmentTemplate,
    department_templates,
    get_department_template,
)


class CommandCenterService:
    """Coordinate organization state without owning execution or presentation."""

    def __init__(
        self,
        repository: CommandCenterRepository,
        agent_manager,
        *,
        provider_manager=None,
        routing_service=None,
        workspace_manager=None,
        now: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.repository = repository
        self.agent_manager = agent_manager
        self.provider_manager = provider_manager
        self.routing_service = routing_service
        self.workspace_manager = workspace_manager
        self.team_integration = None
        self._now = now or utc_now
        self._id_factory = id_factory or (
            lambda prefix: f"{prefix}-{uuid4().hex}"
        )

    def set_team_integration(self, integration) -> None:
        """Attach the optional Team integration after dependency wiring."""
        self.team_integration = integration

    def ensure_default_organization(self) -> Organization:
        try:
            return self.repository.load_organization()
        except FileNotFoundError:
            organization = Organization.create(now=self._timestamp())
            self.repository.save_organization(organization)
            self.record_activity(
                event_type="organization.created",
                source_type=ActivitySourceType.ORGANIZATION,
                source_id=organization.organization_id,
                message=f"Created organization {organization.name}.",
            )
            return organization

    def organization(self) -> Organization:
        return self.repository.load_organization()

    def templates(self) -> tuple[DepartmentTemplate, ...]:
        return department_templates()

    def template(self, identifier: str) -> DepartmentTemplate:
        return get_department_template(identifier)

    def create_department(
        self,
        *,
        name: str | None = None,
        department_id: str | None = None,
        description: str = "",
        icon: str = "",
        workflow_policy_reference: str = "",
        template: str | None = None,
    ) -> Department:
        selected = self.template(template) if template else None
        selected_name = str(name or (selected.name if selected else "")).strip()
        if not selected_name:
            raise ValueError("Department creation requires a name or template.")
        selected_description = (
            description or (selected.description if selected else "")
        )
        selected_icon = icon or (selected.icon if selected else "")
        identity = normalize_id(
            department_id or selected_name,
            "Department ID",
        )
        for existing in self.repository.list_departments():
            if existing.department_id == identity:
                raise FileExistsError(f"Department already exists: {identity}")
            if existing.name.casefold() == selected_name.casefold():
                raise FileExistsError(
                    f"Department name already exists: {selected_name}"
                )
        department = Department.create(
            department_id=identity,
            name=selected_name,
            description=selected_description,
            icon=selected_icon,
            workflow_policy_reference=workflow_policy_reference,
            now=self._timestamp(),
        )
        self.repository.save_department(department)
        self.record_activity(
            event_type="department.created",
            source_type=ActivitySourceType.DEPARTMENT,
            source_id=department.department_id,
            department_id=department.department_id,
            message=f"Created department {department.name}.",
            metadata={
                "template_id": selected.template_id if selected else "",
            },
        )
        return department

    def departments(self) -> tuple[Department, ...]:
        return tuple(sorted(
            self.repository.list_departments(),
            key=lambda item: (item.name.casefold(), item.department_id),
        ))

    def department(self, identifier: str) -> Department:
        try:
            normalized = normalize_id(identifier, "Department ID")
            try:
                return self.repository.load_department(normalized)
            except FileNotFoundError:
                pass
        except ValueError:
            pass
        matches = [
            item for item in self.repository.list_departments()
            if item.name.casefold() == str(identifier).strip().casefold()
        ]
        if not matches:
            raise FileNotFoundError(f"Department not found: {identifier}")
        if len(matches) > 1:
            raise ValueError(f"Department name is ambiguous: {identifier}")
        return matches[0]

    def add_agent(self, department_reference: str, agent_reference: str) -> Department:
        department = self.department(department_reference)
        agent = self._load_agent(agent_reference)
        agent_id = str(agent.agent_id)
        if agent_id in department.agent_ids:
            return department
        updated = department.with_agent(agent_id, self._timestamp())
        self.repository.save_department(updated, overwrite=True)
        self.record_activity(
            event_type="department.agent_added",
            source_type=ActivitySourceType.DEPARTMENT,
            source_id=updated.department_id,
            department_id=updated.department_id,
            agent_id=agent_id,
            message=f"Added agent {agent_id} to {updated.name}.",
        )
        return updated

    def remove_agent(
        self,
        department_reference: str,
        agent_reference: str,
    ) -> Department:
        department = self.department(department_reference)
        agent_id = self._membership_agent_id(department, agent_reference)
        updated = department.without_agent(agent_id, self._timestamp())
        self.repository.save_department(updated, overwrite=True)
        self.record_activity(
            event_type="department.agent_removed",
            source_type=ActivitySourceType.DEPARTMENT,
            source_id=updated.department_id,
            department_id=updated.department_id,
            agent_id=agent_id,
            message=f"Removed agent {agent_id} from {updated.name}.",
        )
        return updated

    def create_job(
        self,
        *,
        title: str,
        goal: str,
        priority: JobPriority | str = JobPriority.NORMAL,
        department: str = "",
        assigned_agents: tuple[str, ...] | list[str] = (),
        workspace_reference: str = "",
        created_by: str = "user",
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> Job:
        department_id = (
            self.department(department).department_id
            if str(department).strip()
            else ""
        )
        agents = tuple(
            self._load_assignable_agent(reference).agent_id
            for reference in assigned_agents
        )
        if len(set(agents)) != len(agents):
            raise ValueError("Job assignments cannot contain duplicate agents.")
        workspace = ""
        if str(workspace_reference).strip():
            workspace = str(
                Path(workspace_reference).expanduser().resolve()
            )
        identity = normalize_id(
            job_id or self._id_factory("job"),
            "Job ID",
        )
        job = Job.create(
            job_id=identity,
            title=title,
            goal=goal,
            priority=priority,
            department_id=department_id,
            assigned_agent_ids=agents,
            workspace_reference=workspace,
            created_by=created_by,
            metadata=metadata,
            now=self._timestamp(),
        )
        self.repository.save_job(job)
        self.record_activity(
            event_type="job.created",
            source_type=ActivitySourceType.JOB,
            source_id=job.job_id,
            job_id=job.job_id,
            department_id=job.department_id,
            message=f"Created job {job.title}.",
            metadata={"priority": job.priority.value},
        )
        return job

    def jobs(self) -> tuple[Job, ...]:
        return tuple(sorted(
            self.repository.list_jobs(),
            key=lambda item: (item.created_at, item.job_id),
            reverse=True,
        ))

    def job(self, job_id: str) -> Job:
        return self.repository.load_job(job_id)

    def assign_job(self, job_id: str, agent_reference: str) -> Job:
        job = self.job(job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            raise ValueError(f"Cannot assign a terminal job: {job.status.value}")
        if JobTeamIntegration.from_job(job).active_link is not None:
            raise ValueError(
                "Cannot change assignments while an AI Team workflow is linked."
            )
        agent = self._load_assignable_agent(agent_reference)
        if agent.agent_id in job.assigned_agent_ids:
            return job
        updated = replace(
            job,
            assigned_agent_ids=(*job.assigned_agent_ids, agent.agent_id),
            updated_at=self._timestamp(),
        )
        updated = Job.from_value(updated.to_dict())
        self.repository.save_job(updated, overwrite=True)
        self.record_activity(
            event_type="job.assigned",
            source_type=ActivitySourceType.JOB,
            source_id=updated.job_id,
            job_id=updated.job_id,
            department_id=updated.department_id,
            agent_id=agent.agent_id,
            message=f"Assigned agent {agent.agent_id} to {updated.title}.",
        )
        return updated

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus | str,
        *,
        current_stage: str | None = None,
        result_summary: str | None = None,
        error_summary: str | None = None,
    ) -> Job:
        current = self.job(job_id)
        target = JobStatus.parse(status)
        if target == current.status:
            return current
        linked = JobTeamIntegration.from_job(current)
        linked_completion = (
            linked.active_link is not None
            and current.status == JobStatus.AWAITING_REVIEW
            and target == JobStatus.COMPLETED
        )
        if linked.active_link is not None and not linked_completion:
            raise PermissionError(
                "Linked AI Team job status is authoritative; use cc job sync "
                "or the existing Team workflow command."
            )
        if target not in JOB_TRANSITIONS[current.status]:
            raise ValueError(
                f"Invalid job transition: {current.status.value} -> {target.value}"
            )
        if (
            target == JobStatus.RUNNING
            and current.approval_state not in {
                ApprovalState.NOT_REQUIRED,
                ApprovalState.APPROVED,
            }
        ):
            raise PermissionError(
                "Job cannot run unless approval is not required or was explicitly approved."
            )
        timestamp = self._timestamp()
        approval_state = current.approval_state
        if target == JobStatus.AWAITING_APPROVAL:
            approval_state = ApprovalState.PENDING
        elif target in {JobStatus.CANCELLED, JobStatus.FAILED} and (
            approval_state == ApprovalState.PENDING
        ):
            approval_state = ApprovalState.CANCELLED
        started_at = current.started_at
        if target == JobStatus.RUNNING and not started_at:
            started_at = timestamp
        completed_at = timestamp if target in TERMINAL_JOB_STATUSES else ""
        progress = 100 if target == JobStatus.COMPLETED else current.progress
        metadata = dict(current.metadata)
        if linked_completion:
            active_link = linked.active_link
            assert active_link is not None
            completed_link = replace(
                active_link,
                last_synced_at=timestamp,
                external_status="completed",
                active_agent_id="",
                next_action="No further action is required.",
                active=False,
            )
            metadata["team_integration"] = linked.with_link(
                completed_link
            ).to_dict()
        updated = replace(
            current,
            status=target,
            approval_state=approval_state,
            current_stage=(
                str(current_stage).strip()
                if current_stage is not None
                else current.current_stage
            ),
            progress=progress,
            result_summary=(
                str(result_summary).strip()
                if result_summary is not None
                else current.result_summary
            ),
            error_summary=(
                str(error_summary).strip()
                if error_summary is not None
                else current.error_summary
            ),
            started_at=started_at,
            completed_at=completed_at,
            updated_at=timestamp,
            metadata=metadata,
        )
        updated = Job.from_value(updated.to_dict())
        self.repository.save_job(updated, overwrite=True)
        event_type = {
            JobStatus.QUEUED: "job.queued",
            JobStatus.COMPLETED: "job.completed",
            JobStatus.FAILED: "job.failed",
            JobStatus.CANCELLED: "job.cancelled",
        }.get(target, "job.status_changed")
        self.record_activity(
            event_type=event_type,
            source_type=ActivitySourceType.JOB,
            source_id=updated.job_id,
            job_id=updated.job_id,
            department_id=updated.department_id,
            message=(
                f"Job {updated.title} changed from "
                f"{current.status.value} to {updated.status.value}."
            ),
            severity=(
                ActivitySeverity.ERROR
                if target == JobStatus.FAILED
                else ActivitySeverity.INFO
            ),
            metadata={
                "previous_status": current.status.value,
                "status": updated.status.value,
            },
        )
        if target == JobStatus.AWAITING_APPROVAL:
            self.record_activity(
                event_type="approval.requested",
                source_type=ActivitySourceType.APPROVAL,
                source_id=updated.job_id,
                job_id=updated.job_id,
                department_id=updated.department_id,
                message=f"Approval requested for job {updated.title}.",
            )
        if linked_completion:
            self.record_activity(
                event_type="review.completed",
                source_type=ActivitySourceType.TEAM,
                source_id=updated.job_id,
                job_id=updated.job_id,
                department_id=updated.department_id,
                message=f"Final human review completed for job {updated.title}.",
                metadata={"stage": WorkflowStage.FINAL_REVIEW.value},
            )
        return updated

    def update_job_progress(
        self,
        job_id: str,
        progress: int,
        *,
        current_stage: str | None = None,
    ) -> Job:
        current = self.job(job_id)
        if current.status in TERMINAL_JOB_STATUSES:
            raise ValueError("Terminal job progress cannot be changed.")
        if JobTeamIntegration.from_job(current).active_link is not None:
            raise PermissionError(
                "Linked AI Team job progress is authoritative; use cc job sync."
            )
        if isinstance(progress, bool) or not isinstance(progress, int):
            raise ValueError("Job progress must be an integer.")
        if not 0 <= progress <= 100:
            raise ValueError("Job progress must be between 0 and 100.")
        if progress < current.progress:
            raise ValueError("Job progress cannot move backward.")
        stage = (
            str(current_stage).strip()
            if current_stage is not None
            else current.current_stage
        )
        if progress == current.progress and stage == current.current_stage:
            return current
        updated = replace(
            current,
            progress=progress,
            current_stage=stage,
            updated_at=self._timestamp(),
        )
        updated = Job.from_value(updated.to_dict())
        self.repository.save_job(updated, overwrite=True)
        self.record_activity(
            event_type="job.progress_updated",
            source_type=ActivitySourceType.JOB,
            source_id=updated.job_id,
            job_id=updated.job_id,
            department_id=updated.department_id,
            message=f"Job {updated.title} progress updated to {progress} percent.",
            metadata={"progress": progress, "stage": stage},
        )
        return updated

    def synchronize_job(
        self,
        job_id: str,
        *,
        status: JobStatus | str,
        current_stage: str,
        progress: int,
        approval_state: ApprovalState | str,
        metadata: dict[str, Any],
        workspace_reference: str | None = None,
        result_summary: str | None = None,
        error_summary: str | None = None,
    ) -> Job:
        """Persist one provider-neutral Team projection without CLI side effects."""
        current = self.job(job_id)
        target = JobStatus.parse(status)
        approval = ApprovalState.parse(approval_state)
        if current.status in TERMINAL_JOB_STATUSES and target != current.status:
            raise ValueError("Terminal jobs cannot be synchronized to a new status.")
        if not self._status_reachable(current.status, target):
            raise ValueError(
                f"AI Team state cannot map {current.status.value} to {target.value}."
            )
        if target == JobStatus.RUNNING and approval not in {
            ApprovalState.NOT_REQUIRED,
            ApprovalState.APPROVED,
        }:
            raise PermissionError(
                "Authoritative approval must resolve before implementation runs."
            )
        if isinstance(progress, bool) or not isinstance(progress, int):
            raise ValueError("Job progress must be an integer.")
        if not 0 <= progress <= 100:
            raise ValueError("Job progress must be between 0 and 100.")
        if progress < current.progress:
            progress = current.progress
        if target == JobStatus.COMPLETED:
            progress = 100
        timestamp = self._timestamp()
        completed_at = (
            current.completed_at
            if current.status in TERMINAL_JOB_STATUSES
            else timestamp if target in TERMINAL_JOB_STATUSES else ""
        )
        started_at = current.started_at
        if target in {
            JobStatus.RUNNING,
            JobStatus.AWAITING_REVIEW,
            JobStatus.COMPLETED,
        } and not started_at:
            started_at = timestamp
        workspace = (
            current.workspace_reference
            if workspace_reference is None
            else str(workspace_reference).strip()
        )
        result = (
            current.result_summary
            if result_summary is None
            else str(result_summary).strip()
        )
        error = (
            current.error_summary
            if error_summary is None
            else str(error_summary).strip()
        )
        semantic = (
            target,
            str(current_stage).strip(),
            progress,
            approval,
            metadata,
            workspace,
            result,
            error,
            started_at,
            completed_at,
        )
        existing = (
            current.status,
            current.current_stage,
            current.progress,
            current.approval_state,
            current.metadata,
            current.workspace_reference,
            current.result_summary,
            current.error_summary,
            current.started_at,
            current.completed_at,
        )
        if semantic == existing:
            return current
        updated = replace(
            current,
            status=target,
            current_stage=str(current_stage).strip(),
            progress=progress,
            approval_state=approval,
            metadata=dict(metadata),
            workspace_reference=workspace,
            result_summary=result,
            error_summary=error,
            started_at=started_at,
            completed_at=completed_at,
            updated_at=timestamp,
        )
        updated = Job.from_value(updated.to_dict())
        self.repository.save_job(updated, overwrite=True)
        return updated

    def resolve_job_approval(
        self,
        job_id: str,
        state: ApprovalState | str,
    ) -> Job:
        current = self.job(job_id)
        if JobTeamIntegration.from_job(current).active_link is not None:
            raise PermissionError(
                "Linked AI Team approvals are authoritative and cannot be "
                "resolved through Command Center."
            )
        target = ApprovalState.parse(state)
        if current.approval_state != ApprovalState.PENDING:
            raise ValueError("Job does not have a pending approval.")
        if target not in {
            ApprovalState.APPROVED,
            ApprovalState.DENIED,
            ApprovalState.CANCELLED,
        }:
            raise ValueError("Pending approval must be approved, denied, or cancelled.")
        updated = replace(
            current,
            approval_state=target,
            updated_at=self._timestamp(),
        )
        updated = Job.from_value(updated.to_dict())
        self.repository.save_job(updated, overwrite=True)
        self.record_activity(
            event_type="approval.resolved",
            source_type=ActivitySourceType.APPROVAL,
            source_id=updated.job_id,
            job_id=updated.job_id,
            department_id=updated.department_id,
            message=f"Approval for job {updated.title} was {target.value}.",
            metadata={"approval_state": target.value},
        )
        return updated

    def cancel_job(self, job_id: str) -> Job:
        current = self.job(job_id)
        if JobTeamIntegration.from_job(current).active_link is not None:
            if self.team_integration is None:
                raise ValueError(
                    "AI Team integration is unavailable; linked job was not cancelled."
                )
            return self.team_integration.cancel(job_id)
        return self.update_job_status(job_id, JobStatus.CANCELLED)

    def record_activity(
        self,
        *,
        event_type: str,
        source_type: ActivitySourceType | str,
        source_id: str,
        message: str,
        severity: ActivitySeverity | str = ActivitySeverity.INFO,
        job_id: str = "",
        department_id: str = "",
        agent_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent.create(
            event_id=self._id_factory("event"),
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            message=message,
            timestamp=self._timestamp(),
            severity=severity,
            job_id=job_id,
            department_id=department_id,
            agent_id=agent_id,
            metadata=metadata,
        )
        self.repository.append_activity(event)
        return event

    def activity(self, limit: int = 20) -> tuple[ActivityEvent, ...]:
        return self.repository.list_activity(limit)

    def snapshot(
        self,
        *,
        activity_limit: int = 20,
        completed_limit: int = 10,
    ) -> dict[str, Any]:
        """Build a detached, deterministic, display-safe JSON contract."""
        warnings: list[str] = []
        try:
            organization = self.repository.load_organization()
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            organization = None
            warnings.append(f"Organization is unavailable: {self._safe_error(exc)}")
        try:
            departments = self.departments()
        except (OSError, PermissionError, ValueError) as exc:
            departments = ()
            warnings.append(f"Departments are unavailable: {self._safe_error(exc)}")
        try:
            jobs = self.jobs()
        except (OSError, PermissionError, ValueError) as exc:
            jobs = ()
            warnings.append(f"Jobs are unavailable: {self._safe_error(exc)}")
        agents = self._all_agents(warnings)
        agent_index = {str(item.agent_id): item for item in agents}

        jobs_by_department: dict[str, list[Job]] = {
            department.department_id: [] for department in departments
        }
        for job in jobs:
            if job.department_id:
                if job.department_id in jobs_by_department:
                    jobs_by_department[job.department_id].append(job)
                else:
                    warnings.append(
                        f"Job {job.job_id} references missing department "
                        f"{job.department_id}."
                    )

        grouped: dict[str, list[dict[str, Any]]] = {}
        department_summaries: list[dict[str, Any]] = []
        referenced_agents: set[str] = set()
        for department in departments:
            resolved: list[dict[str, Any]] = []
            for agent_id in department.agent_ids:
                referenced_agents.add(agent_id)
                agent = agent_index.get(agent_id)
                if agent is None:
                    warnings.append(
                        f"Department {department.name} references missing agent "
                        f"{agent_id}."
                    )
                    resolved.append({
                        "id": agent_id,
                        "name": agent_id,
                        "scope": "unknown",
                        "enabled": False,
                        "reference_status": "missing",
                    })
                    continue
                enabled = bool(getattr(agent, "enabled", False))
                if not enabled:
                    warnings.append(
                        f"Department {department.name} references disabled agent "
                        f"{agent_id}."
                    )
                resolved.append(self._agent_summary(agent))
            grouped[department.department_id] = sorted(
                resolved, key=lambda item: (item["name"].casefold(), item["id"])
            )
            department_jobs = jobs_by_department[department.department_id]
            active_agent_ids = sorted({
                link.active_agent_id
                for item in department_jobs
                if item.status not in TERMINAL_JOB_STATUSES
                for link in [self._display_link(item, warnings)]
                if link is not None and link.active_agent_id
            })
            department_summaries.append({
                "id": department.department_id,
                "name": department.name,
                "description": department.description,
                "icon": department.icon,
                "enabled": department.enabled,
                "agent_count": len(department.agent_ids),
                "enabled_agent_count": sum(
                    1 for item in resolved
                    if item["reference_status"] == "available" and item["enabled"]
                ),
                "active_job_count": sum(
                    item.status in ACTIVE_JOB_STATUSES for item in department_jobs
                ),
                "queued_job_count": sum(
                    item.status == JobStatus.QUEUED for item in department_jobs
                ),
                "awaiting_approval_count": sum(
                    item.status == JobStatus.AWAITING_APPROVAL
                    for item in department_jobs
                ),
                "awaiting_review_count": sum(
                    item.status == JobStatus.AWAITING_REVIEW
                    for item in department_jobs
                ),
                "active_agent_ids": active_agent_ids,
            })

        grouped["unassigned"] = sorted(
            (
                self._agent_summary(agent)
                for agent in agents
                if str(agent.agent_id) not in referenced_agents
            ),
            key=lambda item: (item["name"].casefold(), item["id"]),
        )
        active_jobs = [
            self._job_summary(job) for job in jobs
            if job.status in ACTIVE_JOB_STATUSES
        ]
        queued_jobs = [
            self._job_summary(job) for job in jobs
            if job.status == JobStatus.QUEUED
        ]
        approvals = [
            self._job_summary(job) for job in jobs
            if job.status == JobStatus.AWAITING_APPROVAL
        ]
        reviews = [
            self._job_summary(job) for job in jobs
            if job.status == JobStatus.AWAITING_REVIEW
        ]
        completed = [
            self._job_summary(job) for job in jobs
            if job.status == JobStatus.COMPLETED
        ][:max(0, completed_limit)]
        for job in jobs:
            for agent_id in job.assigned_agent_ids:
                agent = agent_index.get(agent_id)
                if agent is None:
                    warnings.append(
                        f"Job {job.job_id} references missing agent {agent_id}."
                    )
                elif not bool(getattr(agent, "enabled", False)):
                    warnings.append(
                        f"Job {job.job_id} references disabled agent {agent_id}."
                    )
        try:
            recent_activity = [
                item.to_dict()
                for item in self.repository.list_activity(activity_limit)
            ]
        except (OSError, PermissionError, ValueError) as exc:
            recent_activity = []
            warnings.append(f"Activity is unavailable: {self._safe_error(exc)}")

        provider_health = self._provider_health(warnings)
        workspace_summary = self._workspace_summary(warnings)
        workflow_summary = {
            "planning": sum(
                item.status == JobStatus.PLANNING
                for item in jobs
            ),
            "implementing": sum(
                item.current_stage == WorkflowStage.IMPLEMENTATION.value
                for item in jobs
            ),
            "testing": sum(
                item.current_stage == WorkflowStage.TESTING.value
                for item in jobs
            ),
            "awaiting_approval": len(approvals),
            "awaiting_review": len(reviews),
            "failed_requiring_attention": sum(
                item.status == JobStatus.FAILED for item in jobs
            ),
        }
        result = {
            "schema_version": COMMAND_CENTER_SNAPSHOT_SCHEMA_VERSION,
            "generated_at": self._timestamp(),
            "organization": (
                {
                    "id": organization.organization_id,
                    "name": organization.name,
                    "description": organization.description,
                    "enabled": organization.enabled,
                }
                if organization is not None
                else None
            ),
            "departments": department_summaries,
            "agent_counts": {
                "total": len(agents),
                "enabled": sum(
                    bool(getattr(item, "enabled", False)) for item in agents
                ),
                "disabled": sum(
                    not bool(getattr(item, "enabled", False)) for item in agents
                ),
                "referenced": len(referenced_agents),
            },
            "agents_by_department": grouped,
            "active_jobs": active_jobs,
            "queued_jobs": queued_jobs,
            "jobs_awaiting_approval": approvals,
            "jobs_awaiting_review": reviews,
            "recently_completed_jobs": completed,
            "recent_activity": recent_activity,
            "workflow_summary": workflow_summary,
            "provider_health": provider_health,
            "workspace": workspace_summary,
            "warnings": sorted(set(warnings), key=str.casefold),
        }
        validate_safe_value(result, "Command Center snapshot", max_serialized_bytes=1_000_000)
        return result

    def doctor(self) -> dict[str, Any]:
        """Run read-only storage and reference validation."""
        diagnostics = list(self.repository.diagnostics())
        try:
            departments = self.repository.list_departments()
        except (OSError, PermissionError, ValueError):
            departments = ()
        try:
            jobs = self.repository.list_jobs()
        except (OSError, PermissionError, ValueError):
            jobs = ()
        agents = self._all_agents_for_doctor(diagnostics)
        agent_index = {str(item.agent_id): item for item in agents}
        department_ids = {item.department_id for item in departments}
        job_ids = {item.job_id for item in jobs}

        for department in departments:
            for agent_id in department.agent_ids:
                agent = agent_index.get(agent_id)
                if agent is None:
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "department.missing_agent",
                        f"Department {department.department_id} references "
                        f"missing agent {agent_id}.",
                        f"departments/{department.department_id}.yaml",
                    ))
                elif not bool(getattr(agent, "enabled", False)):
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "department.disabled_agent",
                        f"Department {department.department_id} references "
                        f"disabled agent {agent_id}.",
                        f"departments/{department.department_id}.yaml",
                    ))

        for job in jobs:
            if job.department_id and job.department_id not in department_ids:
                diagnostics.append(RepositoryDiagnostic(
                    "error",
                    "job.missing_department",
                    f"Job {job.job_id} references missing department "
                    f"{job.department_id}.",
                    f"jobs/{job.job_id}.yaml",
                ))
            for agent_id in job.assigned_agent_ids:
                agent = agent_index.get(agent_id)
                if agent is None:
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "job.missing_agent",
                        f"Job {job.job_id} references missing agent {agent_id}.",
                        f"jobs/{job.job_id}.yaml",
                    ))
                elif not bool(getattr(agent, "enabled", False)):
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "job.disabled_agent",
                        f"Job {job.job_id} references disabled agent {agent_id}.",
                        f"jobs/{job.job_id}.yaml",
                    ))
            if job.workspace_reference:
                workspace = Path(job.workspace_reference)
                if not workspace.exists() or not workspace.is_dir():
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "job.workspace_inaccessible",
                        f"Job {job.job_id} references an inaccessible workspace.",
                        f"jobs/{job.job_id}.yaml",
                    ))
        try:
            events = self.repository.list_activity(1_000)
        except (OSError, PermissionError, ValueError):
            events = ()
        integration_event_signatures: set[tuple[str, str, str, str]] = set()
        for event in events:
            if event.job_id and event.job_id not in job_ids:
                diagnostics.append(RepositoryDiagnostic(
                    "warning",
                    "activity.missing_job",
                    f"Activity {event.event_id} references missing job "
                    f"{event.job_id}.",
                    "activity.jsonl",
                ))
            if event.event_type.startswith((
                "workflow.", "approval.", "execution.", "testing.", "review.",
                "job.sync_",
            )):
                signature = (
                    event.event_type,
                    event.job_id,
                    event.message,
                    repr(event.metadata),
                )
                if signature in integration_event_signatures:
                    diagnostics.append(RepositoryDiagnostic(
                        "warning",
                        "integration.duplicate_activity",
                        f"Duplicate integration activity is detectable for "
                        f"job {event.job_id or 'unknown'}.",
                        "activity.jsonl",
                    ))
                integration_event_signatures.add(signature)
        if self.team_integration is not None:
            try:
                diagnostics.extend(self.team_integration.doctor_issues(jobs))
            except (OSError, PermissionError, RuntimeError, ValueError) as exc:
                diagnostics.append(RepositoryDiagnostic(
                    "error",
                    "integration.doctor_unavailable",
                    "AI Team integration health could not be inspected: "
                    f"{self._safe_error(exc)}",
                ))
            if event.department_id and event.department_id not in department_ids:
                diagnostics.append(RepositoryDiagnostic(
                    "warning",
                    "activity.missing_department",
                    f"Activity {event.event_id} references missing department "
                    f"{event.department_id}.",
                    "activity.jsonl",
                ))
        diagnostics = sorted(
            diagnostics,
            key=lambda item: (
                {"error": 0, "warning": 1, "info": 2}.get(item.severity, 3),
                item.code,
                item.record,
                item.message,
            ),
        )
        errors = sum(item.severity == "error" for item in diagnostics)
        warnings = sum(item.severity == "warning" for item in diagnostics)
        return {
            "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
            "checked_at": self._timestamp(),
            "ok": errors == 0,
            "error_count": errors,
            "warning_count": warnings,
            "issues": [item.to_dict() for item in diagnostics],
        }

    def _load_agent(self, reference: str):
        try:
            return self.agent_manager.load(reference)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Agent not found: {reference}") from exc

    def _load_assignable_agent(self, reference: str):
        agent = self._load_agent(reference)
        if not bool(getattr(agent, "enabled", False)):
            raise ValueError(f"Agent is disabled: {agent.agent_id}")
        return agent

    def _membership_agent_id(
        self,
        department: Department,
        reference: str,
    ) -> str:
        try:
            agent = self._load_agent(reference)
            return str(agent.agent_id)
        except FileNotFoundError:
            try:
                normalized = normalize_id(reference, "Agent ID")
            except ValueError as exc:
                raise FileNotFoundError(f"Agent not found: {reference}") from exc
            if normalized in department.agent_ids:
                return normalized
            raise

    def _all_agents(self, warnings: list[str]) -> tuple[Any, ...]:
        try:
            return tuple(self.agent_manager.all())
        except (OSError, PermissionError, ValueError) as exc:
            warnings.append(f"Agents are unavailable: {self._safe_error(exc)}")
            return ()

    def _all_agents_for_doctor(
        self,
        diagnostics: list[RepositoryDiagnostic],
    ) -> tuple[Any, ...]:
        try:
            return tuple(self.agent_manager.all())
        except (OSError, PermissionError, ValueError) as exc:
            diagnostics.append(RepositoryDiagnostic(
                "error",
                "agent.repository_invalid",
                f"Agent repository could not be read: {self._safe_error(exc)}",
            ))
            return ()

    @staticmethod
    def _agent_summary(agent) -> dict[str, Any]:
        return {
            "id": str(agent.agent_id),
            "name": str(agent.name)[:120],
            "scope": str(getattr(agent, "scope", "permanent"))[:40],
            "enabled": bool(getattr(agent, "enabled", False)),
            "reference_status": "available",
        }

    @staticmethod
    def _job_summary(job: Job) -> dict[str, Any]:
        link = None
        integration_warning = ""
        try:
            integration = JobTeamIntegration.from_job(job)
            link = integration.active_link
            if link is None and integration.links:
                link = integration.links[-1]
        except ValueError:
            integration_warning = "Team integration metadata is invalid."
        return {
            "id": job.job_id,
            "title": job.title,
            "status": job.status.value,
            "priority": job.priority.value,
            "department_id": job.department_id,
            "assigned_agent_ids": list(job.assigned_agent_ids),
            "approval_state": job.approval_state.value,
            "current_stage": job.current_stage,
            "progress": job.progress,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "result_summary": job.result_summary,
            "error_summary": job.error_summary,
            "active_agent_id": link.active_agent_id if link else "",
            "team_link": (
                {
                    "team_task_id": link.team_task_id,
                    "team_run_id": link.team_run_id,
                    "workflow_id": link.workflow_id,
                    "external_status": link.external_status,
                    "execution_engine": link.execution_engine,
                    "last_synced_at": link.last_synced_at,
                    "active": link.active,
                }
                if link is not None
                else None
            ),
            "next_action": (
                link.next_action
                if link is not None
                else "Launch the job when ready."
            ),
            "warnings": (
                list(link.synchronization_warnings)
                if link is not None
                else [integration_warning] if integration_warning else []
            ),
        }

    @staticmethod
    def _display_link(
        job: Job,
        warnings: list[str],
    ) -> TeamIntegrationLink | None:
        try:
            integration = JobTeamIntegration.from_job(job)
        except ValueError:
            warnings.append(
                f"Job {job.job_id} has invalid Team integration metadata."
            )
            return None
        if integration.active_link is not None:
            return integration.active_link
        return integration.links[-1] if integration.links else None

    def _provider_health(self, warnings: list[str]) -> dict[str, Any]:
        if self.provider_manager is None:
            return {
                "available": False,
                "routing_enabled": False,
                "routing_profile": "",
                "providers": [],
            }
        try:
            providers = [
                {
                    "id": str(item.key),
                    "status": (
                        "ready"
                        if item.enabled and item.configured
                        else "unavailable"
                        if item.enabled
                        else "disabled"
                    ),
                    "enabled": bool(item.enabled),
                    "configured": bool(item.configured),
                    "active": bool(item.active),
                    "model": str(item.model)[:200],
                }
                for item in self.provider_manager.statuses()
            ]
            routing_enabled = bool(
                getattr(self.routing_service, "enabled", False)
            )
            routing_profile = str(
                getattr(self.routing_service, "profile", "")
            )
            return {
                "available": True,
                "routing_enabled": routing_enabled,
                "routing_profile": routing_profile,
                "providers": providers,
            }
        except Exception as exc:
            warnings.append(
                f"Provider health is unavailable: {type(exc).__name__}."
            )
            return {
                "available": False,
                "routing_enabled": False,
                "routing_profile": "",
                "providers": [],
            }

    def _workspace_summary(self, warnings: list[str]) -> dict[str, Any]:
        if self.workspace_manager is None:
            return {"available": False, "name": "", "mode": "", "is_git": False}
        try:
            root = Path(self.workspace_manager.root)
            capabilities = self.workspace_manager.capabilities
            return {
                "available": root.exists() and root.is_dir(),
                "name": root.name,
                "mode": str(getattr(capabilities, "mode", "standard")),
                "is_git": bool(
                    getattr(capabilities, "is_git_repository", False)
                ),
                "branch": str(getattr(capabilities, "branch", ""))[:200],
            }
        except Exception as exc:
            warnings.append(
                f"Workspace summary is unavailable: {type(exc).__name__}."
            )
            return {"available": False, "name": "", "mode": "", "is_git": False}

    @staticmethod
    def _status_reachable(current: JobStatus, target: JobStatus) -> bool:
        if current == target:
            return True
        pending = [current]
        visited = {current}
        while pending:
            status = pending.pop(0)
            for candidate in JOB_TRANSITIONS[status]:
                if candidate == target:
                    return True
                if candidate not in visited:
                    visited.add(candidate)
                    pending.append(candidate)
        return False

    def _timestamp(self) -> str:
        return parse_timestamp(self._now(), "Command Center timestamp")

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, FileNotFoundError):
            return "record not found"
        text = str(error).strip()
        return text[:300] if text else type(error).__name__
