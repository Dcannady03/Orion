from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orion.application.commands.mission_cli import MissionCliAdapter, dispatch_mission
from orion.application.events import EventStore, EventTypes, OrionEvent
from orion.application.goals.proposals.integrity import proposal_plan_hash
from orion.application.goals.proposals.models import (
    GOAL_PROPOSAL_SCHEMA_VERSION,
    GoalProposal,
    GoalProposalSnapshot,
    GoalProposalStatus,
    GoalProposalStep,
    GoalProposalStepStatus,
)
from orion.application.goals.proposals.repository import GoalProposalRepository
from orion.application.missions import (
    MISSION_PROGRESS,
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionApplicationHandler,
    MissionListRequest,
    MissionProjectionEngine,
    MissionReferenceRequest,
    MissionRepository,
    MissionService,
    MissionStage,
    MissionStatus,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = "2026-08-08T12:00:00Z"
PROPOSAL_ID = f"proposal-{'a' * 32}"
GOAL_ID = "goal-mission-test"
TEAM_TASK_ID = "team-mission-test"


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


def make_proposal(
    status: GoalProposalStatus = GoalProposalStatus.CONSUMED,
    *,
    proposal_id: str = PROPOSAL_ID,
    goal_id: str = GOAL_ID,
    team_task_id: str = TEAM_TASK_ID,
) -> GoalProposal:
    step_status = {
        GoalProposalStatus.ACCEPTED: GoalProposalStepStatus.ACCEPTED,
        GoalProposalStatus.CONSUMED: GoalProposalStepStatus.CONSUMED,
        GoalProposalStatus.FAILED: GoalProposalStepStatus.FAILED,
    }.get(status, GoalProposalStepStatus.PENDING)
    step = GoalProposalStep(
        step_id=f"{proposal_id}-step-001",
        step_number=1,
        capability_id="team.plan",
        reason="Plan the accepted goal through AI Team.",
        requires_approval=False,
        mutates_state=True,
        required_inputs=("goal",),
        resolved_inputs={"goal": "Prepare Orion for release"},
        expected_outputs=("team_task_id",),
        required_permissions=("team.plan",),
        status=step_status,
        application_request_type="TeamPlanRequest",
    )
    snapshot = GoalProposalSnapshot(
        proposal_id=proposal_id,
        goal_id=goal_id,
        version=1,
        goal_text="Prepare Orion for release",
        classification="engineering",
        workspace="C:/workspace/orion",
        department_id="engineering",
        department_name="Engineering",
        priority="normal",
        created_at=NOW_TEXT,
        expires_at="2026-08-09T12:00:00Z",
        registry_fingerprint="b" * 64,
        capability_fingerprint="c" * 64,
        steps=(step,),
        source="goal_engine",
        metadata={"goal_warnings": ["Review scope."], "goal_risks": ["Regression risk."]},
    )
    accepted = status in {
        GoalProposalStatus.ACCEPTED,
        GoalProposalStatus.CONSUMED,
        GoalProposalStatus.FAILED,
    }
    return GoalProposal(
        schema_version=GOAL_PROPOSAL_SCHEMA_VERSION,
        proposal_id=proposal_id,
        goal_id=goal_id,
        version=1,
        status=status,
        goal_text=snapshot.goal_text,
        classification=snapshot.classification,
        workspace=snapshot.workspace,
        department_id=snapshot.department_id,
        department_name=snapshot.department_name,
        priority=snapshot.priority,
        created_at=snapshot.created_at,
        updated_at=NOW_TEXT,
        expires_at=snapshot.expires_at,
        plan_hash=proposal_plan_hash(snapshot),
        registry_fingerprint=snapshot.registry_fingerprint,
        capability_fingerprint=snapshot.capability_fingerprint,
        steps=(step,),
        current_step=1,
        accepted_at=NOW_TEXT if accepted else "",
        accepted_by="operator" if accepted else "",
        rejected_at=NOW_TEXT if status is GoalProposalStatus.REJECTED else "",
        rejected_by="operator" if status is GoalProposalStatus.REJECTED else "",
        consumed_at=NOW_TEXT if status is GoalProposalStatus.CONSUMED else "",
        failed_at=NOW_TEXT if status is GoalProposalStatus.FAILED else "",
        failure_code="dispatch_failed" if status is GoalProposalStatus.FAILED else "",
        failure_message="Safe dispatch failure." if status is GoalProposalStatus.FAILED else "",
        attempted_capability_id="team.plan" if accepted else "",
        source=snapshot.source,
        dispatch_summary=(
            {
                "team_task_id": team_task_id,
                "status": "awaiting_approval",
                "stage": "awaiting_approval",
                "approval_required": True,
                "approval_status": "pending",
            }
            if status is GoalProposalStatus.CONSUMED and team_task_id else {}
        ),
        metadata=snapshot.metadata,
    )


def event(
    number: int,
    event_type: str,
    *,
    goal_id: str = GOAL_ID,
    proposal_id: str = PROPOSAL_ID,
    team_task_id: str = TEAM_TASK_ID,
    occurred_at: str | None = None,
) -> OrionEvent:
    is_team = event_type == EventTypes.TEAM_PLAN_CREATED
    data = (
        {
            "team_task_id": team_task_id,
            "status": "awaiting_approval",
            "approval_required": True,
            "selected_agent_count": 0,
        }
        if is_team else {
            "proposal_id": proposal_id,
            "goal_id": goal_id,
            "version": 1,
            "status": event_type.rsplit(".", 1)[-1],
        }
    )
    return OrionEvent(
        event_id=f"event-{number:032x}",
        event_type=event_type,
        occurred_at=occurred_at or (
            NOW + timedelta(seconds=number)
        ).isoformat().replace("+00:00", "Z"),
        source="ai_team" if is_team else "goal_proposals",
        severity="notice",
        correlation_id=goal_id,
        causation_id=f"event-{3:032x}" if is_team else "",
        subject_id=team_task_id if is_team else proposal_id,
        data=data,
    )


class MissionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install_root = self.root / "application"
        self.install_root.mkdir()
        self.proposals = GoalProposalRepository(
            self.root / "user" / "goals" / "proposals",
            forbidden_root=self.install_root,
        )
        self.events = EventStore(
            self.root / "user" / "events",
            forbidden_root=self.install_root,
        )
        self.repository = MissionRepository(
            self.root / "user" / "missions",
            forbidden_root=self.install_root,
        )
        self.clock = Clock()
        self.service = MissionService(
            self.repository,
            self.proposals,
            self.events,
            clock=self.clock,
            id_factory=lambda: f"mission-{'d' * 32}",
        )
        self.application = MissionApplicationHandler(self.service)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def save_proposal(self, proposal: GoalProposal | None = None) -> GoalProposal:
        selected = proposal or make_proposal()
        self.proposals.save(selected)
        return selected

    def append_chain(self) -> tuple[OrionEvent, ...]:
        chain = (
            event(1, EventTypes.GOAL_PROPOSAL_CREATED),
            event(2, EventTypes.GOAL_PROPOSAL_VALIDATED),
            event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED),
            event(4, EventTypes.TEAM_PLAN_CREATED),
            event(5, EventTypes.GOAL_PROPOSAL_CONSUMED),
        )
        for item in chain:
            self.events.append(item)
        return chain


