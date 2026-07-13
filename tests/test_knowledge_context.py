import tempfile
import unittest
from pathlib import Path

from orion.conversation import ContextBuilder, ConversationService
from orion.knowledge import KnowledgeIndex


class KnowledgeContextTests(unittest.TestCase):
    def test_context_includes_only_compact_index_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.py").write_text("class Sample:\n    pass\n", encoding="utf-8")
            conversation = ConversationService(root)
            index = KnowledgeIndex(root)
            index.build()
            context = ContextBuilder(conversation, knowledge_index=index).build()
            self.assertIn("Workspace knowledge index:", context)
            self.assertIn("1 classes", context)
            self.assertNotIn("class Sample", context)


if __name__ == "__main__":
    unittest.main()
