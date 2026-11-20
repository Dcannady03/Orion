"""Tests for the Orion unified health check (v0.4.1)."""
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
    HealthChecker,
    HealthReport,
    HealthState,
    ObservabilityApplicationHandler,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.services.base import ServiceState, ServiceStatus
from orion.services.provider_manager import ProviderStatus
from orion.ui.console import BASE_COMMANDS


Usage = namedtuple("Usage", "total used free")

_GIB = 1024 ** 3
_MIB = 1024 ** 2


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


class HealthCheckTests(TestCase):
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

    def checker(self, **kwargs):
        defaults = {
            "config_manager": _config(
                {"workspace.default_path": str(self.paths.user_root)}
            ),
            "provider_manager": _providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
                ProviderStatus("openai", True, True, False, "gpt-5"),
                ProviderStatus("gemini", True, False, False, ""),
            ),
            "services": _services({
                "weather": _service(ServiceState.AVAILABLE, "Weather ready."),
                "email": _service(ServiceState.AVAILABLE, "Mail connected."),
            }),
            "event_store": _event_store("2026-11-20T11:00:00+00:00"),
            "disk_usage": lambda path: Usage(100 * _GIB, 50 * _GIB, 50 * _GIB),
            "clock": self.clock,
        }
        defaults.update(kwargs)
        return HealthChecker(self.paths, **defaults)

    def test_full_check_is_ok_when_everything_is_healthy(self):
        report = self.checker().check()
        self.assertIsInstance(report, HealthReport)
        self.assertEqual(report.overall, HealthState.OK)
        self.assertEqual(report.generated_at, "2026-11-20T12:00:00+00:00")
        self.assertEqual(
            [check.name for check in report.checks],
            ["disk", "providers", "services", "config", "workspace", "events"],
        )
        self.assertTrue(all(check.state is HealthState.OK for check in report.checks))

    def test_report_serializes_to_json_safe_dict(self):
        report = self.checker().check()
        data = report.to_dict()
        self.assertEqual(data["overall"], "ok")
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_disk_warning_below_one_gib_free(self):
        report = self.checker(
            disk_usage=lambda path: Usage(8 * _GIB, 8 * _GIB - 512 * _MIB, 512 * _MIB)
        ).check("disk")
        self.assertEqual(report.overall, HealthState.WARNING)
        self.assertIn("512.0 MB free", report.checks[0].detail)

    def test_disk_error_below_256_mib_free(self):
        report = self.checker(
            disk_usage=lambda path: Usage(100 * _GIB, 100 * _GIB - 100 * _MIB, 100 * _MIB)
        ).check("disk")
        self.assertEqual(report.overall, HealthState.ERROR)

    def test_disk_error_below_five_percent_free(self):
        report = self.checker(
            disk_usage=lambda path: Usage(1000 * _GIB, 960 * _GIB, 40 * _GIB)
        ).check("disk")
        self.assertEqual(report.overall, HealthState.ERROR)

    def test_disk_check_failure_is_unknown_not_fatal(self):
        def broken(_path):
            raise OSError("disk gone")

        report = self.checker(disk_usage=broken).check("disk")
        self.assertEqual(report.checks[0].state, HealthState.UNKNOWN)
        self.assertIn("OSError", report.checks[0].detail)

    def test_active_provider_not_configured_is_error(self):
        report = self.checker(provider_manager=_providers(
            ProviderStatus("ollama", True, True, False, "llama3"),
            ProviderStatus("gemini", True, False, True, ""),
        )).check("providers")
        self.assertEqual(report.overall, HealthState.ERROR)
        self.assertIn("gemini", report.checks[0].detail)

    def test_no_active_provider_is_warning(self):
        report = self.checker(provider_manager=_providers(
            ProviderStatus("ollama", True, True, False, "llama3"),
        )).check("providers")
        self.assertEqual(report.overall, HealthState.WARNING)

    def test_missing_provider_manager_is_unknown(self):
        report = self.checker(provider_manager=None).check("providers")
        self.assertEqual(report.checks[0].state, HealthState.UNKNOWN)

    def test_degraded_service_is_warning_and_unavailable_is_error(self):
        report = self.checker(services=_services({
            "weather": _service(ServiceState.DEGRADED, "Weather slow."),
            "email": _service(ServiceState.UNAVAILABLE, "Mail down."),
        })).check("services")
        self.assertEqual(report.overall, HealthState.ERROR)
        self.assertIn("Mail down", report.checks[0].detail)

    def test_service_status_exception_is_unknown(self):
        broken = SimpleNamespace(get_status=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        report = self.checker(services=_services({"broken": broken})).check("services")
        self.assertEqual(report.checks[0].state, HealthState.UNKNOWN)
        self.assertEqual(report.checks[0].data["services"]["broken"]["state"], "unknown")

    def test_services_without_get_status_are_not_reported(self):
        report = self.checker(services=_services({"plain": object()})).check("services")
        self.assertEqual(report.checks[0].state, HealthState.UNKNOWN)
        self.assertEqual(report.checks[0].data["reporting"], 0)

    def test_empty_config_is_error(self):
        report = self.checker(config_manager=_config(config={})).check("config")
        self.assertEqual(report.overall, HealthState.ERROR)

    def test_missing_workspace_is_warning(self):
        missing = Path(self.temp.name) / "no-such-workspace"
        report = self.checker(config_manager=_config(
            {"workspace.default_path": str(missing)}
        )).check("workspace")
        self.assertEqual(report.overall, HealthState.WARNING)

    def test_workspace_path_that_is_a_file_is_error(self):
        target = Path(self.temp.name) / "not-a-dir.txt"
        target.write_text("x", encoding="utf-8")
        report = self.checker(config_manager=_config(
            {"workspace.default_path": str(target)}
        )).check("workspace")
        self.assertEqual(report.overall, HealthState.ERROR)

    def test_disabled_event_store_is_warning(self):
        report = self.checker(event_store=None).check("events")
        self.assertEqual(report.overall, HealthState.WARNING)

    def test_event_store_warnings_surface_as_warning(self):
        report = self.checker(event_store=_event_store(
            "2026-11-20T11:00:00+00:00", ("Malformed record ignored.",)
        )).check("events")
        self.assertEqual(report.overall, HealthState.WARNING)
        self.assertIn("Malformed record", report.checks[0].detail)

    def test_section_filter_runs_only_that_section(self):
        report = self.checker().check("disk")
        self.assertEqual([check.name for check in report.checks], ["disk"])

    def test_unknown_section_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.checker().check("nope")

    def test_checker_requires_orion_paths(self):
        with self.assertRaises(TypeError):
            HealthChecker(object())


class HealthHandlerTests(TestCase):
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
            config_manager=_config({"workspace.default_path": str(root)}),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
            ),
            services=_services({"weather": _service(ServiceState.AVAILABLE, "Weather ready.")}),
            event_store=_event_store(),
            clock=_Clock(),
        )

    def test_health_returns_success_result_with_data(self):
        result = self.handler.health()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["overall"], "ok")
        self.assertIn("Orion Health Check", result.message)
        self.assertIn("health disk", result.next_actions)

    def test_health_section_result_contains_only_that_section(self):
        result = self.handler.health("disk")
        self.assertEqual([item["name"] for item in result.data["checks"]], ["disk"])

    def test_health_warnings_list_non_ok_checks(self):
        handler = ObservabilityApplicationHandler(
            paths=self.paths,
            config_manager=_config({"workspace.default_path": str(self.paths.user_root)}),
            provider_manager=_providers(
                ProviderStatus("gemini", True, False, True, ""),
            ),
            services=_services({}),
            event_store=None,
            clock=_Clock(),
        )
        result = handler.health()
        self.assertEqual(result.status, "warning")
        self.assertTrue(any("providers" in warning for warning in result.warnings))

    def test_unknown_section_is_failure(self):
        result = self.handler.health("nope")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failure")


