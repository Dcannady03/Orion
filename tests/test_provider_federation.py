import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.config import ConfigManager
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.intelligence.factory import AIProviderFactory
from orion.intelligence.gemini_provider import GeminiProvider
from orion.intelligence.openai_provider import OpenAIProvider
from orion.intelligence.secrets import SecretStore
from orion.services.provider_manager import ProviderManager
from orion.services.vault import ProviderVerificationError


class ProviderFederationTests(unittest.TestCase):
    def manager(self, root):
        manager = ConfigManager(str(Path(root) / "default.yaml"))
        manager.config = {
            "providers": {
                "default": "ollama",
                "secrets_path": str(Path(root) / "secrets.yaml"),
                "ollama": {"enabled": True, "base_url": "http://localhost:11434", "model": "qwen:7b"},
                "openai": {"enabled": True, "model": "gpt-test", "base_url": "https://example.test/v1"},
                "gemini": {"enabled": True, "model": "gemini-test", "base_url": "https://example.test/v1beta"},
            }
        }
        manager.save()
        return manager

    def test_secret_store_persists_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp) / ".orion" / "secrets.yaml")
            store.set("openai", "sk-test")
            self.assertEqual(store.get("openai"), "sk-test")
            self.assertNotIn("sk-test", (Path(tmp) / "default.yaml").read_text() if (Path(tmp) / "default.yaml").exists() else "")

    def test_factory_builds_cloud_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            store.set("gemini", "gm-test")
            self.assertIsInstance(AIProviderFactory(manager, store).create("openai"), OpenAIProvider)
            self.assertIsInstance(AIProviderFactory(manager, store).create("gemini"), GeminiProvider)

    @patch("orion.intelligence.openai_provider.requests.post")
    def test_openai_chat_uses_responses_api(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"output_text": "Hello from OpenAI"}
        post.return_value = response
        provider = OpenAIProvider("gpt-test", "sk-test", base_url="https://example.test/v1")
        self.assertEqual(provider.chat("hello", "You are Orion"), "Hello from OpenAI")
        self.assertEqual(post.call_args.kwargs["json"]["instructions"], "You are Orion")

    @patch("orion.intelligence.gemini_provider.requests.post")
    def test_gemini_chat_uses_system_instruction(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]}
        post.return_value = response
        provider = GeminiProvider("gemini-test", "gm-test", base_url="https://example.test/v1beta")
        self.assertEqual(provider.chat("hello", "You are Orion"), "Hello from Gemini")
        self.assertIn("systemInstruction", post.call_args.kwargs["json"])

    def test_provider_manager_switches_brain_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            old = SimpleNamespace()
            orion = SimpleNamespace(
                ai_provider=old,
                brain=SimpleNamespace(ai_provider=old),
                ai_control=SimpleNamespace(provider=old),
            )
            service = ProviderManager(orion, manager, store)
            active = service.activate("openai")
            self.assertIs(orion.brain.ai_provider, active)
            self.assertEqual(manager.get("providers.default"), "openai")

    @patch("orion.intelligence.openai_provider.requests.get")
    def test_openai_connection_test_uses_models_endpoint(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"id": "gpt-test"}]}
        get.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            orion = SimpleNamespace()
            service = ProviderManager(orion, manager, store)
            self.assertEqual(service.test_connection("openai"), ["gpt-test"])
            self.assertTrue(get.call_args.args[0].endswith("/models"))

    def test_openai_connection_test_requires_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            service = ProviderManager(SimpleNamespace(), manager, SecretStore(Path(tmp) / "secrets.yaml"))
            with self.assertRaisesRegex(ValueError, "API key is not configured"):
                service.test_connection("openai")

    @patch("orion.intelligence.openai_provider.requests.get")
    def test_candidate_credential_is_verified_without_persistence(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"id": "gpt-candidate"}]}
        get.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "vault.yaml")
            service = ProviderManager(None, manager, store)

            models = service.verify_credentials("openai", "candidate-secret")

            self.assertEqual(models, ["gpt-candidate"])
            self.assertFalse(store.get_file_value("openai"))
            self.assertEqual(
                get.call_args.kwargs["headers"]["Authorization"],
                "Bearer candidate-secret",
            )

    def test_activation_without_runtime_uses_normal_default_provider_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "vault.yaml")
            store.set("gemini", "gem-test")
            service = ProviderManager(None, manager, store)
            active = service.activate("gemini")
            self.assertEqual(active.name(), "gemini:gemini-test")
            self.assertEqual(manager.get("providers.default"), "gemini")

    @patch("orion.services.vault.VaultService.connect_provider")
    def test_legacy_configure_api_uses_verified_vault_transaction(self, connect_provider):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            store = SecretStore(Path(tmp) / "vault.yaml")
            service = ProviderManager(None, manager, store)

            service.configure("openai", "candidate-secret", "gpt-candidate")

            connect_provider.assert_called_once_with(
                "openai",
                "candidate-secret",
                model="gpt-candidate",
            )
            self.assertFalse(store.get_file_value("openai"))

    def test_default_provider_manager_resolves_vault_under_external_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OrionPaths(root / "application", root / "user")
            values = {"vault.path": "vault/vault.yaml"}
            config = SimpleNamespace(
                paths=paths,
                get=lambda key, default=None: values.get(key, default),
            )
            service = ProviderManager(None, config)
            self.assertEqual(service.secrets.path, paths.vault)

    def test_normal_provider_command_uses_vault_verify_then_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.manager(tmp)
            verified = SimpleNamespace(models=("gpt-test", "gpt-alt"))
            vault = Mock()
            vault.verify_provider.return_value = verified
            provider_manager = Mock()
            provider_manager.activate.return_value = SimpleNamespace(name=lambda: "openai:gpt-test")
            router = CommandRouter(SimpleNamespace(
                vault=vault,
                provider_manager=provider_manager,
                config_manager=config,
            ))

            with patch("orion.core.router.getpass", return_value="candidate-secret"), patch(
                "builtins.input", side_effect=["", ""]
            ), patch("builtins.print") as output:
                router.configure_ai_provider("openai")

            vault.verify_provider.assert_called_once_with("openai", "candidate-secret")
            vault.commit_provider.assert_called_once_with(verified, model="gpt-test")
            provider_manager.activate.assert_called_once_with("openai")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertNotIn("candidate-secret", rendered)

    def test_normal_provider_command_preserves_state_after_verification_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.manager(tmp)
            vault = Mock()
            vault.verify_provider.side_effect = ProviderVerificationError(
                "OpenAI credentials could not be verified (ConnectionError)."
            )
            provider_manager = Mock()
            router = CommandRouter(SimpleNamespace(
                vault=vault,
                provider_manager=provider_manager,
                config_manager=config,
            ))

            with patch("orion.core.router.getpass", return_value="rejected-secret"), patch(
                "builtins.print"
            ) as output:
                router.configure_ai_provider("openai")

            vault.commit_provider.assert_not_called()
            provider_manager.activate.assert_not_called()
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Existing credentials and active provider were preserved", rendered)
            self.assertNotIn("rejected-secret", rendered)


if __name__ == "__main__":
    unittest.main()
