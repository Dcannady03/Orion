"""Execution-engine-neutral lifecycle adapters for Command Center jobs."""
from __future__ import annotations

from typing import Any

from orion.command_center.models import ApprovalState, JobStatus


class CommandCenterJobUpdateAdapter:
    """Allow Team and future engines to report lifecycle state safely.

    This adapter never executes work, grants permissions, or creates an approval.
    It delegates all transitions to CommandCenterService, including its approval
    and validation rules.
    """

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

    def request_approval(self, job_id: str, *, stage: str = "awaiting approval"):
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

    def awaiting_review(self, job_id: str, *, stage: str = "awaiting review"):
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
        """Map the current provider-neutral TeamTask state onto an existing job."""
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
