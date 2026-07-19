import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from orion.core.config import ConfigManager
from orion.core.router import CommandRouter
from orion.services.execution_engines import ENGINE_STATUS_READY, ExecutionEngine
from orion.services.provider_manager import ProviderStatus
from orion.services.team import TeamOrchestrator, TeamTaskStore
from orion.services.team_roles import ROLE_SPECS, TeamRoleRegistry


class MemoryConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.local_config = {}
        self.save_count = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.save_count += 1


class FakeProviderManager:
    def __init__(self, *, enabled=None, configured=None, models=None, errors=None):
        self.enabled = {key: True for key in ("ollama", "openai", "gemini")}
        self.enabled.update(enabled or {})
        self.configured = {key: True for key in ("ollama", "openai", "gemini")}
        self.configured.update(configured or {})
        self.available_models = {
            "ollama": ["local-test"],
            "openai": ["gpt-test"],
            "gemini": ["gemini-test"],
        }
        self.available_models.update(models or {})
        self.errors = errors or {}

    def statuses(self):
        return [
            ProviderStatus(
                key,
                self.enabled[key],
                self.configured[key],
                key == "openai",
                self.available_models[key][0],
            )
            for key in ("ollama", "openai", "gemini")
        ]

    def models(self, provider):
        if provider in self.errors:
            raise self.errors[provider]
        return list(self.available_models[provider])


class FakeRouting:
    profile = "balanced"

    def __init__(self, order=("openai", "ollama", "gemini")):
        self.order = order

    def provider_order(self, _prompt):
        return self.order


class FakeExecutionEngines:
    def __init__(self, *, ready=True):
        self.ready = ready

    def engine(self, engine_id):
        if engine_id != "codex":
            raise ValueError(f"Execution engine is not recognized: {engine_id}")
        return ExecutionEngine(
            engine_id="codex",
            name="Codex CLI",
            status=ENGINE_STATUS_READY if self.ready else "not_installed",
            installed=self.ready,
            cli_support=True,
            implementation_supported=True,
            executable="C:/tools/codex.cmd" if self.ready else "",
        )

    def status(self):
        return (self.engine("codex"),)


