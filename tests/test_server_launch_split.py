"""Prepare/execute split for Command Center job launch (server Phase A).

The server answers job launches with 202 after linking the job durably,
then runs Team planning detached. These tests pin the split: prepare
links without planning, execute plans exactly once, and a cancellation
or superseding link between prepare and execute wins.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from orion.command_center import (
    ApprovalState,
    JobStatus,
    JobTeamIntegration,
    LaunchPreparation,
    LaunchValidationError,
    WorkflowStage,
)
from tests.test_command_center_workflow import (
    JOB_ID,
    TEAM_TASK_ID,
    WorkflowFixture,
)


class LaunchSplitTests(unittest.TestCase, WorkflowFixture):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.service, self.integration, self.team, _, _ = self.build(self.root)
        self.create_job(self.service, str(self.root))

    def test_prepare_links_job_without_planning(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        self.assertIsInstance(preparation, LaunchPreparation)
        self.assertEqual(preparation.team_task_id, TEAM_TASK_ID)
        self.assertEqual(preparation.job.job_id, JOB_ID)
        self.assertEqual(preparation.job.status, JobStatus.PLANNING)
        self.assertEqual(preparation.job.progress, 5)
        self.assertTrue(preparation.preview.allowed)
        self.assertFalse(self.team.plan_calls)
        stored = self.service.job(JOB_ID)
        self.assertEqual(stored.status, JobStatus.PLANNING)
        link = JobTeamIntegration.from_job(stored).active_link
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.team_task_id, TEAM_TASK_ID)
        self.assertEqual(link.external_status, "planning")

    def test_prepare_then_execute_matches_launch(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        result = self.integration.execute_launch(preparation)
        self.assertEqual(result.team_task_id, TEAM_TASK_ID)
        self.assertEqual(result.job.job_id, JOB_ID)
        self.assertEqual(len(self.team.plan_calls), 1)
        self.assertEqual(self.team.plan_calls[0]["task_id"], TEAM_TASK_ID)
        self.assertEqual(
            self.team.plan_calls[0]["goal"],
            "Create a safe implementation plan and stop for approval.",
        )

    def test_prepare_rejects_invalid_launch(self) -> None:
        self.service.create_job(
            job_id="job-no-department",
            title="No department",
            goal="Nothing to launch.",
            department="",
            workspace_reference=str(self.root),
        )
        with self.assertRaises(LaunchValidationError):
            self.integration.prepare_launch("job-no-department")
        self.assertFalse(self.team.plan_calls)

    def test_execute_refuses_after_cancel(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        current = self.service.job(JOB_ID)
        self.service.synchronize_job(
            JOB_ID,
            status=JobStatus.CANCELLED,
            current_stage=WorkflowStage.CANCELLED.value,
            progress=current.progress,
            approval_state=current.approval_state,
            metadata=dict(current.metadata),
        )
        with self.assertRaises(ValueError) as caught:
            self.integration.execute_launch(preparation)
        self.assertIn("cancelled or superseded", str(caught.exception))
        self.assertFalse(self.team.plan_calls)

    def test_execute_refuses_after_link_cleared(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        current = self.service.job(JOB_ID)
        metadata = dict(current.metadata)
        metadata.pop("team_integration", None)
        self.service.synchronize_job(
            JOB_ID,
            status=JobStatus.PLANNING,
            current_stage=WorkflowStage.PLANNING.value,
            progress=current.progress,
            approval_state=current.approval_state,
            metadata=metadata,
        )
        with self.assertRaises(ValueError):
            self.integration.execute_launch(preparation)
        self.assertFalse(self.team.plan_calls)

    def test_execute_twice_second_plans_again_only_after_reprepare(self) -> None:
        # Execute is not itself idempotent: once planning has run, the job
        # has moved on (AWAITING_APPROVAL), so a second execute of the same
        # preparation is refused by the PLANNING re-check.
        preparation = self.integration.prepare_launch(JOB_ID)
        self.integration.execute_launch(preparation)
        with self.assertRaises(ValueError):
            self.integration.execute_launch(preparation)
        self.assertEqual(len(self.team.plan_calls), 1)

    def test_execute_requires_a_preparation(self) -> None:
        with self.assertRaises(TypeError):
            self.integration.execute_launch(object())  # type: ignore[arg-type]

    def test_preparation_rejects_non_planning_job(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        queued = replace(
            preparation.job,
            status=JobStatus.QUEUED,
        )
        with self.assertRaises(ValueError):
            LaunchPreparation(
                job=queued,
                team_task_id=preparation.team_task_id,
                preview=preparation.preview,
                link=preparation.link,
            )

    def test_preparation_rejects_mismatched_link(self) -> None:
        preparation = self.integration.prepare_launch(JOB_ID)
        other_link = replace(
            preparation.link,
            team_task_id="team-someone-else",
        )
        with self.assertRaises(ValueError):
            LaunchPreparation(
                job=preparation.job,
                team_task_id=preparation.team_task_id,
                preview=preparation.preview,
                link=other_link,
            )


if __name__ == "__main__":
    unittest.main()
