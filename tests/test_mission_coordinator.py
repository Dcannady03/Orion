from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import inspect
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamApprovalRequest,
)
from orion.application.commands.mission_cli import MissionCliAdapter, dispatch_mission
from orion.application.events import (
    EventBus,
    EventFactory,
    EventPublisher,
    EventStore,
    EventTypes,
    OrionEvent,
)
from orion.application.interface_actions import cli_command_for_action
from orion.application.missions import (
    MissionAdvanceRequest,
    MissionApplicationHandler,
    MissionCoordinationError,
    MissionCoordinationRepository,
    MissionCoordinator,
    MissionDispatchUncertainError,
    MissionLink,
    MissionOperationTranslator,
    MissionProjectionEngine,
    MissionStage,
    MissionStalePreviewError,
    MissionStatus,
)
from orion.application.results import ApplicationResult
from tests.test_mission_engine import (
    GOAL_ID,
    NOW,
    NOW_TEXT,
    PROPOSAL_ID,
    TEAM_TASK_ID,
    MissionFixture,
    event,
)


PLAN_HASH = "f" * 64
APPROVAL_ID = "approval-mission-coordinator-001"


class FakeTeamApplication:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store
        self.plan_hash = PLAN_HASH
        self.existing_approval_count = 0
        self.inspection_available = True
        self.approval_result = ApplicationResult.success(
            "Team plan approved.",
            data={
                "team_task_id": TEAM_TASK_ID,
                "approval_id": APPROVAL_ID,
                "status": "approved",
                "plan_sha256": PLAN_HASH,
            },
        )
        self.emit_event = True
        self.raise_on_approve = False
        self.approval_details_calls = []
        self.approve_calls = []
        self.started: Event | None = None
        self.release: Event | None = None

    def approval_details(self, request):
        self.approval_details_calls.append(request)
        return ApplicationResult.success(
            "Approval details",
            data={
                "team_task_id": request.team_task_id,
                "status": "awaiting_approval",
                "plan_sha256": self.plan_hash,
                "approval_inspection_available": self.inspection_available,
                "existing_approval_count": self.existing_approval_count,
            },
        )

    def approve(self, request):
        self.approve_calls.append(request)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        if self.raise_on_approve:
            raise RuntimeError("simulated uncertain boundary")
        if self.approval_result.ok and self.emit_event:
            occurred_at = (NOW + timedelta(seconds=10)).isoformat().replace(
                "+00:00", "Z"
            )
            self.event_store.append(OrionEvent(
                event_id=f"event-{10:032x}",
                event_type=EventTypes.TEAM_PLAN_APPROVED,
                occurred_at=occurred_at,
                source="ai_team",
                severity="notice",
                correlation_id=request.correlation_id or TEAM_TASK_ID,
                causation_id=request.causation_id or "",
                subject_id=request.team_task_id,
                data={
                    "team_task_id": request.team_task_id,
                    "approval_id": APPROVAL_ID,
                    "status": "approved",
                    "approval_required": True,
                    "approval_status": "approved",
                    "plan_sha256": request.plan_sha256,
                },
            ))
        return self.approval_result


class MissionCoordinatorFixture(MissionFixture):
    def setUp(self) -> None:
        super().setUp()
        self.save_proposal()
        self.append_chain()
        self.mission = self.service.create(PROPOSAL_ID).mission
        self.team_application = FakeTeamApplication(self.events)
        self.audit_repository = MissionCoordinationRepository(
            self.root / "user" / "missions" / "coordination",
            forbidden_root=self.install_root,
            lock_timeout_seconds=0.2,
        )
        self.coordinator = MissionCoordinator(
            self.service,
            self.team_application,
            self.audit_repository,
            clock=self.clock,
            attempt_id_factory=lambda: f"mission-attempt-{'e' * 32}",
        )
        self.application = MissionApplicationHandler(
            self.service,
            self.coordinator,
        )