class MissionModelTests(MissionFixture):
    def test_model_is_immutable_json_safe_and_schema_versioned(self) -> None:
        self.save_proposal()
        mission = self.service.create(PROPOSAL_ID).mission
        self.assertEqual(mission.schema_version, MISSION_SCHEMA_VERSION)
        self.assertEqual(json.loads(mission.to_json())["mission_id"], mission.mission_id)
        with self.assertRaises(FrozenInstanceError):
            mission.status = MissionStatus.FAILED  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "Mission status"):
            replace(mission, status="executing")
        with self.assertRaisesRegex(ValueError, "schema"):
            Mission.from_value({**mission.to_dict(), "schema_version": 99})

    def test_runtime_path_is_external_and_repository_location_is_rejected(self) -> None:
        paths = OrionPaths(self.install_root, self.root / "user-data")
        self.assertEqual(paths.missions, self.root / "user-data" / "missions")
        self.assertFalse(paths.missions.is_relative_to(paths.install_root))
        with self.assertRaisesRegex(ValueError, "application repository"):
            MissionRepository(
                self.install_root / "missions",
                forbidden_root=self.install_root,
            )


class MissionRepositoryTests(MissionFixture):
    def test_create_load_list_duplicate_and_atomic_storage(self) -> None:
        self.save_proposal()
        first = self.service.create(PROPOSAL_ID)
        second = self.service.create(PROPOSAL_ID)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.mission.mission_id, second.mission.mission_id)
        self.assertEqual(self.repository.get(first.mission.mission_id), second.mission)
        self.assertEqual(len(self.repository.list(proposal_id=PROPOSAL_ID)), 1)
        self.assertFalse(tuple(self.repository.root.glob("*.tmp")))
        self.assertEqual(
            json.loads((self.repository.root / f"{first.mission.mission_id}.json").read_text())["schema_version"],
            1,
        )

    def test_malformed_unknown_schema_and_symlink_fail_safely(self) -> None:
        self.repository.root.mkdir(parents=True)
        malformed = self.repository.root / f"mission-{'e' * 32}.json"
        malformed.write_text("{bad json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.repository.get(malformed.stem)
        malformed.unlink()

        self.save_proposal()
        mission = self.service.create(PROPOSAL_ID).mission
        path = self.repository.root / f"{mission.mission_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.repository.get(mission.mission_id)

        if hasattr(Path, "symlink_to"):
            link = self.root / "mission-link"
            try:
                link.symlink_to(self.repository.root, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(PermissionError):
                MissionRepository(link).list()


class MissionCreationProjectionTests(MissionFixture):
    def test_manual_event_chain_projects_existing_consumed_proposal(self) -> None:
        self.save_proposal()
        chain = self.append_chain()
        result = self.application.create(PROPOSAL_ID)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "awaiting_approval")
        self.assertEqual(result.data["stage"], "approval")
        self.assertEqual(result.data["progress"], MISSION_PROGRESS["awaiting_approval"])
        self.assertEqual(result.data["team_task_id"], TEAM_TASK_ID)
        self.assertEqual(result.data["last_event_id"], chain[-1].event_id)
        self.assertEqual(result.data["event_count"], 5)
        self.assertIn(f"team approve {TEAM_TASK_ID}", result.next_actions)
        self.assertIn(f"team status {TEAM_TASK_ID}", result.next_actions)
        self.assertFalse(any("team." in item for item in result.next_actions))

    def test_accepted_proposal_is_observed_without_dispatch(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        with patch("subprocess.run") as subprocess_run, patch("os.system") as os_system:
            result = self.application.create(PROPOSAL_ID)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "planning")
        self.assertEqual(result.data["progress"], MISSION_PROGRESS["proposal_accepted"])
        self.assertNotIn("team_task_id", result.data)
        subprocess_run.assert_not_called()
        os_system.assert_not_called()

    def test_consumed_proposal_without_plan_event_does_not_invent_approval(self) -> None:
        self.save_proposal()
        mission = self.service.create(PROPOSAL_ID).mission
        self.assertEqual(mission.status, MissionStatus.PLANNING)
        self.assertEqual(mission.stage, MissionStage.TEAM_PLANNING)
        self.assertEqual(mission.progress, MISSION_PROGRESS["proposal_consumed"])
        self.assertEqual(mission.next_action, "")
        self.assertEqual(mission.link("team_task").subject_id, TEAM_TASK_ID)
        replayed = MissionProjectionEngine().project(
            mission,
            (event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED),),
        )
        self.assertEqual(replayed.proposal_status, "consumed")
        json.dumps(replayed.to_dict(), allow_nan=False)

    def test_created_event_only_preserves_noneligible_projection_baseline(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        mission = self.service.create(PROPOSAL_ID).mission
        baseline = replace(
            mission,
            proposal_status="pending",
            status=MissionStatus.CREATED,
            stage=MissionStage.PROPOSAL,
            progress=MISSION_PROGRESS["created"],
            current_action="",
            next_action="",
        )
        projected = MissionProjectionEngine().project(
            baseline,
            (event(1, EventTypes.GOAL_PROPOSAL_CREATED),),
        )
        self.assertEqual(projected.status, MissionStatus.CREATED)
        self.assertEqual(projected.progress, MISSION_PROGRESS["created"])
        self.assertEqual(len(projected.event_refs), 1)

    def test_rejected_expired_pending_and_hash_invalid_proposals_are_refused(self) -> None:
        for index, status in enumerate((
            GoalProposalStatus.PENDING,
            GoalProposalStatus.REJECTED,
            GoalProposalStatus.EXPIRED,
        ), 1):
            proposal_id = f"proposal-{index:032x}"
            with self.subTest(status=status.value):
                self.proposals.save(make_proposal(status, proposal_id=proposal_id, team_task_id=""))
                result = self.application.create(proposal_id)
                self.assertFalse(result.ok)
        invalid_id = f"proposal-{9:032x}"
        invalid = make_proposal(GoalProposalStatus.ACCEPTED, proposal_id=invalid_id, team_task_id="")
        object.__setattr__(invalid, "plan_hash", "0" * 64)
        self.proposals.save(invalid)
        self.assertFalse(self.application.create(invalid_id).ok)

    def test_reconciliation_updates_stale_mission_and_restart_loads_it(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        created = self.service.create(PROPOSAL_ID).mission
        self.events.append(event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED))
        self.events.append(event(4, EventTypes.TEAM_PLAN_CREATED))
        reconciled = self.service.reconcile(created.mission_id)
        self.assertTrue(reconciled.changed)
        self.assertEqual(reconciled.mission.status, MissionStatus.AWAITING_APPROVAL)

        restarted = MissionService(
            MissionRepository(self.repository.root, forbidden_root=self.install_root),
            GoalProposalRepository(self.proposals.root, forbidden_root=self.install_root),
            EventStore(self.events.root, forbidden_root=self.install_root),
            clock=self.clock,
        )
        loaded = restarted.get(created.mission_id)
        self.assertEqual(loaded.status, MissionStatus.AWAITING_APPROVAL)
        self.assertFalse(restarted.reconcile(created.mission_id).changed)

    def test_projection_ignores_unrelated_and_deduplicates_out_of_order_events(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        mission = self.service.create(PROPOSAL_ID).mission
        related = event(4, EventTypes.TEAM_PLAN_CREATED, occurred_at=NOW_TEXT)
        accepted = event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED, occurred_at=NOW_TEXT)
        unrelated = event(
            6,
            EventTypes.TEAM_PLAN_CREATED,
            goal_id="goal-unrelated",
            team_task_id="team-unrelated",
            occurred_at=NOW_TEXT,
        )
        malformed = replace(
            event(8, EventTypes.TEAM_PLAN_CREATED, occurred_at=NOW_TEXT),
            data={"team_task_id": "team-payload-mismatch", "goal": mission.goal_text},
        )
        projected = MissionProjectionEngine().project(
            mission,
            (related, unrelated, malformed, accepted, related),
        )
        self.assertEqual(projected.status, MissionStatus.AWAITING_APPROVAL)
        self.assertEqual(len(projected.event_refs), 2)
        self.assertEqual(projected.links[-1].subject_id, TEAM_TASK_ID)

    def test_failed_event_is_terminal_without_retry_action(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        mission = self.service.create(PROPOSAL_ID).mission
        failed = event(7, EventTypes.GOAL_PROPOSAL_FAILED)
        projected = MissionProjectionEngine().project(mission, (failed,))
        self.assertEqual(projected.status, MissionStatus.FAILED)
        self.assertEqual(projected.stage, MissionStage.FAILED)
        self.assertEqual(projected.next_action, "")

    def test_creation_and_reconciliation_write_only_mission_storage(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        self.events.append(event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED))
        proposal_before = {
            path.name: path.read_bytes() for path in self.proposals.root.glob("*.json")
        }
        events_before = {
            path.name: path.read_bytes() for path in self.events.root.glob("*.jsonl")
        }
        workspace = self.root / "workspace"
        workspace.mkdir()
        marker = workspace / "unchanged.txt"
        marker.write_text("unchanged", encoding="utf-8")
        with patch("subprocess.run") as subprocess_run, patch("os.system") as os_system:
            created = self.service.create(PROPOSAL_ID).mission
            self.service.reconcile(created.mission_id)
        self.assertEqual(
            proposal_before,
            {path.name: path.read_bytes() for path in self.proposals.root.glob("*.json")},
        )
        self.assertEqual(
            events_before,
            {path.name: path.read_bytes() for path in self.events.root.glob("*.jsonl")},
        )
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        subprocess_run.assert_not_called()
        os_system.assert_not_called()


class MissionHistoryValidationTests(MissionFixture):
    def test_history_is_bounded_newest_first_and_does_not_republish(self) -> None:
        self.save_proposal()
        chain = self.append_chain()
        ignored = event(8, EventTypes.GOAL_PLAN_CREATED)
        self.events.append(ignored)
        mission = self.service.create(PROPOSAL_ID).mission
        before = sum(path.stat().st_size for path in self.events.root.glob("*.jsonl"))
        events, warnings = self.service.history(mission.mission_id, limit=2)
        after = sum(path.stat().st_size for path in self.events.root.glob("*.jsonl"))
        self.assertFalse(warnings)
        self.assertEqual([item.event_id for item in events], [chain[-1].event_id, chain[-2].event_id])
        self.assertNotIn(ignored.event_id, {item.event_id for item in events})
        self.assertEqual(before, after)

    def test_validation_detects_stale_projection_and_reconcile_is_explicit(self) -> None:
        self.save_proposal(make_proposal(GoalProposalStatus.ACCEPTED, team_task_id=""))
        mission = self.service.create(PROPOSAL_ID).mission
        self.events.append(event(3, EventTypes.GOAL_PROPOSAL_ACCEPTED))
        self.events.append(event(4, EventTypes.TEAM_PLAN_CREATED))
        validation = self.service.validate(mission.mission_id)
        self.assertFalse(validation.valid)
        self.assertFalse(validation.projection_matches)
        self.assertEqual(self.repository.get(mission.mission_id).status, MissionStatus.PLANNING)
        self.service.reconcile(mission.mission_id)
        self.assertTrue(self.service.validate(mission.mission_id).valid)

    def test_unavailable_event_history_preserves_persisted_projection(self) -> None:
        self.save_proposal()
        self.append_chain()
        mission = self.service.create(PROPOSAL_ID).mission

        class UnavailableStore:
            history_max_limit = 1_000

            @staticmethod
            def history(**_filters):
                raise OSError("history offline")

        unavailable = MissionService(
            self.repository,
            self.proposals,
            UnavailableStore(),
            clock=self.clock,
        )
        reconciled = unavailable.reconcile(mission.mission_id)
        self.assertEqual(reconciled.mission.status, MissionStatus.AWAITING_APPROVAL)
        self.assertIn("unavailable", reconciled.mission.warnings[-1])
        self.assertFalse(unavailable.validate(mission.mission_id).valid)


class MissionCliTests(MissionFixture):
    def test_all_supported_cli_commands_delegate_without_domain_execution(self) -> None:
        self.save_proposal()
        self.append_chain()
        runtime = SimpleNamespace(mission_application=self.application)
        output: list[str] = []
        adapter = MissionCliAdapter(runtime, output_provider=output.append)
        created = adapter.handle(f"create {PROPOSAL_ID}")
        mission_id = str(created.data["mission_id"])
        for command in (
            f"show {mission_id}",
            "list --status awaiting_approval",
            f"history {mission_id} --limit 3",
            f"validate {mission_id}",
            f"reconcile {mission_id}",
        ):
            with self.subTest(command=command):
                self.assertTrue(adapter.handle(command).ok)
        self.assertTrue(output)

    def test_router_delegates_only_mission_family_and_completions_are_present(self) -> None:
        self.save_proposal()
        runtime = SimpleNamespace(mission_application=self.application)
        with redirect_stdout(StringIO()):
            self.assertTrue(CommandRouter(runtime).handle("mission list"))
        with redirect_stdout(StringIO()):
            self.assertTrue(dispatch_mission(runtime, "mission list"))
        self.assertFalse(dispatch_mission(runtime, "goal plan test"))
        for command in (
            "mission create",
            "mission show",
            "mission list",
            "mission history",
            "mission validate",
            "mission reconcile",
        ):
            self.assertIn(command, BASE_COMMANDS)


if __name__ == "__main__":
    unittest.main()
