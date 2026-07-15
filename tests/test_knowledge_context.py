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
            self.assertIn("knowledge index:", context)
            self.assertIn("1 classes", context)
            self.assertNotIn("class Sample", context)


if __name__ == "__main__":
    unittest.main()

class FreshKnowledgeContextTests(unittest.TestCase):
    def test_context_rebuilds_index_after_source_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.py"
            source.write_text("class First:\n    pass\n", encoding="utf-8")
            conversation = ConversationService(root)
            index = KnowledgeIndex(root)
            index.build()
            # Force a source timestamp newer than the generated index.
            import os, time
            future = time.time() + 2
            source.write_text("class First:\n    pass\nclass Second:\n    pass\n", encoding="utf-8")
            os.utime(source, (future, future))
            context = ContextBuilder(conversation, knowledge_index=index).build()
            self.assertIn("2 classes", context)
            self.assertIn("authoritative", context)

