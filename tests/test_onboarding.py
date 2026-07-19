import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from orion.core.config import ConfigManager
from orion.core.onboarding import FirstContact
from orion.intelligence.secrets import SecretStore
from orion.services.ai_routing import AIRoutingService
from orion.services.execution_engines import (
    ENGINE_STATUS_INSTALLED,
    ENGINE_STATUS_NOT_INSTALLED,
    ExecutionEngine,
)
from orion.services.provider_manager import ProviderStatus
from orion.services.vault import VaultService


class AnswerQueue:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        try:
            return next(self.answers)
        except StopIteration as exc:
            raise AssertionError(f"No test answer remains for prompt: {prompt}") from exc


class FakeProviderManager:
    MODELS = {
        "ollama": ["qwen:7b", "qwen:14b"],
        "openai": ["gpt-test", "gpt-alt"],
        "gemini": ["gemini-test", "gemini-pro"],
    }

    def __init__(self, config, store, *, failures=(), ollama_available=True):
        self.config = config
        self.secrets = store
        self.failures = set(failures)
        self.ollama_available = ollama_available
        self.preview_calls = []
        self.verify_calls = []
        self.activations = []

    def preview_models(self, provider, **kwargs):
        self.preview_calls.append((provider, dict(kwargs)))
        if provider == "ollama" and not self.ollama_available:
            raise ConnectionError("local service unavailable")
        return list(self.MODELS[provider])

    def verify_credentials(self, provider, api_key, *, model=None):
        self.verify_calls.append(provider)
        if provider in self.failures:
            raise ConnectionError("credential rejected")
        if not api_key:
            raise ValueError("missing key")
        return list(self.MODELS[provider])

    def test_connection(self, provider):
        if provider in self.failures:
            raise ConnectionError("connection unavailable")
        if provider != "ollama" and not self.secrets.get(provider):
            raise ValueError("missing key")
        return self.preview_models(provider)

    def activate(self, provider, persist=True):
        if provider in self.failures:
            raise ConnectionError("activation failed")
        if persist:
            self.config.set("providers.default", provider)
            self.config.save()
        self.activations.append(provider)
        model = self.config.get(f"providers.{provider}.model", "")
        return SimpleNamespace(name=lambda: f"{provider}:{model}")

    def statuses(self):
        active = self.config.get("providers.default", "ollama")
        results = []
        for provider in ("ollama", "openai", "gemini"):
            enabled = bool(self.config.get(f"providers.{provider}.enabled", provider == "ollama"))
            configured = provider == "ollama" or bool(self.secrets.get(provider))
            results.append(ProviderStatus(
                provider,
                enabled,
                configured,
                provider == active,
                self.config.get(f"providers.{provider}.model", ""),
            ))
        return results


class FakeExecutionEngines:
    def __init__(self, *, codex_installed):
        self.codex_installed = codex_installed

    def status(self):
        return (
            ExecutionEngine(
                "codex",
                "Codex CLI",
                ENGINE_STATUS_INSTALLED if self.codex_installed else ENGINE_STATUS_NOT_INSTALLED,
                self.codex_installed,
                True,
                True,
                executable="C:/tools/codex.cmd" if self.codex_installed else "",
            ),
            ExecutionEngine(
                "chatgpt_desktop",
                "ChatGPT Desktop",
                ENGINE_STATUS_INSTALLED,
                True,
                False,
                False,
            ),
        )


