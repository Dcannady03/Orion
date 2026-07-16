import unittest
from unittest.mock import patch

from orion.services.ai_routing import AIRoutingService


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.saved += 1


class Status:
    def __init__(self, key, enabled=True, configured=True):
        self.key = key
        self.enabled = enabled
        self.configured = configured


class FakeManager:
    def __init__(self, statuses):
        self._statuses = statuses
        self.secrets = object()

    def statuses(self):
        return self._statuses


class FakeProvider:
    def __init__(self, key, result=None, error=None):
        self.key = key
        self.model = f"{key}-model"
        self.result = result
        self.error = error

    def chat(self, prompt, system_prompt=None):
        if self.error:
            raise self.error
        return self.result or f"{self.key}:{prompt}"


class AIRoutingTests(unittest.TestCase):
    def make_service(self, profile="balanced", ready=("ollama", "openai", "gemini")):
        config = FakeConfig({"ai.routing.enabled": True, "ai.routing.profile": profile})
        manager = FakeManager([Status(key) for key in ready])
        return AIRoutingService(config, manager), config

    def test_balanced_routes_short_requests_to_ollama(self):
        service, _ = self.make_service()
        self.assertEqual(service.provider_order("hello"), ("ollama", "openai", "gemini"))

    def test_balanced_routes_coding_requests_to_openai(self):
        service, _ = self.make_service()
        order = service.provider_order("Review this Python traceback and fix the bug")
        self.assertEqual(order[0], "openai")

    def test_research_profile_prefers_gemini(self):
        service, _ = self.make_service(profile="research")
        self.assertEqual(service.provider_order("Summarize this report")[0], "gemini")

    def test_unconfigured_providers_are_excluded(self):
        service, _ = self.make_service(ready=("ollama",))
        self.assertEqual(service.provider_order("review this architecture"), ("ollama",))

    def test_provider_failure_uses_next_fallback_and_records_reason(self):
        service, _ = self.make_service()
        providers = {
            "ollama": FakeProvider("ollama", error=ConnectionError("timed out")),
            "openai": FakeProvider("openai", result="cloud answer"),
        }
        with patch("orion.services.ai_routing.AIProviderFactory.create", side_effect=lambda key: providers[key]):
            response = service.route_chat("hello")
        self.assertEqual(response, "cloud answer")
        self.assertEqual(service.last_decision.provider, "openai")
        self.assertIn("ollama", service.last_decision.reason)
        self.assertTrue(service.last_decision.success)

    def test_profile_and_enabled_state_persist(self):
        service, config = self.make_service()
        service.set_profile("coding")
        service.set_enabled(False)
        self.assertEqual(config.values["ai.routing.profile"], "coding")
        self.assertEqual(config.values["ai.active_profile"], "coding")
        self.assertFalse(config.values["ai.routing.enabled"])
        self.assertEqual(config.saved, 2)

    def test_unknown_profile_is_rejected(self):
        service, _ = self.make_service()
        with self.assertRaisesRegex(ValueError, "Unknown routing profile"):
            service.set_profile("magic")


if __name__ == "__main__":
    unittest.main()
