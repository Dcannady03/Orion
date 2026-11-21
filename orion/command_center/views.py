"""Shared Command Center job views for the CLI and the local API.

The job view is the read model for one job: identity, lifecycle, the
active (or most recent) AI Team link, and recent job activity. Both the
``cc`` command handler and the server API render this same shape so the
two interfaces never drift apart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orion.command_center.models import JobTeamIntegration


def job_view(service: Any, job: Any) -> dict[str, Any]:
    """Serialize one job with its Team link and recent activity."""
    link = None
    warnings: list[str] = []
    assignments: list[dict[str, Any]] = []
    try:
        integration = JobTeamIntegration.from_job(job)
        link = integration.active_link
        if link is None and integration.links:
            link = integration.links[-1]
    except ValueError as exc:
        warnings.append(str(exc)[:500])
    if link is not None:
        warnings.extend(link.synchronization_warnings)
        assignments = [
            item.to_dict() for item in link.role_assignments
        ]
    try:
        events = [
            event.to_dict()
            for event in service.activity(1_000)
            if event.job_id == job.job_id
        ][-5:]
    except (OSError, PermissionError, ValueError):
        events = []
        warnings.append("Recent job activity is unavailable.")
    workspace_name = (
        Path(job.workspace_reference).name if job.workspace_reference else ""
    )
    return {
        "id": job.job_id,
        "title": job.title,
        "department": job.department_id or "Unassigned",
        "status": job.status.value,
        "stage": job.current_stage or "not_started",
        "progress": job.progress,
        "priority": job.priority.value,
        "approval_state": job.approval_state.value,
        "approval_id": link.approval_id if link else "",
        "workspace": workspace_name or "Not set",
        "workflow": link.workflow_id if link else "",
        "team_task_id": link.team_task_id if link else "",
        "team_run_id": link.team_run_id if link else "",
        "active_agent_id": link.active_agent_id if link else "",
        "external_status": link.external_status if link else "",
        "execution_engine": link.execution_engine if link else "",
        "next_action": (
            link.next_action
            if link is not None
            else "Launch the job when ready."
        ),
        "assignments": assignments,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "result_summary": job.result_summary,
        "error_summary": job.error_summary,
        "warnings": list(dict.fromkeys(warnings)),
        "recent_activity": events,
    }
