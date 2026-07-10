"""Tests for Orion session memory and CLI routing."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from orion.core.router import CommandRouter
from orion.memory.session import SessionMemory


class SessionMemoryTests(unittest.TestCase):
    def test_set_get_delete_and_clear(self):
        memory = SessionMemory()
        self.assertEqual(memory.set("Goal", "Build Session Memory"), "goal")
        self.assertEqual(memory.get("GOAL"), "Build Session Memory")
        self.assertTrue(memory.exists("goal"))
        self.assertTrue(memory.delete("goal"))
        self.assertFalse(memory.delete("goal"))
        memory.set("project", "Orion")
        self.assertEqual(memory.clear(), 1)
        self.assertEqual(memory.all(), {})

    def test_rejects_invalid_entries(self):
        memory = SessionMemory()
        with self.assertRaises(ValueError):
            memory.set("", "value")
        with self.assertRaises(ValueError):
            memory.set("two words", "value")
        with self.assertRaises(ValueError):
            memory.set("key", "")

    def test_all_returns_detached_snapshot(self):
        memory = SessionMemory()
        memory.set("project", "Orion")
        snapshot = memory.all()
        snapshot["project"] = "Changed"
        self.assertEqual(memory.get("project"), "Orion")


class RouterMemoryTests(unittest.TestCase):
    def setUp(self):
        class FakeOrion:
            session_memory = SessionMemory()

        self.router = CommandRouter(FakeOrion())

    def run_command(self, command):
        output = io.StringIO()
        with redirect_stdout(output):
            self.router.handle(command)
        return output.getvalue()

    def test_memory_command_flow(self):
        self.assertIn("Remembered: goal", self.run_command("remember goal Build Orion"))
        self.assertIn("goal = Build Orion", self.run_command("recall goal"))
        self.assertIn("goal = Build Orion", self.run_command("memory"))
        self.assertIn("Forgot: goal", self.run_command("forget goal"))
        self.assertIn("Memory not found", self.run_command("recall goal"))

    def test_clear_memory_confirmation(self):
        self.router.handle("remember project Orion")
        with patch("builtins.input", return_value="y"):
            output = self.run_command("clear memory")
        self.assertIn("Cleared 1", output)
        self.assertEqual(len(self.router.orion.session_memory), 0)


if __name__ == "__main__":
    unittest.main()