class FirstContactTests(unittest.TestCase):
    def build(
        self,
        root,
        answers,
        *,
        secrets=(),
        failures=(),
        ollama_available=True,
        codex_installed=False,
    ):
        root = Path(root)
        config_path = root / "config.yaml"
        profile_path = root / "profile.yaml"
        defaults = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
        config = ConfigManager(defaults, local_config_path=config_path)
        config.defaults = ConfigManager._read_yaml(defaults)
        config.local_config = (
            ConfigManager._read_yaml(config_path) if config_path.exists() else {}
        )
        config.config = ConfigManager._deep_merge(config.defaults, config.local_config)
        store = SecretStore(root / "user" / "vault" / "vault.yaml")
        manager = FakeProviderManager(
            config,
            store,
            failures=failures,
            ollama_available=ollama_available,
        )
        vault = VaultService(config, manager, store)
        routing = AIRoutingService(config, manager)
        output = []
        answer_queue = AnswerQueue(answers)
        secret_queue = AnswerQueue(secrets)
        setup = FirstContact(
            config_path,
            profile_path,
            input_provider=answer_queue,
            secret_input_provider=secret_queue,
            output_provider=output.append,
            config_manager=config,
            provider_manager=manager,
            vault=vault,
            routing_service=routing,
            execution_engines=FakeExecutionEngines(codex_installed=codex_installed),
        )
        return setup, config, store, manager, output

    @staticmethod
    def base_answers(workspace, ai_choice, *, confirmation="yes"):
        return [
            "Daniel",
            "Daniel Cannady",
            "Yuba City, California",
            "America/Los_Angeles",
            "English",
            "4",
            str(workspace),
            str(ai_choice),
            "yes",
            "1",
            "1",
            "no",
            confirmation,
        ]

    def test_missing_profile_requires_first_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            setup, _, _, _, _ = self.build(temp, [])
            self.assertTrue(setup.is_required)

    def test_existing_named_profile_skips_first_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            setup, config, _, _, _ = self.build(root, [])
            config.save()
            setup.profile_path.write_text("preferred_name: Daniel\n", encoding="utf-8")
            result = setup.run()
            self.assertFalse(setup.is_required)
            self.assertFalse(result.completed)

    def test_ollama_only_setup_discovers_and_selects_local_model(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = self.base_answers(workspace, 1) + ["", "2"]
            setup, config, _, manager, output = self.build(temp, answers)
            result = setup.run()

            self.assertTrue(result.completed)
            self.assertEqual(config.get("providers.default"), "ollama")
            self.assertEqual(config.get("providers.ollama.model"), "qwen:14b")
            self.assertEqual(manager.preview_calls[0][0], "ollama")
            self.assertIn("[OK] Ollama connected", "\n".join(output))

    def test_ollama_unavailable_does_not_block_first_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = self.base_answers(workspace, 1) + [""]
            setup, config, _, _, output = self.build(
                temp,
                answers,
                ollama_available=False,
            )
            result = setup.run()
            self.assertTrue(result.completed)
            self.assertEqual(config.get("providers.default"), "ollama")
            self.assertIn("First Contact will continue", "\n".join(output))

    def test_openai_setup_verifies_then_saves_to_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = self.base_answers(workspace, 2) + ["1"]
            secret = "sk-onboarding-secret"
            setup, config, store, manager, output = self.build(
                temp,
                answers,
                secrets=[secret],
                codex_installed=True,
            )
            result = setup.run()

            self.assertTrue(result.completed)
            self.assertEqual(manager.verify_calls, ["openai"])
            self.assertEqual(store.get_file_value("openai"), secret)
            self.assertEqual(config.get("providers.default"), "openai")
            self.assertEqual(config.get("providers.openai.model"), "gpt-test")
            rendered = "\n".join(output)
            self.assertIn("Codex CLI: Ready", rendered)
            self.assertIn("not a CLI execution engine", rendered)

    def test_openai_verification_failure_preserves_previous_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = self.base_answers(workspace, 2)
            setup, config, store, manager, output = self.build(
                temp,
                answers,
                secrets=["invalid-openai-key"],
                failures={"openai"},
            )
            result = setup.run()

            self.assertTrue(result.completed)
            self.assertEqual(config.get("providers.default"), "ollama")
            self.assertFalse(store.get_file_value("openai"))
            self.assertEqual(manager.activations, [])
            self.assertIn("Existing credentials and active provider were preserved", "\n".join(output))

    def test_gemini_setup_verifies_then_saves_to_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = self.base_answers(workspace, 3) + ["2"]
            setup, config, store, manager, _ = self.build(
                temp,
                answers,
                secrets=["gemini-onboarding-secret"],
            )
            setup.run()
            self.assertEqual(manager.verify_calls, ["gemini"])
            self.assertTrue(store.get_file_value("gemini"))
            self.assertEqual(config.get("providers.default"), "gemini")
            self.assertEqual(config.get("providers.gemini.model"), "gemini-pro")

    def test_multiple_provider_setup_selects_active_provider_and_routing_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            answers = [
                "Daniel", "Daniel Cannady", "Yuba City", "America/Los_Angeles",
                "English", "4", str(workspace), "4",
                "yes", "yes", "yes",
                "yes", "1", "1", "no", "yes",
                "", "1",
                "1",
                "2",
                "2",
                "3",
            ]
            setup, config, store, manager, output = self.build(
                temp,
                answers,
                secrets=["openai-secret", "gemini-secret"],
            )
            setup.run()

            self.assertEqual(manager.verify_calls, ["openai", "gemini"])
            self.assertTrue(store.get_file_value("openai"))
            self.assertTrue(store.get_file_value("gemini"))
            self.assertEqual(config.get("providers.default"), "openai")
            self.assertEqual(config.get("ai.routing.profile"), "coding")
            self.assertIn("AI routing profile: Coding", "\n".join(output))

    def test_skip_ai_setup_does_not_probe_or_change_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            setup, config, _, manager, _ = self.build(
                temp,
                self.base_answers(workspace, 5),
            )
            setup.run()
            self.assertEqual(config.get("providers.default"), "ollama")
            self.assertEqual(manager.preview_calls, [])
            self.assertEqual(manager.verify_calls, [])

    def test_cancellation_writes_no_profile_config_or_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            setup, _, store, manager, output = self.build(
                root,
                self.base_answers(root / "workspace", 5, confirmation="no"),
            )
            result = setup.run()
            self.assertFalse(result.completed)
            self.assertFalse(setup.config_path.exists())
            self.assertFalse(setup.profile_path.exists())
            self.assertFalse(store.path.exists())
            self.assertEqual(manager.verify_calls, [])
            self.assertIn("No files were changed", "\n".join(output))

    def test_rerun_preserves_existing_profile_workspace_credentials_and_services(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            answers = ["", "", "", "", "", "", "", "5", "", "", "", "", "yes"]
            setup, config, store, _, _ = self.build(root, answers)
            existing_workspace = root / "existing-workspace"
            config.set("workspace.default_path", str(existing_workspace))
            config.set("providers.default", "openai")
            config.set("providers.openai.enabled", True)
            config.set("connect.discord_bot.enabled", True)
            config.set("connect.discord_bot.owner_user_ids", ["123"])
            config.save()
            store.set("openai", "existing-openai-key")
            store.set("discord_bot", "existing-discord-token")
            setup.profile_path.write_text(
                yaml.safe_dump({
                    "name": "Daniel Cannady",
                    "preferred_name": "Daniel",
                    "location": "Yuba City",
                    "timezone": "America/Los_Angeles",
                    "language": "English",
                    "intended_use": "A mix of everything",
                    "custom": "preserve-me",
                }),
                encoding="utf-8",
            )

            setup.run(force=True)

            profile = yaml.safe_load(setup.profile_path.read_text(encoding="utf-8"))
            self.assertEqual(profile["custom"], "preserve-me")
            self.assertEqual(config.get("workspace.default_path"), str(existing_workspace))
            self.assertEqual(config.get("providers.default"), "openai")
            self.assertEqual(store.get_file_value("openai"), "existing-openai-key")
            self.assertEqual(store.get_file_value("discord_bot"), "existing-discord-token")
            self.assertEqual(config.get("connect.discord_bot.owner_user_ids"), ["123"])

    def test_failed_new_provider_preserves_existing_active_provider_and_credential(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            answers = ["", "", "", "", "", "", "", "3", "", "", "", "", "yes"]
            setup, config, store, manager, _ = self.build(
                root,
                answers,
                secrets=["bad-gemini-key"],
                failures={"gemini"},
            )
            config.set("providers.default", "openai")
            config.set("providers.openai.enabled", True)
            config.save()
            store.set("openai", "working-openai-key")
            setup.profile_path.write_text(
                "preferred_name: Daniel\nname: Daniel\nlocation: Yuba City\n",
                encoding="utf-8",
            )

            setup.run(force=True)

            self.assertEqual(config.get("providers.default"), "openai")
            self.assertEqual(store.get_file_value("openai"), "working-openai-key")
            self.assertFalse(store.get_file_value("gemini"))
            self.assertEqual(manager.activations, [])

    def test_secret_appears_only_in_vault_not_config_profile_output_or_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            secret = "sk-never-display-this-value"
            setup, _, store, _, output = self.build(
                root,
                self.base_answers(workspace, 2) + ["1"],
                secrets=[secret],
            )
            setup.run()

            self.assertEqual(store.get_file_value("openai"), secret)
            self.assertNotIn(secret, setup.config_path.read_text(encoding="utf-8"))
            self.assertNotIn(secret, setup.profile_path.read_text(encoding="utf-8"))
            self.assertNotIn(secret, "\n".join(output))
            non_vault_files = [
                path for path in root.rglob("*")
                if path.is_file() and path.resolve() != store.path.resolve()
            ]
            self.assertTrue(all(secret not in path.read_text(encoding="utf-8") for path in non_vault_files))

    def test_execution_summary_reports_codex_installed_and_unavailable(self):
        for installed, expected in ((True, "Codex CLI: Ready"), (False, "Codex CLI: Not Installed")):
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as temp:
                workspace = Path(temp) / "workspace"
                setup, _, _, _, output = self.build(
                    temp,
                    self.base_answers(workspace, 5),
                    codex_installed=installed,
                )
                setup.run()
                rendered = "\n".join(output)
                self.assertIn(expected, rendered)
                self.assertIn("ChatGPT Desktop is not a CLI execution engine", rendered)


if __name__ == "__main__":
    unittest.main()
