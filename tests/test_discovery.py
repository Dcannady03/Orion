"""Tests for v0.3.2 Discovery application catalog and launching."""
from pathlib import Path
import tempfile
import unittest

from orion.actions import ActionHistory, ActionService, PolicyDecision
from orion.services.discovery import (
    ApplicationCatalog,
    ApplicationDiscoveryService,
    ApplicationLauncherService,
    ApplicationMatcher,
)


class DiscoveryTests(unittest.TestCase):
    def make_catalog(self, root: str):
        workspace = Path(root) / "workspace"
        apps = Path(root) / "start-menu"
        workspace.mkdir()
        apps.mkdir()
        (apps / "Google Chrome.lnk").write_text("shortcut")
        (apps / "Visual Studio Code.lnk").write_text("shortcut")
        (apps / "Visual Studio.lnk").write_text("shortcut")
        (apps / "ignore.txt").write_text("not an app")
        catalog = ApplicationCatalog(workspace)
        discovery = ApplicationDiscoveryService(catalog, roots=[apps])
        discovery.scan()
        return workspace, catalog

    def test_scan_builds_persistent_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, catalog = self.make_catalog(root)
            self.assertEqual(len(catalog.applications()), 3)
            reloaded = ApplicationCatalog(workspace)
            self.assertEqual([app.name for app in reloaded.applications()], [
                "Google Chrome", "Visual Studio", "Visual Studio Code"
            ])

    def test_catalog_rebinds_without_leaking_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            first_workspace, catalog = self.make_catalog(root)
            chrome = ApplicationMatcher(catalog).resolve("chrome").application
            catalog.set_alias("browser", chrome.path)
            second_workspace = Path(root) / "other-workspace"
            second_workspace.mkdir()
            catalog.bind(second_workspace)
            self.assertEqual(catalog.applications(), ())
            self.assertEqual(catalog.aliases(), {})
            catalog.bind(first_workspace)
            self.assertIn("browser", catalog.aliases())

    def test_exact_fuzzy_and_alias_matching(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, catalog = self.make_catalog(root)
            matcher = ApplicationMatcher(catalog)
            self.assertEqual(matcher.resolve("google chrome").application.name, "Google Chrome")
            self.assertEqual(matcher.resolve("chrom").application.name, "Google Chrome")
            code = matcher.find("visual studio code")[0].application
            catalog.set_alias("coding", code.path)
            self.assertEqual(ApplicationMatcher(ApplicationCatalog(workspace)).resolve("coding").matched_by, "alias")

    def test_duplicate_shortcuts_do_not_create_false_ambiguity(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            start_menu = Path(root) / "start-menu"
            desktop = Path(root) / "desktop"
            workspace.mkdir()
            start_menu.mkdir()
            desktop.mkdir()
            (start_menu / "Google Chrome.lnk").write_text("shortcut")
            (desktop / "Google Chrome.lnk").write_text("shortcut")
            catalog = ApplicationCatalog(workspace)
            discovery = ApplicationDiscoveryService(catalog, roots=[start_menu, desktop])
            discovery.scan()

            matcher = ApplicationMatcher(catalog)
            matches = matcher.find("chrome")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].application.name, "Google Chrome")
            self.assertIsNotNone(matcher.resolve("chrome"))

    def test_ambiguous_query_does_not_guess(self):
        with tempfile.TemporaryDirectory() as root:
            _, catalog = self.make_catalog(root)
            matcher = ApplicationMatcher(catalog)
            self.assertIsNone(matcher.resolve("visual"))

    def test_launcher_uses_direct_match_then_search_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            _, catalog = self.make_catalog(root)
            launched, searched = [], []
            launcher = ApplicationLauncherService(
                ApplicationMatcher(catalog), launched.append, searched.append
            )
            output = launcher.launch("chrome")
            self.assertIn("Google Chrome", output)
            self.assertTrue(launched[0].endswith("Google Chrome.lnk"))
            fallback = launcher.launch("Crimson Desert")
            self.assertIn("Searching Windows", fallback)
            self.assertEqual(searched, ["Crimson Desert"])

    def test_open_app_runs_through_approval_action(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, catalog = self.make_catalog(root)
            launched = []
            launcher = ApplicationLauncherService(ApplicationMatcher(catalog), launched.append, lambda _: None)
            actions = ActionService(ActionHistory(workspace))
            actions.register_handler("open_app", lambda action: launcher.launch(action.parameters["name"]))
            actions.approval.set_policy("open_app", PolicyDecision.REQUIRE_APPROVAL)
            action = actions.create("open_app", {"name": "chrome"})
            with self.assertRaises(PermissionError):
                actions.execute(action)
            actions.approve(action.id)
            result = actions.execute(action)
            self.assertTrue(result.success)
            self.assertEqual(len(launched), 1)


if __name__ == "__main__":
    unittest.main()
