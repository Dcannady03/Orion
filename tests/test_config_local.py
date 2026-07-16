import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from orion.core.config import ConfigManager


class LocalConfigurationTests(unittest.TestCase):
    def write_yaml(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    def test_local_overrides_merge_without_modifying_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            defaults = root / "app" / "config" / "default.yaml"
            local = root / "user" / "local.yaml"
            self.write_yaml(defaults, {
                "orion": {"version": "0.4.7"},
                "connect": {"discord_bot": {"enabled": False, "owner_user_ids": []}},
            })
            self.write_yaml(local, {
                "connect": {"discord_bot": {"enabled": True, "owner_user_ids": [123]}},
            })

            manager = ConfigManager(defaults, local)
            manager.load()
            self.assertTrue(manager.get("connect.discord_bot.enabled"))
            self.assertEqual(manager.get("connect.discord_bot.owner_user_ids"), [123])
            self.assertEqual(manager.get("orion.version"), "0.4.7")

            manager.set("connect.discord_bot.allowed_channel_ids", [456])
            manager.save()

            unchanged = yaml.safe_load(defaults.read_text(encoding="utf-8"))
            saved_local = yaml.safe_load(local.read_text(encoding="utf-8"))
            self.assertEqual(unchanged["connect"]["discord_bot"]["owner_user_ids"], [])
            self.assertEqual(saved_local["connect"]["discord_bot"]["owner_user_ids"], [123])
            self.assertEqual(saved_local["connect"]["discord_bot"]["allowed_channel_ids"], [456])

    def test_default_runtime_path_is_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with patch.dict(os.environ, {}, clear=False), patch("pathlib.Path.home", return_value=home):
                manager = ConfigManager()
            self.assertEqual(manager.local_config_path, home / ".orion" / "config.yaml")

    def test_recovers_discord_settings_from_latest_update_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "Orion"
            defaults = install / "config" / "default.yaml"
            local = root / "user" / "local.yaml"
            self.write_yaml(defaults, {
                "orion": {"version": "0.4.7.2"},
                "connect": {"discord_bot": {
                    "enabled": False,
                    "owner_user_ids": [],
                    "allowed_channel_ids": [],
                }},
            })
            old_backup = root / "Orion-backups" / "update-20260716-080000" / "config" / "default.yaml"
            new_backup = root / "Orion-backups" / "update-20260716-090000" / "config" / "default.yaml"
            self.write_yaml(old_backup, {
                "orion": {"version": "0.4.6"},
                "connect": {"discord_bot": {"enabled": True, "owner_user_ids": [1]}},
            })
            self.write_yaml(new_backup, {
                "orion": {"version": "0.4.7.1"},
                "connect": {"discord_bot": {
                    "enabled": True,
                    "owner_user_ids": [999],
                    "allowed_channel_ids": [777],
                }},
            })

            manager = ConfigManager(defaults, local)
            manager.load()

            self.assertEqual(manager.get("connect.discord_bot.owner_user_ids"), [999])
            self.assertEqual(manager.get("connect.discord_bot.allowed_channel_ids"), [777])
            self.assertTrue(local.exists())
            self.assertNotIn("orion", yaml.safe_load(local.read_text(encoding="utf-8")))
            self.assertEqual(manager.recovered_from, new_backup)

    def test_explicit_path_keeps_single_file_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "default.yaml"
            self.write_yaml(path, {"weather": {"location": ""}})
            manager = ConfigManager(path)
            manager.load()
            manager.set("weather.location", "Yuba City")
            manager.save()
            self.assertEqual(yaml.safe_load(path.read_text())["weather"]["location"], "Yuba City")


if __name__ == "__main__":
    unittest.main()
