"""Tests for Orion's read-only Code Skill."""

import tempfile
import unittest
from pathlib import Path

from orion.services.workspace import WorkspaceManager
from orion.skills.code import CodeSkill


class CodeSkillTests(unittest.TestCase):
    def test_reads_and_inspects_python_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "hello.py").write_text("print('hello')\n", encoding="utf-8")
            skill = CodeSkill(WorkspaceManager(root))

            self.assertEqual(skill.read_file("hello.py"), "print('hello')\n")
            info = skill.inspect_file("hello.py")
            self.assertEqual(info.language, "Python")
            self.assertEqual(info.line_count, 1)

    def test_blocks_workspace_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill = CodeSkill(WorkspaceManager(temp_dir))
            with self.assertRaises(PermissionError):
                skill.read_file("../outside.py")

    def test_rejects_large_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "large.txt").write_text("x" * 20, encoding="utf-8")
            skill = CodeSkill(WorkspaceManager(root), max_read_bytes=10)
            with self.assertRaises(ValueError):
                skill.read_file("large.txt")

    def test_tree_ignores_git_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("hidden", encoding="utf-8")
            (root / "orion").mkdir()
            (root / "orion" / "main.py").write_text("", encoding="utf-8")
            skill = CodeSkill(WorkspaceManager(root))

            tree = skill.tree()
            self.assertTrue(any("orion" in line for line in tree))
            self.assertFalse(any(".git" in line for line in tree))


if __name__ == "__main__":
    unittest.main()
