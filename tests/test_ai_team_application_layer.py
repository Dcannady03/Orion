import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.application.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    default_capability_registry,
)
from orion.application.commands.ai_team_cli import (
    AiTeamCliAdapter,
    dispatch_ai_team,
)
from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamApprovalRequest,
    TeamImplementationRequest,
    TeamPlanRequest,
    TeamRollbackRequest,
    TeamRunRequest,
    TeamTaskRequest,
    team_run_lifecycle_data,
    team_run_next_actions,
    team_run_stage,
    team_task_interface_actions,
    team_task_next_actions,
)
from orion.application.interface_actions import cli_command_for_action
from orion.application.results import ApplicationResult
from orion.core.router import CommandRouter
from orion.services.team import TeamTask
from tests.test_command_center_workflow import WorkflowFixture


NOW = "2026-07-30T12:00:00+00:00"


def make_task(status="awaiting_approval", *, task_id="team-application-001"):
    return TeamTask(
        task_id=task_id,
        goal="Prepare Orion for release",
        status=status,
        final_plan=["Inspect boundaries", "Run the complete test suite"],
        created_at=NOW,
        updated_at=NOW,
        error="planning_error" if status == "failed" else "",
    )


class FakeTeam:
    def __init__(self, task=None, *, plan_error=None):
        self.persisted = task or make_task()
        self.plan_error = plan_error
        self.plan_calls = []

    def plan(self, goal, **kwargs):
        self.plan_calls.append((goal, kwargs))
        if self.plan_error is not None:
            raise self.plan_error
        self.persisted.goal = goal
        return self.persisted

    def task(self, task_id):
        if task_id != self.persisted.task_id:
            raise FileNotFoundError(f"AI Team task not found: {task_id}")
        return self.persisted

    def recent(self, limit=10):
        return [self.persisted][:limit]


class FakeValidation:
    def __init__(self, status):
        self.status = status
        self.tester_requested = "codex"
        self.tester_resolved = "codex"
        self.fallback_reason = ""
        self.checks = ()
        self.safe_diagnostics = ()
        self.checks_passed = ()
        self.warnings = ()
        self.checks_failed = ("tests",) if status == "failed" else ()
        self.skipped_checks = ()

    @property
    def review_status(self):
        return {
            "passed": "Awaiting Review — Validation Passed",
            "failed": "Awaiting Review — Validation Failed",
        }[self.status]

    def to_dict(self):
        return {"status": self.status, "checks": []}


class FakeDocumentation:
    def __init__(self, status):
        self.status = status
        self.reviewer_requested = "openai:test"
        self.reviewer_resolved = "openai:test"
        self.fallback_reason = ""
        self.documents_inspected = ()
        self.counts_by_severity = {
            "info": 0,
            "warning": 0,
            "error": 1 if status == "failed" else 0,
        }
        self.findings = ()
        self.safe_diagnostics = ()

    @property
    def review_status(self):
        return {
            "passed": "Documentation Passed",
            "failed": "Documentation Failed",
        }[self.status]

    def to_dict(self):
        return {"status": self.status, "findings": []}


def make_run(
    status="awaiting_review",
    *,
    validation=None,
    documentation=None,
    changes=None,
):
    workspace = SimpleNamespace(
        mode="standard",
        is_git_repository=False,
        git_root="",
        branch="",
        commit="",
    )
    return SimpleNamespace(
        run_id="run-application-001",
        team_task_id="team-application-001",
        approval_id="approval-application-001",
        plan_hash="a" * 64,
        workspace_root=str(Path.cwd().resolve()),
        workspace=workspace,
        status=status,
        result=None,
        changes=changes,
        validation=validation,
        validation_history=("validation/validation-001.json",)
        if validation is not None else (),
        documentation=documentation,
        documentation_history=("documentation/docs-001.json",)
        if documentation is not None else (),
        error="execution_error" if status == "failed" else "",
        started_at=NOW,
        completed_at=NOW if status != "executing" else "",
    )


