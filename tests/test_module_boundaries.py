import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_tempo_facade_preserves_existing_public_imports(self):
        import tempo_catalog
        import tempo_client
        import tempo_payments
        import tempo_process
        import tempo_tools
        import tempo_wallet

        self.assertIs(tempo_client._run_process, tempo_process._run_process)
        self.assertIs(
            tempo_client.ProcessOutputLimitExceeded,
            tempo_process.ProcessOutputLimitExceeded,
        )
        self.assertIs(
            tempo_client.TempoRequestBudget, tempo_payments.TempoRequestBudget
        )
        self.assertIs(tempo_client.TempoCallPreview, tempo_payments.TempoCallPreview)
        self.assertIs(tempo_client.decimal_text, tempo_payments.decimal_text)
        self.assertIs(
            tempo_client._safe_public_https_url,
            tempo_catalog._safe_public_https_url,
        )
        self.assertIs(
            tempo_client.restore_wallet_credentials,
            tempo_wallet.restore_wallet_credentials,
        )
        self.assertIs(tempo_client.TEMPO_TOOLS, tempo_tools.TEMPO_TOOLS)

    def test_assistant_facade_preserves_existing_public_imports(self):
        import assistant_postconditions
        import assistant_tool_execution
        import assistant_tool_loop

        self.assertIs(
            assistant_tool_loop.ToolExecutionResult,
            assistant_tool_execution.ToolExecutionResult,
        )
        self.assertIs(
            assistant_tool_loop.claims_calendar_success,
            assistant_postconditions.claims_calendar_success,
        )
        self.assertIs(
            assistant_tool_loop.claims_unverified_side_effect_success,
            assistant_postconditions.claims_unverified_side_effect_success,
        )
        self.assertIs(
            assistant_tool_loop.calendar_action_reply,
            assistant_postconditions.calendar_action_reply,
        )

    def test_extracted_modules_do_not_import_their_facades(self):
        for filename, forbidden in (
            ("tempo_catalog.py", "tempo_client"),
            ("tempo_payments.py", "tempo_client"),
            ("tempo_process.py", "tempo_client"),
            ("tempo_tools.py", "tempo_client"),
            ("tempo_wallet.py", "tempo_client"),
            ("assistant_postconditions.py", "assistant_tool_loop"),
            ("assistant_tool_execution.py", "assistant_tool_loop"),
        ):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text()
                self.assertNotIn(f"import {forbidden}", source)
                self.assertNotIn(f"from {forbidden}", source)


if __name__ == "__main__":
    unittest.main()
