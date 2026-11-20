"""Tests for the Orion diagnostics report (v0.4.6)."""
from __future__ import annotations

import json
import os
from collections import namedtuple
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
    DiagnosticsCollector,
    DiagnosticsReport,
    ObservabilityApplicationHandler,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.services.base import ServiceState, ServiceStatus
from orion.services.provider_manager import ProviderStatus
from orion.ui.console import BASE_COMMANDS


Usage = namedtuple("Usage", "total used free")

_GIB = 1024 ** 3


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _config(values=None, *, config=None):
    values = dict(values or {})
    return SimpleNamespace(
        config=config if config is not None else {"providers": {}, "workspace": {}},
        get=lambda key, default=None: values.get(key, default),
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/local.yaml"),
    )


def _providers(*statuses):
    return SimpleNamespace(statuses=lambda: list(statuses))


def _services(mapping):
    return SimpleNamespace(snapshot=lambda: dict(mapping))


def _service(state, message=""):
    return SimpleNamespace(get_status=lambda: ServiceStatus(state, message))


def _event_store(latest=None, warnings=()):
    return SimpleNamespace(latest_at=lambda: (latest, tuple(warnings)))


def _collector(paths, clock, **kwargs):
    defaults = {
        "config_manager": _config(
            {"workspace.default_path": str(paths.user_root)}
        ),
        "provider_manager": _providers(
            ProviderStatus("ollama", True, True, True, "llama3"),
            ProviderStatus("openai", True, True, False, "gpt-5"),
        ),
        "services": _services({
            "weather": _service(ServiceState.AVAILABLE, "Weather ready."),
        }),
        "event_store": _event_store("2026-11-20T11:00:00+00:00"),
        "disk_usage": lambda path: Usage(100 * _GIB, 50 * _GIB, 50 * _GIB),
        "clock": clock,
        "version": "0.7.0-test",
        "commit": "abc1234",
    }
    defaults.update(kwargs)
    return DiagnosticsCollector(paths, **defaults)


class DiagnosticsCollectorTests(TestCase):
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
        self.clock = _Clock()

    def test_all_sections_present(self):
        report = _collector(self.paths, self.clock).collect()
        self.assertIsInstance(report, DiagnosticsReport)
        self.assertEqual(report.generated_at, "2026-11-20T12:00:00+00:00")
        self.assertEqual(
            sorted(report.sections),
            [
                "config",
                "events",
                "health",
                "orion",
                "platform",
                "providers",
                "services",
                "storage",
            ],
        )

    def test_orion_section_uses_version_and_commit(self):
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["orion"]
        self.assertEqual(section["version"], "0.7.0-test")
        self.assertEqual(section["commit"], "abc1234")
        self.assertEqual(section["user_root"], str(self.paths.user_root))

    def test_git_fallback_when_not_a_repo(self):
        report = _collector(
            self.paths, self.clock, version=None, commit=None,
        ).collect()
        section = report.sections["orion"]
        self.assertEqual(section["commit"], "unknown")
        self.assertEqual(section["dirty"], "unknown")

    def test_health_section_reflects_checker(self):
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["health"]
        self.assertEqual(section["overall"], "ok")
        self.assertEqual(
            sorted(section["checks"]),
            ["config", "disk", "events", "providers", "services", "workspace"],
        )
        self.assertEqual(section["checks"]["disk"]["state"], "ok")

    def test_storage_section_comes_from_disk_check(self):
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["storage"]
        self.assertEqual(section["state"], "ok")
        self.assertEqual(section["total_bytes"], 100 * _GIB)
        self.assertEqual(section["free_bytes"], 50 * _GIB)
        self.assertEqual(section["free_percent"], 50.0)

    def test_providers_section_lists_status_flags(self):
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["providers"]
        self.assertTrue(section["available"])
        self.assertEqual(
            [row["key"] for row in section["providers"]],
            ["ollama", "openai"],
        )
        self.assertTrue(section["providers"][0]["active"])

    def test_services_section_lists_states(self):
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["services"]
        self.assertTrue(section["available"])
        self.assertEqual(section["registered"], 1)
        self.assertEqual(section["states"], {"weather": "ok"})
        self.assertEqual(section["problems"], [])

    def test_events_section_counts_daily_files(self):
        events_root = self.paths.events
        events_root.mkdir(parents=True, exist_ok=True)
        (events_root / "2026-11-19.jsonl").write_text("", encoding="utf-8")
        (events_root / "2026-11-20.jsonl").write_text("", encoding="utf-8")
        (events_root / "notes.txt").write_text("", encoding="utf-8")
        report = _collector(self.paths, self.clock).collect()
        section = report.sections["events"]
        self.assertEqual(section["daily_files"], 2)
        self.assertEqual(section["latest_event_at"], "2026-11-20T11:00:00+00:00")

    def test_report_contains_no_secrets(self):
        secret = "sk-test-secret-value-12345"
        os.environ["OPENAI_API_KEY"] = secret
        report = _collector(self.paths, self.clock).collect()
        dumped = json.dumps(report.to_dict())
        self.assertNotIn(secret, dumped)
        self.assertNotIn("OPENAI_API_KEY", dumped)
        self.assertNotIn("GEMINI_API_KEY", dumped)

    def test_broken_section_isolated(self):
        collector = _collector(self.paths, self.clock)
        collector._section_platform = lambda: 1 / 0  # noqa: SLF001 - white-box isolation check
        report = collector.collect()
        self.assertIn("error", report.sections["platform"])
        self.assertEqual(report.sections["health"]["overall"], "ok")

    def test_report_serializes_to_json_safe_dict(self):
        report = _collector(self.paths, self.clock).collect()
        data = report.to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_missing_dependencies_report_unknown(self):
        collector = DiagnosticsCollector(
            self.paths, clock=self.clock, version="x", commit="y",
        )
        report = collector.collect()
        self.assertEqual(report.sections["providers"]["available"], False)
        self.assertEqual(report.sections["services"]["available"], False)
        self.assertEqual(report.sections["health"]["overall"], "warning")


