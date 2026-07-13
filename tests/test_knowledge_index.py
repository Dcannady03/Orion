import json
import tempfile
import unittest
from pathlib import Path

from orion.knowledge import KnowledgeIndex


class KnowledgeIndexTests(unittest.TestCase):
    def test_builds_structural_workspace_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "app.py").write_text(
                "import os\nfrom pathlib import Path\n\nclass Demo:\n    pass\n\ndef run():\n    pass\n# TODO: finish\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_app.py").write_text("def test_run():\n    pass\n", encoding="utf-8")
            index = KnowledgeIndex(root)
            data = index.build()
            self.assertEqual(data["stats"]["files"], 2)
            self.assertEqual(data["stats"]["classes"], 1)
            self.assertEqual(data["stats"]["functions"], 2)
            self.assertEqual(data["stats"]["todos"], 1)
            self.assertEqual(data["stats"]["tests"], 1)
            self.assertTrue(index.exists())
            self.assertEqual(index.query("Demo")[0]["type"], "symbol")

    def test_ignores_orion_database_and_rebinds_without_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            (first / "alpha.py").write_text("class Alpha:\n    pass\n", encoding="utf-8")
            (second / "beta.py").write_text("class Beta:\n    pass\n", encoding="utf-8")
            index = KnowledgeIndex(first)
            index.build()
            self.assertTrue(index.query("Alpha"))
            index.bind(second)
            self.assertFalse(index.exists())
            index.build()
            self.assertFalse(index.query("Alpha"))
            self.assertTrue(index.query("Beta"))
            stored = json.loads((second / ".orion" / "knowledge-index.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["stats"]["files"], 1)

    def test_requires_build_before_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            index = KnowledgeIndex(temporary)
            with self.assertRaises(FileNotFoundError):
                index.status()


if __name__ == "__main__":
    unittest.main()