class FakeBridge:
    def __init__(self, run=None):
        self.persisted = run or make_run()
        self.workspace_capabilities = self.persisted.workspace
        self.execute = Mock()
        self.approve = Mock()

    def run(self, run_id):
        if run_id != self.persisted.run_id:
            raise FileNotFoundError(f"AI Team run not found: {run_id}")
        return self.persisted

    def latest_validatable_run(self):
        return self.persisted

    def latest_documentable_run(self):
        return self.persisted

    def validate(self, run_id):
        return self.persisted

    def document(self, run_id):
        return self.persisted

    def execution_context(self, *args):
        raise ValueError("Approval was not found.")

    def rollback(self, run_id):
        self.persisted.status = "rolled_back"
        return self.persisted


class AiTeamCapabilityTests(unittest.TestCase):
    def test_team_capabilities_are_stable_deterministic_and_json_safe(self):
        registry = default_capability_registry()
        identifiers = [
            item.capability_id
            for item in registry.list()
            if item.capability_id.startswith("team.")
        ]
        self.assertEqual(identifiers, sorted(identifiers))
        self.assertEqual(identifiers, [
            "team.approve",
            "team.documentation_review",
            "team.implement",
            "team.list",
            "team.plan",
            "team.rollback",
            "team.show",
            "team.sync",
            "team.validate",
        ])
        self.assertNotIn("team.cancel", identifiers)
        self.assertNotIn("team.review", identifiers)
        json.dumps(registry.to_dict())

    def test_mutation_and_approval_metadata_matches_real_boundaries(self):
        registry = default_capability_registry()
        self.assertFalse(registry.lookup("team.show").mutates_state)
        self.assertFalse(registry.lookup("team.list").mutates_state)
        self.assertTrue(registry.lookup("team.plan").mutates_state)
        self.assertFalse(registry.lookup("team.plan").requires_approval)
        for identifier in ("team.approve", "team.implement", "team.rollback"):
            with self.subTest(identifier=identifier):
                definition = registry.lookup(identifier)
                self.assertTrue(definition.mutates_state)
                self.assertTrue(definition.requires_approval)
        self.assertIn(
            "workspace.write",
            registry.lookup("team.implement").required_permissions,
        )
        self.assertNotIn(
            "workspace.write",
            registry.lookup("team.validate").required_permissions,
        )

    def test_registry_still_rejects_duplicate_team_ids(self):
        definition = CapabilityDefinition(
            "team.example",
            "Example",
            False,
            False,
        )
        with self.assertRaises(ValueError):
            CapabilityRegistry((definition, definition))


