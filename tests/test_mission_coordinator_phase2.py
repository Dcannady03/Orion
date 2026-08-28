from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamImplementationRequest,
    TeamRunRequest,
)
from orion.application.events import (
    EventBus,
    EventFactory,
    EventPublisher,
    EventTypes,
    OrionEvent,
)
from orion.application.missions import (
    MissionAdvanceRequest,
    MissionCoordinationError,
    MissionCoordinationRepository,
    MissionCoordinator,
    MissionDispatchUncertainError,
    MissionStage,
    MissionStalePreviewError,
    MissionStatus,
)
from orion.application.results import ApplicationResult
from tests.test_mission_coordinator import APPROVAL_ID, PLAN_HASH
from tests.test_mission_engine import (
    GOAL_ID,
    NOW,
    PROPOSAL_ID,
    TEAM_TASK_ID,
    MissionFixture,
    event,
)


RUN_ID = "run-mission-phase2-001"
VALIDATION_ID = "validation-0001"
DOCUMENTATION_ID = "documentation-0001"


class Phase2TeamApplication:
    """Persisted-fact fake with the same one-operation application surface."""

    def __init__(self, event_store) -> None:
        self.event_store = event_store
        self.plan_hash = PLAN_HASH
        self.approval_plan_hash = PLAN_HASH
        self.approval_id = APPROVAL_ID
        self.runs: list[dict[str, object]] = []
        self.unresolved_runs: list[dict[str, object]] = []
        self.inspection_available = True
        self.emit_events = True
        self.raise_on_implement = False
        self.implement_calls: list[TeamImplementationRequest] = []
        self.validate_calls: list[TeamRunRequest] = []
        self.documentation_calls: list[TeamRunRequest] = []
        self.inspection_calls = 0

    def coordination_details(self, request):
        self.inspection_calls += 1
        return ApplicationResult.success(
            "read-only lifecycle facts",
            data={
                "team_task_id": request.team_task_id,
                "task_status": "awaiting_approval",
                "plan_sha256": self.plan_hash,
                "approval_inspection_available": self.inspection_available,
                "approvals": [{
                    "team_task_id": request.team_task_id,
                    "approval_id": self.approval_id,
                    "plan_sha256": self.approval_plan_hash,
                    "workspace": "C:/workspace/orion",
                    "approved_at": self._time(10),
                }],
                "run_inspection_available": self.inspection_available,
                "runs": [dict(item) for item in self.runs],
                "unresolved_runs": [dict(item) for item in self.unresolved_runs],
                "read_only": True,
            },
        )

    def implement(self, request: TeamImplementationRequest) -> ApplicationResult:
        self.implement_calls.append(request)
        if self.raise_on_implement:
            raise RuntimeError("uncertain implementation boundary")
        run = {
            "run_id": RUN_ID,
            "team_task_id": request.team_task_id,
            "approval_id": request.approval_id,
            "plan_sha256": self.plan_hash,
            "status": "awaiting_review",
            "stage": "validation",
            "implementation_status": "complete",
            "started_at": self._time(20),
            "completed_at": self._time(21),
            "validation_status": "",
            "validation_id": "",
            "validation_completed_at": "",
            "documentation_review_status": "",
            "documentation_review_id": "",
            "documentation_review_completed_at": "",
            "error": "",
        }
        self.runs = [run]
        if self.emit_events:
            self._append(
                20,
                EventTypes.TEAM_IMPLEMENTATION_STARTED,
                request,
                {
                    "status": "executing",
                    "stage": "implementation",
                    "implementation_status": "executing",
                    "started_at": run["started_at"],
                },
            )
            self._append(
                21,
                EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
                request,
                {
                    "status": "awaiting_review",
                    "stage": "validation",
                    "implementation_status": "complete",
                    "started_at": run["started_at"],
                    "completed_at": run["completed_at"],
                },
            )
        return ApplicationResult.success(
            "implementation complete",
            data={
                "run_id": RUN_ID,
                "team_task_id": request.team_task_id,
                "approval_id": request.approval_id,
                "status": "awaiting_review",
                "stage": "validation",
            },
        )

    def validate(self, request: TeamRunRequest) -> ApplicationResult:
        self.validate_calls.append(request)
        run = self.runs[0]
        run.update({
            "validation_status": "passed",
            "validation_id": VALIDATION_ID,
            "validation_completed_at": self._time(22),
        })
        if self.emit_events:
            self._append(
                22,
                EventTypes.TEAM_VALIDATION_COMPLETED,
                request,
                {
                    "status": "awaiting_review",
                    "stage": "documentation_review",
                    "validation_id": VALIDATION_ID,
                    "validation_status": "passed",
                    "completed_at": run["validation_completed_at"],
                },
            )
        return ApplicationResult.success(
            "validation complete",
            data={
                "run_id": RUN_ID,
                "validation_id": VALIDATION_ID,
                "validation_status": "passed",
                "status": "awaiting_review",
                "stage": "documentation_review",
            },
        )

    def documentation_review(self, request: TeamRunRequest) -> ApplicationResult:
        self.documentation_calls.append(request)
        run = self.runs[0]
        run.update({
            "documentation_review_status": "passed",
            "documentation_review_id": DOCUMENTATION_ID,
            "documentation_review_completed_at": self._time(23),
        })
        if self.emit_events:
            self._append(
                23,
                EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED,
                request,
                {
                    "status": "awaiting_review",
                    "stage": "final_review",
                    "documentation_review_id": DOCUMENTATION_ID,
                    "documentation_review_status": "passed",
                    "completed_at": run["documentation_review_completed_at"],
                },
            )
        return ApplicationResult.success(
            "documentation review complete",
            data={
                "run_id": RUN_ID,
                "documentation_review_id": DOCUMENTATION_ID,
                "documentation_review_status": "passed",
                "status": "awaiting_review",
                "stage": "final_review",
            },
        )

    def append_validation_status(self, status: str) -> None:
        run = self.runs[0]
        run.update({
            "validation_status": status,
            "validation_id": VALIDATION_ID,
            "validation_completed_at": self._time(22),
        })
        request = SimpleNamespace(
            correlation_id=GOAL_ID,
            causation_id=f"event-{21:032x}",
        )
        self._append(
            22,
            EventTypes.TEAM_VALIDATION_COMPLETED,
            request,
            {
                "status": "awaiting_review",
                "stage": "documentation_review",
                "validation_id": VALIDATION_ID,
                "validation_status": status,
                "completed_at": run["validation_completed_at"],
            },
        )

    def append_implementation_failure(self) -> None:
        run = {
            "run_id": RUN_ID,
            "team_task_id": TEAM_TASK_ID,
            "approval_id": APPROVAL_ID,
            "plan_sha256": self.plan_hash,
            "status": "failed",
            "stage": "implementation",
            "implementation_status": "failed",
            "started_at": self._time(20),
            "completed_at": self._time(21),
            "validation_status": "",
            "validation_id": "",
            "validation_completed_at": "",
            "documentation_review_status": "",
            "documentation_review_id": "",
            "documentation_review_completed_at": "",
            "error": "execution_failed",
        }
        self.runs = [run]
        request = SimpleNamespace(
            correlation_id=GOAL_ID,
            causation_id=f"event-{10:032x}",
        )
        self._append(
            20,
            EventTypes.TEAM_IMPLEMENTATION_STARTED,
            request,
            {
                "status": "executing",
                "stage": "implementation",
                "implementation_status": "executing",
                "started_at": run["started_at"],
            },
        )
        self._append(
            21,
            EventTypes.TEAM_IMPLEMENTATION_FAILED,
            request,
            {
                "status": "failed",
                "stage": "implementation",
                "implementation_status": "failed",
                "started_at": run["started_at"],
                "completed_at": run["completed_at"],
                "error_category": run["error"],
            },
        )

    def append_documentation_status(self, status: str) -> None:
        run = self.runs[0]
        run.update({
            "documentation_review_status": status,
            "documentation_review_id": DOCUMENTATION_ID,
            "documentation_review_completed_at": self._time(23),
        })
        request = SimpleNamespace(
            correlation_id=GOAL_ID,
            causation_id=f"event-{22:032x}",
        )
        self._append(
            23,
            EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED,
            request,
            {
                "status": "awaiting_review",
                "stage": "final_review",
                "documentation_review_id": DOCUMENTATION_ID,
                "documentation_review_status": status,
                "completed_at": run["documentation_review_completed_at"],
            },
        )

    def append_final_review(self, *, completed: bool) -> None:
        self.event_store.append(OrionEvent(
            event_id=f"event-{24:032x}",
            event_type=(
                EventTypes.TEAM_FINAL_REVIEW_COMPLETED
                if completed else EventTypes.TEAM_FINAL_REVIEW_BLOCKED
            ),
            occurred_at=self._time(24),
            source="ai_team",
            severity="notice" if completed else "warning",
            correlation_id=GOAL_ID,
            causation_id=f"event-{23:032x}",
            subject_id=RUN_ID,
            data={
                "team_task_id": TEAM_TASK_ID,
                "run_id": RUN_ID,
                "approval_id": APPROVAL_ID,
                "plan_sha256": self.plan_hash,
                "validation_id": VALIDATION_ID,
                "documentation_review_id": DOCUMENTATION_ID,
                "status": "completed" if completed else "blocked",
                "decision": "accepted" if completed else "changes_requested",
            },
        ))

    def _append(self, number, event_type, request, data) -> None:
        self.event_store.append(OrionEvent(
            event_id=f"event-{number:032x}",
            event_type=event_type,
            occurred_at=self._time(number),
            source="ai_team",
            severity="notice",
            correlation_id=request.correlation_id or TEAM_TASK_ID,
            causation_id=request.causation_id,
            subject_id=RUN_ID,
            data={
                "team_task_id": TEAM_TASK_ID,
                "run_id": RUN_ID,
                "approval_id": APPROVAL_ID,
                "plan_sha256": self.plan_hash,
                **data,
            },
        ))

    @staticmethod
    def _time(seconds: int) -> str:
        return (NOW + timedelta(seconds=seconds)).isoformat().replace(
            "+00:00", "Z"
        )