class MissionNextOperationTests(MissionCoordinatorFixture):
    def test_awaiting_approval_resolves_only_typed_team_approve(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        operation = preview.operation
        self.assertFalse(operation.blocked)
        self.assertEqual(operation.capability_id, "team.approve")
        self.assertEqual(operation.subject_id, TEAM_TASK_ID)
        self.assertEqual(operation.cli_representation, f"team approve {TEAM_TASK_ID}")
        self.assertNotIn("team.approve ", operation.cli_representation)
        self.assertTrue(operation.requires_confirmation)
        self.assertTrue(operation.requires_downstream_approval)
        self.assertTrue(operation.mutates_state)
        self.assertEqual(operation.resolved_inputs["plan_sha256"], PLAN_HASH)
        self.assertFalse(self.team_application.approve_calls)

    def test_unsupported_missing_failed_and_final_review_states_block(self) -> None:
        without_task = replace(
            self.mission,
            links=tuple(
                item for item in self.mission.links if item.link_type != "team_task"
            ),
        )
        failed = replace(
            self.mission,
            status=MissionStatus.FAILED,
            stage=MissionStage.FAILED,
        )
        planning = replace(
            self.mission,
            status=MissionStatus.PLANNING,
            stage=MissionStage.TEAM_PLANNING,
        )
        awaiting_review = replace(
            self.mission,
            status=MissionStatus.AWAITING_REVIEW,
            stage=MissionStage.FINAL_REVIEW,
        )
        approval = MissionLink(
            "approval",
            APPROVAL_ID,
            EventTypes.TEAM_PLAN_APPROVED,
            NOW_TEXT,
        )
        approved = replace(
            self.mission,
            status=MissionStatus.APPROVED,
            stage=MissionStage.IMPLEMENTATION,
            links=tuple((*self.mission.links, approval)),
        )
        cases = (
            (without_task, "no authoritative Team task"),
            (failed, "cannot be advanced"),
            (planning, "No supported operation"),
            (awaiting_review, "no typed completion operation"),
            (approved, "coordination inspection is unavailable"),
        )
        for mission, expected in cases:
            with self.subTest(expected=expected):
                operation = self.coordinator.determine_operation(mission)
                self.assertTrue(operation.blocked)
                self.assertIn(expected, operation.blocked_reason)

    def test_existing_or_uninspectable_approval_blocks_fail_closed(self) -> None:
        self.team_application.existing_approval_count = 1
        existing = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(existing.operation.blocked)
        self.assertIn("already exists", existing.operation.blocked_reason)
        self.team_application.existing_approval_count = 0
        self.team_application.inspection_available = False
        unavailable = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(unavailable.operation.blocked)
        self.assertIn("fails closed", unavailable.operation.blocked_reason)
        self.assertFalse(self.team_application.approve_calls)

    def test_interface_actions_use_real_mission_and_team_cli_syntax(self) -> None:
        self.assertEqual(
            cli_command_for_action(
                "team.approve", {"team_task_id": TEAM_TASK_ID}
            ),
            f"team approve {TEAM_TASK_ID}",
        )
        self.assertEqual(
            cli_command_for_action(
                "mission.next", {"mission_id": self.mission.mission_id}
            ),
            f"mission next {self.mission.mission_id}",
        )
        self.assertEqual(
            cli_command_for_action(
                "mission.advance", {"mission_id": self.mission.mission_id}
            ),
            f"mission advance {self.mission.mission_id}",
        )

    def test_malformed_approval_event_cannot_advance_projection(self) -> None:
        self.events.append(OrionEvent(
            event_id=f"event-{30:032x}",
            event_type=EventTypes.TEAM_PLAN_APPROVED,
            occurred_at=(NOW + timedelta(seconds=11)).isoformat().replace(
                "+00:00", "Z"
            ),
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
                "approval_status": "pending",
                "plan_sha256": PLAN_HASH,
            },
        ))
        reconciled = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(reconciled.status, MissionStatus.AWAITING_APPROVAL)
        self.assertIsNone(reconciled.link("approval"))

    def test_late_approval_event_cannot_resurrect_failed_mission(self) -> None:
        failed = event(
            31,
            EventTypes.GOAL_PROPOSAL_FAILED,
            occurred_at=(NOW + timedelta(seconds=11)).isoformat().replace(
                "+00:00", "Z"
            ),
        )
        approved = OrionEvent(
            event_id=f"event-{32:032x}",
            event_type=EventTypes.TEAM_PLAN_APPROVED,
            occurred_at=(NOW + timedelta(seconds=12)).isoformat().replace(
                "+00:00", "Z"
            ),
            source="ai_team",
            severity="notice",
            correlation_id=GOAL_ID,
            causation_id=failed.event_id,
            subject_id=TEAM_TASK_ID,
            data={
                "team_task_id": TEAM_TASK_ID,
                "approval_id": APPROVAL_ID,
                "status": "approved",
                "approval_required": True,
                "approval_status": "approved",
                "plan_sha256": PLAN_HASH,
            },
        )
        projected = MissionProjectionEngine().project(
            self.mission,
            (failed, approved),
        )
        self.assertEqual(projected.status, MissionStatus.FAILED)
        self.assertFalse(any(link.link_type == "approval" for link in projected.links))


