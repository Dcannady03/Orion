import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.config import ConfigManager
from orion.intelligence.factory import AIProviderFactory
from orion.intelligence.gemini_provider import GeminiProvider
from orion.intelligence.openai_provider import OpenAIProvider
from orion.intelligence.secrets import SecretStore
from orion.services.provider_manager import ProviderManager


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


if __name__ == "__main__":
    unittest.main()