class AiTeamHandlerTests(unittest.TestCase):
    def test_plan_returns_json_safe_semantic_lifecycle_data(self):
        team = FakeTeam()
        handler = AiTeamApplicationHandler(SimpleNamespace(team=team))

        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            result = handler.plan(TeamPlanRequest("Prepare Orion for release"))

        self.assertIsInstance(result, ApplicationResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["team_task_id"], "team-application-001")
        self.assertEqual(result.data["status"], "awaiting_approval")
        self.assertEqual(result.data["stage"], "awaiting_approval")
        self.assertTrue(result.data["approval_required"])
        self.assertIn("team approve team-application-001", result.next_actions)
        self.assertIn("team status team-application-001", result.next_actions)
        self.assertNotIn("team.approve", " ".join(result.next_actions))
        self.assertNotIn("team.show", " ".join(result.next_actions))
        self.assertEqual(
            [item["capability_id"] for item in result.data["interface_actions"]],
            ["team.approve", "team.show"],
        )
        json.loads(result.to_json())
        self.assertEqual(team.plan_calls, [("Prepare Orion for release", {})])

    def test_list_and_show_return_structured_tasks(self):
        team = FakeTeam()
        handler = AiTeamApplicationHandler(SimpleNamespace(team=team))

        listing = handler.list()
        shown = handler.show_task(TeamTaskRequest("team-application-001"))

        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(
            listing.data["tasks"][0]["team_task_id"],
            "team-application-001",
        )
        self.assertEqual(shown.data["goal"], "Prepare Orion for release")
        self.assertIn("AI Team Plan", shown.message)

    def test_provider_or_agent_resolution_failure_is_mapped(self):
        team = FakeTeam(plan_error=ValueError("No viable provider route is available."))
        result = AiTeamApplicationHandler(
            SimpleNamespace(team=team)
        ).plan(TeamPlanRequest("Plan safely"))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_type"], "ValueError")
        self.assertIn("No viable provider route", result.message)

    def test_invalid_task_and_run_ids_return_failures(self):
        runtime = SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=FakeBridge(),
        )
        handler = AiTeamApplicationHandler(runtime)
        self.assertFalse(handler.show_task(TeamTaskRequest("")).ok)
        self.assertFalse(handler.show_run(TeamRunRequest("")).ok)
        self.assertFalse(
            handler.show_run(TeamRunRequest("run-does-not-exist")).ok
        )

    def test_plan_hash_mismatch_never_calls_approval_service(self):
        bridge = FakeBridge()
        handler = AiTeamApplicationHandler(SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=bridge,
        ))
        result = handler.approve(TeamApprovalRequest(
            "team-application-001",
            plan_sha256="0" * 64,
        ))
        self.assertFalse(result.ok)
        self.assertIn("does not match", result.message)
        bridge.approve.assert_not_called()

    def test_implementation_before_approval_is_refused(self):
        bridge = FakeBridge()
        engines = SimpleNamespace(require_codex=lambda: object())
        handler = AiTeamApplicationHandler(SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=bridge,
            execution_engines=engines,
        ))
        result = handler.implement(TeamImplementationRequest(
            "team-application-001",
            "approval-missing-001",
        ))
        self.assertFalse(result.ok)
        self.assertIn("Approval was not found", result.message)
        bridge.execute.assert_not_called()

    def test_validation_and_documentation_results_keep_review_failures_semantic(self):
        failed_validation = make_run(validation=FakeValidation("failed"))
        validation_handler = AiTeamApplicationHandler(SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=FakeBridge(failed_validation),
        ))
        validation = validation_handler.validate(
            TeamRunRequest(failed_validation.run_id)
        )
        self.assertTrue(validation.ok)
        self.assertEqual(validation.data["validation_status"], "failed")
        self.assertEqual(validation.data["stage"], "documentation_review")

        failed_docs = make_run(
            validation=FakeValidation("passed"),
            documentation=FakeDocumentation("failed"),
        )
        documentation_handler = AiTeamApplicationHandler(SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=FakeBridge(failed_docs),
        ))
        documentation = documentation_handler.documentation_review(
            TeamRunRequest(failed_docs.run_id)
        )
        self.assertTrue(documentation.ok)
        self.assertEqual(
            documentation.data["documentation_review_status"],
            "failed",
        )
        self.assertEqual(documentation.data["stage"], "final_review")
        self.assertNotIn("team.approve", " ".join(documentation.next_actions))

    def test_rollback_requires_confirmation_and_terminal_result_has_no_implement(self):
        run = make_run()
        bridge = FakeBridge(run)
        handler = AiTeamApplicationHandler(SimpleNamespace(
            team=FakeTeam(),
            codex_bridge=bridge,
        ))
        refused = handler.rollback(TeamRollbackRequest(run.run_id))
        self.assertFalse(refused.ok)
        self.assertEqual(run.status, "awaiting_review")

        completed = handler.rollback(
            TeamRollbackRequest(run.run_id, confirmed=True)
        )
        self.assertTrue(completed.ok)
        self.assertEqual(completed.data["status"], "rolled_back")
        self.assertNotIn("team.implement", " ".join(completed.next_actions))

    def test_run_payload_is_json_safe_and_contains_no_live_objects(self):
        run = make_run(
            validation=FakeValidation("passed"),
            documentation=FakeDocumentation("passed"),
        )
        data = team_run_lifecycle_data(run)
        encoded = json.dumps(data)
        self.assertIn("run-application-001", encoded)
        self.assertEqual(data["stage"], "final_review")
        self.assertEqual(data["review_status"], "awaiting_review")


