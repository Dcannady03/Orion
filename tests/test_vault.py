import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orion.core.config import ConfigManager
from orion.intelligence.secrets import SecretStore
from orion.services.vault import VaultService


class VaultTests(unittest.TestCase):
    def config(self, root):
        manager = ConfigManager(str(Path(root) / "default.yaml"))
        manager.config = {
            "providers": {
                "default": "ollama",
                "secrets_path": str(Path(root) / ".orion" / "secrets.yaml"),
                "openai": {"enabled": False},
                "gemini": {"enabled": False},
            },
            "vault": {"path": str(Path(root) / ".orion" / "vault.yaml")},
        }
        manager.save()
        return manager

    def test_add_lists_and_removes_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            vault = VaultService(config)
            vault.add("gemini", "gem-test")
            entries = {item.key: item for item in vault.list_entries()}
            self.assertTrue(entries["gemini"].configured)
            self.assertEqual(entries["gemini"].source, "local vault")
            self.assertNotIn("gem-test", (Path(tmp) / "default.yaml").read_text(encoding="utf-8"))
            vault.remove("gemini")
            self.assertFalse(vault.store.get("gemini"))
            self.assertFalse(config.get("providers.gemini.enabled"))

    def test_environment_variable_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp) / "vault.yaml")
            store.set("gemini", "file-key")
            with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}):
                self.assertEqual(store.get("gemini"), "env-key")
                self.assertIn("environment", store.source("gemini"))

    def test_health_checks_configured_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            vault = VaultService(config)
            vault.add("gemini", "gem-test")
            results = {item.key: item for item in vault.health(lambda key: ["model-a"])}
            self.assertTrue(results["gemini"].healthy)
            self.assertIn("1 compatible", results["gemini"].message)
            self.assertFalse(results["openai"].configured)

    def test_migrates_legacy_secret_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            legacy = SecretStore(config.get("providers.secrets_path"))
            legacy.set("openai", "legacy-key")
            vault = VaultService(config)
            self.assertTrue(vault.migrate_legacy_store())
            self.assertEqual(vault.store.get("openai"), "legacy-key")


if __name__ == "__main__":
    unittest.main()
