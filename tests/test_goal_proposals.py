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

from orion.application.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    default_capability_registry,
)
from orion.application.commands.ai_team_commands import TeamPlanRequest
from orion.application.commands.goal_cli import dispatch_goal
from orion.application.commands.goal_proposal_cli import GoalProposalCliAdapter
from orion.application.goals import GoalEngine, GoalRequest
from orion.application.goals.proposals import (
    CreateGoalProposalRequest,
    GoalProposalAcceptance,
    GoalProposalApplicationHandler,
    GoalProposalRejection,
    GoalProposalRepository,
    GoalProposalService,
    GoalProposalStatus,
    GoalProposalTranslator,
    canonical_json,
    proposal_plan_hash,
    registry_fingerprint,
)
from orion.application.goals.proposals.handler import (
    GoalProposalReferenceRequest,
    ListGoalProposalsRequest,
)
from orion.application.goals.proposals.service import GoalProposalError
from orion.application.results import ApplicationResult
from orion.core.paths import OrionPaths
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mutations = 0

    def set_workspace(self, _root: Path) -> None:
        self.mutations += 1
        raise AssertionError("Proposal operations must not change workspaces.")

    def refresh_capabilities(self) -> None:
        self.mutations += 1
        raise AssertionError("Proposal validation must not refresh workspaces.")


class _CommandCenter:
    def __init__(self, names: tuple[str, ...] = ("Engineering",)) -> None:
        self.items = tuple(
            SimpleNamespace(
                department_id=name.casefold().replace(" ", "-"),
                name=name,
                enabled=True,
            )
            for name in names
        )
        self.reads = 0
        self.mutations = 0

    def departments(self):
        self.reads += 1
        return self.items

    def create_job(self, *_args, **_kwargs):
        self.mutations += 1
        raise AssertionError("Proposal operations must not create jobs.")


class _TeamApplication:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[TeamPlanRequest] = []

    def plan(self, request: TeamPlanRequest) -> ApplicationResult:
        self.requests.append(request)
        if self.fail:
            return ApplicationResult.failure(
                "AI Team planning failed safely.",
                data={"status": "failed"},
                errors=("provider unavailable",),
            )
        return ApplicationResult.success(
            "AI Team plan awaits its separate approval.",
            data={
                "team_task_id": "team-task-proposal-test",
                "status": "awaiting_approval",
                "stage": "planning",
                "approval_required": True,
                "approval_status": "pending",
            },
            next_actions=("team approve team-task-proposal-test",),
        )


class GoalProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspace"
        self.workspace_root.mkdir()
        self.install_root = self.root / "application"
        self.install_root.mkdir()
        self.storage_root = self.root / "user-data" / "goals" / "proposals"
        self.workspace = _Workspace(self.workspace_root)
        self.command_center = _CommandCenter()
        self.team_application = _TeamApplication()
        self.clock = _Clock()
        self.registry = default_capability_registry()
        self.engine = GoalEngine(
            self.registry,
            workspace_manager=self.workspace,
            project_context=SimpleNamespace(
                workspace_root=self.workspace_root,
                initialized=False,
            ),
            command_center=self.command_center,
        )
        self.repository = GoalProposalRepository(
            self.storage_root,
            forbidden_root=self.install_root,
        )
        ids = iter(
            f"proposal-{letter * 32}"
            for letter in "abcdef0123456789"
        )
        self.service = GoalProposalService(
            self.repository,
            self.registry,
            translator=GoalProposalTranslator(),
            team_application=self.team_application,
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
            id_factory=lambda: next(ids),
        )
        self.application = GoalProposalApplicationHandler(
            self.engine,
            self.service,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def plan(self, text: str = "Prepare Orion for release."):
        return self.engine.plan(GoalRequest(text))

    def create(self, text: str = "Prepare Orion for release."):
        return self.service.create(self.plan(text))

    def test_creation_persists_immutable_json_without_execution(self) -> None:
        proposal = self.create()

        self.assertEqual(proposal.status, GoalProposalStatus.PENDING)
        self.assertEqual(proposal.version, 1)
        self.assertEqual(proposal.current.capability_id, "team.plan")
        self.assertEqual(
            [item.capability_id for item in proposal.steps],
            [
                "team.plan",
                "team.implement",
                "team.validate",
                "team.documentation_review",
            ],
        )
        self.assertEqual(
            proposal.current.application_request_type,
            "TeamPlanRequest",
        )
        self.assertEqual(proposal.current.resolved_inputs["goal"], proposal.goal_text)
        self.assertEqual(proposal_plan_hash(proposal.snapshot()), proposal.plan_hash)
        self.assertEqual(len(proposal.registry_fingerprint), 64)
        self.assertEqual(len(proposal.capability_fingerprint), 64)
        self.assertTrue((self.storage_root / f"{proposal.proposal_id}.json").is_file())
        self.assertEqual(self.team_application.requests, [])
        self.assertEqual(self.workspace.mutations, 0)
        self.assertEqual(self.command_center.mutations, 0)

        payload = json.loads(proposal.to_json())
        self.assertEqual(payload["proposal_id"], proposal.proposal_id)
        with self.assertRaises(FrozenInstanceError):
            proposal.status = GoalProposalStatus.CONSUMED  # type: ignore[misc]

    def test_canonical_hashing_is_stable_across_mapping_order(self) -> None:
        left = {"b": 2, "a": {"d": 4, "c": 3}}
        right = {"a": {"c": 3, "d": 4}, "b": 2}

        self.assertEqual(canonical_json(left), canonical_json(right))
        proposal = self.create()
        reloaded = self.repository.get(proposal.proposal_id)
        self.assertEqual(
            proposal_plan_hash(reloaded.snapshot()),
            proposal.plan_hash,
        )

    def test_repository_save_load_duplicate_missing_and_ordering(self) -> None:
        first = self.create()
        self.clock.advance(minutes=1)
        second = self.create()

        self.assertEqual(self.repository.get(first.proposal_id), first)
        with self.assertRaises(FileExistsError):
            self.repository.save(first)
        with self.assertRaises(FileNotFoundError):
            self.repository.get(f"proposal-{'f' * 32}")
        listed = self.repository.list()
        self.assertEqual(
            [item.proposal_id for item in listed],
            [second.proposal_id, first.proposal_id],
        )

    def test_record_lock_fails_closed_for_overlapping_process_update(self) -> None:
        proposal = self.create()
        lock_path = self.storage_root / f".{proposal.proposal_id}.json.lock"
        lock_path.write_text("other-process\n", encoding="utf-8")

        timestamp = self.clock().isoformat().replace("+00:00", "Z")
        replacement = replace(
            proposal,
            status=GoalProposalStatus.REJECTED,
            updated_at=timestamp,
            rejected_at=timestamp,
            rejected_by="operator",
        )
        with self.assertRaisesRegex(PermissionError, "already being updated"):
            self.repository.replace(
                replacement,
                expected_status=GoalProposalStatus.PENDING,
            )
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.PENDING,
        )

    def test_repository_rejects_malformed_json_and_repository_path(self) -> None:
        proposal = self.create()
        path = self.storage_root / f"{proposal.proposal_id}.json"
        path.write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.repository.get(proposal.proposal_id)
        with self.assertRaisesRegex(ValueError, "application repository"):
            GoalProposalRepository(
                self.install_root / "runtime" / "proposals",
                forbidden_root=self.install_root,
            )

    def test_repository_rejects_symlinked_storage_root(self) -> None:
        target = self.root / "proposal-target"
        target.mkdir()
        link = self.root / "proposal-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are not available.")

        repository = GoalProposalRepository(
            link,
            forbidden_root=self.install_root,
        )
        with self.assertRaisesRegex(PermissionError, "symlink"):
            repository.save(self.create())

    def test_orion_paths_use_external_goal_proposal_location(self) -> None:
        paths = OrionPaths(
            install_root=self.install_root,
            user_root=self.root / "external-user",
        )

        self.assertEqual(
            paths.goal_proposals,
            (self.root / "external-user" / "goals" / "proposals").resolve(),
        )
        self.assertFalse(
            str(paths.goal_proposals).startswith(str(paths.install_root))
        )

    def test_creation_rejects_stale_or_missing_capability_metadata(self) -> None:
        plan = self.plan()
        stale_step = replace(
            plan.capability_steps[0],
            required_permissions=("invented.permission",),
        )
        stale_plan = replace(
            plan,
            capability_steps=(stale_step, *plan.capability_steps[1:]),
        )
        with self.assertRaisesRegex(
            GoalProposalError,
            "metadata is stale",
        ):
            self.service.create(stale_plan)

        missing_service = GoalProposalService(
            self.repository,
            CapabilityRegistry(),
            translator=GoalProposalTranslator(),
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
        )
        with self.assertRaisesRegex(
            GoalProposalError,
            "no longer registered",
        ):
            missing_service.create(plan)

    def test_expiry_override_is_bounded_and_default_is_24_hours(self) -> None:
        proposal = self.create()
        created = datetime.fromisoformat(proposal.created_at)
        expires = datetime.fromisoformat(proposal.expires_at)
        self.assertEqual(expires - created, timedelta(hours=24))

        for value in (0, -1, 169, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.service.create(self.plan(), expiry_hours=value)  # type: ignore[arg-type]

    def test_pending_proposal_validation_is_read_only_and_valid(self) -> None:
        proposal = self.create()
        before = self.repository.get(proposal.proposal_id).to_json()

        validation = self.service.validate(proposal.proposal_id)

        self.assertTrue(validation.valid)
        self.assertEqual(validation.validation_status, "valid")
        self.assertTrue(validation.plan_hash_valid)
        self.assertTrue(validation.capability_fingerprint_valid)
        self.assertTrue(validation.workspace_valid)
        self.assertTrue(validation.department_valid)
        self.assertTrue(validation.inputs_valid)
        self.assertTrue(validation.translation_supported)
        self.assertEqual(
            self.repository.get(proposal.proposal_id).to_json(),
            before,
        )
        self.assertEqual(self.team_application.requests, [])
        self.assertEqual(self.workspace.mutations, 0)

    def test_expired_proposal_is_safely_marked_and_cannot_be_accepted(self) -> None:
        proposal = self.create()
        self.clock.advance(hours=25)

        validation = self.service.validate(proposal.proposal_id)

        self.assertFalse(validation.valid)
        self.assertEqual(validation.validation_status, "expired")
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.EXPIRED,
        )
        with self.assertRaises(GoalProposalError):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                True,
            ))
        self.assertEqual(self.team_application.requests, [])

    def test_hash_mismatch_marks_proposal_invalid(self) -> None:
        proposal = self.create()
        path = self.storage_root / f"{proposal.proposal_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["plan_hash"] = "0" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")

        validation = self.service.validate(proposal.proposal_id)

        self.assertFalse(validation.plan_hash_valid)
        self.assertEqual(validation.validation_status, "invalid")
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.INVALID,
        )

    def test_selected_capability_change_invalidates_but_unrelated_change_warns(self) -> None:
        proposal = self.create()
        definitions = list(self.registry.list())
        changed = []
        for item in definitions:
            if item.capability_id == "team.plan":
                changed.append(CapabilityDefinition(
                    item.capability_id,
                    item.description,
                    item.mutates_state,
                    True,
                    item.required_permissions,
                    item.input_schema,
                    item.output_schema,
                ))
            else:
                changed.append(item)
        changed_service = GoalProposalService(
            self.repository,
            CapabilityRegistry(tuple(changed)),
            translator=GoalProposalTranslator(),
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
        )

        invalid = changed_service.validate(proposal.proposal_id)
        self.assertFalse(invalid.capability_fingerprint_valid)
        self.assertEqual(invalid.validation_status, "invalid")

        # Use a fresh pending proposal for the harmless full-registry addition.
        self.repository = GoalProposalRepository(
            self.root / "other-user" / "goals" / "proposals"
        )
        clean_service = GoalProposalService(
            self.repository,
            self.registry,
            translator=GoalProposalTranslator(),
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
            id_factory=lambda: f"proposal-{'9' * 32}",
        )
        clean = clean_service.create(self.plan())
        extra = CapabilityDefinition(
            "extra.read",
            "Read an unrelated external summary.",
            False,
            False,
        )
        expanded_service = GoalProposalService(
            self.repository,
            CapabilityRegistry((*self.registry.list(), extra)),
            translator=GoalProposalTranslator(),
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
        )
        warning = expanded_service.validate(clean.proposal_id)
        self.assertTrue(warning.valid)
        self.assertTrue(warning.registry_fingerprint_changed)
        self.assertTrue(warning.warnings)

    def test_missing_workspace_and_department_block_without_execution(self) -> None:
        proposal = self.create()
        self.workspace.root = self.root / "different-workspace"
        self.command_center.items = ()

        validation = self.service.validate(proposal.proposal_id)

        self.assertFalse(validation.valid)
        self.assertFalse(validation.workspace_valid)
        self.assertFalse(validation.department_valid)
        self.assertEqual(validation.validation_status, "blocked")
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.PENDING,
        )
        self.assertEqual(self.team_application.requests, [])

    def test_missing_current_input_is_invalid(self) -> None:
        proposal = self.create()
        path = self.storage_root / f"{proposal.proposal_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["steps"][0]["resolved_inputs"] = {}
        provisional = type(proposal).from_value({
            **payload,
            "plan_hash": proposal.plan_hash,
        })
        payload["plan_hash"] = proposal_plan_hash(provisional.snapshot())
        path.write_text(json.dumps(payload), encoding="utf-8")

        validation = self.service.validate(proposal.proposal_id)

        self.assertFalse(validation.inputs_valid)
        self.assertEqual(validation.validation_status, "invalid")

    def test_acceptance_requires_confirmation_and_exact_hash(self) -> None:
        proposal = self.create()
        with self.assertRaisesRegex(GoalProposalError, "confirmation"):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                False,
            ))
        with self.assertRaisesRegex(GoalProposalError, "hash"):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                "0" * 64,
                True,
            ))
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.PENDING,
        )
        self.assertEqual(self.team_application.requests, [])

    def test_acceptance_translates_exact_team_request_and_consumes_once(self) -> None:
        proposal = self.create()

        dispatch = self.service.accept(GoalProposalAcceptance(
            proposal.proposal_id,
            proposal.plan_hash,
            True,
            accepted_by="operator",
        ))

        self.assertEqual(dispatch.proposal.status, GoalProposalStatus.CONSUMED)
        self.assertEqual(dispatch.proposal.accepted_by, "operator")
        self.assertEqual(dispatch.proposal.current.status.value, "consumed")
        self.assertEqual(len(self.team_application.requests), 1)
        request = self.team_application.requests[0]
        self.assertIsInstance(request, TeamPlanRequest)
        self.assertEqual(request.goal, proposal.goal_text)
        self.assertEqual(
            dispatch.application_result.data["approval_status"],
            "pending",
        )
        self.assertTrue(dispatch.application_result.data["approval_required"])

        with self.assertRaises(GoalProposalError):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                True,
            ))
        self.assertEqual(len(self.team_application.requests), 1)

    def test_downstream_failure_is_terminal_and_not_retried(self) -> None:
        self.service.team_application = _TeamApplication(fail=True)
        proposal = self.create()

        dispatch = self.service.accept(GoalProposalAcceptance(
            proposal.proposal_id,
            proposal.plan_hash,
            True,
        ))

        self.assertEqual(dispatch.proposal.status, GoalProposalStatus.FAILED)
        self.assertEqual(dispatch.proposal.failure_code, "dispatch_failed")
        self.assertFalse(dispatch.proposal.retry_eligible)
        self.assertEqual(len(self.service.team_application.requests), 1)
        with self.assertRaises(GoalProposalError):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                True,
            ))
        self.assertEqual(len(self.service.team_application.requests), 1)

    def test_unsupported_translation_remains_pending_and_blocked(self) -> None:
        custom = CapabilityRegistry((
            CapabilityDefinition(
                "crew.plan",
                "Plan a bounded team goal.",
                True,
                False,
                input_schema={
                    "type": "object",
                    "properties": {"goal": {"type": "string"}},
                    "required": ["goal"],
                },
                output_schema={"type": "object"},
            ),
        ))
        engine = GoalEngine(
            custom,
            workspace_manager=self.workspace,
            command_center=self.command_center,
        )
        repository = GoalProposalRepository(
            self.root / "unsupported" / "goals" / "proposals"
        )
        service = GoalProposalService(
            repository,
            custom,
            translator=GoalProposalTranslator(),
            team_application=self.team_application,
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
            id_factory=lambda: f"proposal-{'8' * 32}",
        )
        proposal = service.create(engine.plan(GoalRequest("Plan a roadmap.")))

        validation = service.validate(proposal.proposal_id)

        self.assertFalse(validation.translation_supported)
        self.assertEqual(validation.validation_status, "blocked")
        self.assertEqual(
            repository.get(proposal.proposal_id).status,
            GoalProposalStatus.PENDING,
        )
        with self.assertRaises(GoalProposalError):
            service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                True,
            ))
        self.assertEqual(self.team_application.requests, [])

    def test_rejection_preserves_history_and_prevents_acceptance(self) -> None:
        proposal = self.create()

        rejected = self.service.reject(GoalProposalRejection(
            proposal.proposal_id,
            rejected_by="operator",
            reason="Wrong workspace",
        ))

        self.assertEqual(rejected.status, GoalProposalStatus.REJECTED)
        self.assertEqual(rejected.rejection_reason, "Wrong workspace")
        self.assertEqual(
            self.repository.get(proposal.proposal_id).status,
            GoalProposalStatus.REJECTED,
        )
        with self.assertRaises(GoalProposalError):
            self.service.reject(GoalProposalRejection(proposal.proposal_id))
        with self.assertRaises(GoalProposalError):
            self.service.accept(GoalProposalAcceptance(
                proposal.proposal_id,
                proposal.plan_hash,
                True,
            ))
        self.assertEqual(self.team_application.requests, [])

    def test_versions_never_overwrite_and_explicit_supersession_blocks_old(self) -> None:
        first = self.create()
        second = self.create()
        self.assertNotEqual(first.proposal_id, second.proposal_id)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(
            self.repository.get(first.proposal_id).status,
            GoalProposalStatus.PENDING,
        )

        third = self.service.create(
            self.plan(),
            supersedes=first.proposal_id,
        )

        old = self.repository.get(first.proposal_id)
        self.assertEqual(third.version, 3)
        self.assertEqual(third.supersedes, first.proposal_id)
        self.assertEqual(old.status, GoalProposalStatus.SUPERSEDED)
        self.assertEqual(old.superseded_by, third.proposal_id)
        with self.assertRaises(GoalProposalError):
            self.service.accept(GoalProposalAcceptance(
                first.proposal_id,
                first.plan_hash,
                True,
            ))
        self.assertEqual(len(self.repository.list(goal_id=first.goal_id)), 3)

    def test_application_handler_returns_structured_lifecycle_results(self) -> None:
        created = self.application.create(CreateGoalProposalRequest(
            GoalRequest("Prepare Orion for release.")
        ))
        self.assertTrue(created.ok)
        proposal_id = str(created.data["proposal"]["proposal_id"])

        shown = self.application.show(GoalProposalReferenceRequest(proposal_id))
        listed = self.application.list(ListGoalProposalsRequest(status="pending"))
        validated = self.application.validate(
            GoalProposalReferenceRequest(proposal_id)
        )
        rejected = self.application.reject(GoalProposalRejection(
            proposal_id,
            reason="Review again",
        ))

        for result in (shown, listed, validated, rejected):
            self.assertIsInstance(result, ApplicationResult)
            self.assertTrue(result.ok)
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(rejected.data["proposal"]["status"], "rejected")

    def test_application_accept_preserves_downstream_result_and_approval(self) -> None:
        proposal = self.create()

        result = self.application.accept(GoalProposalAcceptance(
            proposal.proposal_id,
            proposal.plan_hash,
            True,
        ))

        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "consumed")
        self.assertEqual(result.data["dispatched_capability_count"], 1)
        self.assertEqual(
            result.data["application_result"]["data"]["approval_status"],
            "pending",
        )
        self.assertIn(
            "not AI Team implementation approval",
            result.message,
        )

    def test_cli_create_list_validate_reject_and_accept_confirmation(self) -> None:
        runtime = SimpleNamespace(
            goal_proposal_application=self.application,
        )
        output: list[str] = []
        def confirm(prompt: str) -> str:
            output.append(prompt)
            return "y"
        adapter = GoalProposalCliAdapter(
            runtime,
            input_provider=confirm,
            output_provider=output.append,
        )

        created = adapter.handle('create "Prepare Orion for release"')
        proposal_id = str(created.data["proposal"]["proposal_id"])
        self.assertTrue(adapter.handle("list --status pending").ok)
        self.assertTrue(adapter.handle(f"show {proposal_id}").ok)
        self.assertTrue(adapter.handle(f"validate {proposal_id}").ok)
        accepted = adapter.handle(f"accept {proposal_id}")
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.data["status"], "consumed")
        self.assertEqual(len(self.team_application.requests), 1)
        self.assertIn("Accept this proposal", "\n".join(output))

        second = adapter.handle('create "Prepare Orion for release"')
        second_id = str(second.data["proposal"]["proposal_id"])
        rejected = adapter.handle(
            f'reject {second_id} --reason "Wrong workspace"'
        )
        self.assertTrue(rejected.ok)
        self.assertEqual(rejected.data["proposal"]["status"], "rejected")

    def test_cli_no_confirmation_dispatches_nothing_and_router_dispatches_only(self) -> None:
        proposal = self.create()
        runtime = SimpleNamespace(
            goal_proposal_application=self.application,
        )
        output: list[str] = []
        adapter = GoalProposalCliAdapter(
            runtime,
            input_provider=lambda _prompt: "n",
            output_provider=output.append,
        )

        declined = adapter.handle(f"accept {proposal.proposal_id}")
        self.assertTrue(declined.ok)
        self.assertEqual(declined.data["dispatched_capability_count"], 0)
        self.assertEqual(self.team_application.requests, [])

        captured = StringIO()
        with redirect_stdout(captured):
            handled = dispatch_goal(runtime, "goal proposal list")
        self.assertTrue(handled)
        self.assertIn("Goal Proposals", captured.getvalue())
        for command in (
            "goal proposal create",
            "goal proposal show",
            "goal proposal list",
            "goal proposal validate",
            "goal proposal accept",
            "goal proposal reject",
        ):
            self.assertIn(command, BASE_COMMANDS)

    def test_registry_fingerprint_is_deterministic(self) -> None:
        first = registry_fingerprint(self.registry)
        second = registry_fingerprint(
            CapabilityRegistry(tuple(reversed(self.registry.list())))
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