class MissionCoordinatorPhase2Fixture(MissionFixture):
    def setUp(self) -> None:
        super().setUp()
        self.save_proposal()
        self.append_chain()
        self.mission = self.service.create(PROPOSAL_ID).mission
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
        self.mission = self.service.reconcile(self.mission.mission_id).mission
        self.team_application = Phase2TeamApplication(self.events)
        self.audit_repository = MissionCoordinationRepository(
            self.root / "user" / "missions" / "coordination",
            forbidden_root=self.install_root,
        )
        self.attempt_number = 0
        self.coordinator = self._coordinator()

    def _coordinator(self) -> MissionCoordinator:
        def attempt_id():
            self.attempt_number += 1
            return f"mission-attempt-{self.attempt_number:032x}"

        return MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=attempt_id,
        )

    def advance(self):
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(preview.operation.blocked)
        return preview, self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
            actor="operator",
        ))

    def implement(self) -> None:
        preview, result = self.advance()
        self.assertEqual(preview.operation.capability_id, "team.implement")
        self.assertEqual(result.new_status, "awaiting_validation")

    def validate(self) -> None:
        preview, result = self.advance()
        self.assertEqual(preview.operation.capability_id, "team.validate")
        self.assertEqual(result.new_status, "awaiting_documentation")

    def document(self) -> None:
        preview, result = self.advance()
        self.assertEqual(
            preview.operation.capability_id,
            "team.documentation_review",
        )
        self.assertEqual(result.new_status, "awaiting_review")