class HealthCliTests(TestCase):
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
            config_manager=_config({"workspace.default_path": str(root)}),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
            ),
            services=_services({"weather": _service(ServiceState.AVAILABLE, "Weather ready.")}),
            event_store=_event_store(),
            clock=_Clock(),
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_health_command_renders_report(self):
        result = self.adapter.handle("health")
        self.assertTrue(result.ok)
        self.assertIn("Orion Health Check", self.output.getvalue())

    def test_health_section_command(self):
        result = self.adapter.handle("health disk")
        self.assertTrue(result.ok)
        self.assertEqual([item["name"] for item in result.data["checks"]], ["disk"])

    def test_health_json_output_parses(self):
        result = self.adapter.handle("health --json")
        self.assertTrue(result.ok)
        parsed = json.loads(self.output.getvalue())
        self.assertEqual(parsed["data"]["overall"], "ok")

    def test_unknown_health_option_is_usage_failure(self):
        result = self.adapter.handle("health --nope")
        self.assertFalse(result.ok)
        self.assertIn("Usage: health", result.message)

    def test_unknown_command_is_usage_failure(self):
        result = self.adapter.handle("nope")
        self.assertFalse(result.ok)
        self.assertIn("Usage: health", result.message)

    def test_missing_handler_is_failure(self):
        adapter = ObservabilityCliAdapter(
            SimpleNamespace(), output_provider=self.output.write
        )
        result = adapter.handle("health")
        self.assertFalse(result.ok)
        self.assertIn("unavailable", result.message)

    def test_dispatch_recognizes_health_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "health"))
            self.assertTrue(dispatch_observability(self.runtime, "  HEALTH disk "))
        self.assertFalse(dispatch_observability(self.runtime, "status"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Health Check", rendered)

    def test_router_handles_health(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            self.assertTrue(router.handle("health"))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Health Check", rendered)

    def test_help_completion_and_user_guide_cover_every_health_command(self):
        commands = (
            "health", "health disk", "health providers", "health services",
            "health config", "health workspace", "health events",
        )
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        guide = (Path(__file__).resolve().parents[1] / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
        for command in commands:
            self.assertIn(command, BASE_COMMANDS)
            self.assertIn(command, help_text)
            self.assertIn(command, guide)
