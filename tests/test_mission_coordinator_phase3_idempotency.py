"""Mission Coordinator Phase 3: final-review idempotency regression suite.

Proves the new final-review paths are as safe as the old ones: a decision
token cannot be replayed, swapped, or dispatched twice — including across
a resolution-cleared attempt and across concurrent advances.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from orion.application.missions import (
    MissionAdvanceAudit,
    MissionAdvanceRequest,
    MissionCoordinationError,
    MissionStalePreviewError,
)
from tests.test_mission_coordinator_phase2 import (
    FINAL_REVIEW_ID,
    RUN_ID,
)
from tests.test_mission_coordinator_phase3_history import (
    MissionCoordinatorHistoryFixture,
)


class FinalReviewIdempotencyTests(MissionCoordinatorHistoryFixture):
    def _write_succeeded_final_review_audit(self, token: str) -> None:
        current = self.audit_repository.get(self.mission.mission_id)
        audit = MissionAdvanceAudit(
            schema_version=1,
            mission_id=self.mission.mission_id,
            attempt_id="mission-attempt-" + "9" * 32,
            advance_token=token,
            capability_id="team.final_review",
            subject_id=RUN_ID,
            actor="operator",
            state="succeeded",
            started_at="2026-07-18T13:10:00Z",
            finished_at="2026-07-18T13:11:00Z",
            result_status="succeeded",
            downstream_reference=FINAL_REVIEW_ID,
        )
        self.audit_repository.write(
            audit,
            expected_attempt_id=current.attempt_id if current else None,
        )

    def _advance_final_review(self, token: str, decision: str):
        return self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            token,
            confirmed=True,
            actor="operator",
            decision=decision,
        ))

    def test_same_token_replay_blocked_after_success(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertFalse(preview.operation.blocked)
        self._write_succeeded_final_review_audit(preview.advance_token)
        replayed = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertTrue(replayed.operation.blocked)
        self.assertIn("already dispatched", replayed.operation.blocked_reason)
        with self.assertRaisesRegex(
            MissionCoordinationError, "already dispatched"
        ):
            self._advance_final_review(preview.advance_token, "accepted")
        self.assertEqual(len(self.team_application.final_review_calls), 0)

    def test_accept_token_cannot_be_used_for_reject(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        with self.assertRaises(MissionStalePreviewError):
            self._advance_final_review(preview.advance_token, "rejected")
        self.assertEqual(len(self.team_application.final_review_calls), 0)

    def test_reject_token_cannot_be_used_for_accept(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="rejected"
        )
        with self.assertRaises(MissionStalePreviewError):
            self._advance_final_review(preview.advance_token, "accepted")
        self.assertEqual(len(self.team_application.final_review_calls), 0)

    def test_decision_is_written_once_even_without_an_observed_event(self):
        self.to_final_review()
        self.team_application.emit_events = False
        _preview, result = self.final_review(decision="accepted")
        self.assertEqual(result.new_status, "awaiting_review")
        self.assertEqual(len(self.team_application.final_review_calls), 1)
        self.assertEqual(
            self.team_application.runs[0]["final_review_decision"],
            "accepted",
        )
        # The decision exists in Team state even though the Mission never
        # observed the event: a second attempt must block fail-closed.
        blocked = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertTrue(blocked.operation.blocked)
        with self.assertRaises(MissionCoordinationError):
            self._advance_final_review(_preview.advance_token, "accepted")
        self.assertEqual(len(self.team_application.final_review_calls), 1)

    def test_rejected_final_review_cannot_be_replayed_as_accept(self):
        self.to_final_review()
        self.final_review(decision="rejected")
        self.assertEqual(len(self.team_application.final_review_calls), 1)
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status.value, "blocked")
        blocked = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertTrue(blocked.operation.blocked)
        self.assertEqual(len(self.team_application.final_review_calls), 1)

    def test_resolution_cleared_final_review_dispatches_exactly_once(self):
        self.to_final_review()
        self._write_stuck_audit("team.final_review", RUN_ID)
        cleared = self._resolve("did_not_run")
        self.assertEqual(cleared.audit_outcome, "cleared")
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        _preview, result = self.final_review(decision="accepted")
        self.assertEqual(result.new_status, "completed")
        self.assertEqual(len(self.team_application.final_review_calls), 1)
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "succeeded")
        self.assertEqual(audit.advance_token, _preview.advance_token)
        blocked = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertTrue(blocked.operation.blocked)
        self.assertEqual(len(self.team_application.final_review_calls), 1)

    def test_concurrent_final_review_advances_dispatch_once(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        first = self._coordinator()
        second = self._coordinator()
        barrier = threading.Barrier(2)
        results: list = []
        errors: list = []

        def attempt(coordinator):
            barrier.wait(timeout=5)
            try:
                results.append(coordinator.advance(MissionAdvanceRequest(
                    self.mission.mission_id,
                    preview.advance_token,
                    confirmed=True,
                    actor="operator",
                    decision="accepted",
                )))
            except MissionCoordinationError as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(attempt, (first, second)))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(self.team_application.final_review_calls), 1)
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status.value, "completed")


if __name__ == "__main__":
    unittest.main()
