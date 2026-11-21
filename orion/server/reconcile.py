"""Startup reconciliation for in-flight detached operations.

Read-only: this module never mutates a record and never replays work.
On startup it classifies every detached operation that was in flight
when the server last stopped:

- Mission advances with a ``reserved`` or ``uncertain`` audit are
  uncertain — the operator resolves them via the Mission Coordinator's
  resolve workflow (ran / did-not-run / cannot-determine).
- Jobs in PLANNING with an active Team link whose task is missing,
  still planning, or failed are uncertain or need a sync — the Team
  task record is the durable truth for job launches.
- Jobs in RUNNING with an active link are uncertain — the run record
  is authoritative and the Codex child may or may not have survived.

The report is logged at startup and surfaced via GET /api/healthz so
an operator (or the future supervisor) can see what needs attention.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UncertainMissionOperation:
    mission_id: str
    attempt_id: str
    capability_id: str
    subject_id: str
    state: str
    started_at: str
    lock_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "attempt_id": self.attempt_id,
            "capability_id": self.capability_id,
            "subject_id": self.subject_id,
            "state": self.state,
            "started_at": self.started_at,
            "lock_present": self.lock_present,
            "resolution": (
                f"POST /api/missions/{self.mission_id}/resolve/preview, "
                "then resolve with the operator's finding."
            ),
        }


@dataclass(frozen=True)
class UncertainJobLaunch:
    job_id: str
    team_task_id: str
    job_status: str
    team_task_status: str
    classification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "team_task_id": self.team_task_id,
            "job_status": self.job_status,
            "team_task_status": self.team_task_status,
            "classification": self.classification,
            "resolution": (
                f"POST /api/jobs/{self.job_id}/sync to reconcile from the "
                "Team record, or POST /api/jobs/{job_id}/cancel."
            ),
        }


@dataclass(frozen=True)
class ReconciliationReport:
    """What startup found in flight. Empty means a clean restart."""

    missions: tuple[UncertainMissionOperation, ...] = ()
    jobs: tuple[UncertainJobLaunch, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def uncertain_count(self) -> int:
        return len(self.missions) + len(self.jobs)

    @property
    def clean(self) -> bool:
        return self.uncertain_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "uncertain_count": self.uncertain_count,
            "missions": [item.to_dict() for item in self.missions],
            "jobs": [item.to_dict() for item in self.jobs],
            "warnings": list(self.warnings),
        }


# Job states that mean work was in flight when the server stopped.
_IN_FLIGHT_JOB_STATUSES = frozenset({"planning", "running"})


def reconcile_startup(
    *,
    missions: Any,
    coordination_repository: Any,
    command_center: Any,
    team: Any,
    logger: logging.Logger | None = None,
) -> ReconciliationReport:
    """Classify in-flight detached operations. Never mutates, never replays."""
    log = logger or logging.getLogger("orion.server.reconcile")
    warnings: list[str] = []
    uncertain_missions: list[UncertainMissionOperation] = []
    uncertain_jobs: list[UncertainJobLaunch] = []

    try:
        mission_list = missions.list(limit=500)
    except Exception as exc:  # noqa: BLE001 - reconcile must never break startup
        warnings.append(f"Mission list is unavailable for reconciliation: {exc}")
        mission_list = ()
    for mission in mission_list:
        try:
            audit = coordination_repository.get(mission.mission_id)
        except Exception as exc:  # noqa: BLE001 - one bad audit must not hide the rest
            warnings.append(
                f"Coordination audit for {mission.mission_id} is unreadable: {exc}"
            )
            continue
        if audit is None or audit.state not in {"reserved", "uncertain"}:
            continue
        try:
            lock_present = bool(
                coordination_repository.lock_exists(mission.mission_id)
            )
        except Exception:  # noqa: BLE001 - lock state is advisory here
            lock_present = False
        uncertain_missions.append(UncertainMissionOperation(
            mission_id=mission.mission_id,
            attempt_id=audit.attempt_id,
            capability_id=audit.capability_id,
            subject_id=audit.subject_id,
            state=audit.state,
            started_at=audit.started_at,
            lock_present=lock_present,
        ))

    try:
        job_list = command_center.jobs()
    except Exception as exc:  # noqa: BLE001 - reconcile must never break startup
        warnings.append(f"Job list is unavailable for reconciliation: {exc}")
        job_list = ()
    for job in job_list:
        status = str(getattr(job.status, "value", job.status)).lower()
        if status not in _IN_FLIGHT_JOB_STATUSES:
            continue
        try:
            from orion.command_center.models import JobTeamIntegration

            link = JobTeamIntegration.from_job(job).active_link
        except Exception as exc:  # noqa: BLE001 - one bad job must not hide the rest
            warnings.append(f"Job {job.job_id} link is unreadable: {exc}")
            continue
        if link is None:
            continue
        try:
            task = team.task(link.team_task_id)
            task_status = str(getattr(task, "status", "")).strip().lower()
        except FileNotFoundError:
            task_status = "missing"
        except Exception as exc:  # noqa: BLE001 - one bad task must not hide the rest
            warnings.append(
                f"Team task {link.team_task_id} is unreadable: {exc}"
            )
            continue
        if status == "running":
            classification = "uncertain"
        elif task_status in {"missing", "planning", "failed"}:
            classification = "uncertain"
        else:
            # Planning finished but the job was never synced to the
            # Team record's outcome; a sync settles it.
            classification = "needs_sync"
        uncertain_jobs.append(UncertainJobLaunch(
            job_id=job.job_id,
            team_task_id=link.team_task_id,
            job_status=status,
            team_task_status=task_status,
            classification=classification,
        ))

    report = ReconciliationReport(
        missions=tuple(uncertain_missions),
        jobs=tuple(uncertain_jobs),
        warnings=tuple(warnings),
    )
    if report.clean:
        log.info("startup reconciliation: no in-flight operations found")
    else:
        log.warning(
            "startup reconciliation: %d uncertain operation(s) need operator "
            "attention; nothing was replayed",
            report.uncertain_count,
        )
    return report