class AiTeamLifecycleTests(unittest.TestCase):
    def test_task_next_actions_follow_persisted_status(self):
        self.assertEqual(
            team_task_next_actions(make_task("planning")),
            ("team status team-application-001",),
        )
        self.assertEqual(
            team_task_next_actions(make_task("awaiting_approval"))[0],
            "team approve team-application-001",
        )
        self.assertNotIn(
            "team implement",
            " ".join(team_task_next_actions(make_task("failed"))),
        )
        semantic = team_task_interface_actions(make_task("awaiting_approval"))
        self.assertEqual(
            tuple(action.capability_id for action in semantic),
            ("team.approve", "team.show"),
        )

    def test_run_stage_and_next_actions_cover_review_transitions(self):
        cases = (
            (make_run("executing"), "executing"),
            (make_run(), "validation"),
            (
                make_run(validation=FakeValidation("passed")),
                "documentation_review",
            ),
            (
                make_run(
                    validation=FakeValidation("passed"),
                    documentation=FakeDocumentation("passed"),
                ),
                "final_review",
            ),
            (make_run("rolled_back"), "rolled_back"),
        )
        for run, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(team_run_stage(run), expected)
        rolled_back = cases[-1][0]
        self.assertEqual(
            team_run_next_actions(rolled_back),
            ("team run run-application-001",),
        )
        failed = make_run("failed", changes=SimpleNamespace())
        self.assertIn(
            "team rollback run-application-001",
            team_run_next_actions(failed),
        )

    def test_review_next_actions_are_supported_cli_commands(self):
        actions = team_run_next_actions(make_run())
        self.assertEqual(actions, (
            "team run run-application-001",
            "team test run-application-001",
            "team docs run-application-001",
            "team rollback run-application-001",
        ))
        self.assertFalse(any("team." in action for action in actions))

    def test_capability_to_cli_mapping_is_explicit_and_context_aware(self):
        cases = (
            ("team.list", {}, "team"),
            ("team.show", {"team_task_id": "team-example"}, "team status team-example"),
            ("team.show", {"run_id": "run-example"}, "team run run-example"),
            ("team.plan", {}, 'team plan "<goal>"'),
            ("team.approve", {"team_task_id": "team-example"}, "team approve team-example"),
            (
                "team.implement",
                {"team_task_id": "team-example", "approval_id": "approval-example"},
                "team implement team-example approval-example",
            ),
            ("team.validate", {"run_id": "run-example"}, "team test run-example"),
            (
                "team.documentation_review",
                {"run_id": "run-example"},
                "team docs run-example",
            ),
            ("team.rollback", {"run_id": "run-example"}, "team rollback run-example"),
            ("team.sync", {"team_task_id": "team-example"}, None),
        )
        for capability_id, context, expected in cases:
            with self.subTest(capability_id=capability_id, context=context):
                self.assertEqual(
                    cli_command_for_action(capability_id, context),
                    expected,
                )

    def test_final_plan_display_normalizes_generated_numbering_only(self):
        task = make_task()
        task.final_plan = [
            "1. Audit current code",
            "2) Implement cleanup",
            "3 - Run tests",
            "Step 4: Document boundary",
            "",
        ]
        numbered = AiTeamApplicationHandler(
            SimpleNamespace(team=FakeTeam(task))
        ).show_task(TeamTaskRequest(task.task_id)).message
        self.assertIn("  1. Audit current code", numbered)
        self.assertIn("  2. Implement cleanup", numbered)
        self.assertIn("  3. Run tests", numbered)
        self.assertIn("  4. Document boundary", numbered)
        self.assertIn("  5. (empty step)", numbered)
        self.assertNotIn("1. 1.", numbered)
        self.assertNotIn("2. 2)", numbered)

        task.final_plan = ["Audit current code", "Run tests"]
        unnumbered = AiTeamApplicationHandler(
            SimpleNamespace(team=FakeTeam(task))
        ).show_task(TeamTaskRequest(task.task_id)).message
        self.assertIn("  1. Audit current code", unnumbered)
        self.assertIn("  2. Run tests", unnumbered)