class MissionCoordinatorPhase2LifecycleTests(MissionCoordinatorPhase2Fixture):
    def test_each_confirmed_advance_dispatches_one_typed_operation_then_stops(self):
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertEqual(preview.operation.capability_id, "team.implement")
        self.assertEqual(preview.operation.resolved_inputs["approval_id"], APPROVAL_ID)
        self.assertFalse(self.team_application.implement_calls)

        self.implement()
        implementation = self.team_application.implement_calls[0]
        self.assertIsInstance(implementation, TeamImplementationRequest)
        self.assertFalse(implementation.run_followups)
        self.assertFalse(self.team_application.validate_calls)
        self.assertFalse(self.team_application.documentation_calls)

        self.validate()
        validation = self.team_application.validate_calls[0]
        self.assertIsInstance(validation, TeamRunRequest)
        self.assertFalse(validation.run_followups)
        self.assertEqual(len(self.team_application.implement_calls), 1)
        self.assertFalse(self.team_application.documentation_calls)

        self.document()
        documentation = self.team_application.documentation_calls[0]
        self.assertFalse(documentation.run_followups)
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(blocked.operation.blocked)
        self.assertIn("no typed completion operation", blocked.operation.blocked_reason)
        self.assertEqual(
            (
                len(self.team_application.implement_calls),
                len(self.team_application.validate_calls),
                len(self.team_application.documentation_calls),
            ),
            (1, 1, 1),
        )
        json.dumps(blocked.to_dict(), allow_nan=False)

    def test_restart_preserves_next_token_when_authoritative_state_is_unchanged(self):
        self.implement()
        first = self.coordinator.preview(self.mission.mission_id)
        restarted = self._coordinator()
        second = restarted.preview(self.mission.mission_id)
        self.assertEqual(first.operation.capability_id, "team.validate")
        self.assertEqual(first.advance_token, second.advance_token)

    def test_phase2_state_change_invalidates_token_before_dispatch(self):
        self.implement()
        preview = self.coordinator.preview(self.mission.mission_id)
        self.team_application.runs[0]["completed_at"] = (
            Phase2TeamApplication._time(99)
        )
        with self.assertRaises(MissionStalePreviewError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertFalse(self.team_application.validate_calls)

    def test_success_without_event_blocks_duplicate_implementation(self):
        self.team_application.emit_events = False
        preview, result = self.advance()
        self.assertEqual(preview.operation.capability_id, "team.implement")
        self.assertEqual(result.new_status, "approved")
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(blocked.operation.blocked)
        self.assertIn("already exists", blocked.operation.blocked_reason)
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertEqual(len(self.team_application.implement_calls), 1)

    def test_uncertain_dispatch_blocks_restart_and_never_replays(self):
        self.team_application.raise_on_implement = True
        preview = self.coordinator.preview(self.mission.mission_id)
        with self.assertRaises(MissionDispatchUncertainError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertEqual(
            self.audit_repository.get(self.mission.mission_id).state,
            "uncertain",
        )
        restarted = self._coordinator()
        blocked = restarted.preview(self.mission.mission_id)
        self.assertIn("outcome is uncertain", blocked.operation.blocked_reason)
        self.assertEqual(len(self.team_application.implement_calls), 1)

    def test_authoritative_mismatch_and_unresolved_run_fail_closed(self):
        self.team_application.plan_hash = "a" * 64
        mismatch = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(mismatch.operation.blocked)
        self.assertIn("cannot be verified", mismatch.operation.blocked_reason)
        self.team_application.plan_hash = PLAN_HASH
        self.team_application.unresolved_runs = [{
            "run_id": "run-unresolved-001",
            "started_at": Phase2TeamApplication._time(19),
            "category": "unresolved_run_record",
        }]
        unresolved = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(unresolved.operation.blocked)
        self.assertIn("unresolved", unresolved.operation.blocked_reason)
        self.assertFalse(self.team_application.implement_calls)

    def test_read_only_preview_inspects_but_never_mutates(self):
        first = self.coordinator.preview(self.mission.mission_id)
        second = self.coordinator.preview(self.mission.mission_id)
        self.assertEqual(first.advance_token, second.advance_token)
        self.assertGreaterEqual(self.team_application.inspection_calls, 2)
        self.assertFalse(self.team_application.implement_calls)
        self.assertFalse(self.team_application.validate_calls)
        self.assertFalse(self.team_application.documentation_calls)


class MissionCoordinatorPhase2ProjectionTests(MissionCoordinatorPhase2Fixture):
    def test_implementation_failure_is_terminal_without_retry(self):
        self.team_application.append_implementation_failure()
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status, MissionStatus.FAILED)
        self.assertEqual(mission.stage, MissionStage.FAILED)
        self.assertIsNotNone(mission.link("team_run"))
        preview = self.coordinator.preview(mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("cannot be advanced", preview.operation.blocked_reason)

    def test_validation_failure_blocks_without_documentation_retry(self):
        self.implement()
        self.team_application.append_validation_status("failed")
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status, MissionStatus.BLOCKED)
        self.assertEqual(mission.stage, MissionStage.VALIDATION)
        preview = self.coordinator.preview(mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("automatic retry", preview.operation.blocked_reason)
        self.assertFalse(self.team_application.documentation_calls)

    def test_documentation_failure_blocks_at_documentation_stage(self):
        self.implement()
        self.validate()
        self.team_application.append_documentation_status("failed")
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status, MissionStatus.BLOCKED)
        self.assertEqual(mission.stage, MissionStage.DOCUMENTATION_REVIEW)

    def test_final_review_events_are_observed_but_not_dispatched(self):
        self.implement()
        self.validate()
        self.document()
        self.team_application.append_final_review(completed=True)
        completed = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(completed.status, MissionStatus.COMPLETED)
        self.assertEqual(completed.stage, MissionStage.COMPLETED)
        self.assertEqual(completed.progress, 100)
        self.assertTrue(completed.completed_at)
        self.assertTrue(
            self.coordinator.preview(completed.mission_id).operation.blocked
        )

    def test_final_review_blocked_event_is_terminal_for_coordinator(self):
        self.implement()
        self.validate()
        self.document()
        self.team_application.append_final_review(completed=False)
        blocked = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(blocked.status, MissionStatus.BLOCKED)
        self.assertEqual(blocked.stage, MissionStage.FINAL_REVIEW)
        preview = self.coordinator.preview(blocked.mission_id)
        self.assertTrue(preview.operation.blocked)

    def test_malformed_or_out_of_order_lifecycle_events_do_not_advance(self):
        malformed = event(
            30,
            EventTypes.GOAL_PROPOSAL_VALIDATED,
        )
        self.events.append(malformed)
        self.events.append(OrionEvent(
            event_id=f"event-{31:032x}",
            event_type=EventTypes.TEAM_VALIDATION_COMPLETED,
            occurred_at=Phase2TeamApplication._time(31),
            source="ai_team",
            severity="notice",
            correlation_id=GOAL_ID,
            subject_id=RUN_ID,
            data={
                "team_task_id": TEAM_TASK_ID,
                "run_id": RUN_ID,
                "approval_id": APPROVAL_ID,
                "plan_sha256": PLAN_HASH,
                "status": "awaiting_review",
                "stage": "documentation_review",
                "validation_id": VALIDATION_ID,
                "validation_status": "passed",
                "completed_at": Phase2TeamApplication._time(31),
            },
        ))
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status, MissionStatus.APPROVED)
        self.assertIsNone(mission.link("validation"))


