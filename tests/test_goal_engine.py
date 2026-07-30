from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
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
from orion.application.commands.goal_cli import GoalCliAdapter, dispatch_goal
from orion.application.goals import (
    CapabilityStep,
    GoalApplicationHandler,
    GoalEngine,
    GoalPlanningError,
    GoalRequest,
)
from orion.application.results import ApplicationResult
from orion.ui.console import BASE_COMMANDS


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mutations = 0

    def set_workspace(self, _root: Path) -> None:
        self.mutations += 1
        raise AssertionError("Goal Engine must not change workspaces.")

    def refresh_capabilities(self) -> None:
        self.mutations += 1
        raise AssertionError("Goal Engine must not refresh mutable state.")


class _ProjectContext:
    def __init__(self, root: Path, *, initialized: bool = True) -> None:
        self.workspace_root = root
        self.initialized = initialized

    def project(self) -> dict[str, object]:
        return {"name": "Orion Test", "workspace": str(self.workspace_root)}

    def bind(self, _root: Path) -> None:
        raise AssertionError("Goal Engine must not bind project context.")


class _CommandCenter:
    def __init__(self, *names: str) -> None:
        self.items = tuple(
            SimpleNamespace(
                department_id=name.casefold().replace(" ", "-"),
                name=name,
                enabled=True,
            )
            for name in names
        )
        self.reads = 0

    def departments(self):
        self.reads += 1
        return self.items

    def create_job(self, *_args, **_kwargs):
        raise AssertionError("Goal Engine must not create jobs.")


class GoalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def engine(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        departments: tuple[str, ...] = (
            "Engineering",
            "Marketing",
            "Business",
            "Automation",
        ),
    ) -> GoalEngine:
        return GoalEngine(
            registry or default_capability_registry(),
            workspace_manager=_Workspace(self.root),
            project_context=_ProjectContext(self.root),
            command_center=_CommandCenter(*departments),
        )

    def test_deterministic_classification_covers_goal_categories(self) -> None:
        examples = (
            ("Build a Valheim mod that rewards stone.", "Engineering"),
            ("Create a marketing campaign for Orion.", "Marketing"),
            ("Update the README documentation.", "Documentation"),
            ("Research and compare local AI models.", "Research"),
            ("Automate this recurring workflow.", "Automation"),
            ("Perform a security vulnerability audit.", "Security"),
            ("Improve infrastructure monitoring and uptime.", "Operations"),
            ("Prepare a strategy and roadmap.", "Planning"),
            ("Review this project and prepare it for release.", "Release"),
            ("Review today's calendar and inbox.", "Personal Productivity"),
        )
        for goal, category in examples:
            with self.subTest(goal=goal):
                result = self.engine().classify(goal)
                self.assertEqual(result.category, category)
                self.assertGreaterEqual(result.confidence, 0.0)
                self.assertLessEqual(result.confidence, 1.0)
                self.assertTrue(result.matched_terms)

    def test_unknown_goal_has_useful_deterministic_error(self) -> None:
        with self.assertRaisesRegex(
            GoalPlanningError,
            "could not be classified",
        ):
            self.engine().classify("Please do the thing.")

    def test_release_plan_resolves_context_and_registry_capabilities(self) -> None:
        workspace = _Workspace(self.root)
        command_center = _CommandCenter("Engineering")
        engine = GoalEngine(
            default_capability_registry(),
            workspace_manager=workspace,
            project_context=_ProjectContext(self.root),
            command_center=command_center,
        )

        plan = engine.plan(GoalRequest("Prepare Orion for release."))

        self.assertEqual(plan.context.workspace, str(self.root.resolve()))
        self.assertEqual(plan.context.workspace_source, "active_workspace")
        self.assertEqual(plan.context.workspace_mode, "bound")
        self.assertEqual(plan.context.project_name, "Orion Test")
        self.assertEqual(plan.context.department_name, "Engineering")
        self.assertEqual(
            [step.capability_id for step in plan.capability_steps],
            [
                "team.plan",
                "team.implement",
                "team.validate",
                "team.documentation_review",
            ],
        )
        self.assertTrue(plan.approval_required)
        self.assertEqual(
            plan.estimated_stages,
            (
                "planning",
                "approval",
                "implementation",
                "validation",
                "documentation_review",
            ),
        )
        self.assertEqual(workspace.mutations, 0)
        self.assertEqual(command_center.reads, 1)

    def test_explicit_workspace_does_not_change_active_workspace(self) -> None:
        active = self.root / "active"
        explicit = self.root / "explicit"
        active.mkdir()
        explicit.mkdir()
        workspace = _Workspace(active)
        engine = GoalEngine(
            default_capability_registry(),
            workspace_manager=workspace,
            project_context=_ProjectContext(active),
            command_center=_CommandCenter("Engineering"),
        )

        plan = engine.plan(GoalRequest(
            "Build a project feature.",
            workspace=str(explicit),
        ))

        self.assertEqual(plan.context.workspace, str(explicit.resolve()))
        self.assertEqual(plan.context.workspace_source, "explicit")
        self.assertEqual(workspace.root, active)
        self.assertEqual(workspace.mutations, 0)

    def test_project_context_resolves_without_active_manager(self) -> None:
        engine = GoalEngine(
            default_capability_registry(),
            project_context=_ProjectContext(self.root),
            command_center=_CommandCenter("Business"),
        )

        plan = engine.plan(GoalRequest("Research project alternatives."))

        self.assertEqual(plan.context.workspace_source, "project_context")
        self.assertEqual(plan.context.department_name, "Business")

    def test_missing_workspace_returns_application_failure(self) -> None:
        application = GoalApplicationHandler(GoalEngine(
            default_capability_registry(),
            command_center=_CommandCenter("Engineering"),
        ))

        result = application.plan(GoalRequest("Build a project feature."))

        self.assertIsInstance(result, ApplicationResult)
        self.assertFalse(result.ok)
        self.assertIn("No workspace could be resolved", result.message)
        self.assertIs(result.data["planning_only"], True)

    def test_unknown_department_fails_instead_of_inventing_one(self) -> None:
        application = GoalApplicationHandler(self.engine(
            departments=("Engineering",),
        ))

        result = application.plan(GoalRequest(
            "Build a project feature.",
            department="Imaginary",
        ))

        self.assertFalse(result.ok)
        self.assertIn("Department not found or disabled", result.message)
        self.assertIn("Engineering", result.message)

    def test_unmatched_department_remains_unassigned(self) -> None:
        plan = self.engine(
            departments=("Marketing",),
        ).plan(GoalRequest("Prepare a release."))

        self.assertEqual(plan.context.department_id, "")
        self.assertEqual(plan.context.department_name, "")
        self.assertTrue(
            any("remains unassigned" in item for item in plan.warnings)
        )

    def test_discovery_uses_registry_metadata_not_fixed_ids(self) -> None:
        def definition(
            capability_id: str,
            description: str,
            *,
            approval: bool = False,
        ) -> CapabilityDefinition:
            return CapabilityDefinition(
                capability_id,
                description,
                mutates_state=True,
                requires_approval=approval,
                input_schema={"type": "object", "required": ["goal"]},
                output_schema={
                    "type": "object",
                    "properties": {"artifact": {"type": "string"}},
                },
            )

        registry = CapabilityRegistry((
            definition("crew.plan", "Plan a bounded team goal."),
            definition(
                "workspace.implement",
                "Implement approved workspace work.",
                approval=True,
            ),
            definition("quality.validate", "Validate tests read-only."),
            definition(
                "quality.documentation-review",
                "Documentation review read-only.",
            ),
        ))

        plan = self.engine(registry=registry).plan(
            GoalRequest("Prepare a release.")
        )

        self.assertEqual(
            [item.capability_id for item in plan.capability_steps],
            [
                "crew.plan",
                "workspace.implement",
                "quality.validate",
                "quality.documentation-review",
            ],
        )
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.capability_steps[0].required_inputs, ("goal",))
        self.assertEqual(plan.capability_steps[0].expected_outputs, ("artifact",))

    def test_no_matching_capability_is_safe_failure(self) -> None:
        application = GoalApplicationHandler(self.engine(
            registry=CapabilityRegistry(),
        ))

        result = application.plan(GoalRequest("Prepare a release."))

        self.assertFalse(result.ok)
        self.assertIn("No registered Orion capabilities", result.message)
        self.assertIs(result.data["planning_only"], True)

    def test_models_are_immutable_and_json_serializable(self) -> None:
        plan = self.engine().plan(GoalRequest("Prepare Orion for release."))
        payload = json.loads(plan.to_json())

        self.assertTrue(payload["goal_id"].startswith("goal-"))
        self.assertEqual(payload["estimated_capabilities"], 4)
        self.assertIs(
            payload["execution_preview"]["informational_only"],
            True,
        )
        self.assertIs(
            payload["capability_steps"][1]["requires_approval"],
            True,
        )
        with self.assertRaises(FrozenInstanceError):
            plan.goal = "changed"  # type: ignore[misc]
        step = CapabilityStep(
            1,
            "team.plan",
            "reason",
            False,
            False,
            "planning",
        )
        with self.assertRaises(FrozenInstanceError):
            step.reason = "changed"  # type: ignore[misc]

    def test_application_views_return_application_results(self) -> None:
        application = GoalApplicationHandler(self.engine())
        request = GoalRequest("Prepare Orion for release.")

        for command in (
            "plan",
            "explain",
            "preview",
            "capabilities",
            "classify",
            "validate",
        ):
            with self.subTest(command=command):
                result = getattr(application, command)(request)
                self.assertIsInstance(result, ApplicationResult)
                self.assertTrue(result.ok)
                self.assertIs(result.data["planning_only"], True)

    def test_ai_flag_is_future_ready_but_never_invoked(self) -> None:
        plan = self.engine().plan(GoalRequest(
            "Build a project feature.",
            allow_ai_planning=True,
        ))

        self.assertTrue(
            any("not enabled in v0.8.2" in item for item in plan.warnings)
        )
        self.assertIn("deterministic planner", " ".join(plan.warnings))

    def test_requested_outcome_contributes_to_classification(self) -> None:
        result = self.engine().classify(GoalRequest(
            "Review this project.",
            requested_outcome="Prepare a release.",
        ))

        self.assertEqual(result.category, "Release")

    def test_goal_cli_parses_and_renders_without_execution(self) -> None:
        output: list[str] = []
        command_center = _CommandCenter("Engineering")
        runtime = SimpleNamespace(
            capability_registry=default_capability_registry(),
            workspace_manager=_Workspace(self.root),
            project_context=_ProjectContext(self.root),
            command_center=command_center,
        )
        adapter = GoalCliAdapter(runtime, output_provider=output.append)

        result = adapter.handle(
            'preview "Prepare Orion for release" --department Engineering'
        )

        self.assertTrue(result.ok)
        self.assertIs(result.data["planning_only"], True)
        self.assertIn(
            "Preview only. No capability was executed.",
            "\n".join(output),
        )
        self.assertEqual(command_center.reads, 1)

    def test_goal_cli_rejects_execution_modes(self) -> None:
        output: list[str] = []
        runtime = SimpleNamespace(
            workspace_manager=_Workspace(self.root),
            project_context=_ProjectContext(self.root),
            command_center=_CommandCenter("Engineering"),
        )
        result = GoalCliAdapter(runtime, output_provider=output.append).handle(
            'plan "Prepare a release" --execution-mode execute'
        )

        self.assertFalse(result.ok)
        self.assertIn("cannot execute work", result.message)

    def test_dispatch_and_completions_expose_command_family(self) -> None:
        runtime = SimpleNamespace(
            workspace_manager=_Workspace(self.root),
            project_context=_ProjectContext(self.root),
            command_center=_CommandCenter("Engineering"),
        )
        output = StringIO()
        with redirect_stdout(output):
            handled = dispatch_goal(
                runtime,
                'goal classify "Analyze project"',
            )

        self.assertTrue(handled)
        self.assertFalse(dispatch_goal(runtime, "team plan something"))
        self.assertIn("Goal Classification", output.getvalue())
        for command in (
            "goal plan",
            "goal explain",
            "goal preview",
            "goal capabilities",
            "goal classify",
            "goal validate",
        ):
            self.assertIn(command, BASE_COMMANDS)


if __name__ == "__main__":
    unittest.main()
