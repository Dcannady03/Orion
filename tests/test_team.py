import json
import tempfile
import unittest
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
                "Send plan to Engineer Review",
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

    def test_roles_report_resolved_provider_and_reserved_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, _, _, _ = self.build(tmp)
            roles = {role.name: role for role in team.roles()}
            self.assertEqual(roles["architect"].provider, "openai")
            self.assertEqual(roles["reviewer"].provider, "ollama")
            self.assertTrue(roles["engineer"].active)
            self.assertFalse(roles["reviewer"].active)


class TeamRouterTests(unittest.TestCase):
    def test_quoted_team_plan_goal_is_unwrapped_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = TeamOrchestratorTests()
            team, _, _, _ = helper.build(tmp)
            orion = SimpleNamespace(team=team)
            router = CommandRouter(orion)
            with patch("builtins.print") as output:
                self.assertTrue(router.handle('team plan "Add OpenAI image generation"'))
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("AI Team Plan", rendered)
            self.assertIn("Goal: Add OpenAI image generation", rendered)
            self.assertIn("Status: Awaiting Approval", rendered)
            self.assertIn("No implementation has been performed", rendered)

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
