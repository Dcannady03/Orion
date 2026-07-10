"""Tests for persistent Orion project context."""

import tempfile
import unittest
from pathlib import Path

from orion.services.project_context import ProjectContext


class ProjectContextTests(unittest.TestCase):
    def test_initialize_creates_portable_project_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = ProjectContext(temp_dir)
            project = context.initialize(name="Test Project", current_goal="Build tests")

            self.assertTrue(context.initialized)
            self.assertEqual(project["name"], "Test Project")
            for filename in ProjectContext.FILES.values():
                self.assertTrue((Path(temp_dir) / ".orion" / filename).exists())
            self.assertEqual(len(context.history()), 1)

    def test_project_values_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = ProjectContext(temp_dir)
            first.initialize(name="Orion")
            first.set_field("goal", "Build File Search")
            first.add_note("Persistent note")

            second = ProjectContext(temp_dir)
            self.assertEqual(second.project()["current_goal"], "Build File Search")
            self.assertGreaterEqual(len(second.history()), 3)
            notes = (Path(temp_dir) / ".orion" / "notes.md").read_text(encoding="utf-8")
            self.assertIn("Persistent note", notes)

    def test_missing_context_requires_initialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = ProjectContext(temp_dir)
            with self.assertRaises(FileNotFoundError):
                context.project()

    def test_corrupt_json_is_reported_without_overwriting_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = ProjectContext(temp_dir)
            context.initialize()
            project_file = Path(temp_dir) / ".orion" / "project.json"
            project_file.write_text("{broken", encoding="utf-8")

            with self.assertRaises(ValueError):
                context.project()
            self.assertEqual(project_file.read_text(encoding="utf-8"), "{broken")

    def test_bind_changes_project_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            context = ProjectContext(first)
            context.initialize(name="First")
            context.bind(second)
            self.assertFalse(context.initialized)
            context.initialize(name="Second")
            self.assertEqual(context.project()["name"], "Second")


if __name__ == "__main__":
    unittest.main()
