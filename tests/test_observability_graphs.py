"""Tests for Orion health graphs (dependency and service health graphs)."""
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
    HealthCheck,
    HealthReport,
    HealthSnapshotHistory,
    HealthSnapshotStore,
    HealthState,
    ObservabilityApplicationHandler,
    build_dependency_graph,
    build_timelines,
)
import orion.application.observability.graphs as graphs_module
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


def _report(generated_at, disk=HealthState.OK, providers=HealthState.OK):
    checks = (
        HealthCheck("disk", disk, f"disk {disk.value}"),
        HealthCheck("providers", providers, f"providers {providers.value}"),
        HealthCheck("services", HealthState.OK, "services ok"),
        HealthCheck("config", HealthState.OK, "config ok"),
        HealthCheck("workspace", HealthState.OK, "workspace ok"),
        HealthCheck("events", HealthState.OK, "events ok"),
    )
    return HealthReport(generated_at=generated_at, checks=checks)


def _handler(paths, clock):
    return ObservabilityApplicationHandler(
        paths=paths,
        config_manager=_config({"workspace.default_path": str(paths.user_root)}),
        provider_manager=_providers(
            ProviderStatus("ollama", True, True, True, "llama3"),
        ),
        services=_services({
            "weather": _service(ServiceState.AVAILABLE, "Weather ready."),
        }),
        event_store=_event_store("2026-11-20T11:00:00+00:00"),
        clock=clock,
    )


class SnapshotStoreTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = HealthSnapshotStore(Path(self.temp.name) / "observability")

    def test_append_and_load_roundtrip(self):
        first = _report("2026-11-20T10:00:00+00:00")
        second = _report("2026-11-20T11:00:00+00:00", disk=HealthState.WARNING)
        self.store.append(first)
        self.store.append(second)
        history = self.store.load()
        self.assertEqual(history.total, 2)
        self.assertEqual(history.skipped, 0)
        self.assertEqual(
            [item.generated_at for item in history.snapshots],
            ["2026-11-20T10:00:00+00:00", "2026-11-20T11:00:00+00:00"],
        )
        self.assertEqual(history.snapshots[1].checks[0].state, HealthState.WARNING)

    def test_load_missing_file_is_empty(self):
        history = self.store.load()
        self.assertEqual(history, HealthSnapshotHistory())

    def test_malformed_lines_skipped(self):
        self.store.append(_report("2026-11-20T10:00:00+00:00"))
        with open(self.store.path, "a", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write('{"generated_at": 5}\n')
        history = self.store.load()
        self.assertEqual(history.total, 1)
        self.assertEqual(history.skipped, 2)

    def test_load_limit_keeps_newest(self):
        for hour in range(5):
            self.store.append(_report(f"2026-11-20T0{hour}:00:00+00:00"))
        history = self.store.load(limit=2)
        self.assertEqual(history.total, 5)
        self.assertEqual(
            [item.generated_at for item in history.snapshots],
            ["2026-11-20T03:00:00+00:00", "2026-11-20T04:00:00+00:00"],
        )

    def test_prune_keeps_max_snapshots(self):
        with patch.object(graphs_module, "MAX_SNAPSHOTS", 3):
            for hour in range(5):
                self.store.append(_report(f"2026-11-20T0{hour}:00:00+00:00"))
        lines = self.store.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)


class TimelineTests(TestCase):
    def test_sparkline_counts_and_transitions(self):
        history = HealthSnapshotHistory((
            _report("2026-11-20T10:00:00+00:00"),
            _report("2026-11-20T11:00:00+00:00", disk=HealthState.WARNING),
            _report("2026-11-20T12:00:00+00:00", disk=HealthState.ERROR),
        ), 0, 3)
        graphs = build_timelines(history)
        disk = next(item for item in graphs.sections if item.section == "disk")
        self.assertEqual(disk.sparkline, "=~X")
        self.assertEqual(disk.counts["ok"], 1)
        self.assertEqual(disk.counts["warning"], 1)
        self.assertEqual(disk.counts["error"], 1)
        self.assertEqual(disk.transitions, 2)
        self.assertEqual(disk.first_at, "2026-11-20T10:00:00+00:00")
        self.assertEqual(disk.last_at, "2026-11-20T12:00:00+00:00")
        self.assertEqual(graphs.overall.sparkline, "=~X")

    def test_empty_history_has_empty_timelines(self):
        graphs = build_timelines(HealthSnapshotHistory())
        self.assertEqual(graphs.overall.sparkline, "")
        self.assertEqual(graphs.overall.snapshots, 0)
        self.assertEqual(graphs.sections, ())

    def test_graphs_serialize_to_json_safe_dict(self):
        history = HealthSnapshotHistory((
            _report("2026-11-20T10:00:00+00:00"),
        ), 0, 1)
        data = build_timelines(history).to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)


