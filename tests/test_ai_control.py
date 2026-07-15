import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.core.config import ConfigManager
from orion.core.router import CommandRouter
from orion.intelligence.ollama_provider import OllamaProvider
from orion.services.ai_control import AIControlService, AIModelInfo


class AIControlTests(unittest.TestCase):
    def manager(self, tmp, model="qwen:7b"):
        manager = ConfigManager(str(Path(tmp) / "default.yaml"))
        manager.config = {"providers": {"ollama": {"model": model}}, "ai": {}}
        manager.save()
        return manager

    def test_recommends_smallest_model_for_fastest(self):
        service = AIControlService(SimpleNamespace(), SimpleNamespace())
        models = [
            AIModelInfo("large", size_bytes=20 * 1024**3, parameter_size="35B"),
            AIModelInfo("small", size_bytes=3 * 1024**3, parameter_size="3B"),
        ]
        self.assertEqual(service.recommend("fastest", models).name, "small")

    def test_natural_language_switch_can_be_saved_as_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            provider = OllamaProvider("http://localhost:11434", "qwen:7b")
            provider.list_models = lambda: ["qwen:7b", "qwen:9b"]
            orion = SimpleNamespace(ai_provider=provider, config_manager=manager)
            router = CommandRouter(orion)
            with patch("builtins.input", return_value="y"), patch("sys.stdout", new_callable=io.StringIO):
                router.handle("switch to qwen:9b")
            self.assertEqual(provider.model, "qwen:9b")
            self.assertEqual(manager.get("providers.ollama.model"), "qwen:9b")

    def test_natural_language_switch_can_be_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            provider = OllamaProvider("http://localhost:11434", "qwen:7b")
            provider.list_models = lambda: ["qwen:7b", "qwen:9b"]
            router = CommandRouter(SimpleNamespace(ai_provider=provider, config_manager=manager))
            with patch("builtins.input", return_value="n"), patch("sys.stdout", new_callable=io.StringIO) as out:
                router.handle("ai use qwen:9b")
            self.assertEqual(provider.model, "qwen:9b")
            self.assertEqual(manager.get("providers.ollama.model"), "qwen:7b")
            self.assertIn("session only", out.getvalue())

    def test_status_shows_session_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            provider = OllamaProvider("http://localhost:11434", "qwen:9b")
            provider.list_models = lambda: ["qwen:7b", "qwen:9b"]
            router = CommandRouter(SimpleNamespace(ai_provider=provider, config_manager=manager))
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                router.show_ai_status()
            text = out.getvalue()
            self.assertIn("Default model   : qwen:7b", text)
            self.assertIn("Session override: Enabled", text)

    def test_profile_sets_temperature_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp, "large:35b")
            provider = OllamaProvider("http://localhost:11434", "large:35b")
            provider.list_models = lambda: ["large:35b", "small:3b"]
            service = AIControlService(provider, manager)
            result = service.activate_profile("lightweight")
            self.assertEqual(result["model"], "small:3b")
            self.assertEqual(manager.get("ai.active_profile"), "lightweight")
            self.assertEqual(manager.get("ai.temperature"), 0.4)

    def test_model_tags_recognize_vision_and_coding(self):
        info = AIModelInfo("qwen-vl:9b", family="qwen", parameter_size="9B", capabilities=("vision", "completion"))
        self.assertIn("Vision", info.tags)
        self.assertIn("Coding", info.tags)
        self.assertIn("Fast", info.tags)


if __name__ == "__main__":
    unittest.main()
