"""Startup reconciliation tests (server Phase A).

Reconcile is read-only: it classifies in-flight detached operations as
uncertain or needs-sync, never mutates a record, and never replays
work.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orion.application.missions import (
    MissionAdvanceRequest,
)
from orion.server.reconcile import reconcile_startup
from tests.test_command_center_workflow import (
    JOB_ID,
    TEAM_TASK_ID,
    WorkflowFixture,
)
from tests.test_mission_coordinator import MissionCoordinatorFixture


class ReconcileTests(MissionCoordinatorFixture):
    def setUp(self) -> None:
        super().setUp()
        self._cc_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cc_temp.cleanup)
        cc_root = Path(self._cc_temp.name)
        self.cc_service, self.integration, self.fake_team, _, _ = (
            WorkflowFixture().build(cc_root)
        )
        self.cc_root = cc_root

    def _reconcile(self):
        return reconcile_startup(
            missions=self.service,
            coordination_repository=self.audit_repository,
            command_center=self.cc_service,
            team=self.fake_team,
        )

    def test_clean_restart_reports_nothing(self) -> None:
        report = self._reconcile()
        self.assertTrue(report.clean)
        self.assertEqual(report.uncertain_count, 0)
        self.assertEqual(report.missions, ())
        self.assertEqual(report.jobs, ())

    def test_reserved_mission_audit_is_uncertain(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
            actor="operator",
        ))
        report = self._reconcile()
        self.assertFalse(report.clean)
        self.assertEqual(len(report.missions), 1)
        item = report.missions[0]
        self.assertEqual(item.mission_id, self.mission.mission_id)
        self.assertEqual(item.attempt_id, dispatch.audit.attempt_id)
        self.assertEqual(item.state, "reserved")
        self.assertEqual(item.capability_id, "team.approve")
        # Read-only: the audit is still reserved, nothing replayed.
        stored = self.audit_repository.get(self.mission.mission_id)
        assert stored is not None
        self.assertEqual(stored.state, "reserved")
        self.assertFalse(self.team_application.approve_calls)

    def test_planning_job_with_missing_task_is_uncertain(self) -> None:
        WorkflowFixture.create_job(self.cc_service, str(self.cc_root))
        self.integration.prepare_launch(JOB_ID)
        report = self._reconcile()
        self.assertEqual(len(report.jobs), 1)
        item = report.jobs[0]
        self.assertEqual(item.job_id, JOB_ID)
        self.assertEqual(item.team_task_id, TEAM_TASK_ID)
        self.assertEqual(item.team_task_status, "missing")
        self.assertEqual(item.classification, "uncertain")
        # Read-only: the job is still PLANNING, no planning call ran.
        self.assertEqual(self.cc_service.job(JOB_ID).status.value, "planning")
        self.assertFalse(self.fake_team.plan_calls)

    def test_planning_job_with_finished_task_needs_sync(self) -> None:
        WorkflowFixture.create_job(self.cc_service, str(self.cc_root))
        preparation = self.integration.prepare_launch(JOB_ID)
        self.integration.execute_launch(preparation)
        # Simulate a crash between planning and the final sync by
        # moving the job back to PLANNING with its link intact.
        from orion.command_center import ApprovalState, JobStatus, WorkflowStage

        current = self.cc_service.job(JOB_ID)
        self.cc_service.synchronize_job(
            JOB_ID,
            status=JobStatus.PLANNING,
            current_stage=WorkflowStage.PLANNING.value,
            progress=current.progress,
            approval_state=ApprovalState.NOT_REQUIRED,
            metadata=dict(current.metadata),
        )
        report = self._reconcile()
        self.assertEqual(len(report.jobs), 1)
        self.assertEqual(report.jobs[0].classification, "needs_sync")
        self.assertEqual(
            report.jobs[0].team_task_status, "awaiting_approval"
        )

    def test_running_job_is_uncertain(self) -> None:
        WorkflowFixture.create_job(self.cc_service, str(self.cc_root))
        preparation = self.integration.prepare_launch(JOB_ID)
        self.integration.execute_launch(preparation)
        from orion.command_center import ApprovalState, JobStatus, WorkflowStage

        current = self.cc_service.job(JOB_ID)
        self.cc_service.synchronize_job(
            JOB_ID,
            status=JobStatus.RUNNING,
            current_stage=WorkflowStage.IMPLEMENTATION.value,
            progress=current.progress,
            approval_state=ApprovalState.APPROVED,
            metadata=dict(current.metadata),
        )
        report = self._reconcile()
        self.assertEqual(len(report.jobs), 1)
        self.assertEqual(report.jobs[0].classification, "uncertain")
        self.assertEqual(report.jobs[0].job_status, "running")

    def test_unlinked_planning_job_is_not_reported(self) -> None:
        # A job in PLANNING without a Team link is not a detached
        # launch; reconcile leaves it alone.
        job = WorkflowFixture.create_job(self.cc_service, str(self.cc_root))
        from orion.command_center import ApprovalState, JobStatus, WorkflowStage

        self.cc_service.synchronize_job(
            JOB_ID,
            status=JobStatus.PLANNING,
            current_stage=WorkflowStage.PLANNING.value,
            progress=5,
            approval_state=ApprovalState.NOT_REQUIRED,
            metadata=dict(job.metadata),
        )
        report = self._reconcile()
        self.assertEqual(report.jobs, ())

    def test_report_serializes(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        self.coordinator.dispatch(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
            actor="operator",
        ))
        data = self._reconcile().to_dict()
        self.assertFalse(data["clean"])
        self.assertEqual(data["uncertain_count"], 1)
        self.assertEqual(len(data["missions"]), 1)
        self.assertIn("resolve/preview", data["missions"][0]["resolution"])


if __name__ == "__main__":
    unittest.main()