class MissionPreviewTokenTests(MissionCoordinatorFixture):
    def test_preview_is_immutable_json_safe_deterministic_and_execution_free(self) -> None:
        first = self.coordinator.preview(self.mission.mission_id)
        second = self.coordinator.preview(self.mission.mission_id)
        self.assertEqual(first.advance_token, second.advance_token)
        self.assertEqual(len(first.advance_token), 64)
        self.assertFalse(first.operation.blocked)
        json.dumps(first.to_dict(), allow_nan=False)
        with self.assertRaises(FrozenInstanceError):
            first.advance_token = "0" * 64  # type: ignore[misc]
        self.assertFalse(self.team_application.approve_calls)

    def test_token_binds_team_plan_hash_and_survives_restart_if_unchanged(self) -> None:
        first = self.coordinator.preview(self.mission.mission_id)
        restarted = MissionCoordinator(
            self.service,
            self.team_application,
            MissionCoordinationRepository(
                self.audit_repository.root,
                forbidden_root=self.install_root,
            ),
            clock=self.clock,
        )
        self.assertEqual(
            restarted.preview(self.mission.mission_id).advance_token,
            first.advance_token,
        )
        self.team_application.plan_hash = "a" * 64
        changed = restarted.preview(self.mission.mission_id)
        self.assertNotEqual(changed.advance_token, first.advance_token)


