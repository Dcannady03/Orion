import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from orion.agents import AgentDefinition, AgentPermissions, AgentRegistry, built_in_agents
from orion.core.router import CommandRouter
from orion.services.team import TeamOrchestrator, TeamTaskStore


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeProvider:
    def __init__(self, model="base-model", response=None):
        self.model = model
        self.response = response or json.dumps({
            "summary": "Configuration understood",
            "recommendations": ["Keep the work bounded"],
            "risks": [],
            "next_action": "Await approval",
        })
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


def definition(
    agent_id="security-reviewer",
    *,
    name="Security Reviewer",
    provider="configured-default",
    model="configured-default",
    instructions="Review plans for unsafe defaults and secrets exposure.",
    tools=("read_files", "inspect_diff"),
    enabled=True,
):
    permissions = AgentPermissions.for_tools(tools)
    return AgentDefinition.from_value(agent_id, {
        "name": name,
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "instructions": instructions,
        "tools": list(tools),
        "limits": {"max_turns": 3, "can_modify_files": False},
        "permissions": permissions.to_dict(),
    })


class AgentRegistryTests(unittest.TestCase):
    def registry(self, root, *, values=None, factory=None):
        defaults = {
            "providers.default": "ollama",
            "providers.ollama.model": "local-model",
            "providers.openai.model": "openai-model",
            "providers.gemini.model": "gemini-model",
        }
        defaults.update(values or {})
        return AgentRegistry(Path(root) / "user" / "agents", FlatConfig(defaults), factory)

    def test_round_trip_uses_external_yaml_and_normalized_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp)
            agent = definition("security_reviewer")
            path = registry.save(agent, overwrite=False)

            self.assertEqual(path, Path(tmp) / "user" / "agents" / "security-reviewer.yaml")
            self.assertEqual(registry.load("security_reviewer"), agent)
            stored = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertTrue(stored["permissions"]["filesystem"]["read"])
            self.assertFalse(stored["permissions"]["filesystem"]["write"])
            self.assertFalse(stored["limits"]["can_modify_files"])

    def test_strict_schema_permissions_and_agent_ids_are_validated(self):
        valid = definition().to_dict()
        mutations = {
            "unknown field": lambda value: value.update(unexpected=True),
            "unsupported provider": lambda value: value.update(provider="codex"),
            "permission conflict": lambda value: value["limits"].update(can_modify_files=True),
            "tool without permission": lambda value: value["permissions"]["filesystem"].update(read=False),
            "unknown tool": lambda value: value["tools"].append("future_unreviewed_tool"),
            "arbitrary shell without write": lambda value: value["permissions"]["shell"].update(arbitrary_commands=True),
            "Git mutation without write": lambda value: value["permissions"]["git"].update(push=True),
            "invalid turns": lambda value: value["limits"].update(max_turns=0),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                value = json.loads(json.dumps(valid))
                mutate(value)
                with self.assertRaises(ValueError):
                    AgentDefinition.from_value("security-reviewer", value)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.registry(tmp).load("../../vault/vault")

    def test_built_in_agents_preserve_legacy_assignments_and_user_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = FlatConfig({
                "team.roles.architect.provider": "openai",
                "team.roles.architect.model": "architect-model",
            })
            registry = AgentRegistry(Path(tmp) / "user" / "agents", config)
            registry.ensure_defaults(built_in_agents(config))
            architect = registry.load("architect")
            self.assertEqual((architect.provider, architect.model), ("openai", "architect-model"))
            self.assertEqual(
                {item.agent_id for item in registry.all()},
                {"architect", "engineer", "reviewer"},
            )

            customized = replace(architect, instructions="My persistent custom instructions.")
            registry.save(customized, overwrite=True)
            registry.ensure_defaults(built_in_agents(config))
            self.assertEqual(
                registry.load("architect").instructions,
                "My persistent custom instructions.",
            )

    def test_enable_and_disable_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp)
            registry.save(definition())
            self.assertFalse(registry.set_enabled("security-reviewer", False).enabled)
            self.assertFalse(registry.load("security-reviewer").enabled)
            self.assertTrue(registry.set_enabled("security-reviewer", True).enabled)

    def test_agent_test_is_one_bounded_call_with_no_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider()
            factory = FakeFactory({"openai": provider})
            registry = self.registry(tmp, factory=factory)
            registry.save(definition(
                provider="openai",
                model="security-model",
                instructions="Inspect security assumptions carefully.",
            ))

            result = registry.test("security-reviewer")

            self.assertEqual(factory.created, ["openai"])
            self.assertEqual(provider.model, "security-model")
            self.assertEqual(len(provider.calls), 1)
            self.assertIn("Inspect security assumptions carefully", provider.calls[0][1])
            self.assertIn("No tools are available", provider.calls[0][1])
            self.assertIn("Do not modify files", provider.calls[0][1])
            self.assertEqual(result.response.summary, "Configuration understood")

    def test_agent_test_rejects_disabled_agents_and_schema_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = json.dumps({
                "summary": "Ready",
                "recommendations": ["Plan"],
                "risks": [],
                "next_action": "Wait",
                "unexpected": True,
            })
            provider = FakeProvider(response=response)
            registry = self.registry(tmp, factory=FakeFactory({"ollama": provider}))
            registry.save(definition(tools=()))
            with self.assertRaises(ValueError):
                registry.test("security-reviewer")
            registry.set_enabled("security-reviewer", False)
            with self.assertRaises(ValueError):
                registry.test("security-reviewer")
            registry.set_enabled("security-reviewer", True)
            provider.chat = Mock(side_effect=RuntimeError("secret provider detail"))
            with self.assertRaises(RuntimeError) as raised:
                registry.test("security-reviewer")
            self.assertNotIn("secret provider detail", str(raised.exception))

    def test_team_roles_use_assigned_agents_and_keep_permissions_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = FlatConfig({
                "team.enabled": True,
                "team.roles.architect.agent": "security-reviewer",
                "team.roles.engineer.agent": "test-engineer",
                "team.roles.reviewer.agent": "security-reviewer",
                "providers.default": "ollama",
                "providers.ollama.model": "local-model",
                "providers.openai.model": "openai-model",
                "providers.gemini.model": "gemini-model",
            })
            architect_provider = FakeProvider(response=json.dumps({
                "summary": "Architecture ready",
                "recommendations": ["Design safely"],
                "risks": ["Secrets"],
                "next_action": "Review",
            }))
            engineer_provider = FakeProvider(response=json.dumps({
                "summary": "Review ready",
                "recommendations": ["Design safely", "Add tests"],
                "risks": [],
                "next_action": "Await approval",
            }))
            factory = FakeFactory({"openai": architect_provider, "gemini": engineer_provider})
            registry = AgentRegistry(Path(tmp) / "user" / "agents", config, factory)
            registry.save(definition(
                provider="openai",
                model="security-model",
                instructions="Apply the custom security reviewer instructions.",
            ))
            registry.save(definition(
                "test-engineer",
                name="Test Engineer",
                provider="gemini",
                model="test-model",
                instructions="Require bounded regression coverage.",
                tools=("run_tests",),
            ))
            team = TeamOrchestrator(
                config,
                TeamTaskStore(Path(tmp) / "user" / "team" / "tasks"),
                factory,
                registry,
                now=lambda: datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
                id_factory=lambda: "team-agent-001",
            )

            task = team.plan("Plan an agent-backed feature")

            self.assertEqual(task.final_plan, ["Design safely", "Add tests"])
            self.assertEqual(factory.created, ["openai", "gemini"])
            self.assertIn("custom security reviewer instructions", architect_provider.calls[0][1])
            self.assertIn("no tools are available", architect_provider.calls[0][1].lower())
            self.assertIn("Require bounded regression coverage", engineer_provider.calls[0][1])
            roles = {role.name: role for role in team.roles()}
            self.assertEqual(roles["architect"].agent_id, "security-reviewer")
            self.assertEqual(roles["engineer"].agent_name, "Test Engineer")

            registry.set_enabled("security-reviewer", False)
            with self.assertRaises(ValueError):
                team.roles()

    def test_router_creates_shows_lists_and_disables_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp)
            router = CommandRouter(SimpleNamespace(agents=registry))
            answers = [
                "security-reviewer",
                "Security Reviewer",
                "openai",
                "configured-default",
                "Review secrets and unsafe defaults.",
                "read_files, inspect_diff",
                "3",
            ]
            with patch("builtins.input", side_effect=answers), patch("builtins.print") as output:
                router.handle("agent create")
                router.handle("agent show security-reviewer")
                router.handle("agent disable security-reviewer")
                router.handle("agent list")
            agent = registry.load("security-reviewer")
            self.assertFalse(agent.enabled)
            self.assertTrue(agent.permissions.filesystem.read)
            self.assertFalse(agent.permissions.filesystem.write)
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Created Security Reviewer", rendered)
            self.assertIn("Phase 1 enforcement", rendered)
            self.assertIn("is now disabled", rendered)


if __name__ == "__main__":
    unittest.main()
