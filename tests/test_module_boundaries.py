import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_loop_uses_shared_execution_and_postcondition_helpers(self):
        from calbot.assistant import execution, loop, postconditions

        self.assertIs(loop.ToolExecutionResult, execution.ToolExecutionResult)
        self.assertIs(
            loop.claims_calendar_success,
            postconditions.claims_calendar_success,
        )

    def test_assistant_helpers_do_not_import_loop_facade(self):
        for filename in (
            "calbot/assistant/postconditions.py",
            "calbot/assistant/execution.py",
        ):
            source = (ROOT / filename).read_text()
            self.assertNotIn("from calbot.assistant.loop", source)

    def test_runtime_delegates_calendar_writes_to_mutation_executor(self):
        runtime = (ROOT / "calbot" / "runtime.py").read_text()
        mutations = (ROOT / "calbot" / "mutations.py").read_text()

        self.assertIn("CalendarMutationExecutor", runtime)
        self.assertNotIn("preview_mutation", runtime)
        self.assertIn("preview_mutation", mutations)
        self.assertIn("calendar_action_reply", mutations)

    def test_low_level_modules_do_not_depend_on_runtime_or_telegram(self):
        for filename in (
            "calbot/calendar/contracts.py",
            "calbot/concurrency.py",
            "calbot/config.py",
            "calbot/personality.py",
        ):
            source = (ROOT / filename).read_text()
            self.assertNotIn("calbot.runtime", source)
            self.assertNotIn("calbot.telegram_app", source)

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
