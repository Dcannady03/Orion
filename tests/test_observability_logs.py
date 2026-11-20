"""Tests for the Orion log viewer (v0.4.0)."""
from __future__ import annotations

import json
import os
import uuid
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
from orion.application.events.models import EventSeverity, OrionEvent
from orion.application.events.store import EventStore
from orion.application.observability import (
    LogSearchRequest,
    LogViewer,
    ObservabilityApplicationHandler,
)
import orion.application.observability.logs as logs_module
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


def _event(event_type, occurred_at, severity=EventSeverity.INFO, data=None):
    return OrionEvent(
        event_id=f"event-{uuid.uuid4().hex}",
        event_type=event_type,
        occurred_at=occurred_at,
        source="test",
        severity=severity,
        data=data or {},
    )


def _seed_store(store):
    store.append(_event(
        "test.service.started", "2026-11-20T10:00:00Z",
        data={"message": "service started"},
    ))
    store.append(_event(
        "test.service.warned", "2026-11-20T10:05:00Z", EventSeverity.WARNING,
        data={"message": "disk space low"},
    ))
    store.append(_event(
        "test.service.failed", "2026-11-20T10:10:00Z", EventSeverity.ERROR,
        data={"message": "disk check failed"},
    ))


def _seed_log_file(logs_dir):
    (logs_dir / "app.log").write_text(
        "2026-11-20T10:02:00Z INFO Orion started\n"
        "2026-11-20T10:07:00Z ERROR Disk check failed\n"
        "2026-11-20T10:08:00Z WARNING Low disk space\n"
        "a line without a timestamp\n"
        "2026-11-20T10:09:00Z DEBUG verbose detail\n",
        encoding="utf-8",
    )


class LogViewerTests(TestCase):
    def setUp(self):
        # Ambient provider keys change onboarding/vault behavior; isolate tests.
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
        self.store = EventStore(self.paths.events)
        _seed_store(self.store)
        _seed_log_file(self.paths.logs)
        self.viewer = LogViewer(self.paths.logs, self.store)

    def test_events_newest_first(self):
        result = self.viewer.search(LogSearchRequest(source="events"))
        self.assertEqual(
            [entry.event_type for entry in result.entries],
            [
                "test.service.failed",
                "test.service.warned",
                "test.service.started",
            ],
        )

    def test_severity_filter_is_exact(self):
        result = self.viewer.search(
            LogSearchRequest(severity="error", source="events")
        )
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].event_type, "test.service.failed")

    def test_event_type_filter(self):
        result = self.viewer.search(LogSearchRequest(
            event_type="test.service.started", source="events",
        ))
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].severity, "info")

    def test_search_text_matches_message(self):
        result = self.viewer.search(
            LogSearchRequest(search="disk", source="events")
        )
        self.assertEqual(
            [entry.event_type for entry in result.entries],
            ["test.service.failed", "test.service.warned"],
        )

    def test_time_range_filter(self):
        result = self.viewer.search(LogSearchRequest(
            start_time="2026-11-20T10:04:00Z",
            end_time="2026-11-20T10:06:00Z",
            source="events",
        ))
        self.assertEqual(
            [entry.event_type for entry in result.entries],
            ["test.service.warned"],
        )

    def test_limit_applied(self):
        result = self.viewer.search(
            LogSearchRequest(limit=2, source="events")
        )
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.limit, 2)

    def test_files_source_reads_plain_logs(self):
        result = self.viewer.search(LogSearchRequest(source="files"))
        self.assertEqual(
            [(entry.severity, entry.occurred_at) for entry in result.entries],
            [
                ("debug", "2026-11-20T10:09:00Z"),
                ("warning", "2026-11-20T10:08:00Z"),
                ("error", "2026-11-20T10:07:00Z"),
                ("info", "2026-11-20T10:02:00Z"),
                ("info", ""),
            ],
        )
        self.assertEqual(result.entries[0].source, "app.log")

    def test_files_severity_filter(self):
        result = self.viewer.search(
            LogSearchRequest(severity="error", source="files")
        )
        self.assertEqual(len(result.entries), 1)
        self.assertIn("Disk check failed", result.entries[0].message)

    def test_files_time_range_excludes_untimestamped_lines(self):
        result = self.viewer.search(LogSearchRequest(
            start_time="2026-11-20T10:00:00Z",
            end_time="2026-11-20T10:10:00Z",
            source="files",
        ))
        self.assertEqual(len(result.entries), 4)
        self.assertTrue(all(entry.occurred_at for entry in result.entries))

    def test_all_source_merges_both_newest_first(self):
        result = self.viewer.search(LogSearchRequest(source="all"))
        self.assertEqual(len(result.entries), 8)
        self.assertEqual(result.entries[0].event_type, "test.service.failed")
        self.assertEqual(result.entries[-1].occurred_at, "")
        timestamps = [entry.occurred_at for entry in result.entries if entry.occurred_at]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_search_across_both_sources(self):
        result = self.viewer.search(LogSearchRequest(search="disk"))
        self.assertEqual(len(result.entries), 4)

    def test_unknown_severity_raises(self):
        with self.assertRaises(ValueError):
            self.viewer.search(LogSearchRequest(severity="nope"))

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            self.viewer.search(LogSearchRequest(source="nope"))

    def test_start_after_end_raises(self):
        with self.assertRaises(ValueError):
            self.viewer.search(LogSearchRequest(
                start_time="2026-11-20T11:00:00Z",
                end_time="2026-11-20T10:00:00Z",
            ))

    def test_bad_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.viewer.search(LogSearchRequest(start_time="not-a-time"))

    def test_limit_clamped_to_max(self):
        result = self.viewer.search(LogSearchRequest(limit=5000))
        self.assertEqual(result.limit, logs_module.MAX_LOG_LIMIT)

    def test_missing_sources_are_empty(self):
        viewer = LogViewer(Path(self.temp.name) / "no-logs-here", None)
        result = viewer.search(LogSearchRequest())
        self.assertEqual(result.entries, ())
        self.assertEqual(result.warnings, ())

    def test_oversized_file_skipped_with_warning(self):
        with patch.object(logs_module, "MAX_LOG_FILE_BYTES", 10):
            result = self.viewer.search(LogSearchRequest(source="files"))
        self.assertEqual(result.entries, ())
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Skipped app.log", result.warnings[0])


