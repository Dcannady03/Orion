import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orion.plugins.manager import PluginManager
from orion.services.registry import ServiceRegistry
from orion.services.workspace import WorkspaceManager


class SearchPluginTests(unittest.TestCase):
    def make_orion(self, root: Path):
        services = ServiceRegistry()
        workspace = services.register("workspace", WorkspaceManager(root))
        return SimpleNamespace(services=services, workspace_manager=workspace, search_skill=None)

    def test_plugin_loads_registers_service_and_handles_search(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text("class DemoPlugin:\n", encoding="utf-8")
            orion = self.make_orion(root)
            plugin_path = Path(__file__).parents[1] / "plugins" / "search" / "plugin.py"
            manager = PluginManager(orion, plugin_path.parents[1])
            self.assertTrue(manager.load(plugin_path))
            self.assertIsNotNone(orion.services.find("search"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                handled = manager.dispatch("search --type py DemoPlugin")
            self.assertTrue(handled)
            self.assertIn("sample.py", output.getvalue())

    def test_file_search_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "search_target.md").write_text("hello\n", encoding="utf-8")
            orion = self.make_orion(root)
            plugin_path = Path(__file__).parents[1] / "plugins" / "search" / "plugin.py"
            manager = PluginManager(orion, plugin_path.parents[1])
            self.assertTrue(manager.load(plugin_path))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                handled = manager.dispatch("find --files target")
            self.assertTrue(handled)
            self.assertIn("search_target.md", output.getvalue())


if __name__ == "__main__":
    unittest.main()
