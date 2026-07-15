import tempfile
import unittest
from pathlib import Path

import yaml

from orion.core.onboarding import FirstContact


class AnswerQueue:
    def __init__(self, answers):
        self.answers = iter(answers)

    def __call__(self, prompt):
        return next(self.answers)


class FirstContactTests(unittest.TestCase):
    def test_missing_profile_requires_first_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            setup = FirstContact(root / "config.yaml", root / "profile.yaml")
            self.assertTrue(setup.is_required)

    def test_existing_named_profile_skips_first_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.yaml"
            profile = root / "profile.yaml"
            config.write_text("orion:\n  name: Orion\n", encoding="utf-8")
            profile.write_text("preferred_name: Daniel\n", encoding="utf-8")
            setup = FirstContact(config, profile)
            result = setup.run()
            self.assertFalse(setup.is_required)
            self.assertFalse(result.completed)

    def test_guided_setup_writes_profile_and_service_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config" / "default.yaml"
            profile = root / "config" / "profile.yaml"
            workspace = root / "workspace"
            answers = AnswerQueue([
                "Daniel", "Daniel Cannady", "Yuba City, California",
                "America/Los_Angeles", "English", "4", str(workspace),
                "1", "", "qwen3.6:35b", "yes", "4", "2", "yes", "yes",
            ])
            output = []
            setup = FirstContact(config, profile, input_provider=answers, output_provider=output.append)
            result = setup.run()

            self.assertTrue(result.completed)
            self.assertTrue(workspace.exists())
            profile_data = yaml.safe_load(profile.read_text(encoding="utf-8"))
            config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(profile_data["preferred_name"], "Daniel")
            self.assertEqual(profile_data["location"], "Yuba City, California")
            self.assertTrue(config_data["calendar"]["google"]["enabled"])
            self.assertTrue(config_data["calendar"]["microsoft"]["enabled"])
            self.assertEqual(config_data["email"]["provider"], "gmail")
            self.assertTrue(config_data["docker"]["enabled"])
            self.assertTrue(config_data["onboarding"]["completed"])
            self.assertIn("First Contact complete.", output)

    def test_cancel_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            answers = AnswerQueue([
                "Daniel", "", "Yuba City", "", "", "", str(root / "workspace"),
                "", "", "", "", "", "", "", "no",
            ])
            setup = FirstContact(
                root / "default.yaml", root / "profile.yaml",
                input_provider=answers, output_provider=lambda message: None,
            )
            result = setup.run()
            self.assertFalse(result.completed)
            self.assertFalse((root / "default.yaml").exists())
            self.assertFalse((root / "profile.yaml").exists())

    def test_force_mode_backs_up_existing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "default.yaml"
            profile = root / "profile.yaml"
            config.write_text("old: config\n", encoding="utf-8")
            profile.write_text("preferred_name: Old\n", encoding="utf-8")
            answers = AnswerQueue([
                "New", "", "Somewhere", "", "", "", str(root),
                "", "", "", "", "", "", "", "yes",
            ])
            setup = FirstContact(config, profile, input_provider=answers, output_provider=lambda message: None)
            setup.run(force=True)
            self.assertTrue(config.with_suffix(".yaml.before-first-contact").exists())
            self.assertTrue(profile.with_suffix(".yaml.before-first-contact").exists())


if __name__ == "__main__":
    unittest.main()
