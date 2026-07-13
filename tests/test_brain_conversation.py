"""Tests that Brain records and reuses conversation context."""
import tempfile
import unittest

from orion.conversation import ConversationService
from orion.intelligence.brain import Brain
from orion.memory.session import SessionMemory
from orion.services.registry import ServiceRegistry


class FakeProvider:
    def __init__(self):
        self.system_prompt = ""
    def name(self):
        return "fake"
    def chat(self, prompt, system_prompt=None):
        self.system_prompt = system_prompt or ""
        return "response"


class FakeIdentity:
    def build(self):
        return "identity"


class BrainConversationTests(unittest.TestCase):
    def test_brain_records_exchange_and_reuses_context(self):
        with tempfile.TemporaryDirectory() as root:
            services = ServiceRegistry()
            conversation = services.register("conversation", ConversationService(root))
            provider = FakeProvider()
            brain = Brain(provider, memory=SessionMemory(), services=services)
            brain.identity_prompt = FakeIdentity()
            self.assertEqual(brain.ask("first question"), "response")
            self.assertEqual(len(conversation.recent()), 2)
            brain.ask("follow up")
            self.assertIn("first question", provider.system_prompt)
            self.assertEqual(len(conversation.recent()), 4)


if __name__ == "__main__":
    unittest.main()