class DiagnosticsHandlerTests(TestCase):
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
            paths=self.paths,
            config_manager=_config(
                {"workspace.default_path": str(self.paths.user_root)}
            ),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
            ),
            services=_services({
                "weather": _service(ServiceState.AVAILABLE, "Weather ready."),
            }),
            event_store=_event_store("2026-11-20T11:00:00+00:00"),
            clock=_Clock(),
        )

    def test_diagnostics_returns_sections_in_data(self):
        result = self.handler.diagnostics()
        self.assertTrue(result.ok)
        self.assertIn("Orion Diagnostics", result.message)
        self.assertEqual(result.data["read_only"], True)
        self.assertIn("health", result.data["sections"])
        self.assertIn("platform", result.data["sections"])

    def test_diagnostics_result_is_json_safe(self):
        result = self.handler.diagnostics()
        payload = json.loads(result.to_json())
        self.assertEqual(
            sorted(payload["data"]["sections"]),
            [
                "config",
                "events",
                "health",
                "orion",
                "platform",
                "providers",
                "services",
                "storage",
            ],
        )


class DiagnosticsCliTests(TestCase):
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
            paths=self.paths,
            config_manager=_config(
                {"workspace.default_path": str(self.paths.user_root)}
            ),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
            ),
            services=_services({
                "weather": _service(ServiceState.AVAILABLE, "Weather ready."),
            }),
            event_store=_event_store("2026-11-20T11:00:00+00:00"),
            clock=_Clock(),
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_diagnostics_renders_sections(self):
        result = self.adapter.handle("diagnostics")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Orion Diagnostics", rendered)
        self.assertIn("Health:", rendered)
        self.assertIn("Platform:", rendered)

    def test_diagnostics_json_output_parses(self):
        result = self.adapter.handle("diagnostics --json")
        self.assertTrue(result.ok)
        payload = json.loads(self.output.getvalue())
        self.assertIn("orion", payload["data"]["sections"])

    def test_diagnostics_unknown_option_is_usage(self):
        result = self.adapter.handle("diagnostics --bogus")
        self.assertFalse(result.ok)
        self.assertIn("Unknown diagnostics option", result.message)

    def test_dispatch_recognizes_diagnostics_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "diagnostics"))
            self.assertTrue(dispatch_observability(self.runtime, "  DIAGNOSTICS "))
        self.assertFalse(dispatch_observability(self.runtime, "diagnose"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Diagnostics", rendered)

    def test_router_handles_diagnostics(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("diagnostics")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Diagnostics", rendered)


class DiagnosticsCoverageTests(TestCase):
    def test_diagnostics_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("diagnostics", help_text)
        self.assertIn("diagnostics", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("diagnostics", guide_text)
