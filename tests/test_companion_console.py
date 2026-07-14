import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

from orion.ui.console import OrionCompleter


class CompanionConsoleTests(unittest.TestCase):
    def test_base_command_completion_includes_open(self):
        orion = Mock()
        completer = OrionCompleter(orion)
        document = Mock(text_before_cursor="op")
        values = [item.text for item in completer.get_completions(document, None)]
        self.assertIn("open", values)

    def test_application_completion_uses_catalog(self):
        app = SimpleNamespace(name="Google Chrome")
        orion = Mock()
        orion.application_catalog.applications.return_value = (app,)
        completer = OrionCompleter(orion)
        document = Mock(text_before_cursor="open Goo")
        values = [item.text for item in completer.get_completions(document, None)]
        self.assertIn("open Google Chrome", values)


if __name__ == "__main__":
    unittest.main()