class DependencyGraphTests(TestCase):
    def test_tree_expands_providers_and_services(self):
        report = HealthReport(
            generated_at="2026-11-20T12:00:00+00:00",
            checks=(
                HealthCheck("disk", HealthState.OK, "disk ok"),
                HealthCheck(
                    "providers", HealthState.OK, "providers ok",
                    {"providers": [
                        {"key": "ollama", "enabled": True, "configured": True,
                         "active": True, "model": "llama3"},
                        {"key": "gemini", "enabled": True, "configured": False,
                         "active": False, "model": ""},
                    ]},
                ),
                HealthCheck(
                    "services", HealthState.OK, "services ok",
                    {"services": {"weather": {"state": "ok", "message": "Weather ready."}}},
                ),
                HealthCheck("config", HealthState.OK, "config ok"),
                HealthCheck("workspace", HealthState.OK, "workspace ok"),
                HealthCheck("events", HealthState.OK, "events ok"),
            ),
        )
        tree = build_dependency_graph(report)
        self.assertEqual(tree.name, "orion")
        self.assertEqual(tree.state, HealthState.OK)
        providers = next(child for child in tree.children if child.name == "providers")
        self.assertEqual(
            [child.name for child in providers.children], ["ollama", "gemini"]
        )
        self.assertEqual(providers.children[0].state, HealthState.OK)
        self.assertEqual(providers.children[1].state, HealthState.WARNING)
        services = next(child for child in tree.children if child.name == "services")
        self.assertEqual(services.children[0].name, "weather")

    def test_unconfigured_active_provider_is_error(self):
        report = HealthReport(
            generated_at="2026-11-20T12:00:00+00:00",
            checks=(
                HealthCheck(
                    "providers", HealthState.ERROR, "active provider not configured",
                    {"providers": [
                        {"key": "gemini", "enabled": True, "configured": False,
                         "active": True, "model": ""},
                    ]},
                ),
            ),
        )
        tree = build_dependency_graph(report)
        providers = tree.children[0]
        self.assertEqual(providers.children[0].state, HealthState.ERROR)

    def test_tree_renders_as_ascii(self):
        tree = build_dependency_graph(_report("2026-11-20T12:00:00+00:00"))
        lines = tree.render()
        self.assertTrue(lines[0].startswith("+-- orion [OK]"))
        self.assertTrue(any("+-- providers [OK]" in line for line in lines))


class GraphsHandlerTests(TestCase):
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
        self.handler = _handler(self.paths, _Clock())

    def test_graphs_no_snapshots_points_to_record(self):
        result = self.handler.graphs()
        self.assertTrue(result.ok)
        self.assertIn("No health snapshots recorded yet", result.message)
        self.assertIn("health --record", result.next_actions)

    def test_graphs_with_snapshots_renders_timelines(self):
        self.handler.health(record=True)
        self.handler.health(record=True)
        result = self.handler.graphs()
        self.assertTrue(result.ok)
        self.assertIn("Orion Health Graphs", result.message)
        self.assertEqual(result.data["snapshots"], 2)
        self.assertEqual(result.data["overall"]["sparkline"], "==")

    def test_graphs_section_filters_timeline(self):
        self.handler.health(record=True)
        result = self.handler.graphs("disk")
        self.assertTrue(result.ok)
        self.assertIn("Disk (", result.message)
        self.assertNotIn("Providers (", result.message)

    def test_graphs_dependencies_uses_live_check(self):
        result = self.handler.graphs("dependencies")
        self.assertTrue(result.ok)
        self.assertIn("Orion Dependency Graph", result.message)
        self.assertEqual(result.data["kind"], "dependencies")
        self.assertEqual(result.data["tree"]["name"], "orion")

    def test_graphs_unknown_section_is_failure(self):
        result = self.handler.graphs("nope")
        self.assertFalse(result.ok)
        self.assertIn("Unknown graphs section", result.message)

    def test_health_record_saves_snapshot(self):
        result = self.handler.health(record=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["recorded"])
        self.assertIn("Snapshot recorded", result.message)
        path = Path(result.data["snapshot_path"])
        self.assertTrue(path.is_file())
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_health_without_record_writes_nothing(self):
        result = self.handler.health()
        self.assertTrue(result.ok)
        self.assertFalse((self.paths.observability / "health-snapshots.jsonl").exists())


class GraphsCliTests(TestCase):
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
        self.handler = _handler(self.paths, _Clock())
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_graphs_renders_timelines(self):
        self.adapter.handle("health --record")
        self.output.truncate(0)
        self.output.seek(0)
        result = self.adapter.handle("graphs")
        self.assertTrue(result.ok)
        self.assertIn("Orion Health Graphs", self.output.getvalue())

    def test_graphs_json_output_parses(self):
        self.adapter.handle("health --record")
        self.output.truncate(0)
        self.output.seek(0)
        result = self.adapter.handle("graphs --json")
        self.assertTrue(result.ok)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(payload["data"]["snapshots"], 1)

    def test_graphs_dependencies_renders_tree(self):
        result = self.adapter.handle("graphs dependencies")
        self.assertTrue(result.ok)
        self.assertIn("Orion Dependency Graph", self.output.getvalue())

    def test_health_record_flag(self):
        result = self.adapter.handle("health --record")
        self.assertTrue(result.ok)
        self.assertIn("Snapshot recorded", self.output.getvalue())

    def test_graphs_unknown_option_is_usage(self):
        result = self.adapter.handle("graphs --bogus")
        self.assertFalse(result.ok)
        self.assertIn("Unknown graphs option", result.message)

    def test_dispatch_recognizes_graphs_only(self):
        self.handler.health(record=True)
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "graphs"))
            self.assertTrue(dispatch_observability(self.runtime, "  GRAPHS dependencies "))
        self.assertFalse(dispatch_observability(self.runtime, "graph"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Health Graphs", rendered)
        self.assertIn("Orion Dependency Graph", rendered)

    def test_router_handles_graphs(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("graphs dependencies")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Dependency Graph", rendered)


class GraphsCoverageTests(TestCase):
    def test_graphs_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("graphs", help_text)
        self.assertIn("graphs", BASE_COMMANDS)
        self.assertIn("graphs dependencies", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("graphs", guide_text)
        self.assertIn("health --record", guide_text)
