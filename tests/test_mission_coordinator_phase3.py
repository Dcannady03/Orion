"""Mission Coordinator Phase 3: typed human final-review decision boundary."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import json
import tempfile
import unittest

from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamFinalReviewRequest,
)
from orion.application.commands.mission_cli import MissionCliAdapter
from orion.application.events import (
    EventBus,
    EventFactory,
    EventPublisher,
    EventTypes,
)
from orion.application.missions import (
    MissionAdvanceRequest,
    MissionApplicationHandler,
    MissionCoordinationError,
    MissionNextOperation,
    MissionStalePreviewError,
    MissionStatus,
)
from orion.application.missions.translator import (
    MissionOperationTranslationError,
    MissionOperationTranslator,
)
from orion.application.results import ApplicationResult
from orion.services.codex_bridge import CodexBridgeStore
from orion.services.team_documentation import DocumentationAttempt
from orion.services.team_final_review import FinalReviewDecision
from orion.services.team_validation import ValidationAttempt
from tests.test_mission_coordinator import APPROVAL_ID, PLAN_HASH
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


def _decision_dict(**overrides):
    value = {
        "schema_version": 1,
        "decision_id": FINAL_REVIEW_ID,
        "run_id": RUN_ID,
        "team_task_id": TEAM_TASK_ID,
        "approval_id": APPROVAL_ID,
        "plan_sha256": PLAN_HASH,
        "decision": "accepted",
        "note": "",
        "actor": "user",
        "completed_at": Phase2TeamApplication._time(24),
        "artifact_paths": [f"final-review/{FINAL_REVIEW_ID}.json"],
    }
    value.update(overrides)
    return value


class FinalReviewDecisionTests(unittest.TestCase):
    def test_decision_record_is_immutable_json_safe_and_schema_versioned(self):
        decision = FinalReviewDecision.from_value(_decision_dict())
        self.assertEqual(decision.decision_id, FINAL_REVIEW_ID)
        self.assertEqual(decision.outcome, "completed")
        self.assertEqual(
            json.loads(json.dumps(decision.to_dict()))["decision_id"],
            FINAL_REVIEW_ID,
        )
        rejected = FinalReviewDecision.from_value(
            _decision_dict(decision="rejected")
        )
        self.assertEqual(rejected.outcome, "blocked")
        with self.assertRaisesRegex(ValueError, "not supported"):
            FinalReviewDecision.from_value(_decision_dict(decision="maybe"))
        with self.assertRaisesRegex(ValueError, "plan hash"):
            FinalReviewDecision.from_value(_decision_dict(plan_sha256="abc"))
        with self.assertRaisesRegex(ValueError, "artifact paths"):
            FinalReviewDecision.from_value(
                _decision_dict(artifact_paths=["final-review/final-review-0002.json"])
            )
        with self.assertRaisesRegex(ValueError, "schema version"):
            FinalReviewDecision.from_value(_decision_dict(schema_version=2))

    def test_store_refuses_a_second_decision_for_the_same_run(self):
        with tempfile.TemporaryDirectory(
            prefix="orion-final-review-"
        ) as temp:
            store = CodexBridgeStore(Path(temp) / "bridge")
            first = FinalReviewDecision.from_value(_decision_dict())
            path = store.write_final_review_decision(first)
            self.assertTrue(path.is_file())
            loaded = store.load_final_review_decision(
                RUN_ID, f"final-review/{FINAL_REVIEW_ID}.json"
            )
            self.assertEqual(loaded.to_dict(), first.to_dict())
            with self.assertRaisesRegex(ValueError, "more than one"):
                store.next_final_review_decision(RUN_ID)
            with self.assertRaisesRegex(ValueError, "more than one"):
                store.write_final_review_decision(
                    FinalReviewDecision.from_value(
                        _decision_dict(
                            decision_id="final-review-0002",
                            artifact_paths=[
                                "final-review/final-review-0002.json"
                            ],
                        )
                    )
                )


class RealBridgeFinalReviewTests(unittest.TestCase):
    """Exercise record_final_review against the real bridge and store."""

    @staticmethod
    def _harness():
        # Imported here so the bridge test cases are not collected as part of
        # this module by unittest's loader.
        from tests.test_codex_bridge import CodexBridgeTests

        return CodexBridgeTests(
            "test_approval_persists_immutable_plan_hash_outside_workspace"
        )

    def test_record_final_review_persists_decision_and_refuses_second(self):
        harness = self._harness()
        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, workspace = harness.build(tmp)
            task = team_store.load("team-test-001")
            approval = bridge.approve(task.task_id, actor="owner")
            run = bridge.execute(
                task.task_id, approval.approval_id, run_followups=False
            )
            self.assertEqual(run.status, "awaiting_review")
            with self.assertRaisesRegex(ValueError, "validation"):
                bridge.record_final_review(run.run_id, decision="accepted")
            self._attach_passing_reviews(bridge, run, workspace)
            decided = bridge.record_final_review(
                run.run_id, decision="accepted", note="Ship it."
            )
            self.assertEqual(decided.final_review.decision, "accepted")
            self.assertEqual(decided.final_review.note, "Ship it.")
            self.assertEqual(
                decided.final_review.decision_id, FINAL_REVIEW_ID
            )
            self.assertEqual(
                decided.final_review_history,
                (f"final-review/{FINAL_REVIEW_ID}.json",),
            )
            reloaded = bridge.store.load_run(run.run_id)
            self.assertEqual(
                reloaded.final_review.to_dict(),
                decided.final_review.to_dict(),
            )
            with self.assertRaisesRegex(ValueError, "already has"):
                bridge.record_final_review(run.run_id, decision="rejected")
            with self.assertRaisesRegex(ValueError, "not supported"):
                bridge.record_final_review(run.run_id, decision="maybe")

    def test_record_final_review_refuses_failed_documentation(self):
        harness = self._harness()
        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, workspace = harness.build(tmp)
            task = team_store.load("team-test-001")
            approval = bridge.approve(task.task_id, actor="owner")
            run = bridge.execute(
                task.task_id, approval.approval_id, run_followups=False
            )
            validation = ValidationAttempt.from_value(
                self._validation_dict(run, workspace)
            )
            bridge.store.write_validation_attempt(validation, "validation log")
            updated = replace(
                run,
                validation=validation,
                validation_history=("validation/validation-0001.json",),
            )
            bridge.store.save_run(updated)
            with self.assertRaisesRegex(ValueError, "documentation"):
                bridge.record_final_review(run.run_id, decision="accepted")

    @staticmethod
    def _attach_passing_reviews(bridge, run, workspace):
        validation = ValidationAttempt.from_value(
            RealBridgeFinalReviewTests._validation_dict(run, workspace)
        )
        documentation = DocumentationAttempt.from_value(
            RealBridgeFinalReviewTests._documentation_dict(run, workspace)
        )
        bridge.store.write_validation_attempt(validation, "validation log")
        bridge.store.write_documentation_attempt(
            documentation, "documentation log"
        )
        bridge.store.save_run(replace(
            run,
            validation=validation,
            validation_history=("validation/validation-0001.json",),
            documentation=documentation,
            documentation_history=("documentation/documentation-0001.json",),
        ))

    @staticmethod
    def _validation_dict(run, workspace):
        return {
            "schema_version": 1,
            "attempt_id": "validation-0001",
            "run_id": run.run_id,
            "team_task_id": run.team_task_id,
            "approval_id": run.approval_id,
            "workspace_root": str(workspace.resolve()),
            "tester_requested": "codex",
            "tester_resolved": "codex",
            "execution_engine": "codex",
            "fallback": "",
            "fallback_reason": "",
            "status": "passed",
            "checks": [{
                "check_id": "tests",
                "name": "Tests",
                "status": "passed",
                "summary": "All tests passed.",
                "files": [],
            }],
            "commands": [],
            "checks_passed": ["Tests"],
            "checks_failed": [],
            "warnings": [],
            "skipped_checks": [],
            "files_inspected": [],
            "started_at": "2026-07-18T13:10:00Z",
            "completed_at": "2026-07-18T13:11:00Z",
            "duration_seconds": 60.0,
            "safe_diagnostics": [],
            "artifact_paths": [
                "validation/validation-0001.json",
                "validation/validation-0001.log",
            ],
        }

    @staticmethod
    def _documentation_dict(run, workspace):
        return {
            "schema_version": 1,
            "attempt_id": "documentation-0001",
            "run_id": run.run_id,
            "team_task_id": run.team_task_id,
            "approval_id": run.approval_id,
            "workspace_root": str(workspace.resolve()),
            "workspace": run.workspace.to_dict(),
            "implementation_summary_reference": "implementation-result.json",
            "validation_summary_reference": "validation/validation-0001.json",
            "classification": {
                "required": False,
                "reasons": [],
                "evidence": [],
                "categories": [],
            },
            "reviewer_requested": "openai:test",
            "reviewer_resolved": "openai:test",
            "provider": "openai",
            "model": "test",
            "fallback": "",
            "fallback_reason": "",
            "classification_decision": "deterministic_not_required",
            "classification_reason": "No documentation changes required.",
            "known_commands": [],
            "configuration_changes": [],
            "documents_inspected": [],
            "checks": [],
            "findings": [],
            "status": "passed",
            "counts_by_severity": {"info": 0, "warning": 0, "error": 0},
            "counts_by_category": {},
            "started_at": "2026-07-18T13:12:00Z",
            "completed_at": "2026-07-18T13:13:00Z",
            "duration_seconds": 60.0,
            "usage": {
                "provider": "openai",
                "model": "test",
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": None,
            },
            "safe_error_category": "",
            "safe_diagnostics": [],
            "artifact_paths": [
                "documentation/documentation-0001.json",
                "documentation/documentation-0001.log",
            ],
        }


class Phase3TeamApplication(Phase2TeamApplication):
    """Phase 2 fake plus the typed final-review dispatch surface."""

    def __init__(self, event_store) -> None:
        super().__init__(event_store)
        self.final_review_calls: list[TeamFinalReviewRequest] = []

    def final_review(self, request: TeamFinalReviewRequest) -> ApplicationResult:
        self.final_review_calls.append(request)
        run = self.runs[0]
        run.update({
            "final_review_status": "decided",
            "final_review_id": FINAL_REVIEW_ID,
            "final_review_decision": request.decision,
            "final_review_note": request.note,
            "final_review_completed_at": self._time(24),
        })
        completed = request.decision == "accepted"
        if self.emit_events:
            self._append(
                24,
                (
                    EventTypes.TEAM_FINAL_REVIEW_COMPLETED
                    if completed
                    else EventTypes.TEAM_FINAL_REVIEW_BLOCKED
                ),
                request,
                {
                    "status": "completed" if completed else "blocked",
                    "stage": "complete" if completed else "final_review",
                    "validation_id": VALIDATION_ID,
                    "validation_status": "passed",
                    "documentation_review_id": DOCUMENTATION_ID,
                    "documentation_review_status": "passed",
                    "final_review_id": FINAL_REVIEW_ID,
                    "decision": request.decision,
                    "note": request.note,
                    "completed_at": run["final_review_completed_at"],
                },
            )
        return ApplicationResult.success(
            "final review recorded",
            data={
                "run_id": RUN_ID,
                "final_review_id": FINAL_REVIEW_ID,
                "final_review_decision": request.decision,
                "status": "awaiting_review",
                "stage": "final_review",
            },
        )


class MissionCoordinatorPhase3Fixture(MissionCoordinatorPhase2Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.team_application = Phase3TeamApplication(self.events)
        self.coordinator = self._coordinator()

    def to_final_review(self) -> None:
        self.implement()
        self.validate()
        self.document()

    def final_review(self, decision="accepted", note=""):
        preview = self.coordinator.preview(
            self.mission.mission_id, decision=decision, note=note
        )
        self.assertFalse(preview.operation.blocked)
        self.assertEqual(preview.operation.capability_id, "team.final_review")
        return preview, self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
            actor="operator",
            decision=decision,
            note=note,
        ))


class FinalReviewTranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.translator = MissionOperationTranslator()

    def _operation(self, **overrides):
        inputs = {
            "run_id": RUN_ID,
            "team_task_id": TEAM_TASK_ID,
            "approval_id": APPROVAL_ID,
            "plan_sha256": PLAN_HASH,
            "implementation_completed_at": Phase2TeamApplication._time(21),
            "validation_id": VALIDATION_ID,
            "validation_status": "passed",
            "validation_completed_at": Phase2TeamApplication._time(22),
            "documentation_review_id": DOCUMENTATION_ID,
            "documentation_review_status": "passed",
            "documentation_review_completed_at": (
                Phase2TeamApplication._time(23)
            ),
            "decision": "accepted",
            "note": "",
        }
        if "full_inputs" in overrides:
            inputs = dict(overrides.pop("full_inputs"))
        else:
            inputs.update(overrides.pop("inputs", {}))
        return MissionNextOperation(
            mission_id=MISSION_ID,
            capability_id="team.final_review",
            operation_type="team_final_review",
            subject_id=overrides.pop("subject_id", RUN_ID),
            reason="Documentation review completed.",
            requires_confirmation=True,
            requires_downstream_approval=False,
            mutates_state=True,
            required_inputs=tuple(inputs),
            resolved_inputs=inputs,
            cli_representation=f"mission advance {MISSION_ID}",
            **overrides,
        )

    def test_final_review_is_allowlisted(self):
        self.assertTrue(self.translator.supports("team.final_review"))
        self.assertIn(
            MissionOperationTranslator.TEAM_FINAL_REVIEW,
            MissionOperationTranslator.SUPPORTED,
        )

    def test_final_review_translates_to_typed_request(self):
        translation = self.translator.translate(
            self._operation(inputs={"note": "Ship it."}),
            actor="operator",
            correlation_id=GOAL_ID,
            causation_id="event-00000000000000000000000000000017",
        )
        self.assertEqual(translation.capability_id, "team.final_review")
        self.assertEqual(
            translation.application_request_type, "TeamFinalReviewRequest"
        )
        request = translation.request
        self.assertIsInstance(request, TeamFinalReviewRequest)
        self.assertEqual(request.run_id, RUN_ID)
        self.assertEqual(request.decision, "accepted")
        self.assertEqual(request.note, "Ship it.")
        self.assertEqual(request.correlation_id, GOAL_ID)

    def test_final_review_schema_is_exact(self):
        base = self._operation()
        missing = dict(base.resolved_inputs)
        del missing["decision"]
        with self.assertRaisesRegex(
            MissionOperationTranslationError, "reviewed schema"
        ):
            self.translator.translate(
                self._operation(full_inputs=missing),
                actor="operator",
                correlation_id=GOAL_ID,
                causation_id=None,
            )
        extra = dict(base.resolved_inputs)
        extra["surprise"] = "no"
        with self.assertRaisesRegex(
            MissionOperationTranslationError, "reviewed schema"
        ):
            self.translator.translate(
                self._operation(full_inputs=extra),
                actor="operator",
                correlation_id=GOAL_ID,
                causation_id=None,
            )

    def test_final_review_rejects_invalid_facts(self):
        cases = (
            ({"decision": "maybe"}, "accepted, changes requested"),
            ({"decision": ""}, "is required"),
            ({"validation_status": "failed"}, "validation fact"),
            ({"documentation_review_status": "failed"}, "documentation review fact"),
            ({"plan_sha256": "abc"}, "plan SHA-256"),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                with self.assertRaisesRegex(
                    MissionOperationTranslationError, expected
                ):
                    self.translator.translate(
                        self._operation(inputs=inputs),
                        actor="operator",
                        correlation_id=GOAL_ID,
                        causation_id=None,
                    )

    def test_final_review_subject_must_match_run(self):
        operation = self._operation(subject_id="run-someone-else-0001")
        with self.assertRaisesRegex(
            MissionOperationTranslationError, "subject does not match"
        ):
            self.translator.translate(
                operation,
                actor="operator",
                correlation_id=GOAL_ID,
                causation_id=None,
            )

    def test_final_review_dispatch_calls_handler(self):
        translation = self.translator.translate(
            self._operation(),
            actor="operator",
            correlation_id=GOAL_ID,
            causation_id=None,
        )
        calls = []

        class FakeTeam:
            @staticmethod
            def final_review(request):
                calls.append(request)
                return ApplicationResult.success("recorded")

        result = self.translator.dispatch(
            translation, team_application=FakeTeam()
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], TeamFinalReviewRequest)


class FinalReviewCoordinatorTests(MissionCoordinatorPhase3Fixture):
    def test_final_review_operation_is_eligible_after_documentation(self):
        self.to_final_review()
        preview = self.coordinator.preview(self.mission.mission_id)
        operation = preview.operation
        self.assertFalse(operation.blocked)
        self.assertEqual(operation.capability_id, "team.final_review")
        self.assertEqual(operation.operation_type, "team_final_review")
        self.assertEqual(operation.subject_id, RUN_ID)
        self.assertEqual(
            operation.cli_representation,
            f"mission advance {self.mission.mission_id}",
        )
        self.assertTrue(operation.requires_confirmation)
        self.assertFalse(operation.requires_downstream_approval)
        self.assertTrue(operation.mutates_state)
        self.assertIn("decision", operation.required_inputs)
        self.assertIn("note", operation.required_inputs)
        self.assertEqual(operation.resolved_inputs["decision"], "")
        self.assertEqual(
            operation.resolved_inputs["documentation_review_id"],
            DOCUMENTATION_ID,
        )

    def test_already_decided_run_blocks_fail_closed(self):
        self.to_final_review()
        self.team_application.runs[0].update({
            "final_review_status": "decided",
            "final_review_id": FINAL_REVIEW_ID,
            "final_review_decision": "accepted",
            "final_review_completed_at": Phase2TeamApplication._time(24),
        })
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("already exists", preview.operation.blocked_reason)

    def test_mismatched_run_blocks_fail_closed(self):
        self.to_final_review()
        self.team_application.runs[0]["plan_sha256"] = "0" * 64
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("plan hash", preview.operation.blocked_reason)

    def test_unpassed_validation_or_documentation_blocks(self):
        self.to_final_review()
        self.team_application.runs[0]["validation_status"] = "failed"
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("validation", preview.operation.blocked_reason)
        self.team_application.runs[0]["validation_status"] = "passed"
        self.team_application.runs[0]["documentation_review_status"] = "failed"
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(preview.operation.blocked)
        self.assertIn("documentation", preview.operation.blocked_reason)

    def test_invalid_decision_blocks(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="maybe"
        )
        self.assertTrue(preview.operation.blocked)
        self.assertIn("not accepted", preview.operation.blocked_reason)

    def test_token_binds_decision_and_note(self):
        self.to_final_review()
        accepted = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        rejected = self.coordinator.preview(
            self.mission.mission_id, decision="rejected"
        )
        noted = self.coordinator.preview(
            self.mission.mission_id, decision="accepted", note="Ship it."
        )
        repeated = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        self.assertNotEqual(accepted.advance_token, rejected.advance_token)
        self.assertNotEqual(accepted.advance_token, noted.advance_token)
        self.assertEqual(accepted.advance_token, repeated.advance_token)

    def test_advance_without_decision_is_refused(self):
        self.to_final_review()
        preview = self.coordinator.preview(self.mission.mission_id)
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertFalse(self.team_application.final_review_calls)

    def test_advance_with_mismatched_decision_token_is_stale(self):
        self.to_final_review()
        preview = self.coordinator.preview(
            self.mission.mission_id, decision="accepted"
        )
        with self.assertRaises(MissionStalePreviewError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
                decision="rejected",
            ))
        self.assertFalse(self.team_application.final_review_calls)


class FinalReviewEndToEndTests(MissionCoordinatorPhase3Fixture):
    def test_accepted_final_review_completes_mission(self):
        self.to_final_review()
        preview, result = self.final_review("accepted", note="Ship it.")
        self.assertEqual(result.capability_id, "team.final_review")
        self.assertEqual(result.new_status, "completed")
        self.assertEqual(result.new_stage, "completed")
        self.assertEqual(result.new_progress, 100)
        self.assertEqual(result.audit_state, "succeeded")
        self.assertTrue(result.reconciled)
        calls = self.team_application.final_review_calls
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], TeamFinalReviewRequest)
        self.assertEqual(calls[0].decision, "accepted")
        self.assertEqual(calls[0].note, "Ship it.")
        mission = self.service.get(self.mission.mission_id)
        self.assertEqual(mission.status, MissionStatus.COMPLETED)
        self.assertTrue(mission.completed_at)
        link = mission.link("final_review")
        self.assertIsNotNone(link)
        self.assertEqual(link.subject_id, FINAL_REVIEW_ID)
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.downstream_reference, FINAL_REVIEW_ID)
        completed = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(completed.operation.blocked)
        self.assertEqual(len(self.team_application.final_review_calls), 1)

    def test_changes_requested_blocks_mission(self):
        self.to_final_review()
        _preview, result = self.final_review("changes_requested")
        self.assertEqual(result.new_status, "blocked")
        self.assertEqual(result.new_stage, "final_review")
        self.assertEqual(result.new_progress, 95)
        mission = self.service.get(self.mission.mission_id)
        self.assertEqual(mission.status, MissionStatus.BLOCKED)
        link = mission.link("final_review")
        self.assertIsNotNone(link)
        self.assertEqual(link.subject_id, FINAL_REVIEW_ID)
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(preview.operation.blocked)

    def test_rejected_blocks_mission(self):
        self.to_final_review()
        _preview, result = self.final_review("rejected", note="Not good enough.")
        self.assertEqual(result.new_status, "blocked")
        self.assertEqual(result.new_progress, 95)
        self.assertEqual(
            self.team_application.final_review_calls[0].decision, "rejected"
        )


class FinalReviewHandlerTests(MissionCoordinatorPhase2Fixture):
    def _handler_with_run(self):
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
            validation=SimpleNamespace(
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
            ),
            validation_history=(f"validation/{VALIDATION_ID}.json",),
            documentation=SimpleNamespace(
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
            ),
            documentation_history=(f"documentation/{DOCUMENTATION_ID}.json",),
            final_review=None,
            final_review_history=(),
            error="",
            started_at=Phase2TeamApplication._time(20),
            completed_at=Phase2TeamApplication._time(21),
        )

        class Bridge:
            workspace_capabilities = workspace

            @staticmethod
            def run(_run_id):
                return run

            @staticmethod
            def record_final_review(_run_id, *, decision, note="", actor="user"):
                if run.final_review is not None:
                    raise ValueError(
                        "This Codex run already has a final-review decision."
                    )
                run.final_review = SimpleNamespace(
                    decision_id=FINAL_REVIEW_ID,
                    decision=decision,
                    note=note,
                    actor=actor,
                    completed_at=Phase2TeamApplication._time(24),
                    to_dict=lambda: {
                        "decision_id": FINAL_REVIEW_ID,
                        "decision": decision,
                    },
                )
                run.final_review_history = (
                    f"final-review/{FINAL_REVIEW_ID}.json",
                )
                return run

        publisher = EventPublisher(EventFactory(), EventBus(self.events))
        runtime = SimpleNamespace(
            codex_bridge=Bridge(),
            execution_engines=SimpleNamespace(require_codex=lambda: object()),
            event_publisher=publisher,
        )
        return AiTeamApplicationHandler(runtime), run

    def _final_review_events(self):
        return [
            item
            for item in self.events.history(
                correlation_id=GOAL_ID, limit=100
            ).events
            if item.event_type
            in {
                EventTypes.TEAM_FINAL_REVIEW_COMPLETED,
                EventTypes.TEAM_FINAL_REVIEW_BLOCKED,
            }
        ]

    def test_final_review_handler_publishes_completed_event(self):
        handler, run = self._handler_with_run()
        result = handler.final_review(TeamFinalReviewRequest(
            RUN_ID,
            "accepted",
            note="Ship it.",
            correlation_id=GOAL_ID,
            causation_id=self.mission.last_event_id,
        ))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["final_review_id"], FINAL_REVIEW_ID)
        self.assertEqual(result.data["final_review_decision"], "accepted")
        events = self._final_review_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(
            event.event_type, EventTypes.TEAM_FINAL_REVIEW_COMPLETED
        )
        self.assertEqual(event.source, "ai_team")
        self.assertEqual(event.subject_id, RUN_ID)
        self.assertEqual(event.data["status"], "completed")
        self.assertEqual(event.data["decision"], "accepted")
        self.assertEqual(event.data["note"], "Ship it.")
        self.assertEqual(event.data["validation_id"], VALIDATION_ID)
        self.assertEqual(
            event.data["documentation_review_id"], DOCUMENTATION_ID
        )
        self.assertEqual(event.data["final_review_id"], FINAL_REVIEW_ID)
        self.assertEqual(event.data["plan_sha256"], PLAN_HASH)
        self.assertIsNotNone(run.final_review)

    def test_final_review_handler_publishes_blocked_event(self):
        handler, _run = self._handler_with_run()
        result = handler.final_review(TeamFinalReviewRequest(
            RUN_ID,
            "changes_requested",
            correlation_id=GOAL_ID,
        ))
        self.assertTrue(result.ok)
        events = self._final_review_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].event_type, EventTypes.TEAM_FINAL_REVIEW_BLOCKED
        )
        self.assertEqual(events[0].data["status"], "blocked")
        self.assertEqual(events[0].data["decision"], "changes_requested")

    def test_final_review_handler_refuses_second_decision(self):
        handler, _run = self._handler_with_run()
        first = handler.final_review(TeamFinalReviewRequest(
            RUN_ID, "accepted", correlation_id=GOAL_ID
        ))
        self.assertTrue(first.ok)
        second = handler.final_review(TeamFinalReviewRequest(
            RUN_ID, "rejected", correlation_id=GOAL_ID
        ))
        self.assertFalse(second.ok)
        self.assertEqual(len(self._final_review_events()), 1)


class FinalReviewCliTests(MissionCoordinatorPhase3Fixture):
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

    def test_advance_prompts_for_decision_then_confirms(self):
        self.to_final_review()
        adapter, output = self._adapter(["a", "y"])
        result = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["capability_id"], "team.final_review")
        self.assertEqual(result.data["new_status"], "completed")
        self.assertEqual(len(self.team_application.final_review_calls), 1)
        self.assertEqual(
            self.team_application.final_review_calls[0].decision, "accepted"
        )
        rendered = "\n".join(output)
        self.assertIn("[A/C/R/N]", rendered)
        self.assertIn("Final-review decision: accepted", rendered)

    def test_advance_decision_decline_dispatches_nothing(self):
        self.to_final_review()
        adapter, output = self._adapter(["n"])
        result = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["operation_dispatched"])
        self.assertFalse(self.team_application.final_review_calls)
        self.assertIn("cancelled", "\n".join(output).lower())

    def test_advance_confirmation_decline_dispatches_nothing(self):
        self.to_final_review()
        adapter, _output = self._adapter(["a", "n"])
        result = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["operation_dispatched"])
        self.assertFalse(self.team_application.final_review_calls)

    def test_advance_with_note_flag_records_note(self):
        self.to_final_review()
        adapter, _output = self._adapter(["c", "y"])
        result = adapter.handle(
            f"advance {self.mission.mission_id} --note \"fix the docs\""
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["new_status"], "blocked")
        call = self.team_application.final_review_calls[0]
        self.assertEqual(call.decision, "changes_requested")
        self.assertEqual(call.note, "fix the docs")

    def test_advance_without_final_review_skips_decision_prompt(self):
        adapter, output = self._adapter(["y"])
        result = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["capability_id"], "team.implement")
        self.assertNotIn("[A/C/R/N]", "\n".join(output))


if __name__ == "__main__":
    unittest.main()
