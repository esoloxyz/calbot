import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_tempo_facade_preserves_existing_public_imports(self):
        from calbot.tempo import catalog, client, payments, process, tools, wallet

        self.assertIs(client._run_process, process._run_process)
        self.assertIs(
            client.ProcessOutputLimitExceeded,
            process.ProcessOutputLimitExceeded,
        )
        self.assertIs(client.TempoRequestBudget, payments.TempoRequestBudget)
        self.assertIs(client.TempoCallPreview, payments.TempoCallPreview)
        self.assertIs(client.decimal_text, payments.decimal_text)
        self.assertIs(
            client._safe_public_https_url,
            catalog._safe_public_https_url,
        )
        self.assertIs(
            client.restore_wallet_credentials,
            wallet.restore_wallet_credentials,
        )
        self.assertIs(client.TEMPO_TOOLS, tools.TEMPO_TOOLS)

    def test_assistant_facade_preserves_existing_public_imports(self):
        from calbot.assistant import execution, loop, postconditions

        self.assertIs(
            loop.ToolExecutionResult,
            execution.ToolExecutionResult,
        )
        self.assertIs(
            loop.claims_calendar_success,
            postconditions.claims_calendar_success,
        )
        self.assertIs(
            loop.claims_unverified_side_effect_success,
            postconditions.claims_unverified_side_effect_success,
        )
        self.assertIs(
            loop.calendar_action_reply,
            postconditions.calendar_action_reply,
        )

    def test_extracted_modules_do_not_import_their_facades(self):
        for filename, forbidden in (
            ("calbot/tempo/catalog.py", "calbot.tempo.client"),
            ("calbot/tempo/payments.py", "calbot.tempo.client"),
            ("calbot/tempo/process.py", "calbot.tempo.client"),
            ("calbot/tempo/tools.py", "calbot.tempo.client"),
            ("calbot/tempo/wallet.py", "calbot.tempo.client"),
            ("calbot/assistant/postconditions.py", "calbot.assistant.loop"),
            ("calbot/assistant/execution.py", "calbot.assistant.loop"),
        ):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text()
                self.assertNotIn(f"import {forbidden}", source)
                self.assertNotIn(f"from {forbidden}", source)

    def test_implementation_modules_live_under_the_calbot_package(self):
        root_python_files = {path.name for path in ROOT.glob("*.py")}

        self.assertEqual(root_python_files, {"bot.py"})
        self.assertTrue((ROOT / "calbot" / "assistant").is_dir())
        self.assertTrue((ROOT / "calbot" / "calendar").is_dir())
        self.assertTrue((ROOT / "calbot" / "tempo").is_dir())


if __name__ == "__main__":
    unittest.main()
