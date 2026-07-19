import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.team import (
    TEAM_STATUS_AWAITING_APPROVAL,
    TEAM_STATUS_FAILED,
    TeamOrchestrator,
    TeamPlanningError,
    TeamTaskStore,
)


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeProvider:
    def __init__(self, model, response):
        self.model = model
        self.response = response
        self.calls = []

    def chat(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        return self.response

    def select_model(self, model):
        self.model = model


class FakeFactory:
    def __init__(self, providers):
        self.providers = providers
        self.created = []

    def create(self, provider):
        self.created.append(provider)
        return self.providers[provider]


def role_json(summary, recommendations, risks=None, next_action="Await approval"):
    return json.dumps({
        "summary": summary,
        "recommendations": recommendations,
        "risks": risks or [],
        "next_action": next_action,
    })


class TeamOrchestratorTests(unittest.TestCase):
    def build(self, root, *, architect_response=None, engineer_response=None, values=None):
        defaults = {
            "team.enabled": True,
            "team.roles.architect.provider": "openai",
            "team.roles.architect.model": "configured-default",
            "team.roles.engineer.provider": "gemini",
            "team.roles.engineer.model": "configured-default",
            "team.roles.reviewer.provider": "configured-default",
            "team.roles.reviewer.model": "configured-default",
            "providers.default": "ollama",
            "providers.openai.model": "gpt-test",
            "providers.gemini.model": "gemini-test",
            "providers.ollama.model": "local-test",
            "team.pricing.openai.input_per_million": 1.0,
            "team.pricing.openai.output_per_million": 2.0,
            "team.pricing.gemini.input_per_million": 0.5,
            "team.pricing.gemini.output_per_million": 1.5,
        }
        defaults.update(values or {})
        architect = FakeProvider(
            "gpt-test",
            architect_response or role_json(
                "Architecture ready",
                ["Add a provider-neutral service", "Add persistence tests"],
                ["Keep provider details isolated"],
                "Send plan to Engineering Reviewer",
            ),
        )
        engineer = FakeProvider(
            "gemini-test",
            engineer_response or role_json(
                "Plan reviewed and consolidated",
                ["Define the interface", "Implement the backend", "Add bounded tests"],
                ["Add timeout limits"],
            ),
        )
        factory = FakeFactory({"openai": architect, "gemini": engineer, "ollama": architect})
        now = lambda: datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        orchestrator = TeamOrchestrator(
            FlatConfig(defaults),
            TeamTaskStore(Path(root) / "user" / "team" / "tasks"),
            factory,
            now=now,
            id_factory=lambda: "team-test-001",
        )
        return orchestrator, factory, architect, engineer

    def persisted_task(self, root):
        team, _, _, _ = self.build(root)
        task = team.plan("Plan a feature")
        path = team.store.root / f"{task.task_id}.json"
        return team, path, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_task(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_plan_calls_two_distinct_roles_and_persists_consolidated_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, factory, architect, engineer = self.build(tmp)
            task = team.plan("Add OpenAI image generation")

            self.assertEqual(factory.created, ["openai", "gemini"])
            self.assertEqual(len(architect.calls), 1)
            self.assertEqual(len(engineer.calls), 1)
            self.assertIn("Architecture ready", engineer.calls[0][0])
            self.assertIn("Planning only", architect.calls[0][1])
            self.assertIn("do not modify code", engineer.calls[0][1])
            self.assertEqual(task.status, TEAM_STATUS_AWAITING_APPROVAL)
            self.assertEqual(task.final_plan, ["Define the interface", "Implement the backend", "Add bounded tests"])
            self.assertEqual(len(task.artifacts), 2)
            self.assertEqual(len(task.messages), 3)
            self.assertEqual(len(task.usage), 2)
            self.assertEqual(len(task.role_assignments), 5)
            self.assertEqual(task.artifacts[1].role, "engineer_reviewer")
            self.assertIsNotNone(task.artifacts[0].role_metadata)
            self.assertEqual(
                task.artifacts[0].role_metadata.actual_assignment,
                "openai:gpt-test",
            )
            self.assertGreater(task.total_tokens, 0)
            self.assertIsNotNone(task.estimated_cost_usd)
            self.assertTrue((Path(tmp) / "user" / "team" / "tasks" / "team-test-001.json").exists())

            restored = team.task(task.task_id)
            self.assertEqual(restored.to_dict(), task.to_dict())

    def test_markdown_fenced_json_is_accepted_but_still_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            fenced = "```json\n" + role_json("Plan", ["Step one"]) + "\n```"
            team, _, _, _ = self.build(tmp, architect_response=fenced)
            task = team.plan("Plan a feature")
            self.assertEqual(task.artifact("architect").output.summary, "Plan")

    def test_invalid_role_output_stops_after_first_call_and_persists_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, factory, _, engineer = self.build(tmp, architect_response="not json")
            with self.assertRaises(TeamPlanningError) as raised:
                team.plan("Plan a feature")
            self.assertEqual(raised.exception.task_id, "team-test-001")
            self.assertEqual(factory.created, ["openai"])
            self.assertEqual(engineer.calls, [])
            failed = team.task("team-test-001")
            self.assertEqual(failed.status, TEAM_STATUS_FAILED)
            self.assertIn("Architect role failed", failed.error)
            self.assertNotIn("not json", failed.error)

    def test_explicit_role_model_is_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, architect, _ = self.build(tmp, values={
                "team.roles.architect.model": "architect-special",
            })
            task = team.plan("Plan a feature")
            self.assertEqual(architect.model, "architect-special")
            self.assertEqual(task.usage[0].model, "architect-special")

    def test_unknown_cloud_pricing_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, _, _ = self.build(tmp, values={
                "team.pricing.openai.input_per_million": "not-a-rate",
                "team.pricing.openai.output_per_million": "not-a-rate",
            })
            task = team.plan("Plan a feature")
            self.assertIsNone(task.usage[0].estimated_cost_usd)
            self.assertIsNone(task.estimated_cost_usd)

    def test_disabled_team_and_invalid_task_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, _, _ = self.build(tmp, values={"team.enabled": False})
            with self.assertRaises(ValueError):
                team.plan("Plan a feature")
            with self.assertRaises(ValueError):
                team.task("../../vault/vault")
            self.assertEqual(team.recent(0), [])

    def test_structured_lists_reject_non_string_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid = json.dumps({
                "summary": "Plan",
                "recommendations": [{"step": "not allowed"}],
                "risks": [],
                "next_action": "Review",
            })
            team, factory, _, _ = self.build(tmp, architect_response=invalid)
            with self.assertRaises(TeamPlanningError):
                team.plan("Plan a feature")
            self.assertEqual(factory.created, ["openai"])

    def test_role_output_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = json.loads(role_json("Plan", ["Step one"]))
            value["unexpected"] = "schema drift"
            team, factory, _, _ = self.build(tmp, architect_response=json.dumps(value))
            with self.assertRaises(TeamPlanningError):
                team.plan("Plan a feature")
            self.assertEqual(factory.created, ["openai"])

    def test_persisted_task_rejects_unknown_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, path, value = self.persisted_task(tmp)
            value["status"] = "implementing"
            self.write_task(path, value)
            with self.assertRaises(ValueError):
                team.task("team-test-001")

    def test_persisted_task_rejects_missing_or_invalid_identity_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, path, valid = self.persisted_task(tmp)
            mutations = {
                "missing task_id": lambda value: value.pop("task_id"),
                "empty goal": lambda value: value.update(goal="  "),
                "identity mismatch": lambda value: value.update(task_id="team-other-001"),
                "invalid timestamp": lambda value: value.update(created_at="yesterday"),
                "timezone-naive timestamp": lambda value: value.update(updated_at="2026-07-17T12:00:00"),
                "reversed timestamps": lambda value: value.update(updated_at="2026-07-16T12:00:00+00:00"),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    value = deepcopy(valid)
                    mutate(value)
                    self.write_task(path, value)
                    with self.assertRaises(ValueError):
                        team.task("team-test-001")

    def test_persisted_task_rejects_malformed_messages_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, path, valid = self.persisted_task(tmp)
            mutations = {
                "message missing content": lambda value: value["messages"][0].pop("content"),
                "message unknown field": lambda value: value["messages"][0].update(extra=True),
                "negative token count": lambda value: value["usage"][0].update(input_tokens=-1),
                "non-finite cost": lambda value: value["usage"][0].update(estimated_cost_usd=float("inf")),
                "usage unknown field": lambda value: value["usage"][0].update(extra=True),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    value = deepcopy(valid)
                    mutate(value)
                    self.write_task(path, value)
                    with self.assertRaises(ValueError):
                        team.task("team-test-001")

    def test_oversized_role_response_stops_before_engineer_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, factory, _, _ = self.build(tmp, architect_response="x" * 50_001)
            with self.assertRaises(TeamPlanningError):
                team.plan("Plan a feature")
            self.assertEqual(factory.created, ["openai"])

    def test_recent_tasks_skip_corrupt_json_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, _, _ = self.build(tmp)
            team.store.root.mkdir(parents=True)
            (team.store.root / "bad-task.json").write_text('{"messages": [1]}', encoding="utf-8")
            self.assertEqual(team.recent(), [])

    def test_roles_report_all_workflow_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, _, _ = self.build(tmp)
            roles = {role.name: role for role in team.roles()}
            self.assertEqual(roles["architect"].provider, "openai")
            self.assertEqual(roles["engineer_reviewer"].provider, "gemini")
            self.assertEqual(roles["documentation"].provider, "ollama")
            self.assertEqual(roles["implementation"].engine_id, "codex")
            self.assertEqual(roles["tester"].engine_id, "codex")
            self.assertTrue(roles["engineer_reviewer"].active)
            self.assertFalse(roles["documentation"].active)


class TeamRouterTests(unittest.TestCase):
    def test_quoted_team_plan_goal_is_unwrapped_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = TeamOrchestratorTests()
            team, _, _, _ = helper.build(tmp)
            orion = SimpleNamespace(team=team)
            router = CommandRouter(orion)
            with patch("builtins.input", side_effect=AssertionError("must not prompt")), patch(
                "builtins.print"
            ) as output:
                self.assertTrue(router.handle('team plan "Add OpenAI image generation"'))
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("AI Team Plan", rendered)
            self.assertIn("Goal: Add OpenAI image generation", rendered)
            self.assertIn("Status: Awaiting Approval", rendered)
            self.assertIn("No implementation has been performed", rendered)
            self.assertIn("Approve this exact plan with: team approve", rendered)

    def test_manual_team_plan_mode_never_prompts_in_interactive_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = TeamOrchestratorTests()
            team, _, _, _ = helper.build(tmp)
            router = CommandRouter(
                SimpleNamespace(team=team),
                interactive_team_approval=True,
            )
            with patch("builtins.input", side_effect=AssertionError("must not prompt")), patch(
                "builtins.print"
            ) as output:
                router.handle('team plan --manual "Plan for an automated caller"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Goal: Plan for an automated caller", rendered)
            self.assertIn("Approve this exact plan with: team approve", rendered)

    def test_team_status_reopens_persisted_task(self):
        task = Mock(task_id="team-123", goal="Goal", status="awaiting_approval", error="")
        task.artifact.return_value = None
        task.final_plan = ["One step"]
        task.usage = []
        team = Mock()
        team.task.return_value = task
        router = CommandRouter(SimpleNamespace(team=team))
        with patch("builtins.print") as output:
            router.handle("team status team-123")
        team.task.assert_called_once_with("team-123")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Final Plan", rendered)


if __name__ == "__main__":
    unittest.main()
