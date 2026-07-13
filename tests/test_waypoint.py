"""Tests for Waypoint portable project handoff memory."""
import tempfile
import unittest
from pathlib import Path

from orion.conversation.context import ContextBuilder
from orion.conversation.service import ConversationService
from orion.memory.session import SessionMemory
from orion.services.project_context import ProjectContext


class WaypointTests(unittest.TestCase):
    def test_checkpoint_and_rules_persist_in_memory_database(self):
        with tempfile.TemporaryDirectory() as root:
            context = ProjectContext(root)
            context.initialize(name="FFXI Server", current_goal="Build update-safe modules")
            rule = context.add_rule("Only create modules; never edit upstream files because updates overwrite them.")
            context.add_checkpoint("Module loader complete", current_task="Create combat module", next_step="Add tests")

            reopened = ProjectContext(root)
            self.assertTrue((Path(root) / ".orion" / "memory.db").is_file())
            self.assertEqual(reopened.rules()[0]["id"], rule["id"])
            self.assertEqual(reopened.latest_checkpoint()["next_step"], "Add tests")

    def test_projects_do_not_share_rules_or_checkpoints(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            alpha = ProjectContext(first)
            alpha.initialize(name="Alpha")
            alpha.add_rule("Never edit vendor files.")
            alpha.add_checkpoint("Alpha checkpoint")

            beta = ProjectContext(second)
            beta.initialize(name="Beta")
            self.assertEqual(beta.rules(), [])
            self.assertIsNone(beta.latest_checkpoint())

    def test_context_builder_includes_mandatory_rules_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            project = ProjectContext(root)
            project.initialize(name="FFXI")
            project.add_rule("Changes must be implemented as modules only.")
            project.add_checkpoint("Stopped after module scaffold", next_step="Implement event hooks")
            builder = ContextBuilder(ConversationService(root), SessionMemory(), project)
            text = builder.build()
            self.assertIn("Mandatory project rules", text)
            self.assertIn("modules only", text)
            self.assertIn("Implement event hooks", text)


if __name__ == "__main__":
    unittest.main()
