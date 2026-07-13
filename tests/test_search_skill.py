import tempfile
import unittest
from pathlib import Path

from orion.services.workspace import WorkspaceManager
from orion.skills.search import SearchSkill


class SearchSkillTests(unittest.TestCase):
    def make_skill(self, root: Path, **kwargs) -> SearchSkill:
        return SearchSkill(WorkspaceManager(root), **kwargs)

    def test_searches_text_case_insensitively_with_line_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "alpha.py").write_text("first\nSessionMemory here\n", encoding="utf-8")
            report = self.make_skill(root).search_text("sessionmemory")
            self.assertEqual(len(report.matches), 1)
            self.assertEqual(report.matches[0].relative_path, "alpha.py")
            self.assertEqual(report.matches[0].line_number, 2)

    def test_searches_file_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plugin_manager.py").write_text("pass\n", encoding="utf-8")
            results = self.make_skill(root).search_files("plugin")
            self.assertEqual(results, ("plugin_manager.py",))

    def test_filters_by_file_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.py").write_text("needle\n", encoding="utf-8")
            (root / "two.md").write_text("needle\n", encoding="utf-8")
            report = self.make_skill(root).search_text("needle", file_type="py")
            self.assertEqual([item.relative_path for item in report.matches], ["one.py"])

    def test_supports_regular_expressions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plugin.py").write_text("class SearchPlugin:\n", encoding="utf-8")
            report = self.make_skill(root).search_text(r"class\s+\w+Plugin", regex=True)
            self.assertEqual(len(report.matches), 1)

    def test_invalid_regular_expression_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            skill = self.make_skill(Path(temp))
            with self.assertRaisesRegex(ValueError, "Invalid regular expression"):
                skill.search_text("[", regex=True)

    def test_ignores_generated_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "hidden.py").write_text("needle\n", encoding="utf-8")
            report = self.make_skill(root).search_text("needle")
            self.assertEqual(report.matches, ())

    def test_skips_large_and_binary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "large.txt").write_text("needle" * 100, encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\xff\xfe\x00")
            report = self.make_skill(root, max_file_bytes=20).search_text("needle")
            self.assertEqual(report.matches, ())
            self.assertEqual(report.files_skipped, 2)

    def test_blocks_workspace_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = self.make_skill(root)
            with self.assertRaises(PermissionError):
                skill.search_text("anything", relative_path="..")

    def test_truncates_results_at_safety_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "many.txt").write_text("needle\nneedle\nneedle\n", encoding="utf-8")
            report = self.make_skill(root, max_results=2).search_text("needle")
            self.assertEqual(len(report.matches), 2)
            self.assertTrue(report.truncated)


if __name__ == "__main__":
    unittest.main()
