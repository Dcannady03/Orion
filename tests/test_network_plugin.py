import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from orion.plugins.manager import PluginManager
from orion.services.registry import ServiceRegistry
from orion.services.workspace import WorkspaceManager


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "network" / "plugin.py"


def load_network_module():
    spec = importlib.util.spec_from_file_location("test_network_plugin_module", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class NetworkMonitorTests(unittest.TestCase):
    def test_check_once_returns_all_targets(self):
        module = load_network_module()
        with tempfile.TemporaryDirectory() as temp:
            monitor = module.NetworkMonitor(Path(temp), ping_runner=lambda host, timeout: (True, 12.5, ""))
            results = monitor.check_once()
            self.assertEqual(len(results), 3)
            self.assertTrue(all(result.online for result in results))
            self.assertEqual(results[0].host, "10.0.0.1")

    def test_monitor_tracks_isp_outage_when_router_stays_online(self):
        module = load_network_module()
        def fake_ping(host, timeout):
            return (True, 1.0, "") if host == "10.0.0.1" else (False, None, "unreachable")
        with tempfile.TemporaryDirectory() as temp:
            monitor = module.NetworkMonitor(Path(temp), interval_seconds=1, ping_runner=fake_ping)
            monitor.start(); time.sleep(0.1)
            summary = monitor.summary(); monitor.stop()
            self.assertIn("ISP", summary["diagnosis"])
            self.assertEqual(summary["targets"]["Router"]["failures"], 0)
            self.assertGreaterEqual(summary["targets"]["Cloudflare"]["failures"], 1)
            self.assertTrue(Path(summary["log_path"]).exists())

    def test_plugin_loads_and_registers_network_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); services = ServiceRegistry()
            workspace = services.register("workspace", WorkspaceManager(root))
            orion = SimpleNamespace(services=services, workspace_manager=workspace,
                                    paths=SimpleNamespace(user_root=root / ".orion"))
            manager = PluginManager(orion, PLUGIN_PATH.parents[1])
            self.assertTrue(manager.load(PLUGIN_PATH))
            self.assertIsNotNone(services.find("network"))
            self.assertTrue(manager.dispatch("network config"))


if __name__ == "__main__":
    unittest.main()
