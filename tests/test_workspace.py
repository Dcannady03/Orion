"""Tests for Orion's Workspace Manager."""

import tempfile
import unittest
from pathlib import Path

from orion.services.workspace import WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    def test_lists_directories_before_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "zeta.txt").write_text("hello", encoding="utf-8")
            (root / "alpha").mkdir()

            manager = WorkspaceManager(root)
            entries = manager.list_entries()

            self.assertEqual([entry.name for entry in entries], ["alpha", "zeta.txt"])
            self.assertTrue(entries[0].is_directory)
            self.assertEqual(entries[1].size_bytes, 5)

    def test_prevents_path_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = WorkspaceManager(temp_dir)

            with self.assertRaises(PermissionError):
                manager.resolve("../outside.txt")

    def test_switches_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manager = WorkspaceManager(first)
            selected = manager.set_workspace(second)

            self.assertEqual(selected, Path(second).resolve())


if __name__ == "__main__":
    unittest.main()
