import json
import unittest
from datetime import datetime, timedelta, timezone

from payment_approval import PendingPaymentApproval


class PendingPaymentApprovalTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        self.tool_args = {
            "url": "https://parallelmpp.dev/api/task",
            "method": "POST",
            "body": '{"input":"research Tempo","processor":"pro"}',
            "max_spend": "0.10",
        }
        self.tool_result = json.dumps(
            {
                "error": "Explicit confirmation is required",
                "error_code": "confirmation_required",
                "approval_amount": "0.10",
                "confirmation_prompt": "approve $0.10",
            }
        )

    def test_only_exact_amount_confirmation_approves_pending_call(self):
        pending = PendingPaymentApproval.from_tool_result(
            self.tool_args, self.tool_result, now=self.now
        )

        self.assertIsNotNone(pending)
        self.assertTrue(pending.matches("approve $0.10", now=self.now))
        self.assertTrue(pending.matches("APPROVE 0.10", now=self.now))
        self.assertFalse(pending.matches("yes", now=self.now))
        self.assertFalse(pending.matches("approve $0.30", now=self.now))

    def test_approval_expires_after_ten_minutes(self):
        pending = PendingPaymentApproval.from_tool_result(
            self.tool_args, self.tool_result, now=self.now
        )

        self.assertFalse(
            pending.matches("approve $0.10", now=self.now + timedelta(minutes=11))
        )

    def test_non_confirmation_tool_result_does_not_create_pending_approval(self):
        pending = PendingPaymentApproval.from_tool_result(
            self.tool_args, '{"results":[]}', now=self.now
        )

        self.assertIsNone(pending)


if __name__ == "__main__":
    unittest.main()
