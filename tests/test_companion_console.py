import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from orion.ui.console import Console, OrionCompleter


class CompanionConsoleTests(unittest.TestCase):
    def test_base_command_completion_includes_open(self):
        orion = Mock()
        completer = OrionCompleter(orion)
        document = Mock(text_before_cursor="op")
        values = [item.text for item in completer.get_completions(document, None)]
        self.assertIn("open", values)

    def test_base_command_completion_includes_network_commands(self):
        orion = Mock()
        completer = OrionCompleter(orion)
        document = Mock(text_before_cursor="network ")
        values = [item.text for item in completer.get_completions(document, None)]
        self.assertIn("network status", values)
        self.assertIn("network watch", values)
        ai_document = Mock(text_before_cursor="ai ")
        ai_values = [item.text for item in completer.get_completions(ai_document, None)]
        self.assertIn("ai stats", ai_values)
        self.assertIn("ai health", ai_values)
        team_document = Mock(text_before_cursor="team ")
        team_values = [item.text for item in completer.get_completions(team_document, None)]
        self.assertIn("team plan", team_values)
        self.assertIn("team approve", team_values)
        self.assertIn("team implement", team_values)
        self.assertIn("team run", team_values)
        self.assertIn("team roles", team_values)
        agent_document = SimpleNamespace(text_before_cursor="agent ")
        agent_values = [item.text for item in completer.get_completions(agent_document, None)]
        self.assertIn("agent create", agent_values)
        self.assertIn("agent test", agent_values)
        task_document = SimpleNamespace(text_before_cursor="task ")
        task_values = [item.text for item in completer.get_completions(task_document, None)]
        self.assertIn("task create", task_values)
        self.assertIn("task approve", task_values)
        self.assertIn("task events", task_values)
        self.assertIn("team status", team_values)

    def test_application_completion_uses_catalog(self):
        app = SimpleNamespace(name="Google Chrome")
        orion = Mock()
        orion.application_catalog.applications.return_value = (app,)
        completer = OrionCompleter(orion)
        document = Mock(text_before_cursor="open Goo")
        values = [item.text for item in completer.get_completions(document, None)]
        self.assertIn("open Google Chrome", values)

    def test_home_command_is_available_and_renderer_shows_cards(self):
        self.assertIn("home", __import__("orion.ui.console", fromlist=["BASE_COMMANDS"]).BASE_COMMANDS)
        card = SimpleNamespace(icon="[OK]", title="AI", message="ollama:qwen3.5:9b is connected")
        snapshot = SimpleNamespace(
            greeting="Good morning",
            user_name="Daniel",
            generated_at=__import__("datetime").datetime(2026, 7, 16, 9, 0),
            location="Yuba City, California",
            cards=(card,),
            provider_errors=(),
        )
        with patch("builtins.print") as output:
            Console.render_home(snapshot)
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("ORION", rendered)
        self.assertIn("AI", rendered)
        self.assertIn("online and ready", rendered)


if __name__ == "__main__":
    unittest.main()
