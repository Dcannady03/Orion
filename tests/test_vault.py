import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.config import ConfigManager
from orion.core.paths import OrionPaths
from orion.intelligence.secrets import SecretStore
from orion.services.vault import ProviderVerificationError, VaultService


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

    def test_relative_vault_path_is_resolved_under_external_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OrionPaths(root / "app", root / "user")
            values = {"vault.path": "vault/vault.yaml"}
            config = SimpleNamespace(paths=paths, get=lambda key, default=None: values.get(key, default))
            vault = VaultService(config)
            self.assertEqual(vault.path, root / "user" / "vault" / "vault.yaml")

    def test_recovers_discord_bot_token_from_latest_application_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OrionPaths(root / "app", root / "user")
            values = {
                "vault.path": "vault/vault.yaml",
                "providers.secrets_path": "vault/vault.yaml",
            }
            config = SimpleNamespace(paths=paths, get=lambda key, default=None: values.get(key, default))
            old = paths.backups / "application-20260716-120000-old" / "application" / "vault" / "vault.yaml"
            SecretStore(old).set("discord_bot", "preserved-token")

            vault = VaultService(config)
            self.assertTrue(vault.migrate_legacy_store())
            self.assertEqual(vault.store.get("discord_bot"), "preserved-token")
            self.assertEqual(vault.path, paths.vault)

    def test_existing_discord_bot_token_is_not_overwritten_by_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OrionPaths(root / "app", root / "user")
            values = {
                "vault.path": "vault/vault.yaml",
                "providers.secrets_path": "vault/vault.yaml",
            }
            config = SimpleNamespace(paths=paths, get=lambda key, default=None: values.get(key, default))
            SecretStore(paths.vault).set("discord_bot", "current-token")
            backup = paths.backups / "application-20260716-120000-old" / "application" / "vault" / "vault.yaml"
            SecretStore(backup).set("discord_bot", "older-backup-token")

            vault = VaultService(config)
            self.assertFalse(vault.migrate_legacy_store())
            self.assertEqual(vault.store.get("discord_bot"), "current-token")

    def test_verified_provider_commit_saves_secret_only_in_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            manager = SimpleNamespace(
                verify_credentials=Mock(return_value=["gpt-test", "gpt-alt"])
            )
            vault = VaultService(config, manager)

            verified = vault.verify_provider("openai", "candidate-secret")
            self.assertNotIn("candidate-secret", repr(verified))
            self.assertFalse(vault.store.get_file_value("openai"))

            models = vault.commit_provider(verified, model="gpt-alt")

            self.assertEqual(models, ("gpt-test", "gpt-alt"))
            self.assertEqual(vault.store.get_file_value("openai"), "candidate-secret")
            self.assertTrue(config.get("providers.openai.enabled"))
            self.assertEqual(config.get("providers.openai.model"), "gpt-alt")
            self.assertNotIn(
                "candidate-secret",
                Path(config.config_path).read_text(encoding="utf-8"),
            )

    def test_failed_provider_verification_preserves_existing_secret_and_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.set("providers.default", "openai")
            config.set("providers.openai.enabled", True)
            config.save()
            manager = SimpleNamespace(
                verify_credentials=Mock(side_effect=ConnectionError("rejected secret-value"))
            )
            vault = VaultService(config, manager)
            vault.store.set("openai", "working-secret")

            with self.assertRaises(ProviderVerificationError) as raised:
                vault.verify_provider("openai", "replacement-secret")

            self.assertNotIn("replacement-secret", str(raised.exception))
            self.assertNotIn("secret-value", str(raised.exception))
            self.assertEqual(vault.store.get_file_value("openai"), "working-secret")
            self.assertEqual(config.get("providers.default"), "openai")


if __name__ == "__main__":
    unittest.main()
