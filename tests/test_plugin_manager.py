import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orion.plugins.manager import PluginManager
from orion.services.registry import ServiceRegistry
from orion.services.workspace import WorkspaceManager


GOOD_PLUGIN = '''
from orion.plugins.base import OrionPlugin
class Demo(OrionPlugin):
    name = "demo"
    version = "1.2.3"
    description = "Demo plugin"
    def activate(self, context):
        context.services.register("demo_service", object())
    def handle(self, command):
        return command.lower() == "demo"
    def help_lines(self):
        return ["  demo  Run demo"]
def create_plugin():
    return Demo()
'''

BAD_PLUGIN = 'raise RuntimeError("broken on purpose")\n'


class PluginManagerTests(unittest.TestCase):
    def make_orion(self, root: Path):
        services = ServiceRegistry()
        workspace = services.register("workspace", WorkspaceManager(root))
        return SimpleNamespace(services=services, workspace_manager=workspace)

    def test_discovers_loads_and_registers_plugin_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin_file = root / "plugins" / "demo" / "plugin.py"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text(GOOD_PLUGIN, encoding="utf-8")
            orion = self.make_orion(root)
            manager = PluginManager(orion, root / "plugins")
            manager.load_all()
            self.assertEqual(manager.loaded_count(), 1)
            self.assertIsNotNone(orion.services.find("demo_service"))
            self.assertTrue(manager.dispatch("demo"))

    def test_failed_plugin_is_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad = root / "plugins" / "bad" / "plugin.py"
            bad.parent.mkdir(parents=True)
            bad.write_text(BAD_PLUGIN, encoding="utf-8")
            orion = self.make_orion(root)
            manager = PluginManager(orion, root / "plugins")
            manager.load_all()
            self.assertEqual(manager.loaded_count(), 0)
            self.assertEqual(manager.failed_count(), 1)
            self.assertIn("broken on purpose", manager.records()[0].error)

    def test_rejects_missing_factory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin_file = root / "plugins" / "empty" / "plugin.py"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text("VALUE = 1\n", encoding="utf-8")
            manager = PluginManager(self.make_orion(root), root / "plugins")
            self.assertFalse(manager.load(plugin_file))
            self.assertEqual(manager.failed_count(), 1)

    def test_help_lines_are_aggregated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin_file = root / "plugins" / "demo" / "plugin.py"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text(GOOD_PLUGIN, encoding="utf-8")
            manager = PluginManager(self.make_orion(root), root / "plugins")
            manager.load_all()
            self.assertIn("  demo  Run demo", manager.help_lines())

    def test_unknown_command_is_not_claimed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin_file = root / "plugins" / "demo" / "plugin.py"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text(GOOD_PLUGIN, encoding="utf-8")
            manager = PluginManager(self.make_orion(root), root / "plugins")
            manager.load_all()
            self.assertFalse(manager.dispatch("not-demo"))


if __name__ == "__main__":
    unittest.main()
