import json
import subprocess
import unittest
from unittest.mock import patch

from tempo_client import TempoClient, TempoRequestBudget


PARALLEL = {
    "id": "parallel",
    "service_url": "https://parallelmpp.dev",
    "endpoints": [
        {
            "method": "POST",
            "path": "/api/search",
            "payment": {"amount": "10000", "decimals": 6, "dynamic": None},
        },
        {
            "method": "POST",
            "path": "/api/extract",
            "payment": {"amount": "10000", "decimals": 6, "dynamic": None},
        },
        {
            "method": "POST",
            "path": "/api/task",
            "payment": {"amount": "", "decimals": 6, "dynamic": True},
        },
    ],
}


class TempoClientAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.client = TempoClient()
        self.client.bin = "/tmp/fake-tempo"

    @patch("tempo_client.subprocess.run")
    def test_parallel_service_can_be_discovered_and_called_with_a_spend_cap(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"results":[]}', stderr=""
            ),
        ]

        details = json.loads(self.client.service_details("parallel"))
        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/search",
                body='{"query":"Tempo MPP","mode":"fast"}',
            )
        )

        self.assertEqual(details["id"], "parallel")
        self.assertEqual(result, {"results": []})
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/tmp/fake-tempo",
                "wallet",
                "services",
                "parallel",
                "--format",
                "json",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/tmp/fake-tempo",
                "request",
                "-t",
                "-X",
                "POST",
                "--max-spend",
                "0.01",
                "--json",
                '{"query":"Tempo MPP","mode":"fast"}',
                "https://parallelmpp.dev/api/search",
            ],
        )

    @patch("tempo_client.subprocess.run")
    def test_wallet_commands_use_current_cli_argument_order(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )

        self.client.wallet_balance()
        self.client.discover_services("search")

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/tmp/fake-tempo", "wallet", "whoami", "--format", "json"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/tmp/fake-tempo",
                "wallet",
                "services",
                "--search",
                "search",
                "--format",
                "json",
            ],
        )

    @patch("tempo_client.subprocess.run")
    def test_undiscovered_or_method_mismatched_endpoints_are_rejected(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.client.service_details("parallel")

        unknown = json.loads(
            self.client.call_service("https://parallelmpp.dev/api/not-real")
        )
        wrong_method = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/search", method="DELETE"
            )
        )

        self.assertIn("not a discovered Tempo service endpoint", unknown["error"])
        self.assertIn("does not support DELETE", wrong_method["error"])
        self.assertEqual(run.call_count, 1)

    @patch("tempo_client.subprocess.run")
    def test_nonzero_cli_exit_is_returned_as_structured_error(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="wallet is not ready"
        )

        result = json.loads(self.client.wallet_balance())

        self.assertEqual(result["error"], "Tempo command failed")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["details"], "wallet is not ready")

    @patch("tempo_client.subprocess.run")
    def test_caller_cannot_raise_the_configured_spend_ceiling(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.client.service_details("parallel")

        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task", max_spend="0.51"
            )
        )

        self.assertIn("exceeds the configured $0.50 ceiling", result["error"])
        self.assertEqual(run.call_count, 1)

    @patch("tempo_client.subprocess.run")
    def test_dynamic_price_endpoint_requires_explicit_approval(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.client.service_details("parallel")

        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task",
                body='{"input":"research Tempo","processor":"pro"}',
                max_spend="0.10",
                request_budget=TempoRequestBudget(),
            )
        )

        self.assertEqual(result["error_code"], "confirmation_required")
        self.assertEqual(result["approval_amount"], "0.10")
        self.assertEqual(result["confirmation_prompt"], "approve $0.10")
        self.assertEqual(run.call_count, 1)

    @patch("tempo_client.subprocess.run")
    def test_declared_cap_is_never_silently_raised_to_the_service_price(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.client.service_details("parallel")

        fixed = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/search",
                body='{"query":"Tempo"}',
                max_spend="0.001",
                request_budget=TempoRequestBudget(),
            )
        )
        dynamic = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task",
                body='{"input":"research Tempo","processor":"pro"}',
                max_spend="0.01",
                request_budget=TempoRequestBudget(),
            )
        )

        self.assertEqual(fixed["error_code"], "spend_cap_too_low")
        self.assertEqual(dynamic["error_code"], "spend_cap_too_low")
        self.assertEqual(run.call_count, 1)

    @patch("tempo_client.subprocess.run")
    def test_exact_approved_dynamic_call_can_run_once(self, run):
        tool_args = {
            "url": "https://parallelmpp.dev/api/task",
            "method": "POST",
            "body": '{"input":"research Tempo","processor":"pro"}',
            "max_spend": "0.10",
        }
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"run_id":"task_123"}', stderr=""
            ),
        ]
        self.client.service_details("parallel")
        budget = TempoRequestBudget(approved_call=tool_args, approved_limit="0.10")

        result = json.loads(
            self.client.run_tool("tempo_call_service", tool_args, request_budget=budget)
        )

        self.assertEqual(result["run_id"], "task_123")
        self.assertEqual(run.call_count, 2)

    @patch("tempo_client.subprocess.run")
    def test_parallel_task_requires_explicit_input_and_processor_before_spending(
        self, run
    ):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.client.service_details("parallel")

        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task",
                body='{"task":"research Tempo"}',
                max_spend="0.30",
                request_budget=TempoRequestBudget(),
            )
        )

        self.assertEqual(result["error_code"], "invalid_request_schema")
        self.assertIn("processor", result["error"])
        self.assertEqual(run.call_count, 1)

    @patch("tempo_client.subprocess.run")
    def test_paid_request_is_not_retried_after_submission_even_when_it_fails(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=3, stdout="", stderr="network response lost"
            ),
        ]
        self.client.service_details("parallel")
        budget = TempoRequestBudget()

        first = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/search",
                body='{"query":"Tempo"}',
                request_budget=budget,
            )
        )
        second = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/search",
                body='{"query":"Tempo retry"}',
                request_budget=budget,
            )
        )

        self.assertEqual(first["error"], "Tempo command failed")
        self.assertEqual(second["error_code"], "paid_request_already_submitted")
        self.assertEqual(run.call_count, 2)

    @patch("tempo_client.subprocess.run")
    def test_task_status_url_is_authorized_as_free_polling_after_submission(self, run):
        tool_args = {
            "url": "https://parallelmpp.dev/api/task",
            "method": "POST",
            "body": '{"input":"research Tempo","processor":"pro"}',
            "max_spend": "0.10",
        }
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"run_id":"task_123"}', stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"status":"completed"}', stderr=""
            ),
        ]
        self.client.service_details("parallel")
        budget = TempoRequestBudget(approved_call=tool_args, approved_limit="0.10")
        self.client.run_tool("tempo_call_service", tool_args, request_budget=budget)

        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task/task_123",
                method="GET",
                request_budget=budget,
            )
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(run.call_count, 3)

    @patch("tempo_client.subprocess.run")
    def test_request_budget_is_cumulative_across_paid_tool_calls(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"results":[]}', stderr=""
            ),
        ]
        self.client.service_details("parallel")
        budget = TempoRequestBudget()

        self.client.call_service(
            "https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            request_budget=budget,
        )
        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/extract",
                body='{"url":"https://tempo.xyz"}',
                request_budget=budget,
            )
        )

        self.assertEqual(result["error_code"], "paid_request_already_submitted")
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
