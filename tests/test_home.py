import unittest
from datetime import datetime
from types import SimpleNamespace

from orion.services.briefing import BriefingItem, BriefingService
from orion.services.home import HomeService


class _Provider:
    name = "AI"

    def get_briefing(self):
        return (BriefingItem("AI", "Ollama is connected", source=self.name, icon="[OK]"),)


class _ProjectContext:
    initialized = True

    def tasks(self):
        return [
            {"goal": "Build GUI shell", "status": "proposed"},
            {"title": "Foundation", "status": "completed"},
            {"goal": "Old idea", "status": "cancelled"},
        ]

    def project(self):
        return {"name": "Orion", "current_goal": "Expand Home Center"}

    def history(self):
        return [{"timestamp": "2026-07-16T13:00:00+00:00", "summary": "Home Center started"}]


class _ActionHistory:
    def entries(self, limit=None):
        return []


class _Services:
    def names(self):
        return ("home", "briefing", "weather")


class HomeServiceTests(unittest.TestCase):
    def setUp(self):
        briefing = BriefingService()
        briefing.register_provider(_Provider())
        orion = SimpleNamespace(
            user_name="Daniel",
            status="READY",
            profile_manager=SimpleNamespace(get=lambda key, default="": "Yuba City, California"),
            project_context=_ProjectContext(),
            workspace_manager=SimpleNamespace(root=SimpleNamespace(name="First Light")),
            action_history=_ActionHistory(),
            services=_Services(),
            plugin_manager=SimpleNamespace(loaded_count=lambda: 2),
            knowledge_index=SimpleNamespace(built=True),
        )
        self.service = HomeService(orion, briefing)

    def test_builds_interface_neutral_snapshot(self):
        now = datetime(2026, 7, 16, 9, 30)
        snapshot = self.service.build(now=now)

        self.assertEqual(snapshot.greeting, "Good morning")
        self.assertEqual(snapshot.user_name, "Daniel")
        self.assertEqual(snapshot.location, "Yuba City, California")
        self.assertEqual(snapshot.cards[0].title, "AI")
        self.assertEqual(snapshot.cards[0].source, "AI")

    def test_adds_home_center_cards(self):
        snapshot = self.service.build(now=datetime(2026, 7, 16, 9, 30))
        cards = {card.title: card for card in snapshot.cards}

        self.assertEqual(cards["Tasks"].message, "1 open; next: Build GUI shell")
        self.assertEqual(cards["Project"].message, "Orion: Expand Home Center")
        self.assertEqual(cards["Activity"].message, "Home Center started")
        self.assertIn("3 services", cards["System"].message)
        self.assertIn("2 plugins", cards["System"].message)
        self.assertIn("index built", cards["System"].message)

    def test_home_card_failure_is_isolated(self):
        self.service.orion.project_context.tasks = lambda: (_ for _ in ()).throw(ValueError("bad tasks"))
        snapshot = self.service.build(now=datetime(2026, 7, 16, 9, 30))

        self.assertNotIn("Tasks", {card.title for card in snapshot.cards})
        self.assertIn(("Tasks", "bad tasks"), snapshot.provider_errors)
        self.assertIn("System", {card.title for card in snapshot.cards})

    def test_greeting_changes_by_daypart(self):
        self.assertEqual(self.service.build(now=datetime(2026, 7, 16, 13)).greeting, "Good afternoon")
        self.assertEqual(self.service.build(now=datetime(2026, 7, 16, 20)).greeting, "Good evening")


if __name__ == "__main__":
    unittest.main()
