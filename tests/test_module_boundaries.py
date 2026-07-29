import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_assistant_facade_preserves_small_public_imports(self):
        from calbot.assistant import execution, loop, postconditions

        self.assertIs(loop.ToolExecutionResult, execution.ToolExecutionResult)
        self.assertIs(
            loop.claims_calendar_success,
            postconditions.claims_calendar_success,
        )
        self.assertIs(
            loop.calendar_action_reply,
            postconditions.calendar_action_reply,
        )

    def test_assistant_helpers_do_not_import_loop_facade(self):
        for filename in (
            "calbot/assistant/postconditions.py",
            "calbot/assistant/execution.py",
        ):
            source = (ROOT / filename).read_text()
            self.assertNotIn("from calbot.assistant.loop", source)

    def test_only_calendar_domain_package_remains(self):
        self.assertTrue((ROOT / "calbot" / "assistant").is_dir())
        self.assertTrue((ROOT / "calbot" / "calendar").is_dir())
        self.assertFalse((ROOT / "calbot" / "tempo").exists())
        self.assertFalse((ROOT / "calbot" / "doordash").exists())

    def test_root_python_file_is_only_compatibility_launcher(self):
        root_python_files = {path.name for path in ROOT.glob("*.py")}

        self.assertEqual(root_python_files, {"bot.py"})


if __name__ == "__main__":
    unittest.main()
