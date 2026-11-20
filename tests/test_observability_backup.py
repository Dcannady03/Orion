"""Tests for Orion backup export/import (v0.4.2)."""
from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from orion.application.commands.observability_cli import (
    ObservabilityCliAdapter,
    dispatch_observability,
)
from orion.application.observability import (
    BackupService,
    ObservabilityApplicationHandler,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _seed_user_data(user_root: Path) -> None:
    (user_root / "config.yaml").write_text("workspace:\n  default_path: .\n", encoding="utf-8")
    (user_root / "profile.yaml").write_text("name: Daniel\n", encoding="utf-8")
    events = user_root / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "2026-11-20.jsonl").write_text('{"event": "test"}\n', encoding="utf-8")
    missions = user_root / "missions"
    missions.mkdir(parents=True, exist_ok=True)
    (missions / "m1.json").write_text('{"mission": 1}\n', encoding="utf-8")
    vault = user_root / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "vault.yaml").write_text("secret: hunter2\n", encoding="utf-8")
    tokens = user_root / "tokens"
    tokens.mkdir(parents=True, exist_ok=True)
    (tokens / "token.json").write_text('{"token": "abc"}\n', encoding="utf-8")
    logs = user_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "app.log").write_text("log line\n", encoding="utf-8")


def _rewrite_zip(source: Path, target: Path, replace=None, manifest=None):
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if replace and item.filename in replace:
                data = replace[item.filename]
            if manifest is not None and item.filename == "manifest.json":
                data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            dst.writestr(item, data)


def _read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))


class BackupServiceTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        _seed_user_data(self.paths.user_root)
        self.service = BackupService(self.paths, clock=_Clock())

    def test_export_creates_zip_with_manifest(self):
        export = self.service.export("before-test")
        self.assertTrue(export.path.is_file())
        self.assertEqual(export.name, "orion-backup-before-test-20261120-120000.zip")
        self.assertEqual(export.file_count, 4)
        manifest = _read_manifest(export.path)
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["exported_at"], "2026-11-20T12:00:00+00:00")
        self.assertIn("vault", manifest["excluded"])
        self.assertIn("tokens", manifest["excluded"])
        with zipfile.ZipFile(export.path) as archive:
            names = archive.namelist()
        self.assertIn("config.yaml", names)
        self.assertIn("events/2026-11-20.jsonl", names)
        self.assertIn("missions/m1.json", names)
        self.assertNotIn("vault/vault.yaml", names)
        self.assertNotIn("tokens/token.json", names)
        self.assertNotIn("logs/app.log", names)

    def test_export_name_validation(self):
        for bad in ("../escape", "a/b", "a b", "x" * 65, "-leading"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    self.service.export(bad)

    def test_export_duplicate_name_raises(self):
        self.service.export("same")
        with self.assertRaises(ValueError):
            self.service.export("same")

    def test_verify_passes_on_good_export(self):
        export = self.service.export()
        verification = self.service.verify(export.path)
        self.assertTrue(verification.valid)
        self.assertEqual(verification.errors, ())
        self.assertEqual(verification.file_count, 4)

    def test_verify_fails_on_tampered_file(self):
        export = self.service.export()
        tampered = self.paths.backups / "tampered.zip"
        _rewrite_zip(
            export.path, tampered,
            replace={"config.yaml": b"tampered: true\n"},
        )
        verification = self.service.verify(tampered)
        self.assertFalse(verification.valid)
        self.assertTrue(
            any("Checksum mismatch: config.yaml" in e for e in verification.errors)
        )

    def test_verify_fails_without_manifest(self):
        plain = self.paths.backups / "plain.zip"
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("config.yaml", "x")
        verification = self.service.verify(plain)
        self.assertFalse(verification.valid)
        self.assertIn("no manifest", verification.errors[0])

    def test_verify_fails_on_traversal_path(self):
        export = self.service.export()
        manifest = _read_manifest(export.path)
        manifest["files"].append(
            {"path": "../evil.txt", "size_bytes": 1, "sha256": "00"}
        )
        crafted = self.paths.backups / "crafted.zip"
        _rewrite_zip(export.path, crafted, manifest=manifest)
        verification = self.service.verify(crafted)
        self.assertFalse(verification.valid)
        self.assertTrue(
            any("unsafe path" in e for e in verification.errors)
        )

    def test_verify_fails_on_missing_file(self):
        export = self.service.export()
        manifest = _read_manifest(export.path)
        manifest["files"].append(
            {"path": "ghost.txt", "size_bytes": 1, "sha256": "00"}
        )
        crafted = self.paths.backups / "crafted.zip"
        _rewrite_zip(export.path, crafted, manifest=manifest)
        verification = self.service.verify(crafted)
        self.assertFalse(verification.valid)
        self.assertTrue(
            any("missing file: ghost.txt" in e for e in verification.errors)
        )

    def test_verify_fails_on_missing_archive(self):
        verification = self.service.verify(self.paths.backups / "nope.zip")
        self.assertFalse(verification.valid)

    def test_list_backups(self):
        self.service.export("one")
        self.service.export("two")
        found = self.service.list_backups()
        self.assertEqual(
            {info.name for info in found},
            {
                "orion-backup-one-20261120-120000.zip",
                "orion-backup-two-20261120-120000.zip",
            },
        )
        self.assertTrue(all(info.format_version == 1 for info in found))

    def test_plan_import_reports_overwrite_and_new(self):
        export = self.service.export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        (self.paths.user_root / "events" / "2026-11-20.jsonl").unlink()
        plan = self.service.plan_import(export.path)
        self.assertIn("config.yaml", plan.overwrite)
        self.assertIn("events/2026-11-20.jsonl", plan.new)
        self.assertTrue(plan.safety_backup_name.startswith("pre-import-"))

    def test_import_requires_confirmation(self):
        export = self.service.export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.import_backup(export.path)
        self.assertEqual(
            (self.paths.user_root / "config.yaml").read_text(encoding="utf-8"),
            "changed: true\n",
        )

    def test_import_restores_files_and_writes_safety_backup(self):
        export = self.service.export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        changed_bytes = (self.paths.user_root / "config.yaml").read_bytes()
        (self.paths.user_root / "events" / "2026-11-20.jsonl").unlink()
        result = self.service.import_backup(export.path, confirm=True)
        self.assertEqual(result.restored, 4)
        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())
        self.assertEqual(
            (self.paths.user_root / "config.yaml").read_text(encoding="utf-8"),
            "workspace:\n  default_path: .\n",
        )
        self.assertTrue(
            (self.paths.user_root / "events" / "2026-11-20.jsonl").is_file()
        )
        with zipfile.ZipFile(result.safety_backup) as archive:
            self.assertEqual(archive.read("config.yaml"), changed_bytes)

    def test_import_of_tampered_archive_fails_and_changes_nothing(self):
        export = self.service.export()
        tampered = self.paths.backups / "tampered.zip"
        _rewrite_zip(
            export.path, tampered,
            replace={"config.yaml": b"tampered: true\n"},
        )
        (self.paths.user_root / "config.yaml").write_text("current: true\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.import_backup(tampered, confirm=True)
        self.assertEqual(
            (self.paths.user_root / "config.yaml").read_text(encoding="utf-8"),
            "current: true\n",
        )
        self.assertEqual(
            list(self.paths.backups.glob("pre-import-*.zip")), []
        )


class BackupHandlerTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        _seed_user_data(self.paths.user_root)
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )

    def test_backup_export_returns_details(self):
        result = self.handler.backup_export("manual")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["file_count"], 4)
        self.assertIn("Vault and tokens were excluded", result.message)

    def test_backup_list_empty_then_populated(self):
        result = self.handler.backup_list()
        self.assertTrue(result.ok)
        self.assertIn("No backups found", result.message)
        self.handler.backup_export()
        result = self.handler.backup_list()
        self.assertIn("Orion Backups", result.message)
        self.assertEqual(len(result.data["backups"]), 1)

    def test_backup_verify_ok_and_tampered(self):
        export = self.handler.backup_export()
        path = str(export.data["path"])
        result = self.handler.backup_verify(path)
        self.assertTrue(result.ok)
        self.assertIn("checksums OK", result.message)
        tampered = self.paths.backups / "tampered.zip"
        _rewrite_zip(
            Path(path), tampered,
            replace={"config.yaml": b"tampered: true\n"},
        )
        result = self.handler.backup_verify(str(tampered))
        self.assertFalse(result.ok)
        self.assertIn("Checksum mismatch", result.message)

    def test_backup_import_plan(self):
        export = self.handler.backup_export()
        result = self.handler.backup_import_plan(str(export.data["path"]))
        self.assertTrue(result.ok)
        self.assertIn("Backup Import Plan", result.message)
        self.assertEqual(len(result.data["overwrite"]), 4)

    def test_backup_import_without_confirm_is_failure(self):
        export = self.handler.backup_export()
        result = self.handler.backup_import(str(export.data["path"]))
        self.assertFalse(result.ok)
        self.assertIn("requires explicit confirmation", result.message)

    def test_backup_import_with_confirm_restores(self):
        export = self.handler.backup_export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        result = self.handler.backup_import(str(export.data["path"]), confirm=True)
        self.assertTrue(result.ok)
        self.assertIn("4 file(s) restored", result.message)
        self.assertIn("Safety backup", result.message)


class BackupCliTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        _seed_user_data(self.paths.user_root)
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.answers = []
        self.adapter = ObservabilityCliAdapter(
            self.runtime,
            output_provider=self.output.write,
            input_provider=lambda prompt: self.answers.pop(0) if self.answers else "n",
        )

    def test_backup_export_renders(self):
        result = self.adapter.handle("backup export --name cli-test")
        self.assertTrue(result.ok)
        self.assertIn("Backup exported", self.output.getvalue())

    def test_backup_list_renders(self):
        self.adapter.handle("backup export")
        self.output.truncate(0)
        self.output.seek(0)
        result = self.adapter.handle("backup list")
        self.assertTrue(result.ok)
        self.assertIn("Orion Backups", self.output.getvalue())

    def test_backup_verify_renders(self):
        export = self.handler.backup_export()
        result = self.adapter.handle(f"backup verify {export.data['path']}")
        self.assertTrue(result.ok)
        self.assertIn("checksums OK", self.output.getvalue())

    def test_backup_verify_accepts_quoted_windows_path(self):
        export = self.handler.backup_export()
        result = self.adapter.handle(f'backup verify "{export.data["path"]}"')
        self.assertTrue(result.ok)
        self.assertIn("checksums OK", self.output.getvalue())

    def test_backup_import_approves_and_restores(self):
        export = self.handler.backup_export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        self.answers.append("y")
        result = self.adapter.handle(f"backup import {export.data['path']}")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Backup Import Plan", rendered)
        self.assertIn("Sensitive action", rendered)
        self.assertIn("Backup imported", rendered)
        self.assertEqual(
            (self.paths.user_root / "config.yaml").read_text(encoding="utf-8"),
            "workspace:\n  default_path: .\n",
        )

    def test_backup_import_declines_and_changes_nothing(self):
        export = self.handler.backup_export()
        (self.paths.user_root / "config.yaml").write_text("changed: true\n", encoding="utf-8")
        self.answers.append("n")
        result = self.adapter.handle(f"backup import {export.data['path']}")
        self.assertFalse(result.ok)
        self.assertIn("Import cancelled", result.message)
        self.assertEqual(
            (self.paths.user_root / "config.yaml").read_text(encoding="utf-8"),
            "changed: true\n",
        )

    def test_backup_import_json_rejected(self):
        export = self.handler.backup_export()
        result = self.adapter.handle(f"backup import {export.data['path']} --json")
        self.assertFalse(result.ok)
        self.assertIn("does not support --json", result.message)

    def test_backup_unknown_subcommand_is_usage(self):
        result = self.adapter.handle("backup frobnicate")
        self.assertFalse(result.ok)
        self.assertIn("Unknown backup subcommand", result.message)

    def test_backup_export_bad_name_is_failure(self):
        result = self.adapter.handle("backup export --name ../escape")
        self.assertFalse(result.ok)
        self.assertIn("Backup export failed", result.message)

    def test_dispatch_recognizes_backup_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "backup list"))
            self.assertTrue(dispatch_observability(self.runtime, "  BACKUP list "))
        self.assertFalse(dispatch_observability(self.runtime, "backups"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("No backups found", rendered)

    def test_router_handles_backup_list(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("backup list")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("No backups found", rendered)


class BackupCoverageTests(TestCase):
    def test_backup_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("backup export", help_text)
        self.assertIn("backup import", help_text)
        for command in (
            "backup", "backup export", "backup list",
            "backup verify", "backup import",
        ):
            self.assertIn(command, BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("backup export", guide_text)
        self.assertIn("backup import", guide_text)
