"""Mission Coordinator Phase 3, Phase B: uncertainty-resolution workflow."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace

from orion.application.commands.mission_cli import MissionCliAdapter
from orion.application.events import EventTypes, OrionEvent
from orion.application.missions import (
    MissionAdvanceAudit,
    MissionAdvanceRequest,
    MissionApplicationHandler,
    MissionCoordinationError,
    MissionCoordinationRepository,
    MissionCoordinator,
    MissionLockInfo,
    MissionReferenceRequest,
    MissionResolvePreview,
    MissionResolveRequest,
    MissionResolveResult,
    MissionStatus,
)
from orion.application.observability.recovery import RecoveryHistoryStore
from orion.application.results import ApplicationResult
from tests.test_mission_coordinator import (
    APPROVAL_ID,
    PLAN_HASH,
    MissionCoordinatorFixture,
    dead_pid,
)
from tests.test_mission_coordinator_phase2 import (
    DOCUMENTATION_ID,
    FINAL_REVIEW_ID,
    RUN_ID,
    VALIDATION_ID,
    MissionCoordinatorPhase2Fixture,
    Phase2TeamApplication,
)
from tests.test_mission_engine import GOAL_ID, TEAM_TASK_ID


MISSION_ID = "mission-" + "d" * 32
STUCK_ATTEMPT_ID = "mission-attempt-" + "b" * 32
STUCK_TOKEN = "b" * 64


class MissionCoordinatorResolveFixture(MissionCoordinatorPhase2Fixture):
    """Approved mission with a recovery recorder wired into the coordinator."""

    def _coordinator(self) -> MissionCoordinator:
        if not hasattr(self, "recovery_store"):
            self.recovery_store = RecoveryHistoryStore(
                self.root / "user" / "observability"
            )

        def attempt_id():
            self.attempt_number += 1
            return f"mission-attempt-{self.attempt_number:032x}"

        return MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=attempt_id,
            recovery_recorder=self.recovery_store.record,
        )

    def _write_stuck_audit(
        self,
        capability: str,
        subject: str,
        *,
        state: str = "reserved",
        token: str = STUCK_TOKEN,
        attempt_id: str = STUCK_ATTEMPT_ID,
    ) -> MissionAdvanceAudit:
        current = self.audit_repository.get(self.mission.mission_id)
        audit = MissionAdvanceAudit(
            schema_version=1,
            mission_id=self.mission.mission_id,
            attempt_id=attempt_id,
            advance_token=token,
            capability_id=capability,
            subject_id=subject,
            actor="operator",
            state=state,
            started_at=Phase2TeamApplication._time(15),
            finished_at=(
                Phase2TeamApplication._time(16) if state != "reserved" else ""
            ),
            result_status="failure" if state == "failed" else "",
        )
        self.audit_repository.write(
            audit,
            expected_attempt_id=current.attempt_id if current else None,
        )
        return audit

    def _write_lock(self, pid: int, started_at: str | None = None) -> Path:
        path = (
            self.audit_repository.root
            / f".{self.mission.mission_id}.advance.lock"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "mission_id": self.mission.mission_id,
                "pid": pid,
                "started_at": started_at or Phase2TeamApplication._time(15),
            }),
            encoding="utf-8",
        )
        return path

    def _lock_path(self) -> Path:
        return (
            self.audit_repository.root
            / f".{self.mission.mission_id}.advance.lock"
        )

    def _recovery_records(self):
        return self.recovery_store.query(kind="mission-resolve").records

    def _adapter(self, answers):
        output: list[str] = []
        queue = list(answers)

        def scripted(_prompt):
            return queue.pop(0)

        application = MissionApplicationHandler(self.service, self.coordinator)
        runtime = SimpleNamespace(mission_application=application)
        adapter = MissionCliAdapter(
            runtime, output_provider=output.append, input_provider=scripted
        )
        return adapter, output

    def _resolve(self, finding: str, *, coordinator=None):
        target = coordinator or self.coordinator
        return target.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding=finding,
            confirmed=True,
            actor="operator",
        ))


class ResolvePreviewTests(MissionCoordinatorResolveFixture):
    def test_resolve_preview_is_read_only(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.resolvable)
        self.assertTrue(preview.audit_blocked)
        self.assertFalse(preview.lock_blocked)
        self.assertEqual(preview.audit.capability_id, "team.implement")
        self.assertEqual(preview.audit.state, "reserved")
        self.assertEqual(preview.suggested_finding, "did_not_run")
        self.assertFalse(preview.downstream_observed)
        self.assertFalse(self.team_application.implement_calls)
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")
        self.assertEqual(len(self._recovery_records()), 0)

    def test_resolve_preview_on_clean_mission_reports_nothing_to_resolve(self) -> None:
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertIn("No uncertain", preview.blocked_reason)
        self.assertFalse(preview.audit_blocked)
        self.assertFalse(preview.lock_blocked)

    def test_resolve_preview_reports_downstream_unknown_when_inspection_fails(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        self.team_application.inspection_available = False
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertTrue(preview.downstream_unknown)
        self.assertIn("could not be inspected", preview.blocked_reason)

    def test_failed_audit_is_not_uncertain(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID, state="failed")
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertFalse(preview.audit_blocked)
        self.assertIn("No uncertain", preview.blocked_reason)
        with self.assertRaises(MissionCoordinationError):
            self._resolve("did_not_run")

    def test_terminal_mission_is_not_resolvable(self) -> None:
        self.team_application.append_implementation_failure()
        self.service.reconcile(self.mission.mission_id)
        mission = self.service.get(self.mission.mission_id)
        self.assertEqual(mission.status, MissionStatus.FAILED)
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertIn("terminal", preview.blocked_reason)
        with self.assertRaises(MissionCoordinationError):
            self._resolve("ran")


class ResolveRanTests(MissionCoordinatorResolveFixture):
    def test_resolve_ran_completes_audit_and_never_redispatches(self) -> None:
        self.implement()
        prior = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(prior.state, "succeeded")
        # Simulate the crash: the terminal audit write never happened.
        self._write_stuck_audit(
            "team.implement",
            TEAM_TASK_ID,
            attempt_id=prior.attempt_id,
            token=prior.advance_token,
        )
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, RUN_ID)
        self.assertEqual(preview.suggested_finding, "ran")
        result = self._resolve("ran")
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertEqual(result.downstream_reference, RUN_ID)
        self.assertFalse(result.lock_cleared)
        self.assertTrue(result.recovery_record_id)
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "succeeded")
        self.assertEqual(audit.attempt_id, prior.attempt_id)
        self.assertEqual(audit.downstream_reference, RUN_ID)
        # The implementation was never dispatched a second time.
        self.assertEqual(len(self.team_application.implement_calls), 1)
        # The next advance proceeds from the new state with a new token.
        following = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(following.operation.blocked)
        self.assertEqual(following.operation.capability_id, "team.validate")
        self.assertNotEqual(following.advance_token, prior.advance_token)
        _preview, advanced = self.advance()
        self.assertEqual(advanced.capability_id, "team.validate")
        self.assertEqual(len(self.team_application.validate_calls), 1)

    def test_resolve_ran_refused_when_downstream_not_observed(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        with self.assertRaisesRegex(MissionCoordinationError, "not observable"):
            self._resolve("ran")
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")
        self.assertFalse(self.team_application.implement_calls)
        self.assertEqual(len(self._recovery_records()), 0)

    def test_resolve_did_not_run_refused_when_downstream_observed(self) -> None:
        self.implement()
        prior = self.audit_repository.get(self.mission.mission_id)
        self._write_stuck_audit(
            "team.implement",
            TEAM_TASK_ID,
            attempt_id=prior.attempt_id,
            token=prior.advance_token,
        )
        with self.assertRaisesRegex(MissionCoordinationError, "shows the operation ran"):
            self._resolve("did_not_run")
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_resolve_validate_ran_from_team_state_only(self) -> None:
        self.implement()
        run = self.team_application.runs[0]
        run.update({
            "validation_status": "passed",
            "validation_id": VALIDATION_ID,
            "validation_completed_at": Phase2TeamApplication._time(22),
        })
        # No validation event was published, so the Mission has no link;
        # the Team run record is the only evidence the operation ran.
        self._write_stuck_audit("team.validate", RUN_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, VALIDATION_ID)
        result = self._resolve("ran")
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertEqual(result.downstream_reference, VALIDATION_ID)
        self.assertFalse(self.team_application.validate_calls)
        # The Mission still cannot advance: the projection never observed
        # the validation, and the downstream check says so plainly.
        following = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(following.operation.blocked)
        self.assertIn("already exists", following.operation.blocked_reason)

    def test_resolve_documentation_ran_from_team_state_only(self) -> None:
        self.implement()
        self.validate()
        run = self.team_application.runs[0]
        run.update({
            "documentation_review_status": "passed",
            "documentation_review_id": DOCUMENTATION_ID,
            "documentation_review_completed_at": (
                Phase2TeamApplication._time(23)
            ),
        })
        self._write_stuck_audit("team.documentation_review", RUN_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, DOCUMENTATION_ID)
        result = self._resolve("ran")
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertFalse(self.team_application.documentation_calls)

    def test_resolve_final_review_ran_from_team_state_only(self) -> None:
        self.implement()
        self.validate()
        self.document()
        run = self.team_application.runs[0]
        run.update({
            "final_review_status": "decided",
            "final_review_id": FINAL_REVIEW_ID,
            "final_review_decision": "accepted",
            "final_review_completed_at": Phase2TeamApplication._time(24),
        })
        self._write_stuck_audit("team.final_review", RUN_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, FINAL_REVIEW_ID)
        result = self._resolve("ran")
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertEqual(result.downstream_reference, FINAL_REVIEW_ID)


class ResolveDidNotRunTests(MissionCoordinatorResolveFixture):
    def test_resolve_did_not_run_clears_audit_and_reattempts_once(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        result = self._resolve("did_not_run")
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertEqual(result.finding, "did_not_run")
        self.assertTrue(result.recovery_record_id)
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(preview.operation.blocked)
        self.assertEqual(preview.operation.capability_id, "team.implement")
        _preview, advanced = self.advance()
        self.assertEqual(advanced.capability_id, "team.implement")
        self.assertEqual(advanced.new_status, "awaiting_validation")
        self.assertEqual(len(self.team_application.implement_calls), 1)
        # The re-attempted operation is audited like any other advance.
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "succeeded")
        self.assertNotEqual(audit.attempt_id, STUCK_ATTEMPT_ID)

    def test_uncertain_dispatch_resolves_as_did_not_run(self) -> None:
        self.team_application.raise_on_implement = True
        preview = self.coordinator.preview(self.mission.mission_id)
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
                actor="operator",
            ))
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "uncertain")
        self.team_application.raise_on_implement = False
        result = self._resolve("did_not_run")
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        calls_before = len(self.team_application.implement_calls)
        _preview, advanced = self.advance()
        self.assertEqual(advanced.new_status, "awaiting_validation")
        # The re-attempt dispatches exactly once (the uncertain attempt
        # before it is the earlier call).
        self.assertEqual(
            len(self.team_application.implement_calls), calls_before + 1
        )

    def test_resolve_cannot_determine_refuses_and_changes_nothing(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        with self.assertRaisesRegex(
            MissionCoordinationError, "cannot be determined"
        ):
            self.coordinator.resolve(MissionResolveRequest(
                self.mission.mission_id,
                finding="cannot_determine",
                confirmed=True,
                actor="operator",
            ))
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")
        self.assertEqual(len(self._recovery_records()), 0)

    def test_resolve_without_confirmation_refuses(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        with self.assertRaisesRegex(
            MissionCoordinationError, "explicit confirmation"
        ):
            self.coordinator.resolve(MissionResolveRequest(
                self.mission.mission_id,
                finding="did_not_run",
                confirmed=False,
                actor="operator",
            ))
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_resolve_without_finding_refuses(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        with self.assertRaisesRegex(MissionCoordinationError, "state whether"):
            self.coordinator.resolve(MissionResolveRequest(
                self.mission.mission_id,
                confirmed=True,
                actor="operator",
            ))
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_concurrent_resolve_attempts_one_wins(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        first = self._coordinator()
        second = self._coordinator()
        barrier = threading.Barrier(2)
        results: list = []
        errors: list = []

        def attempt(coordinator):
            barrier.wait(timeout=5)
            try:
                results.append(self._resolve("did_not_run", coordinator=coordinator))
            except (MissionCoordinationError, PermissionError) as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(attempt, (first, second)))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        self.assertEqual(len(self._recovery_records()), 1)

    def test_second_resolve_after_success_finds_nothing_to_resolve(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        self._resolve("did_not_run")
        with self.assertRaisesRegex(
            MissionCoordinationError, "No uncertain"
        ):
            self._resolve("did_not_run")
        self.assertEqual(len(self._recovery_records()), 1)


class ResolveRecordingTests(MissionCoordinatorResolveFixture):
    def test_resolution_is_recorded_in_recovery_history(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        result = self._resolve("did_not_run")
        records = self._recovery_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.record_id, result.recovery_record_id)
        self.assertEqual(record.kind, "mission-resolve")
        self.assertEqual(record.target, self.mission.mission_id)
        self.assertEqual(record.status, "success")
        self.assertIn("finding=did_not_run", record.detail)
        self.assertIn(f"attempt={STUCK_ATTEMPT_ID}", record.detail)
        self.assertIn("capability=team.implement", record.detail)
        self.assertIn("actor=operator", record.detail)
        self.assertIn("audit=cleared", record.detail)

    def test_resolution_without_recorder_warns_but_succeeds(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        bare = MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=lambda: f"mission-attempt-{'c' * 32}",
        )
        result = self._resolve("did_not_run", coordinator=bare)
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertEqual(result.recovery_record_id, "")
        self.assertTrue(any(
            "Recovery history is unavailable" in item
            for item in result.warnings
        ))

    def test_recording_failure_warns_but_does_not_break_resolution(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)

        def failing_recorder(*_args, **_kwargs):
            raise OSError("disk full")

        coordinator = MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=lambda: f"mission-attempt-{'c' * 32}",
            recovery_recorder=failing_recorder,
        )
        result = self._resolve("did_not_run", coordinator=coordinator)
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertTrue(any(
            "could not be recorded" in item for item in result.warnings
        ))


class ResolveLockTests(MissionCoordinatorResolveFixture):
    def test_stale_lock_clears_through_resolution_only(self) -> None:
        pid = dead_pid()
        self._write_lock(pid)
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(blocked.operation.blocked)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.resolvable)
        self.assertTrue(preview.lock_blocked)
        self.assertFalse(preview.audit_blocked)
        self.assertIs(preview.lock_holder_alive, False)
        self.assertEqual(preview.lock.pid, pid)
        result = self._resolve("")
        self.assertTrue(result.lock_cleared)
        self.assertEqual(result.audit_outcome, "none")
        self.assertEqual(result.finding, "")
        self.assertFalse(self._lock_path().exists())
        records = self._recovery_records()
        self.assertEqual(len(records), 1)
        self.assertIn("finding=lock-only", records[0].detail)
        unblocked = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(unblocked.operation.blocked)

    def test_stale_lock_and_stuck_audit_resolve_together(self) -> None:
        self._write_lock(dead_pid())
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.resolvable)
        self.assertTrue(preview.audit_blocked)
        self.assertTrue(preview.lock_blocked)
        result = self._resolve("did_not_run")
        self.assertTrue(result.lock_cleared)
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertFalse(self._lock_path().exists())
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))

    def test_live_lock_holder_blocks_resolution(self) -> None:
        self._write_lock(os.getpid())
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertIs(preview.lock_holder_alive, True)
        self.assertIn("live process", preview.blocked_reason)
        with self.assertRaisesRegex(MissionCoordinationError, "live process"):
            self._resolve("did_not_run")
        self.assertTrue(self._lock_path().exists())
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_unknown_lock_owner_blocks_resolution(self) -> None:
        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale", encoding="utf-8")
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.resolvable)
        self.assertIsNone(preview.lock_holder_alive)
        self.assertIn("manually", preview.blocked_reason)
        with self.assertRaisesRegex(MissionCoordinationError, "manually"):
            self._resolve("did_not_run")
        self.assertTrue(path.exists())

    def test_finding_is_refused_for_lock_only_resolution(self) -> None:
        self._write_lock(dead_pid())
        with self.assertRaisesRegex(MissionCoordinationError, "No stuck audit"):
            self._resolve("ran")
        self.assertTrue(self._lock_path().exists())


class ResolveApproveTests(MissionCoordinatorFixture):
    """Approve-stage resolution through the approval-details fallback."""

    def setUp(self) -> None:
        super().setUp()
        self.recovery_store = RecoveryHistoryStore(
            self.root / "user" / "observability"
        )
        self.coordinator = MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=lambda: f"mission-attempt-{'e' * 32}",
            recovery_recorder=self.recovery_store.record,
        )
        self.application = MissionApplicationHandler(self.service, self.coordinator)

    def _write_stuck_audit(self, capability: str, subject: str) -> None:
        self.audit_repository.write(
            MissionAdvanceAudit(
                schema_version=1,
                mission_id=self.mission.mission_id,
                attempt_id=STUCK_ATTEMPT_ID,
                advance_token=STUCK_TOKEN,
                capability_id=capability,
                subject_id=subject,
                actor="operator",
                state="reserved",
                started_at=Phase2TeamApplication._time(15),
            ),
            expected_attempt_id=None,
        )

    def test_resolve_approve_ran_from_team_state(self) -> None:
        self.team_application.existing_approval_count = 1
        self._write_stuck_audit("team.approve", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, APPROVAL_ID)
        self.assertEqual(preview.suggested_finding, "ran")
        result = self.coordinator.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="ran",
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertEqual(result.downstream_reference, APPROVAL_ID)
        self.assertFalse(self.team_application.approve_calls)
        # The Mission still cannot advance until the approval event is
        # observed; the block is now the downstream check, not the audit.
        following = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(following.operation.blocked)
        self.assertIn("already exists", following.operation.blocked_reason)
        # Once the event arrives, the Mission moves on.
        self.events.append(OrionEvent(
            event_id=f"event-{10:032x}",
            event_type=EventTypes.TEAM_PLAN_APPROVED,
            occurred_at=Phase2TeamApplication._time(10),
            source="ai_team",
            severity="notice",
            correlation_id=GOAL_ID,
            causation_id=self.mission.last_event_id,
            subject_id=TEAM_TASK_ID,
            data={
                "team_task_id": TEAM_TASK_ID,
                "approval_id": APPROVAL_ID,
                "status": "approved",
                "approval_required": True,
                "approval_status": "approved",
                "plan_sha256": PLAN_HASH,
            },
        ))
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status, MissionStatus.APPROVED)

    def test_resolve_approve_did_not_run_clears_and_reattempts(self) -> None:
        self._write_stuck_audit("team.approve", TEAM_TASK_ID)
        preview = self.coordinator.resolve_preview(self.mission.mission_id)
        self.assertFalse(preview.downstream_observed)
        self.assertEqual(preview.suggested_finding, "did_not_run")
        result = self.coordinator.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="did_not_run",
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(result.audit_outcome, "cleared")
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        following = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(following.operation.blocked)
        self.assertEqual(following.operation.capability_id, "team.approve")
        advanced = self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            following.advance_token,
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(advanced.new_status, "approved")
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_resolve_approve_ran_via_coordination_details(self) -> None:
        """The general inspection path covers approve when it is available."""
        details = ApplicationResult.success(
            "read-only lifecycle facts",
            data={
                "team_task_id": TEAM_TASK_ID,
                "task_status": "awaiting_approval",
                "plan_sha256": PLAN_HASH,
                "approval_inspection_available": True,
                "approvals": [{
                    "team_task_id": TEAM_TASK_ID,
                    "approval_id": APPROVAL_ID,
                    "plan_sha256": PLAN_HASH,
                }],
                "run_inspection_available": True,
                "runs": [],
                "unresolved_runs": [],
                "read_only": True,
            },
        )

        class DetailsOnlyTeam:
            @staticmethod
            def coordination_details(_request):
                return details

        coordinator = MissionCoordinator(
            self.service,
            DetailsOnlyTeam(),
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=lambda: f"mission-attempt-{'c' * 32}",
        )
        self._write_stuck_audit("team.approve", TEAM_TASK_ID)
        preview = coordinator.resolve_preview(self.mission.mission_id)
        self.assertTrue(preview.downstream_observed)
        self.assertEqual(preview.downstream_reference, APPROVAL_ID)
        result = coordinator.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="ran",
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertEqual(result.downstream_reference, APPROVAL_ID)


class ResolveHandlerTests(MissionCoordinatorResolveFixture):
    def test_resolve_preview_handler_renders_inspection(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        self._write_lock(dead_pid())
        application = MissionApplicationHandler(self.service, self.coordinator)
        result = application.resolve_preview(
            MissionReferenceRequest(self.mission.mission_id)
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.data["resolvable"])
        self.assertEqual(result.data["command"], "resolve_preview")
        self.assertIn("Stuck audit", result.message)
        self.assertIn(STUCK_ATTEMPT_ID, result.message)
        self.assertIn("team.implement", result.message)
        self.assertIn("Coordination lock", result.message)
        self.assertIn("Downstream evidence", result.message)
        self.assertIn("did not run", result.message)

    def test_resolve_preview_handler_reports_clean_mission(self) -> None:
        application = MissionApplicationHandler(self.service, self.coordinator)
        result = application.resolve_preview(
            MissionReferenceRequest(self.mission.mission_id)
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolvable"])
        self.assertIn("No uncertain", result.message)

    def test_resolve_handler_reports_result(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        application = MissionApplicationHandler(self.service, self.coordinator)
        result = application.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="did_not_run",
            confirmed=True,
            actor="operator",
        ))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["command"], "resolve")
        self.assertEqual(result.data["audit_outcome"], "cleared")
        self.assertFalse(result.data["operation_dispatched"])
        self.assertIn("No Team operation was dispatched", result.message)

    def test_resolve_handler_without_coordinator_fails(self) -> None:
        application = MissionApplicationHandler(self.service)
        result = application.resolve(MissionResolveRequest(
            self.mission.mission_id,
            finding="did_not_run",
            confirmed=True,
        ))
        self.assertFalse(result.ok)
        self.assertIn("Coordinator is not available", result.message)


class ResolveCliTests(MissionCoordinatorResolveFixture):
    def test_resolve_cli_ran_flow(self) -> None:
        self.implement()
        prior = self.audit_repository.get(self.mission.mission_id)
        self._write_stuck_audit(
            "team.implement",
            TEAM_TASK_ID,
            attempt_id=prior.attempt_id,
            token=prior.advance_token,
        )
        adapter, output = self._adapter(["r", "y"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["audit_outcome"], "succeeded")
        self.assertEqual(len(self.team_application.implement_calls), 1)
        rendered = "\n".join(output)
        self.assertIn("[R]an / [D]id not run", rendered)
        self.assertIn("will NOT be re-dispatched", rendered)

    def test_resolve_cli_did_not_run_flow(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        adapter, output = self._adapter(["d", "y"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["audit_outcome"], "cleared")
        self.assertIsNone(self.audit_repository.get(self.mission.mission_id))
        self.assertIn("attempted again", "\n".join(output))

    def test_resolve_cli_cannot_determine_dispatches_nothing(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        adapter, output = self._adapter(["c"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolution_applied"])
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")
        self.assertIn("Cannot determine", "\n".join(output))

    def test_resolve_cli_finding_decline_dispatches_nothing(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        adapter, _output = self._adapter(["n"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolution_applied"])
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_resolve_cli_confirmation_decline_dispatches_nothing(self) -> None:
        self._write_stuck_audit("team.implement", TEAM_TASK_ID)
        adapter, _output = self._adapter(["d", "n"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolution_applied"])
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "reserved")

    def test_resolve_cli_lock_only_flow(self) -> None:
        self._write_lock(dead_pid())
        adapter, _output = self._adapter(["y"])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["lock_cleared"])
        self.assertFalse(self._lock_path().exists())

    def test_resolve_cli_nothing_to_resolve_asks_nothing(self) -> None:
        adapter, _output = self._adapter([])
        result = adapter.handle(f"resolve {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolvable"])

    def test_resolve_cli_rejects_bad_usage(self) -> None:
        adapter, _output = self._adapter([])
        result = adapter.handle("resolve")
        self.assertFalse(result.ok)
        self.assertIn("Usage: mission resolve", result.message)


class ResolveModelTests(unittest.TestCase):
    def _audit(self):
        return MissionAdvanceAudit(
            schema_version=1,
            mission_id=MISSION_ID,
            attempt_id=STUCK_ATTEMPT_ID,
            advance_token=STUCK_TOKEN,
            capability_id="team.implement",
            subject_id=TEAM_TASK_ID,
            actor="operator",
            state="reserved",
            started_at="2026-08-08T12:00:15Z",
        )

    def _preview(self, **overrides):
        values = {
            "mission_id": MISSION_ID,
            "status": "approved",
            "stage": "implementation",
            "progress": 35,
            "mission_updated_at": "2026-08-08T12:00:00Z",
            "last_event_id": "",
            "resolvable": True,
        }
        values.update(overrides)
        return MissionResolvePreview(**values)

    def test_preview_validation(self) -> None:
        preview = self._preview(audit=self._audit(), audit_blocked=True)
        self.assertTrue(preview.resolvable)
        with self.assertRaisesRegex(ValueError, "blocked reason"):
            self._preview(resolvable=False)
        with self.assertRaisesRegex(ValueError, "blocked reason"):
            self._preview(resolvable=True, blocked_reason="nope")
        with self.assertRaisesRegex(ValueError, "suggested finding"):
            self._preview(suggested_finding="maybe")
        with self.assertRaisesRegex(ValueError, "requires an audit"):
            self._preview(audit_blocked=True)
        with self.assertRaises(FrozenInstanceError):
            preview.resolvable = False
        json.dumps(preview.to_dict(), allow_nan=False)

    def test_request_and_result_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "finding"):
            MissionResolveRequest(MISSION_ID, finding="maybe")
        request = MissionResolveRequest(
            MISSION_ID, finding="ran", confirmed=True
        )
        self.assertEqual(request.finding, "ran")
        with self.assertRaisesRegex(ValueError, "audit outcome"):
            MissionResolveResult(
                mission_id=MISSION_ID,
                finding="ran",
                audit_outcome="maybe",
                lock_cleared=False,
                downstream_reference="",
                previous_status="approved",
                new_status="approved",
                new_stage="implementation",
                new_progress=35,
                reconciled=False,
                next_action="",
            )
        result = MissionResolveResult(
            mission_id=MISSION_ID,
            finding="did_not_run",
            audit_outcome="cleared",
            lock_cleared=False,
            downstream_reference="",
            previous_status="approved",
            new_status="approved",
            new_stage="implementation",
            new_progress=35,
            reconciled=False,
            next_action="",
        )
        self.assertFalse(result.to_dict()["operation_dispatched"])
        json.dumps(result.to_dict(), allow_nan=False)

    def test_lock_info_known_flag(self) -> None:
        known = MissionLockInfo(MISSION_ID, pid=1234, started_at="2026-08-08T12:00:15Z")
        self.assertTrue(known.known)
        unknown = MissionLockInfo(MISSION_ID)
        self.assertFalse(unknown.known)
        self.assertEqual(unknown.pid, 0)
        with self.assertRaises(TypeError):
            MissionLockInfo(MISSION_ID, pid=-1)


class ResolveRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = MissionCoordinationRepository(
            self.root / "coordination",
            lock_timeout_seconds=0.2,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _audit(self):
        return MissionAdvanceAudit(
            schema_version=1,
            mission_id=MISSION_ID,
            attempt_id=STUCK_ATTEMPT_ID,
            advance_token=STUCK_TOKEN,
            capability_id="team.implement",
            subject_id=TEAM_TASK_ID,
            actor="operator",
            state="reserved",
            started_at="2026-08-08T12:00:15Z",
        )

    def test_advance_lock_records_pid_and_timestamp(self) -> None:
        with self.repository.advance_lock(MISSION_ID):
            info = self.repository.lock_info(MISSION_ID)
            self.assertIsNotNone(info)
            self.assertEqual(info.pid, os.getpid())
            self.assertTrue(info.started_at)
            self.assertTrue(info.known)
            self.assertTrue(self.repository.lock_exists(MISSION_ID))
            self.assertTrue(
                MissionCoordinationRepository.lock_holder_alive(info)
            )
        self.assertIsNone(self.repository.lock_info(MISSION_ID))
        self.assertFalse(self.repository.lock_exists(MISSION_ID))

    def test_lock_info_unknown_for_malformed_content(self) -> None:
        root = self.root / "coordination"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f".{MISSION_ID}.advance.lock"
        path.write_text("stale", encoding="utf-8")
        info = self.repository.lock_info(MISSION_ID)
        self.assertIsNotNone(info)
        self.assertEqual(info.pid, 0)
        self.assertFalse(info.known)
        self.assertIsNone(
            MissionCoordinationRepository.lock_holder_alive(info)
        )
        path.write_text(
            json.dumps({"pid": "not-a-pid", "started_at": "never"}),
            encoding="utf-8",
        )
        self.assertFalse(self.repository.lock_info(MISSION_ID).known)

    def test_lock_info_none_when_absent(self) -> None:
        self.assertIsNone(self.repository.lock_info(MISSION_ID))

    def test_clear_lock_refuses_changed_content(self) -> None:
        root = self.root / "coordination"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f".{MISSION_ID}.advance.lock"
        path.write_text(
            json.dumps({
                "mission_id": MISSION_ID,
                "pid": 1111,
                "started_at": "2026-08-08T12:00:15Z",
            }),
            encoding="utf-8",
        )
        observed = self.repository.lock_info(MISSION_ID)
        path.write_text(
            json.dumps({
                "mission_id": MISSION_ID,
                "pid": 2222,
                "started_at": "2026-08-08T12:01:15Z",
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PermissionError, "changed before clear"):
            self.repository.clear_lock(MISSION_ID, expected=observed)
        self.assertTrue(path.exists())

    def test_clear_lock_returns_false_when_already_gone(self) -> None:
        observed = MissionLockInfo(
            MISSION_ID, pid=1111, started_at="2026-08-08T12:00:15Z"
        )
        self.assertFalse(
            self.repository.clear_lock(MISSION_ID, expected=observed)
        )

    def test_clear_requires_matching_attempt(self) -> None:
        audit = self._audit()
        self.repository.write(audit, expected_attempt_id=None)
        with self.assertRaisesRegex(PermissionError, "changed before clear"):
            self.repository.clear(
                MISSION_ID,
                expected_attempt_id="mission-attempt-" + "0" * 32,
            )
        self.assertIsNotNone(self.repository.get(MISSION_ID))
        cleared = self.repository.clear(
            MISSION_ID, expected_attempt_id=STUCK_ATTEMPT_ID
        )
        self.assertEqual(cleared.attempt_id, STUCK_ATTEMPT_ID)
        self.assertIsNone(self.repository.get(MISSION_ID))

    def test_clear_returns_none_when_absent(self) -> None:
        self.assertIsNone(
            self.repository.clear(MISSION_ID, expected_attempt_id=None)
        )
        with self.assertRaisesRegex(PermissionError, "changed before clear"):
            self.repository.clear(
                MISSION_ID, expected_attempt_id=STUCK_ATTEMPT_ID
            )

    def test_lock_holder_alive_reports_dead_pid(self) -> None:
        pid = dead_pid()
        info = MissionLockInfo(
            MISSION_ID, pid=pid, started_at="2026-08-08T12:00:15Z"
        )
        self.assertIs(
            MissionCoordinationRepository.lock_holder_alive(info), False
        )
        alive = MissionLockInfo(
            MISSION_ID,
            pid=os.getpid(),
            started_at="2026-08-08T12:00:15Z",
        )
        self.assertIs(
            MissionCoordinationRepository.lock_holder_alive(alive), True
        )
        unknown = MissionLockInfo(MISSION_ID)
        self.assertIsNone(
            MissionCoordinationRepository.lock_holder_alive(unknown)
        )


if __name__ == "__main__":
    unittest.main()
