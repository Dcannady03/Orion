"""Dispatch/execute split for Mission advance (server Phase A).

The server answers long-running advances with 202 after reserving the
operation durably, then executes detached. These tests pin the split:
dispatch() reserves without running anything, execute() runs exactly
once, and an operator resolution between dispatch and execute wins.
"""
from __future__ import annotations

from dataclasses import replace
import unittest

from orion.application.missions import (
    MissionAdvanceDispatch,
    MissionAdvanceRequest,
    MissionCoordinationError,
    MissionCoordinator,
    MissionDispatchUncertainError,
    MissionResolveRequest,
)
from tests.test_mission_coordinator import MissionCoordinatorFixture


class MissionDispatchSplitTests(MissionCoordinatorFixture):
    def _request(self, token: str) -> MissionAdvanceRequest:
        return MissionAdvanceRequest(
            self.mission.mission_id,
            token,
            confirmed=True,
            actor="operator",
        )

    def test_dispatch_reserves_audit_without_running_operation(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        self.assertIsInstance(dispatch, MissionAdvanceDispatch)
        self.assertEqual(dispatch.mission_id, self.mission.mission_id)
        self.assertEqual(dispatch.audit.state, "reserved")
        self.assertEqual(dispatch.audit.advance_token, preview.advance_token)
        self.assertEqual(dispatch.audit.capability_id, "team.approve")
        self.assertEqual(dispatch.preview, preview)
        self.assertEqual(dispatch.actor, "operator")
        self.assertFalse(self.team_application.approve_calls)
        stored = self.audit_repository.get(self.mission.mission_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.attempt_id, dispatch.audit.attempt_id)
        self.assertEqual(stored.state, "reserved")
        history = self.audit_repository.read_attempt_history(
            self.mission.mission_id
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].audit.state, "reserved")

    def test_dispatch_then_execute_matches_advance(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        result = self.coordinator.execute(dispatch)
        self.assertTrue(result.operation_dispatched)
        self.assertEqual(result.capability_id, "team.approve")
        self.assertEqual(result.audit_state, "succeeded")
        self.assertEqual(result.previous_status, "awaiting_approval")
        self.assertEqual(result.new_status, "approved")
        self.assertEqual(len(self.team_application.approve_calls), 1)
        stored = self.audit_repository.get(self.mission.mission_id)
        assert stored is not None
        self.assertEqual(stored.state, "succeeded")
        self.assertEqual(stored.attempt_id, dispatch.audit.attempt_id)

    def test_second_dispatch_is_blocked_by_reserved_audit(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        self.coordinator.dispatch(self._request(preview.advance_token))
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.dispatch(self._request(preview.advance_token))
        self.assertFalse(self.team_application.approve_calls)

    def test_execute_twice_second_is_refused(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        self.coordinator.execute(dispatch)
        with self.assertRaises(MissionCoordinationError) as caught:
            self.coordinator.execute(dispatch)
        self.assertIn("already succeeded", str(caught.exception))
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_execute_refuses_after_did_not_run_resolution(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        # The operator inspects, finds nothing ran, and clears the audit.
        resolved = self.coordinator.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="did_not_run",
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(resolved.audit_outcome, "cleared")
        with self.assertRaises(MissionCoordinationError) as caught:
            self.coordinator.execute(dispatch)
        self.assertIn("resolved before it executed", str(caught.exception))
        self.assertFalse(self.team_application.approve_calls)

    def test_execute_refuses_when_audit_was_superseded(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        # Simulate an operator "ran" resolution: the audit is completed
        # as succeeded without execute() running.
        completed = replace(
            dispatch.audit,
            state="succeeded",
            finished_at="2026-08-08T12:00:15Z",
            result_status="success",
        )
        self.audit_repository.write(
            completed,
            expected_attempt_id=dispatch.audit.attempt_id,
        )
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.execute(dispatch)
        self.assertFalse(self.team_application.approve_calls)

    def test_uncertain_execute_leaves_audit_uncertain(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        self.team_application.raise_on_approve = True
        with self.assertRaises(MissionDispatchUncertainError):
            self.coordinator.execute(dispatch)
        stored = self.audit_repository.get(self.mission.mission_id)
        assert stored is not None
        self.assertEqual(stored.state, "uncertain")
        self.assertEqual(stored.attempt_id, dispatch.audit.attempt_id)

    def test_execute_requires_a_dispatch(self) -> None:
        with self.assertRaises(TypeError):
            self.coordinator.execute(object())  # type: ignore[arg-type]

    def test_dispatch_record_rejects_unreserved_audit(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        completed = replace(
            dispatch.audit,
            state="succeeded",
            finished_at="2026-08-08T12:00:15Z",
            result_status="success",
        )
        with self.assertRaises(ValueError):
            MissionAdvanceDispatch(
                mission_id=dispatch.mission_id,
                audit=completed,
                preview=dispatch.preview,
                actor="operator",
                correlation_id=dispatch.correlation_id,
                causation_id=dispatch.causation_id,
                previous_status=dispatch.previous_status,
            )

    def test_dispatch_record_rejects_token_mismatch(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        dispatch = self.coordinator.dispatch(self._request(preview.advance_token))
        other_token = replace(
            dispatch.audit,
            advance_token="0" * 64,
        )
        with self.assertRaises(ValueError):
            MissionAdvanceDispatch(
                mission_id=dispatch.mission_id,
                audit=other_token,
                preview=dispatch.preview,
                actor="operator",
                correlation_id=dispatch.correlation_id,
                causation_id=dispatch.causation_id,
                previous_status=dispatch.previous_status,
            )


class LongRunningClassificationTests(unittest.TestCase):
    def test_long_running_capabilities(self) -> None:
        for capability in (
            "team.implement",
            "team.validate",
            "team.documentation_review",
        ):
            self.assertTrue(
                MissionCoordinator.is_long_running(capability),
                capability,
            )

    def test_fast_capabilities_run_inline(self) -> None:
        for capability in ("team.approve", "team.final_review", "unknown.op"):
            self.assertFalse(
                MissionCoordinator.is_long_running(capability),
                capability,
            )


if __name__ == "__main__":
    unittest.main()