class AiTeamBoundaryTests(unittest.TestCase):
    def test_mapped_next_actions_are_recognized_by_the_team_cli_parser(self):
        application = Mock()
        ok = ApplicationResult.success("recognized")
        for name in (
            "list",
            "plan",
            "show_task",
            "approve",
            "implement",
            "show_run",
            "validate",
            "documentation_review",
            "rollback_preview",
        ):
            getattr(application, name).return_value = ok
        runtime = SimpleNamespace(team_application=application)
        adapter = AiTeamCliAdapter(
            runtime,
            input_provider=lambda _prompt: "n",
            output_provider=lambda _line: None,
        )
        commands = (
            "team",
            'team plan "goal"',
            "team status team-example",
            "team approve team-example",
            "team implement team-example approval-example",
            "team run run-example",
            "team test run-example",
            "team docs run-example",
            "team rollback run-example",
        )
        for command in commands:
            with self.subTest(command=command):
                payload = command[len("team"):].strip()
                result = adapter.handle(payload)
                self.assertNotIn("not recognized", result.message.lower())

        self.assertFalse(dispatch_ai_team(runtime, "team.show team-example"))
        self.assertFalse(dispatch_ai_team(runtime, "team.approve team-example"))

    def test_cli_adapter_preserves_manual_plan_syntax_without_prompting(self):
        output = []
        adapter = AiTeamCliAdapter(
            SimpleNamespace(team=FakeTeam()),
            interactive_approval=True,
            input_provider=lambda _: (_ for _ in ()).throw(
                AssertionError("must not prompt")
            ),
            output_provider=output.append,
        )
        result = adapter.handle('plan --manual "Prepare a release"')
        self.assertTrue(result.ok)
        rendered = "\n".join(output)
        self.assertIn("AI Team Plan", rendered)
        self.assertIn("Goal: Prepare a release", rendered)
        self.assertIn("Approve this exact plan with: team approve", rendered)

    def test_router_only_delegates_the_team_family(self):
        router = CommandRouter(SimpleNamespace())
        with patch("orion.core.router.dispatch_ai_team", return_value=True) as dispatch:
            self.assertTrue(router.handle("team status team-application-001"))
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[1], "team status team-application-001")

    def test_command_center_launch_uses_team_application_when_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = WorkflowFixture().build(tmp)
            job = WorkflowFixture.create_job(service, tmp)
            application = Mock()

            def plan(request):
                task = team.plan(
                    request.goal,
                    selected_agents=list(request.selected_agents),
                    provider=request.provider,
                    model=request.model,
                    task_id=request.task_id,
                )
                return ApplicationResult.success(
                    "",
                    data={
                        "team_task_id": task.task_id,
                        "status": task.status,
                        "stage": task.status,
                    },
                )

            application.plan.side_effect = plan
            integration.team_application = application

            launched = integration.launch(job.job_id)

            self.assertEqual(launched.team_task_id, team.plan_calls[0]["task_id"])
            request = application.plan.call_args.args[0]
            self.assertIsInstance(request, TeamPlanRequest)
            self.assertEqual(request.task_id, launched.team_task_id)


if __name__ == "__main__":
    unittest.main()
