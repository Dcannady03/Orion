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
from orion.application.commands.ai_team_cli import AiTeamCliAdapter
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
    team_task_next_actions,
)
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
        self.assertIn("team.approve team-application-001", result.next_actions)
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
            ("team.show team-application-001",),
        )
        self.assertEqual(
            team_task_next_actions(make_task("awaiting_approval"))[0],
            "team.approve team-application-001",
        )
        self.assertNotIn(
            "team.implement",
            " ".join(team_task_next_actions(make_task("failed"))),
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
            ("team.show run-application-001",),
        )
        failed = make_run("failed", changes=SimpleNamespace())
        self.assertIn(
            "team.rollback run-application-001",
            team_run_next_actions(failed),
        )


class AiTeamBoundaryTests(unittest.TestCase):
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
