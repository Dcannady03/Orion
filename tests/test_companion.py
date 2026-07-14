import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.actions import ActionHistory, ActionService, PolicyDecision
from orion.core.router import CommandRouter
from orion.services.companion import ActionTrustStore, CompanionSettings
from orion.services.discovery import Application, ApplicationCatalog, ApplicationMatcher


class CompanionTests(unittest.TestCase):
    def test_settings_and_trust_persist_and_rebind(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            settings = CompanionSettings(first)
            trust = ActionTrustStore(first)
            settings.set_developer_mode(True)
            trust.trust("open_app", "C:/Apps/Chrome.lnk")

            self.assertTrue(CompanionSettings(first).developer_mode)
            self.assertTrue(ActionTrustStore(first).is_trusted("open_app", "c:/apps/chrome.lnk"))

            settings.bind(second)
            trust.bind(second)
            self.assertFalse(settings.developer_mode)
            self.assertFalse(trust.is_trusted("open_app", "C:/Apps/Chrome.lnk"))

    def _router(self, root: str, launched: list[str]):
        catalog = ApplicationCatalog(root)
        app_path = str(Path(root) / "Google Chrome.lnk")
        catalog.replace([Application("Google Chrome", app_path, "Start Menu")])
        matcher = ApplicationMatcher(catalog)
        actions = ActionService(ActionHistory(root))
        actions.register_handler("open_app", lambda action: launched.append(action.parameters["name"]) or "Opening Google Chrome.")
        actions.approval.set_policy("open_app", PolicyDecision.REQUIRE_APPROVAL, "Approval required.")
        orion = SimpleNamespace(
            application_catalog=catalog,
            application_matcher=matcher,
            action_service=actions,
            action_trust=ActionTrustStore(root),
            companion_settings=CompanionSettings(root),
        )
        return CommandRouter(orion), orion

    def test_yes_approves_and_executes_without_printing_uuid(self):
        with tempfile.TemporaryDirectory() as root:
            launched = []
            router, _ = self._router(root, launched)
            output = io.StringIO()
            with patch("builtins.input", return_value="y"), redirect_stdout(output):
                router.open_app("chrome")
            self.assertEqual(launched, ["chrome"])
            self.assertIn("I found Google Chrome.", output.getvalue())
            self.assertNotIn("Action ID:", output.getvalue())

    def test_always_allow_is_persisted_and_skips_next_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            launched = []
            router, orion = self._router(root, launched)
            with patch("builtins.input", return_value="a"):
                router.open_app("chrome")
            self.assertTrue(orion.action_trust.entries())
            with patch("builtins.input", side_effect=AssertionError("trusted launch should not prompt")):
                router.open_app("chrome")
            self.assertEqual(launched, ["chrome", "chrome"])

    def test_pending_actions_accept_friendly_queue_number(self):
        with tempfile.TemporaryDirectory() as root:
            launched = []
            router, orion = self._router(root, launched)
            action = orion.action_service.create("open_app", {"name": "chrome", "display_name": "Google Chrome"})
            router.action_approve("1")
            self.assertEqual(action.status.value, "succeeded")
            self.assertEqual(launched, ["chrome"])


if __name__ == "__main__":
    unittest.main()