class MissionTranslationAndAdvanceTests(MissionCoordinatorFixture):
    def test_translation_is_explicit_typed_and_calls_only_approve(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        translator = MissionOperationTranslator()
        translated = translator.translate(
            preview.operation,
            actor="operator",
            correlation_id=GOAL_ID,
            causation_id=self.mission.last_event_id,
        )
        self.assertIsInstance(translated.request, TeamApprovalRequest)
        self.assertEqual(translated.request.team_task_id, TEAM_TASK_ID)
        self.assertEqual(translated.request.plan_sha256, PLAN_HASH)
        application = SimpleNamespace(approve=Mock(return_value=ApplicationResult.success("ok")))
        result = translator.dispatch(translated, team_application=application)
        self.assertTrue(result.ok)
        application.approve.assert_called_once_with(translated.request)
        source = inspect.getsource(MissionOperationTranslator)
        self.assertNotIn("importlib", source)
        self.assertNotIn("MissionCliAdapter", source)

    def test_coordinator_has_no_direct_execution_or_cli_dependencies(self) -> None:
        source = "\n".join((
            inspect.getsource(MissionCoordinator),
            inspect.getsource(MissionOperationTranslator),
        )).lower()
        for forbidden in (
            "subprocess",
            "os.system",
            "gitpython",
            "missioncliadapter",
            "execution_engine",
            "workspace_manager",
            "provider_registry",
            "agent_service",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        with patch("subprocess.run") as run, patch("os.system") as system:
            preview = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(preview.operation.blocked)
        run.assert_not_called()
        system.assert_not_called()

    def test_confirmation_token_and_identity_fail_closed_without_dispatch(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        with self.assertRaisesRegex(MissionCoordinationError, "confirmation"):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=False,
            ))
        with self.assertRaisesRegex(MissionCoordinationError, "preview token"):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                confirmed=True,
            ))
        with self.assertRaises(MissionStalePreviewError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                "0" * 64,
                confirmed=True,
            ))
        with self.assertRaises(FileNotFoundError):
            self.coordinator.advance(MissionAdvanceRequest(
                f"mission-{'1' * 32}",
                preview.advance_token,
                confirmed=True,
            ))
        self.assertFalse(self.team_application.approve_calls)

    def test_state_change_after_preview_invalidates_token(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        self.events.append(OrionEvent(
            event_id=f"event-{20:032x}",
            event_type=EventTypes.GOAL_PROPOSAL_VALIDATED,
            occurred_at=(NOW + timedelta(seconds=9)).isoformat().replace(
                "+00:00", "Z"
            ),
            source="goal_proposals",
            severity="info",
            correlation_id=GOAL_ID,
            subject_id=PROPOSAL_ID,
            data={
                "proposal_id": PROPOSAL_ID,
                "goal_id": GOAL_ID,
                "version": 1,
                "status": "consumed",
                "valid": True,
            },
        ))
        with self.assertRaises(MissionStalePreviewError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertFalse(self.team_application.approve_calls)

    def test_success_dispatches_once_reconciles_to_approved_and_stops(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        result = self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
            actor="operator",
        ))
        self.assertEqual(len(self.team_application.approve_calls), 1)
        request = self.team_application.approve_calls[0]
        self.assertIsInstance(request, TeamApprovalRequest)
        self.assertEqual(request.plan_sha256, PLAN_HASH)
        self.assertEqual(request.correlation_id, GOAL_ID)
        self.assertTrue(result.operation_dispatched)
        self.assertEqual(result.new_status, "approved")
        self.assertEqual(result.new_stage, "implementation")
        self.assertEqual(result.new_progress, 35)
        self.assertEqual(result.audit_state, "succeeded")
        self.assertIn("team implement", result.next_action)
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "succeeded")
        self.assertEqual(audit.downstream_reference, APPROVAL_ID)
        self.assertTrue(result.to_dict()["stopped_after_one_operation"])
        with self.assertRaises(MissionCoordinationError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_downstream_failure_reconciles_and_blocks_retry(self) -> None:
        self.team_application.approval_result = ApplicationResult.failure(
            "Approval refused.",
            errors=("Plan state is invalid.",),
        )
        preview = self.coordinator.preview(self.mission.mission_id)
        result = self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
        ))
        self.assertEqual(result.audit_state, "failed")
        self.assertEqual(result.new_status, "awaiting_approval")
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(blocked.operation.blocked)
        self.assertIn("automatic retry", blocked.operation.blocked_reason)
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_success_without_event_warns_and_blocks_duplicate(self) -> None:
        self.team_application.emit_event = False
        preview = self.coordinator.preview(self.mission.mission_id)
        result = self.coordinator.advance(MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
        ))
        self.assertEqual(result.new_status, "awaiting_approval")
        self.assertTrue(any("no authoritative" in item for item in result.warnings))
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertIn("already dispatched", blocked.operation.blocked_reason)
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_uncertain_exception_persists_block_and_never_replays(self) -> None:
        self.team_application.raise_on_approve = True
        preview = self.coordinator.preview(self.mission.mission_id)
        with self.assertRaises(MissionDispatchUncertainError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        audit = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(audit.state, "uncertain")
        restarted = MissionCoordinator(
            self.service,
            self.team_application,
            MissionCoordinationRepository(
                self.audit_repository.root,
                forbidden_root=self.install_root,
            ),
            clock=self.clock,
        )
        blocked = restarted.preview(self.mission.mission_id)
        self.assertIn("outcome is uncertain", blocked.operation.blocked_reason)
        self.assertEqual(len(self.team_application.approve_calls), 1)


class MissionConcurrencyTests(MissionCoordinatorFixture):
    def test_two_coordinators_cannot_dispatch_same_operation_twice(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        self.team_application.started = Event()
        self.team_application.release = Event()
        second_repository = MissionCoordinationRepository(
            self.audit_repository.root,
            forbidden_root=self.install_root,
            lock_timeout_seconds=0.05,
        )
        second = MissionCoordinator(
            self.service,
            self.team_application,
            second_repository,
            clock=self.clock,
        )
        request = MissionAdvanceRequest(
            self.mission.mission_id,
            preview.advance_token,
            confirmed=True,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.coordinator.advance, request)
            self.assertTrue(self.team_application.started.wait(timeout=1))
            with self.assertRaises(TimeoutError):
                second.advance(request)
            self.team_application.release.set()
            completed = first.result(timeout=2)
        self.assertEqual(completed.new_status, "approved")
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_stale_lock_blocks_preview_and_advance(self) -> None:
        preview = self.coordinator.preview(self.mission.mission_id)
        self.audit_repository.root.mkdir(parents=True, exist_ok=True)
        lock = self.audit_repository.root / (
            f".{self.mission.mission_id}.advance.lock"
        )
        lock.write_text("stale", encoding="utf-8")
        blocked = self.coordinator.preview(self.mission.mission_id)
        self.assertTrue(blocked.operation.blocked)
        with self.assertRaises(TimeoutError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
            ))
        self.assertFalse(self.team_application.approve_calls)


class MissionCoordinatorCliTests(MissionCoordinatorFixture):
    def adapter(self, answers=()):
        output = []
        iterator = iter(answers)
        runtime = SimpleNamespace(mission_application=self.application)
        return MissionCliAdapter(
            runtime,
            output_provider=output.append,
            input_provider=lambda _prompt: next(iterator),
        ), output

    def test_next_is_preview_only_and_cancel_or_detail_never_execute(self) -> None:
        adapter, output = self.adapter()
        preview = adapter.handle(f"next {self.mission.mission_id}")
        self.assertTrue(preview.ok)
        self.assertFalse(preview.data["operation_dispatched"])
        self.assertFalse(self.team_application.approve_calls)
        self.assertTrue(any("No capability has been executed" in item for item in output))

        adapter, _output = self.adapter(("d", "n"))
        cancelled = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(cancelled.ok)
        self.assertFalse(cancelled.data["operation_dispatched"])
        self.assertFalse(self.team_application.approve_calls)

    def test_explicit_yes_dispatches_one_operation_and_router_only_delegates(self) -> None:
        adapter, _output = self.adapter(("y",))
        result = adapter.handle(f"advance {self.mission.mission_id}")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["operation_dispatched"])
        self.assertEqual(len(self.team_application.approve_calls), 1)
        runtime = SimpleNamespace(mission_application=self.application)
        with patch.object(self.application, "next", return_value=ApplicationResult.success(
            "blocked", data={"blocked": True}
        )) as next_call, patch("builtins.print"):
            self.assertTrue(dispatch_mission(
                runtime,
                f"mission next {self.mission.mission_id}",
            ))
            next_call.assert_called_once()
        self.assertFalse(dispatch_mission(runtime, "events list"))


