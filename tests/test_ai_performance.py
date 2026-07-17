import tempfile
import unittest
from pathlib import Path

from orion.services.ai_performance import AIPerformanceStore


class AIPerformanceStoreTests(unittest.TestCase):
    def test_records_aggregate_metrics_without_prompt_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stats.json"
            store = AIPerformanceStore(path)
            store.record("ollama", "qwen:9b", 1.0, True)
            store.record("ollama", "qwen:9b", 3.0, False, "timeout")
            row = store.summary()[0]
            self.assertEqual(row["requests"], 2)
            self.assertEqual(row["success_rate_percent"], 50.0)
            self.assertEqual(row["average_duration_seconds"], 2.0)
            self.assertNotIn("prompt", path.read_text(encoding="utf-8").lower())

    def test_metrics_reload_and_health_requires_enough_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stats.json"
            store = AIPerformanceStore(path)
            store.record("openai", "gpt", 0.5, False, "failed")
            self.assertEqual(store.provider_health("openai")["state"], "learning")
            store.record("openai", "gpt", 0.5, False, "failed")
            store.record("openai", "gpt", 0.5, False, "failed")
            reloaded = AIPerformanceStore(path)
            self.assertEqual(reloaded.provider_health("openai")["state"], "unhealthy")

    def test_unwritable_telemetry_does_not_break_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            directory_as_file = Path(temp) / "stats.json"
            directory_as_file.mkdir()
            store = AIPerformanceStore(directory_as_file)
            store.record("ollama", "qwen", 0.1, True)
            self.assertEqual(store.summary()[0]["requests"], 1)


if __name__ == "__main__":
    unittest.main()
