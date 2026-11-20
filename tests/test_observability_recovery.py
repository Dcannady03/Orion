"""Tests for the Orion recovery history (transparent recovery record)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamRollbackRequest,
)
from orion.application.commands.observability_cli import (
    ObservabilityCliAdapter,
    dispatch_observability,
)
from orion.application.observability import (
    ObservabilityApplicationHandler,
    RecoveryHistoryStore,
    RecoveryRecord,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class RecoveryStoreTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecoveryHistoryStore(
            Path(self.temp.name) / "observability", clock=_Clock()
        )

    def test_record_and_query_newest_first(self):
        first = self.store.record("backup-import", "a.zip", detail="restored 4")
        second = self.store.record("team-rollback", "run-1", detail="rolled back")
        query = self.store.query()
        self.assertEqual(query.total, 2)
        self.assertEqual(query.skipped, 0)
        self.assertEqual(
            [entry.record_id for entry in query.records],
            [second.record_id, first.record_id],
        )
        self.assertEqual(query.records[1].kind, "backup-import")
        self.assertEqual(query.records[1].status, "success")
        self.assertEqual(query.records[1].occurred_at, "2026-11-20T12:00:00+00:00")
        self.assertTrue(first.record_id.startswith("rec-"))

    def test_query_filters_by_kind_and_status(self):
        self.store.record("backup-import", "a.zip")
        self.store.record("team-rollback", "run-1", status="failed", detail="boom")
        self.store.record("update-rollback", "abc1234")
        self.assertEqual(
            [entry.kind for entry in self.store.query(kind="team-rollback").records],
            ["team-rollback"],
        )
        self.assertEqual(
            [entry.kind for entry in self.store.query(status="failed").records],
            ["team-rollback"],
        )
        self.assertEqual(
            len(self.store.query(kind="backup-import", status="failed").records), 0
        )

    def test_query_limit(self):
        for index in range(5):
            self.store.record("backup-import", f"{index}.zip")
        query = self.store.query(limit=2)
        self.assertEqual(query.total, 5)
        self.assertEqual(len(query.records), 2)
        self.assertEqual(query.records[0].target, "4.zip")

    def test_get_by_id(self):
        entry = self.store.record("backup-import", "a.zip")
        found = self.store.get(entry.record_id)
        self.assertEqual(found, entry)
        self.assertIsNone(self.store.get("rec-doesnotexist"))

    def test_bad_kind_and_status_raise(self):
        with self.assertRaises(ValueError):
            self.store.record("nope", "x")
        with self.assertRaises(ValueError):
            self.store.record("backup-import", "x", status="nope")
        with self.assertRaises(ValueError):
            self.store.query(kind="nope")
        with self.assertRaises(ValueError):
            self.store.query(status="nope")

    def test_malformed_lines_skipped(self):
        self.store.record("backup-import", "a.zip")
        with open(self.store.path, "a", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write('{"kind": "backup-import"}\n')
        query = self.store.query()
        self.assertEqual(query.total, 1)
        self.assertEqual(query.skipped, 2)

    def test_missing_file_is_empty(self):
        query = self.store.query()
        self.assertEqual(query.records, ())
        self.assertEqual(query.total, 0)

    def test_record_serializes_to_json_safe_dict(self):
        entry = self.store.record("backup-import", "a.zip", safety_backup="pre.zip")
        self.assertEqual(json.loads(json.dumps(entry.to_dict())), entry.to_dict())


class RecoveryHandlerTests(TestCase):
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
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )

    def test_record_recovery_returns_record(self):
        entry = self.handler.record_recovery(
            "team-rollback", "run-1", detail="rolled back"
        )
        self.assertIsInstance(entry, RecoveryRecord)
        self.assertEqual(entry.kind, "team-rollback")

    def test_record_recovery_rejects_bad_kind(self):
        with self.assertRaises(ValueError):
            self.handler.record_recovery("nope", "x")

    def test_recovery_list_empty(self):
        result = self.handler.recovery_list()
        self.assertTrue(result.ok)
        self.assertIn("No recoveries recorded yet", result.message)

    def test_recovery_list_renders_records(self):
        self.handler.record_recovery("backup-import", "a.zip", detail="4 restored")
        self.handler.record_recovery("team-rollback", "run-1", status="failed", detail="boom")
        result = self.handler.recovery_list()
        self.assertTrue(result.ok)
        self.assertIn("Orion Recovery History", result.message)
        self.assertIn("team-rollback", result.message)
        self.assertEqual(len(result.data["records"]), 2)
        self.assertEqual(result.data["total"], 2)

    def test_recovery_list_filters(self):
        self.handler.record_recovery("backup-import", "a.zip")
        self.handler.record_recovery("team-rollback", "run-1")
        result = self.handler.recovery_list(kind="team-rollback")
        self.assertEqual(len(result.data["records"]), 1)
        result = self.handler.recovery_list(kind="nope")
        self.assertFalse(result.ok)
        self.assertIn("Recovery list failed", result.message)

    def test_recovery_show(self):
        entry = self.handler.record_recovery(
            "update-rollback", "abc1234", detail="restored", safety_backup="pre.zip"
        )
        result = self.handler.recovery_show(entry.record_id)
        self.assertTrue(result.ok)
        self.assertIn("Recovery rec-", result.message)
        self.assertIn("abc1234", result.message)
        self.assertIn("pre.zip", result.message)
        missing = self.handler.recovery_show("rec-doesnotexist")
        self.assertFalse(missing.ok)
        self.assertIn("No recovery record", missing.message)

    def test_recovery_result_is_json_safe(self):
        self.handler.record_recovery("backup-import", "a.zip")
        result = self.handler.recovery_list()
        payload = json.loads(result.to_json())
        self.assertEqual(len(payload["data"]["records"]), 1)

    def test_backup_import_is_recorded(self):
        from orion.application.observability import BackupService

        (self.paths.user_root / "config.yaml").write_text("x: 1\n", encoding="utf-8")
        export = BackupService(self.paths, clock=_Clock()).export()
        (self.paths.user_root / "config.yaml").write_text("y: 2\n", encoding="utf-8")
        result = self.handler.backup_import(str(export.path), confirm=True)
        self.assertTrue(result.ok)
        history = self.handler.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        self.assertEqual(history.data["records"][0]["kind"], "backup-import")
        self.assertEqual(history.data["records"][0]["status"], "success")

    def test_failed_backup_import_is_recorded(self):
        result = self.handler.backup_import(
            str(self.paths.backups / "missing.zip"), confirm=True
        )
        self.assertFalse(result.ok)
        history = self.handler.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        self.assertEqual(history.data["records"][0]["status"], "failed")


class TeamRollbackHookTests(TestCase):
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
        self.observability = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )
        self.rolled_back = SimpleNamespace(
            run_id="run-1",
            status="rolled_back",
            files_changed=(),
            files_created=(),
            files_deleted=(),
            preimages=(),
        )
        self.bridge = SimpleNamespace(rollback=lambda run_id: self.rolled_back)
        self.runtime = SimpleNamespace(
            codex_bridge=self.bridge,
            observability_application=self.observability,
        )
        self.handler = AiTeamApplicationHandler(self.runtime)

    def test_successful_team_rollback_is_recorded(self):
        result = self.handler.rollback(
            TeamRollbackRequest(run_id="run-1", confirmed=True)
        )
        self.assertTrue(result.ok)
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        record = history.data["records"][0]
        self.assertEqual(record["kind"], "team-rollback")
        self.assertEqual(record["target"], "run-1")
        self.assertEqual(record["status"], "success")

    def test_failed_team_rollback_is_recorded(self):
        self.bridge.rollback = lambda run_id: (_ for _ in ()).throw(
            FileNotFoundError("run not found")
        )
        result = self.handler.rollback(
            TeamRollbackRequest(run_id="run-1", confirmed=True)
        )
        self.assertFalse(result.ok)
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        self.assertEqual(history.data["records"][0]["status"], "failed")

    def test_unconfirmed_team_rollback_is_not_recorded(self):
        result = self.handler.rollback(
            TeamRollbackRequest(run_id="run-1", confirmed=False)
        )
        self.assertFalse(result.ok)
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 0)

    def test_rollback_without_observability_still_works(self):
        runtime = SimpleNamespace(codex_bridge=self.bridge)
        handler = AiTeamApplicationHandler(runtime)
        result = handler.rollback(
            TeamRollbackRequest(run_id="run-1", confirmed=True)
        )
        self.assertTrue(result.ok)


class UpdateRollbackHookTests(TestCase):
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
        self.observability = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )
        self.update_result = SimpleNamespace(
            current="abc1234def5678", backup="backups/pre.zip"
        )
        self.runtime = SimpleNamespace(
            update_service=SimpleNamespace(rollback=lambda: self.update_result),
            observability_application=self.observability,
        )
        self.router = CommandRouter(self.runtime)

    def test_successful_update_rollback_is_recorded(self):
        with patch("builtins.input", return_value="y"), patch("builtins.print"):
            self.router.update_rollback()
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        record = history.data["records"][0]
        self.assertEqual(record["kind"], "update-rollback")
        self.assertEqual(record["target"], "abc1234def56")
        self.assertEqual(record["status"], "success")

    def test_cancelled_update_rollback_is_not_recorded(self):
        with patch("builtins.input", return_value="n"), patch("builtins.print"):
            self.router.update_rollback()
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 0)

    def test_failed_update_rollback_is_recorded(self):
        self.runtime.update_service.rollback = lambda: (_ for _ in ()).throw(
            RuntimeError("disk full")
        )
        with patch("builtins.input", return_value="y"), patch("builtins.print"):
            self.router.update_rollback()
        history = self.observability.recovery_list()
        self.assertEqual(len(history.data["records"]), 1)
        self.assertEqual(history.data["records"][0]["status"], "failed")


class RecoveryCliTests(TestCase):
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
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, clock=_Clock()
        )
        self.entry = self.handler.record_recovery(
            "backup-import", "a.zip", detail="4 restored"
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_recovery_renders_list(self):
        result = self.adapter.handle("recovery")
        self.assertTrue(result.ok)
        self.assertIn("Orion Recovery History", self.output.getvalue())
        self.assertIn("a.zip", self.output.getvalue())

    def test_recovery_show_renders_detail(self):
        result = self.adapter.handle(f"recovery {self.entry.record_id}")
        self.assertTrue(result.ok)
        self.assertIn("Recovery rec-", self.output.getvalue())
        self.assertIn("4 restored", self.output.getvalue())

    def test_recovery_json_output_parses(self):
        result = self.adapter.handle("recovery --json")
        self.assertTrue(result.ok)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(len(payload["data"]["records"]), 1)

    def test_recovery_unknown_option_is_usage(self):
        result = self.adapter.handle("recovery --bogus")
        self.assertFalse(result.ok)
        self.assertIn("Unknown recovery option", result.message)

    def test_recovery_bad_limit_is_usage(self):
        result = self.adapter.handle("recovery --limit abc")
        self.assertFalse(result.ok)
        self.assertIn("must be an integer", result.message)

    def test_dispatch_recognizes_recovery_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "recovery"))
            self.assertTrue(dispatch_observability(self.runtime, "  RECOVERY --limit 5 "))
        self.assertFalse(dispatch_observability(self.runtime, "recover"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Recovery History", rendered)

    def test_router_handles_recovery(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("recovery")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Recovery History", rendered)


class RecoveryCoverageTests(TestCase):
    def test_recovery_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("recovery", help_text)
        self.assertIn("recovery", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("recovery", guide_text)