class TeamApprovalEventIntegrationTests(unittest.TestCase):
    def test_success_emits_authoritative_approval_event_and_failure_does_not(self) -> None:
        with self.subTest("success"):
            self._assert_approval_event(success=True)
        with self.subTest("failure"):
            self._assert_approval_event(success=False)

    def _assert_approval_event(self, *, success: bool) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = EventStore(root / "events")
            publisher = EventPublisher(EventFactory(), EventBus(store))
            task = SimpleNamespace(
                task_id=TEAM_TASK_ID,
                goal="Prepare Orion for release",
                status="awaiting_approval",
                final_plan=["Inspect boundaries", "Run tests"],
                created_at=NOW_TEXT,
                updated_at=NOW_TEXT,
                selected_agents=[],
                artifacts={},
                artifact=lambda _role: None,
            )
            workspace = SimpleNamespace(
                mode="standard",
                branch="",
                commit="",
                is_git_repository=False,
                git_root="",
            )
            approval = SimpleNamespace(
                team_task_id=TEAM_TASK_ID,
                approval_id=APPROVAL_ID,
                workspace_root=str(root),
                workspace=workspace,
                plan_hash=PLAN_HASH,
                execution_engine="codex",
                approved_at=NOW_TEXT,
                to_dict=lambda: {
                    "team_task_id": TEAM_TASK_ID,
                    "approval_id": APPROVAL_ID,
                    "plan_hash": PLAN_HASH,
                },
            )
            bridge = SimpleNamespace(
                workspace_capabilities=workspace,
                approve=(
                    Mock(return_value=approval)
                    if success else Mock(side_effect=ValueError("approval refused"))
                ),
            )
            runtime = SimpleNamespace(
                team=SimpleNamespace(task=lambda _task_id: task),
                codex_bridge=bridge,
                event_publisher=publisher,
            )
            handler = AiTeamApplicationHandler(runtime)
            result = handler.approve(TeamApprovalRequest(
                TEAM_TASK_ID,
                correlation_id=GOAL_ID,
                causation_id=f"event-{5:032x}",
            ))
            events = store.history(
                event_type=EventTypes.TEAM_PLAN_APPROVED,
                limit=10,
            ).events
            self.assertEqual(result.ok, success)
            self.assertEqual(len(events), 1 if success else 0)
            if success:
                self.assertEqual(events[0].correlation_id, GOAL_ID)
                self.assertEqual(events[0].subject_id, TEAM_TASK_ID)
                self.assertEqual(events[0].data["approval_id"], APPROVAL_ID)


if __name__ == "__main__":
    unittest.main()