class FakeProvider:
    def __init__(self, model, response, *, error=None):
        self.model = model
        self.response = response
        self.error = error
        self.calls = []

    def select_model(self, model):
        self.model = model

    def chat(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        if self.error is not None:
            raise self.error
        return self.response


class FakeFactory:
    def __init__(self, providers):
        self.providers = providers

    def create(self, provider):
        return self.providers[provider]


def role_json(summary, steps):
    return json.dumps({
        "summary": summary,
        "recommendations": steps,
        "risks": [],
        "next_action": "Await approval",
    })


class TeamRoleRegistryTests(unittest.TestCase):
    def config(self, values=None):
        defaults = {
            "providers.default": "openai",
            "providers.openai.model": "gpt-test",
            "providers.ollama.model": "local-test",
            "providers.gemini.model": "gemini-test",
            "ai.routing.profile": "balanced",
            "team.enabled": True,
        }
        defaults.update(values or {})
        return MemoryConfig(defaults)

    def registry(self, *, config=None, providers=None, engines=None, agents=None):
        return TeamRoleRegistry(
            config or self.config(),
            providers or FakeProviderManager(),
            FakeRouting(),
            engines or FakeExecutionEngines(),
            agents,
        )

    def test_defaults_distinguish_planning_execution_and_validation_roles(self):
        roles = {item.role: item for item in self.registry().roles("Plan safely")}
        self.assertEqual(set(roles), {item.role for item in ROLE_SPECS})
        self.assertEqual(roles["architect"].actual_assignment, "openai:gpt-test")
        self.assertEqual(roles["engineer_reviewer"].display_name, "Engineering Reviewer")
        self.assertEqual(roles["implementation"].actual_assignment, "codex")
        self.assertEqual(roles["tester"].actual_assignment, "codex")
        self.assertEqual(roles["documentation"].actual_assignment, "openai:gpt-test")
        self.assertEqual(roles["architect"].category, "Planning model")
        self.assertEqual(roles["implementation"].category, "Execution engine")
        self.assertIn("Validation role", roles["tester"].category)
        self.assertTrue(all(item.source == "default" for item in roles.values()))

    def test_each_role_can_be_set_and_reset(self):
        config = self.config()
        registry = self.registry(config=config)
        assignments = {
            "architect": "gemini:gemini-test",
            "engineer_reviewer": "ollama:local-test",
            "implementation": "codex",
            "tester": "codex",
            "documentation": "gemini:gemini-test",
        }
        for role, assignment in assignments.items():
            changed = registry.set(role, assignment)
            self.assertEqual(changed.source, "user-configured")
            self.assertEqual(config.values[f"team.assignments.{role}"], assignment)
            reset = registry.reset(role)
            self.assertEqual(reset.requested_assignment, ROLE_SPECS[
                [item.role for item in ROLE_SPECS].index(role)
            ].default_assignment)
        self.assertEqual(config.save_count, 10)

    def test_assignment_persists_in_external_local_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            defaults = root / "application" / "config" / "default.yaml"
            local = root / "user" / "config" / "local.yaml"
            defaults.parent.mkdir(parents=True)
            defaults.write_text(yaml.safe_dump({
                "providers": {
                    "default": "openai",
                    "openai": {"model": "gpt-test"},
                    "ollama": {"model": "local-test"},
                    "gemini": {"model": "gemini-test"},
                },
                "ai": {"routing": {"profile": "balanced"}},
                "team": {"assignments": {
                    item.role: item.default_assignment for item in ROLE_SPECS
                }},
            }, sort_keys=False), encoding="utf-8")
            manager = ConfigManager(defaults, local)
            manager.load()
            registry = self.registry(config=manager)
            registry.set("architect", "gemini:gemini-test")

            saved = local.read_text(encoding="utf-8")
            self.assertIn("architect: gemini:gemini-test", saved)
            self.assertNotIn("api_key", saved.lower())
            reloaded = ConfigManager(defaults, local)
            reloaded.load()
            restored = self.registry(config=reloaded).show("architect")
            self.assertEqual(restored.actual_assignment, "gemini:gemini-test")
            self.assertEqual(restored.source, "user-configured")

    def test_invalid_provider_model_and_unavailable_engine_are_rejected(self):
        registry = self.registry(engines=FakeExecutionEngines(ready=False))
        for assignment in ("unknown:model", "openai:missing-model"):
            with self.assertRaises(ValueError):
                registry.set("architect", assignment)
        with self.assertRaises(ValueError):
            registry.set("implementation", "codex")
        with self.assertRaises(ValueError):
            registry.engine("implementation")

    def test_disabled_provider_and_disabled_agent_cannot_be_assigned(self):
        registry = self.registry(
            providers=FakeProviderManager(enabled={"gemini": False})
        )
        with self.assertRaisesRegex(ValueError, "Provider is disabled"):
            registry.set("architect", "gemini:gemini-test")

        disabled = SimpleNamespace(
            agent_id="architect", name="Architect", enabled=False,
            provider="configured-default", model="configured-default",
        )
        agents = SimpleNamespace(load=lambda _agent_id: disabled)
        with self.assertRaisesRegex(ValueError, "disabled agent"):
            self.registry(agents=agents).planning_candidates("architect", "Plan")

    def test_active_planning_model_reports_existing_routing_fallback(self):
        providers = FakeProviderManager(errors={
            "openai": ConnectionError("credential detail must stay private")
        })
        role = self.registry(providers=providers).show("architect", prompt="Plan")
        self.assertEqual(role.actual_assignment, "ollama:local-test")
        self.assertIn("Balanced provider routing", role.fallback)
        self.assertIn("selected ollama:local-test", role.fallback_reason)
        self.assertNotIn("credential detail", role.fallback_reason)

    def test_planner_and_executor_may_use_different_assignments(self):
        config = self.config({"team.assignments.architect": "gemini:gemini-test"})
        registry = self.registry(config=config)
        self.assertEqual(registry.show("architect").provider, "gemini")
        self.assertEqual(registry.engine("implementation").engine_id, "codex")

    def test_artifacts_record_actual_fallback_usage_and_all_role_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config({
                "team.assignments.engineer_reviewer": "gemini:gemini-test",
            })
            registry = self.registry(config=config)
            providers = {
                "openai": FakeProvider(
                    "gpt-test", "", error=ConnectionError("private transport detail")
                ),
                "ollama": FakeProvider(
                    "local-test", role_json("Fallback architecture", ["Step one"])
                ),
                "gemini": FakeProvider(
                    "gemini-test", role_json("Reviewed plan", ["Step one", "Step two"])
                ),
            }
            team = TeamOrchestrator(
                config,
                TeamTaskStore(Path(tmp) / "team" / "tasks"),
                FakeFactory(providers),
                role_registry=registry,
                now=lambda: datetime(2026, 7, 19, tzinfo=timezone.utc),
                id_factory=lambda: "team-role-test",
            )
            task = team.plan("Route planning and execution independently")
            self.assertEqual(len(task.role_assignments), 5)
            architect = task.artifact("architect")
            reviewer = task.artifact("engineer_reviewer")
            self.assertEqual(architect.role_metadata.actual_assignment, "ollama:local-test")
            self.assertIn("selected ollama:local-test", architect.role_metadata.fallback_reason)
            self.assertEqual(reviewer.role_metadata.actual_assignment, "gemini:gemini-test")
            self.assertGreaterEqual(architect.role_metadata.duration_seconds, 0)
            self.assertEqual(
                architect.role_metadata.input_tokens,
                task.usage[0].input_tokens,
            )
            persisted = json.dumps(task.to_dict())
            self.assertNotIn("private transport detail", persisted)

    def test_secret_like_values_never_reach_config_or_artifacts(self):
        secret = "sk-super-secret-value-12345"
        config = self.config()
        registry = self.registry(config=config)
        with self.assertRaisesRegex(ValueError, "credentials"):
            registry.set("architect", f"openai:{secret}")
        serialized = json.dumps(config.values)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("api_key", serialized.lower())

    def test_role_commands_display_set_and_reset_assignments(self):
        config = self.config()
        registry = self.registry(config=config)
        team = SimpleNamespace(roles=lambda: registry.roles(), role_registry=registry)
        router = CommandRouter(SimpleNamespace(team=team, team_roles=registry))
        with patch("builtins.print") as output:
            router.handle("team roles")
            router.handle("team role show architect")
            router.handle("team role set architect gemini:gemini-test")
            router.handle("team role reset architect")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Engineering Reviewer", rendered)
        self.assertIn("Implementation Engine", rendered)
        self.assertIn("Documentation Reviewer", rendered)
        self.assertIn("Source: default", rendered)
        self.assertIn("[OK] Architect -> gemini:gemini-test", rendered)

    def test_unavailable_implementation_role_fails_closed_before_bridge_launch(self):
        engines = FakeExecutionEngines(ready=False)
        registry = self.registry(engines=engines)
        bridge = Mock()
        bridge.execution_engines = engines
        router = CommandRouter(SimpleNamespace(
            team_roles=registry,
            execution_engines=engines,
            codex_bridge=bridge,
        ))
        with patch("builtins.print"):
            router.handle("team implement team-role-test approval-role-test")
        bridge.execution_context.assert_not_called()
        bridge.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
