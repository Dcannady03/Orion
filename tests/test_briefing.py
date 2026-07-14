import unittest
from types import SimpleNamespace

from orion.services.briefing import (
    BriefingItem,
    BriefingPriority,
    BriefingService,
    SystemBriefingProvider,
)


class _Provider:
    def __init__(self, name, items=(), error=None):
        self._name = name
        self.items = items
        self.error = error

    @property
    def name(self):
        return self._name

    def get_briefing(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.items


class BriefingServiceTests(unittest.TestCase):
    def test_orders_by_priority_and_preserves_provider_order(self):
        service = BriefingService()
        service.register_provider(_Provider("normal", (
            BriefingItem("Info", "Later", BriefingPriority.INFORMATIONAL),
            BriefingItem("Critical", "First", BriefingPriority.CRITICAL),
            BriefingItem("Important", "Middle", BriefingPriority.IMPORTANT),
        )))

        briefing = service.build()

        self.assertEqual([item.title for item in briefing.items], ["Critical", "Important", "Info"])
        self.assertEqual(briefing.errors, ())

    def test_provider_failure_is_isolated(self):
        service = BriefingService()
        service.register_provider(_Provider("broken", error="offline"))
        service.register_provider(_Provider("healthy", (BriefingItem("Status", "Ready"),)))

        briefing = service.build()

        self.assertEqual(len(briefing.items), 1)
        self.assertEqual(briefing.items[0].message, "Ready")
        self.assertEqual(briefing.errors[0].provider, "broken")
        self.assertEqual(briefing.errors[0].message, "offline")

    def test_duplicate_registration_requires_replace(self):
        service = BriefingService()
        first = _Provider("Weather")
        second = _Provider("weather")
        service.register_provider(first)
        with self.assertRaises(KeyError):
            service.register_provider(second)
        service.register_provider(second, replace=True)
        self.assertEqual(service.provider_names(), ("weather",))
        self.assertIs(service.remove_provider("WEATHER"), second)

    def test_rejects_invalid_items(self):
        with self.assertRaises(ValueError):
            BriefingItem("", "message")
        with self.assertRaises(ValueError):
            BriefingItem("Title", "")

    def test_system_provider_uses_live_orion_state(self):
        apps = SimpleNamespace(applications=lambda: (1, 2, 3))
        provider = SimpleNamespace(name=lambda: "ollama:qwen")
        orion = SimpleNamespace(
            application_catalog=apps,
            workspace_manager=SimpleNamespace(root=SimpleNamespace(name="Orion")),
            ai_provider=provider,
        )

        items = SystemBriefingProvider(orion).get_briefing()

        self.assertEqual(items[0].message, "Orion is ready")
        self.assertEqual(items[1].message, "ollama:qwen is connected")
        self.assertEqual(items[2].message, "3 discovered")


if __name__ == "__main__":
    unittest.main()
