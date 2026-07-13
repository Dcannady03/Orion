"""Tests for persistent conversation context."""
import tempfile
import unittest
from pathlib import Path

from orion.conversation import ContextBuilder, ConversationService
from orion.memory.session import SessionMemory


class ConversationServiceTests(unittest.TestCase):
    def test_messages_persist_search_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            first = ConversationService(root)
            first.add("user", "How does SessionMemory work?")
            first.add("assistant", "It stores session values.")
            second = ConversationService(root)
            self.assertEqual(len(second.recent()), 2)
            self.assertEqual(len(second.search("sessionmemory")), 1)
            self.assertEqual(second.clear_today(), 2)
            self.assertEqual(second.recent(), [])

    def test_bind_changes_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            service = ConversationService(first)
            service.add("user", "First workspace")
            service.bind(second)
            self.assertEqual(service.recent(), [])
            service.add("assistant", "Second workspace")
            self.assertTrue((Path(second) / ".orion" / "conversations").exists())

    def test_context_builder_includes_recent_messages_and_memory(self):
        with tempfile.TemporaryDirectory() as root:
            service = ConversationService(root)
            service.add("user", "Build conversation context")
            memory = SessionMemory()
            memory.set("project", "Orion")
            context = ContextBuilder(service, memory=memory).build()
            self.assertIn("Build conversation context", context)
            self.assertIn("project: Orion", context)


if __name__ == "__main__":
    unittest.main()