class LogHandlerTests(TestCase):
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
        self.store = EventStore(self.paths.events)
        _seed_store(self.store)
        _seed_log_file(self.paths.logs)
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, event_store=self.store
        )

    def test_logs_returns_entries_in_data(self):
        result = self.handler.logs(LogSearchRequest(source="events"))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["entries"]), 3)
        self.assertEqual(result.data["read_only"], True)
        self.assertIn("Found 3 log entries", result.message)

    def test_logs_empty_result_is_still_success(self):
        result = self.handler.logs(LogSearchRequest(search="zzz-no-match"))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["entries"]), 0)
        self.assertIn("No log entries matched", result.message)

    def test_logs_invalid_filter_is_failure(self):
        result = self.handler.logs(LogSearchRequest(severity="nope"))
        self.assertFalse(result.ok)
        self.assertIn("Log search failed", result.message)

    def test_logs_result_is_json_safe(self):
        result = self.handler.logs(LogSearchRequest(source="all"))
        payload = json.loads(result.to_json())
        self.assertEqual(len(payload["data"]["entries"]), 8)


class LogCliTests(TestCase):
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
        self.store = EventStore(self.paths.events)
        _seed_store(self.store)
        _seed_log_file(self.paths.logs)
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths, event_store=self.store
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_logs_renders_entries(self):
        result = self.adapter.handle("logs --source events")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Orion Log Search", rendered)
        self.assertIn("test.service.failed", rendered)
        self.assertIn("[ERROR]", rendered)

    def test_logs_json_output_parses(self):
        result = self.adapter.handle("logs --source events --json")
        self.assertTrue(result.ok)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(len(payload["data"]["entries"]), 3)

    def test_logs_unknown_option_is_usage(self):
        result = self.adapter.handle("logs --bogus")
        self.assertFalse(result.ok)
        self.assertIn("Unknown logs option", result.message)

    def test_logs_bad_limit_is_usage(self):
        result = self.adapter.handle("logs --limit abc")
        self.assertFalse(result.ok)
        self.assertIn("must be an integer", result.message)

    def test_logs_missing_value_is_usage(self):
        result = self.adapter.handle("logs --severity")
        self.assertFalse(result.ok)
        self.assertIn("needs a value", result.message)

    def test_dispatch_recognizes_logs_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "logs"))
            self.assertTrue(dispatch_observability(self.runtime, "  LOGS --severity error "))
        self.assertFalse(dispatch_observability(self.runtime, "log"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Log Search", rendered)

    def test_router_handles_logs(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("logs --source events")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Log Search", rendered)


class LogCoverageTests(TestCase):
    def test_logs_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("logs", help_text)
        self.assertIn("logs", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("logs", guide_text)