class AiTeamPhase2EventIntegrationTests(MissionCoordinatorPhase2Fixture):
    def test_existing_handlers_emit_persisted_events_without_followup_cascade(self):
        workspace = SimpleNamespace(
            root=Path(self.root).resolve(),
            mode="standard",
            is_git_repository=False,
            git_root="",
            branch="",
            commit="",
        )
        result = SimpleNamespace(
            summary="Implemented one bounded change.",
            tests=(),
            risks=(),
            remaining_work=(),
            review_notes=(),
        )
        changes = SimpleNamespace(
            changes=(),
            diff_truncated=False,
            by_kind=lambda _kind: (),
        )
        run = SimpleNamespace(
            run_id=RUN_ID,
            team_task_id=TEAM_TASK_ID,
            approval_id=APPROVAL_ID,
            plan_hash=PLAN_HASH,
            workspace_root=str(Path(self.root).resolve()),
            workspace=workspace,
            status="awaiting_review",
            result=result,
            changes=changes,
            validation=None,
            validation_history=(),
            documentation=None,
            documentation_history=(),
            error="",
            started_at=Phase2TeamApplication._time(20),
            completed_at=Phase2TeamApplication._time(21),
        )

        class Bridge:
            workspace_capabilities = workspace

            def __init__(self):
                self.execute_followups = []
                self.validation_followups = []

            @staticmethod
            def execution_context(*_args):
                return object()

            def execute(self, _context, *, run_followups=True):
                self.execute_followups.append(run_followups)
                return run

            @staticmethod
            def run(_run_id):
                return run

            def validate(self, _run_id, *, run_followups=True):
                self.validation_followups.append(run_followups)
                run.validation = SimpleNamespace(
                    attempt_id=VALIDATION_ID,
                    status="passed",
                    tester_requested="codex",
                    tester_resolved="codex",
                    fallback_reason="",
                    checks=(),
                    safe_diagnostics=(),
                    checks_passed=(),
                    warnings=(),
                    checks_failed=(),
                    skipped_checks=(),
                    completed_at=Phase2TeamApplication._time(22),
                    review_status="Awaiting Review — Validation Passed",
                    to_dict=lambda: {
                        "attempt_id": VALIDATION_ID,
                        "status": "passed",
                    },
                )
                run.validation_history = (
                    f"validation/{VALIDATION_ID}.json",
                )
                return run

            @staticmethod
            def document(_run_id):
                run.documentation = SimpleNamespace(
                    attempt_id=DOCUMENTATION_ID,
                    status="passed",
                    reviewer_requested="openai:test",
                    reviewer_resolved="openai:test",
                    fallback_reason="",
                    documents_inspected=(),
                    counts_by_severity={"info": 0, "warning": 0, "error": 0},
                    findings=(),
                    safe_diagnostics=(),
                    completed_at=Phase2TeamApplication._time(23),
                    review_status="Documentation Passed",
                    to_dict=lambda: {
                        "attempt_id": DOCUMENTATION_ID,
                        "status": "passed",
                    },
                )
                run.documentation_history = (
                    f"documentation/{DOCUMENTATION_ID}.json",
                )
                return run

        bridge = Bridge()
        publisher = EventPublisher(EventFactory(), EventBus(self.events))
        runtime = SimpleNamespace(
            codex_bridge=bridge,
            execution_engines=SimpleNamespace(require_codex=lambda: object()),
            event_publisher=publisher,
        )
        handler = AiTeamApplicationHandler(runtime)

        implemented = handler.implement(TeamImplementationRequest(
            TEAM_TASK_ID,
            APPROVAL_ID,
            run_followups=False,
            correlation_id=GOAL_ID,
            causation_id=self.mission.last_event_id,
        ))
        self.assertTrue(implemented.ok)
        self.assertEqual(bridge.execute_followups, [False])
        self.assertIsNone(run.validation)

        validated = handler.validate(TeamRunRequest(
            RUN_ID,
            run_followups=False,
            correlation_id=GOAL_ID,
        ))
        self.assertTrue(validated.ok)
        self.assertEqual(bridge.validation_followups, [False])
        self.assertIsNone(run.documentation)

        documented = handler.documentation_review(TeamRunRequest(
            RUN_ID,
            run_followups=False,
            correlation_id=GOAL_ID,
        ))
        self.assertTrue(documented.ok)
        event_types = {
            item.event_type
            for item in self.events.history(
                correlation_id=GOAL_ID,
                limit=100,
            ).events
        }
        self.assertTrue({
            EventTypes.TEAM_IMPLEMENTATION_STARTED,
            EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
            EventTypes.TEAM_VALIDATION_COMPLETED,
            EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED,
        }.issubset(event_types))


if __name__ == "__main__":
    unittest.main()
