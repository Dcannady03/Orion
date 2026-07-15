import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.core.config import ConfigManager
from orion.core.router import CommandRouter
from orion.intelligence.ollama_provider import OllamaProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class OllamaModelTests(unittest.TestCase):
    def test_provider_lists_installed_models(self):
        provider = OllamaProvider("http://localhost:11434", "qwen:7b")
        payload = {"models": [{"name": "qwen:7b"}, {"name": "llama:8b"}]}
        with patch("orion.intelligence.ollama_provider.request.urlopen", return_value=_Response(payload)):
            self.assertEqual(provider.list_models(), ["llama:8b", "qwen:7b"])

    def test_router_changes_model_and_persists_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "default.yaml"
            manager = ConfigManager(str(path))
            manager.config = {"providers": {"ollama": {"model": "old:1b"}}}
            manager.save()
            provider = OllamaProvider("http://localhost:11434", "old:1b")
            provider.list_models = lambda: ["old:1b", "new:7b"]
            orion = SimpleNamespace(ai_provider=provider, config_manager=manager)
            router = CommandRouter(orion)
            with patch("builtins.input", side_effect=["2", "y"]), patch("sys.stdout", new_callable=io.StringIO) as out:
                router.change_ollama_model()
            self.assertEqual(provider.model, "new:7b")
            reloaded = ConfigManager(str(path))
            reloaded.load()
            self.assertEqual(reloaded.get("providers.ollama.model"), "new:7b")
            self.assertIn("Default model updated", out.getvalue())

    def test_router_cancel_keeps_current_model(self):
        provider = OllamaProvider("http://localhost:11434", "old:1b")
        provider.list_models = lambda: ["old:1b", "new:7b"]
        manager = SimpleNamespace(set=lambda *args: None, save=lambda: None)
        router = CommandRouter(SimpleNamespace(ai_provider=provider, config_manager=manager))
        with patch("builtins.input", return_value="0"), patch("sys.stdout", new_callable=io.StringIO):
            router.change_ollama_model()
        self.assertEqual(provider.model, "old:1b")


if __name__ == "__main__":
    unittest.main()

class OllamaWarmModelTests(unittest.TestCase):
    def test_warm_model_uses_non_streaming_generate_request(self):
        provider = OllamaProvider("http://localhost:11434", "qwen:7b")
        captured = {}
        def fake_urlopen(req, timeout):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _Response({"done": True})
        with patch("orion.intelligence.ollama_provider.request.urlopen", side_effect=fake_urlopen):
            provider.warm_model("qwen:9b")
        self.assertEqual(captured["payload"]["model"], "qwen:9b")
        self.assertEqual(captured["payload"]["prompt"], "")
        self.assertFalse(captured["payload"]["stream"])

