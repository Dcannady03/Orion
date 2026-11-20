"""Tests for live runtime provider switching (v0.4.3)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from orion.core.config import ConfigManager
from orion.core.router import CommandRouter
from orion.intelligence.secrets import SecretStore
from orion.services.provider_manager import ProviderManager


def _manager(root):
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


def _runtime(old):
    return SimpleNamespace(
        ai_provider=old,
        brain=SimpleNamespace(ai_provider=old),
        ai_control=SimpleNamespace(provider=old),
    )


class LiveProviderSwitchTests(TestCase):
    def setUp(self):
        # Ambient provider keys change onboarding/vault behavior; isolate tests.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)

    def test_activate_swaps_all_live_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            old = SimpleNamespace()
            orion = _runtime(old)
            service = ProviderManager(orion, manager, store)
            active = service.activate("openai")
            self.assertIs(orion.ai_provider, active)
            self.assertIs(orion.brain.ai_provider, active)
            self.assertIs(orion.ai_control.provider, active)
            self.assertEqual(manager.get("providers.default"), "openai")
            statuses = {item.key: item for item in service.statuses()}
            self.assertTrue(statuses["openai"].active)
            self.assertFalse(statuses["ollama"].active)

    def test_activate_to_unconfigured_provider_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            old = SimpleNamespace()
            orion = _runtime(old)
            service = ProviderManager(orion, manager, store)
            with self.assertRaisesRegex(ValueError, "API key is not configured"):
                service.activate("openai")
            self.assertIs(orion.ai_provider, old)
            self.assertIs(orion.brain.ai_provider, old)
            self.assertIs(orion.ai_control.provider, old)
            self.assertEqual(manager.get("providers.default"), "ollama")

    def test_activate_to_unknown_provider_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            old = SimpleNamespace()
            orion = _runtime(old)
            service = ProviderManager(orion, manager, store)
            with self.assertRaisesRegex(ValueError, "Unknown provider"):
                service.activate("nope")
            self.assertIs(orion.ai_provider, old)
            self.assertEqual(manager.get("providers.default"), "ollama")

    def test_activate_with_partial_runtime_skips_missing_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("gemini", "gm-test")
            old = SimpleNamespace()
            orion = SimpleNamespace(ai_provider=old)
            service = ProviderManager(orion, manager, store)
            active = service.activate("gemini")
            self.assertIs(orion.ai_provider, active)
            self.assertEqual(manager.get("providers.default"), "gemini")

    def test_activate_without_runtime_persists_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            service = ProviderManager(None, manager, store)
            active = service.activate("openai")
            self.assertEqual(active.name(), "openai:gpt-test")
            self.assertEqual(manager.get("providers.default"), "openai")

    def test_activate_without_persist_swaps_but_does_not_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            old = SimpleNamespace()
            orion = _runtime(old)
            service = ProviderManager(orion, manager, store)
            active = service.activate("openai", persist=False)
            self.assertIs(orion.brain.ai_provider, active)
            self.assertEqual(manager.get("providers.default"), "ollama")

    def test_set_model_swaps_all_live_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            old = SimpleNamespace()
            orion = _runtime(old)
            service = ProviderManager(orion, manager, store)
            active = service.set_model("openai", "gpt-alt")
            self.assertIs(orion.ai_provider, active)
            self.assertIs(orion.brain.ai_provider, active)
            self.assertIs(orion.ai_control.provider, active)
            self.assertEqual(active.model, "gpt-alt")
            self.assertEqual(manager.get("providers.default"), "openai")

    def test_set_model_without_runtime_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            store = SecretStore(Path(tmp) / "secrets.yaml")
            store.set("openai", "sk-test")
            service = ProviderManager(None, manager, store)
            active = service.set_model("openai", "gpt-alt")
            self.assertEqual(active.model, "gpt-alt")


class ProviderSwitchRouterTests(TestCase):
    def test_use_ai_provider_reports_live_switch(self):
        provider_manager = Mock()
        provider_manager.activate.return_value = SimpleNamespace(
            name=lambda: "openai:gpt-test"
        )
        router = CommandRouter(SimpleNamespace(provider_manager=provider_manager))
        with patch("builtins.print") as output:
            router.use_ai_provider("openai")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("[OK] Active AI provider: openai:gpt-test", rendered)
        self.assertIn("live now", rendered)
        self.assertNotIn("next time Orion starts", rendered)

    def test_use_ai_provider_reports_activation_failure(self):
        provider_manager = Mock()
        provider_manager.activate.side_effect = ValueError(
            "OpenAI API key is not configured."
        )
        router = CommandRouter(SimpleNamespace(provider_manager=provider_manager))
        with patch("builtins.print") as output:
            router.use_ai_provider("openai")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Could not activate provider", rendered)
        self.assertIn("API key is not configured", rendered)

    def test_router_help_mentions_provider_switch(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("ai provider use", help_text)
