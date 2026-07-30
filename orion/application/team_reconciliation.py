"""Shared application-layer reconciliation for AI Team lifecycle observers."""
from __future__ import annotations

from orion.application.results import ApplicationResult


def synchronize_command_center_team(
    integration,
    *,
    team_task_id: str = "",
    run_id: str = "",
) -> ApplicationResult:
    """Reconcile a Team lifecycle event without coupling callers to its service."""
    if integration is None:
        return ApplicationResult.success(
            "",
            data={"synchronized": False, "reason": "integration_unavailable"},
        )
    try:
        if run_id:
            integration.sync_from_team_run(run_id)
        elif team_task_id:
            integration.sync_from_team_task(team_task_id)
        else:
            return ApplicationResult.success(
                "",
                data={"synchronized": False, "reason": "reference_missing"},
            )
    except FileNotFoundError:
        # Most Team records are intentionally not owned by Command Center.
        return ApplicationResult.success(
            "",
            data={"synchronized": False, "reason": "job_not_linked"},
        )
    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
        message = f"Command Center synchronization warning: {exc}"
        return ApplicationResult.success(message, warnings=(str(exc),))
    return ApplicationResult.success("", data={"synchronized": True})
