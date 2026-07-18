import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from orion.core.config import ConfigManager
from orion.core.paths import OrionPaths
from orion.core.profile import ProfileManager


class UserDataArchitectureTests(unittest.TestCase):
    def test_all_mutable_application_paths_live_outside_install_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "Orion"
            home = root / "home"
            paths = OrionPaths(install, home / ".orion")
            paths.ensure()
            for mutable in (
                paths.config,
                paths.profile,
                paths.vault,
                paths.tokens,
                paths.backups,
                paths.team_tasks,
                paths.agents,
                paths.codex_bridge,
            ):
                self.assertFalse(mutable.is_relative_to(install))

    def test_config_migrates_legacy_local_yaml_to_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "Orion"
            defaults = install / "config" / "default.yaml"
            defaults.parent.mkdir(parents=True)
            defaults.write_text("connect:\n  discord_bot:\n    enabled: false\n", encoding="utf-8")
            user_root = root / "user" / ".orion"
            legacy = user_root / "config" / "local.yaml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("connect:\n  discord_bot:\n    enabled: true\n", encoding="utf-8")
            with patch.dict("os.environ", {"ORION_USER_DATA": str(user_root)}):
                manager = ConfigManager(defaults, user_root / "config.yaml")
                manager.paths = OrionPaths(install, user_root)
                manager.load()
            self.assertTrue(manager.get("connect.discord_bot.enabled"))
            self.assertTrue((user_root / "config.yaml").exists())

    def test_profile_migrates_from_repository_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "Orion"
            legacy = install / "config" / "profile.yaml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(yaml.safe_dump({"preferred_name": "Daniel"}), encoding="utf-8")
            user_root = root / "home" / ".orion"
            with patch("orion.core.profile.OrionPaths", return_value=OrionPaths(install, user_root)):
                profile = ProfileManager()
                profile.load()
            self.assertEqual(profile.name, "Daniel")
            self.assertTrue((user_root / "profile.yaml").exists())


if __name__ == "__main__":
    unittest.main()
