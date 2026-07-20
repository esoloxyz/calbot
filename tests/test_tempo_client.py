import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from calbot.tempo.client import (
    ProcessOutputLimitExceeded,
    TempoClient,
    TempoRequestBudget,
    _run_process,
    _safe_public_https_url,
    decimal_text,
)


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

OPENAI = {
    "id": "openai",
    "service_url": "https://openai.mpp.tempo.xyz",
    "endpoints": [
        {
            "method": "POST",
            "path": "/v1/images/generations",
            "payment": {
                "intent": "charge",
                "amount": "50000",
                "decimals": 6,
                "dynamic": None,
            },
        }
    ],
}

FAL = {
    "id": "fal",
    "service_url": "https://fal.mpp.tempo.xyz",
    "endpoints": [
        {
            "method": "POST",
            "path": "/fal-ai/flux/schnell",
            "payment": {
                "intent": "charge",
                "amount": "3000",
                "decimals": 6,
                "dynamic": None,
            },
        }
    ],
}

NONFINITE_PRICE = {
    "id": "untrusted-price",
    "service_url": "https://service.example",
    "endpoints": [
        {
            "method": "POST",
            "path": "/dynamic",
            "payment": {"amount": "NaN", "decimals": 6, "dynamic": False},
        }
    ],
}


class TempoClientAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.client = TempoClient()
        self.client.bin = "/tmp/fake-tempo"

    def service_details(self, service_id: str) -> str:
        """Prime the directory-derived ID precondition for metadata unit tests."""
        self.client._discovered_service_ids.add(service_id)
        return self.client.service_details(service_id)

    def test_legacy_numeric_loopback_hosts_are_not_treated_as_dns_names(self):
        for url in (
            "https://127.1/x",
            "https://2130706433/x",
            "https://0x7f000001/x",
            "https://0177.0.0.1/x",
            "https://１２７.０.０.１/x",
            "https://127。0。0。1/x",
            "https://%31%32%37.0.0.1/x",
            "https://127%2e0%2e0%2e1/x",
            "https://%30x7f000001/x",
            "https://service.example\\@127.0.0.1/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(_safe_public_https_url(url))

        self.assertTrue(_safe_public_https_url("https://service.example/x"))

    @patch("calbot.tempo.client._run_process")
    def test_service_catalog_and_authorized_endpoints_remain_bounded(self, run):
        def details(command, **kwargs):
            service_id = command[3]
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(
                    {
                        "id": service_id,
                        "service_url": f"https://{service_id}.example",
                        "endpoints": [
                            {
                                "method": "GET",
                                "path": "/status",
                                "payment": {"amount": "0", "decimals": 6},
                            }
                        ],
                    }
                ),
                stderr="",
            )

        run.side_effect = details
        for index in range(150):
            service_id = f"svc{index:03d}"
            self.client._discovered_service_ids.add(service_id)
            self.client.service_details(service_id)

        self.assertEqual(len(self.client._service_catalog), 100)
        self.assertEqual(len(self.client._allowed_endpoints), 100)
        self.assertNotIn(
            "https://svc000.example/status", self.client._allowed_endpoints
        )

    @patch("calbot.tempo.client._run_process")
    def test_parallel_service_can_be_discovered_and_called_with_a_spend_cap(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"results":[]}', stderr=""
            ),
        ]

        details = json.loads(self.service_details("parallel"))
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
                "--stream",
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

    @patch("calbot.tempo.client._run_process")
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

    @patch("calbot.tempo.client._run_process")
    def test_only_directory_returned_service_ids_can_fetch_details(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"services":[{"id":"parallel"}]}',
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
        ]

        rejected = json.loads(self.client.service_details("private-secret"))
        self.client.discover_services("search")
        accepted = json.loads(self.client.service_details("parallel"))

        self.assertIn("not returned by a service search", rejected["error"])
        self.assertEqual(accepted["id"], "parallel")
        self.assertEqual(run.call_count, 2)

    @patch("calbot.tempo.client._run_process")
    def test_discovery_rejects_arbitrary_outbound_text(self, run):
        result = json.loads(self.client.discover_services("send private secret"))

        self.assertIn("supported capability keywords", result["error"])
        run.assert_not_called()

    @patch("calbot.tempo.client._run_process")
    def test_private_metadata_endpoint_is_never_authorized(self, run):
        malicious = {
            "id": "malicious",
            "service_url": "https://169.254.169.254",
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/latest/meta-data",
                    "payment": {"amount": "0", "decimals": 6},
                }
            ],
        }
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(malicious), stderr=""
        )
        self.client._discovered_service_ids.add("malicious")

        self.client.service_details("malicious")
        preview = self.client.preview_call(
            "https://169.254.169.254/latest/meta-data", method="GET"
        )

        self.assertIn("not a discovered", preview.error["error"])
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_refresh_atomically_evicts_stale_service_endpoints(self, run):
        replacement = {
            "id": "parallel",
            "service_url": "https://parallelmpp.dev",
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/new-search",
                    "payment": {"amount": "10000", "decimals": 6},
                }
            ],
        }
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(replacement), stderr=""
            ),
        ]
        self.client._discovered_service_ids.add("parallel")
        self.client.service_details("parallel")
        self.client.service_details("parallel")

        stale = self.client.preview_call("https://parallelmpp.dev/api/search")
        current = self.client.preview_call("https://parallelmpp.dev/api/new-search")

        self.assertIsNotNone(stale.error)
        self.assertIsNone(current.error)

    @patch.dict(
        os.environ,
        {
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "google-secret",
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TEMPO_WALLET_STORE_B64": "wallet-secret",
            "UNRELATED_DEPLOYMENT_SECRET": "other-secret",
        },
    )
    @patch("calbot.tempo.client._run_process")
    def test_tempo_subprocess_does_not_inherit_application_secrets(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )

        self.client.wallet_balance()

        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["TEMPO_HOME"], self.client.tempo_home)
        for secret_name in (
            "ANTHROPIC_API_KEY",
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "TELEGRAM_BOT_TOKEN",
            "TEMPO_WALLET_STORE_B64",
            "UNRELATED_DEPLOYMENT_SECRET",
        ):
            self.assertNotIn(secret_name, child_env)

    @patch("calbot.tempo.client._run_process")
    def test_undiscovered_or_method_mismatched_endpoints_are_rejected(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

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

    @patch("calbot.tempo.client._run_process")
    def test_nonzero_cli_exit_is_returned_as_structured_error(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="wallet is not ready"
        )

        result = json.loads(self.client.wallet_balance())

        self.assertEqual(result["error"], "Tempo command failed")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["details"], "wallet is not ready")

    @patch("calbot.tempo.client._run_process")
    def test_caller_cannot_raise_the_configured_spend_ceiling(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

        result = json.loads(
            self.client.call_service(
                "https://parallelmpp.dev/api/task", max_spend="0.51"
            )
        )

        self.assertIn("exceeds the configured $0.50 ceiling", result["error"])
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_dynamic_price_endpoint_requires_explicit_approval(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

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
        self.assertEqual(result["confirmation_prompt"], "approve")
        self.assertEqual(run.call_count, 1)

    def test_sub_cent_confirmation_keeps_the_exact_amount(self):
        budget = TempoRequestBudget(auto_limit="0.001")

        result = budget.authorize(
            {
                "url": "https://fal.mpp.tempo.xyz/fal-ai/flux/schnell",
                "method": "POST",
                "body": '{"prompt":"a puppy"}',
                "max_spend": "0.003",
            },
            Decimal("0.003"),
            requires_confirmation=False,
        )

        self.assertEqual(result["error_code"], "confirmation_required")
        self.assertEqual(result["approval_amount"], "0.003")
        self.assertEqual(result["confirmation_prompt"], "approve")

    def test_confirmation_required_locks_out_a_different_paid_call(self):
        budget = TempoRequestBudget()
        blocked = budget.authorize(
            {
                "url": "https://parallelmpp.dev/api/task",
                "method": "POST",
                "body": '{"input":"research Tempo","processor":"pro"}',
                "max_spend": "0.10",
            },
            Decimal("0.10"),
            requires_confirmation=True,
        )

        alternate = budget.authorize(
            {
                "url": "https://fal.mpp.tempo.xyz/fal-ai/flux/schnell",
                "method": "POST",
                "body": '{"prompt":"unrelated"}',
                "max_spend": "",
            },
            Decimal("0.003"),
            requires_confirmation=False,
        )

        self.assertEqual(blocked["error_code"], "confirmation_required")
        self.assertEqual(alternate["error_code"], "payment_confirmation_pending")

    def test_approval_turn_rejects_every_other_paid_call_even_under_auto_limit(self):
        approved_call = {
            "url": "https://parallelmpp.dev/api/task",
            "method": "POST",
            "body": '{"input":"research Tempo","processor":"pro"}',
            "max_spend": "0.10",
        }
        budget = TempoRequestBudget(
            approved_call=approved_call,
            approved_limit="0.10",
        )

        result = budget.authorize(
            {
                "url": "https://fal.mpp.tempo.xyz/fal-ai/flux/schnell",
                "method": "POST",
                "body": '{"prompt":"unrelated"}',
                "max_spend": "",
            },
            Decimal("0.003"),
            requires_confirmation=False,
        )

        self.assertEqual(result["error_code"], "approval_scope_mismatch")

    def test_explicit_zero_approval_ceiling_cannot_inherit_auto_spend(self):
        approved_call = {
            "url": "https://service.example/free-submit",
            "method": "POST",
            "body": "{}",
            "max_spend": "",
        }
        budget = TempoRequestBudget(
            auto_limit="0.01",
            approved_call=approved_call,
            approved_limit="0",
        )

        result = budget.authorize(
            approved_call,
            Decimal("0.003"),
            requires_confirmation=False,
        )

        self.assertEqual(result["error_code"], "cumulative_budget_exceeded")
        self.assertEqual(budget.approved_limit, Decimal("0"))

    @patch("calbot.tempo.client._run_process")
    def test_declared_cap_is_never_silently_raised_to_the_service_price(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

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

    @patch("calbot.tempo.client._run_process")
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
        self.service_details("parallel")
        budget = TempoRequestBudget(approved_call=tool_args, approved_limit="0.10")

        result = json.loads(
            self.client.run_tool("tempo_call_service", tool_args, request_budget=budget)
        )

        self.assertEqual(result["run_id"], "task_123")
        self.assertEqual(run.call_count, 2)

    @patch("calbot.tempo.client._run_process")
    def test_parallel_task_requires_explicit_input_and_processor_before_spending(
        self, run
    ):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

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

    @patch("calbot.tempo.client._run_process")
    def test_paid_request_is_not_retried_after_submission_even_when_it_fails(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=3, stdout="", stderr="network response lost"
            ),
        ]
        self.service_details("parallel")
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

        self.assertEqual(first["error_code"], "payment_submission_outcome_unknown")
        self.assertIn("do not retry", first["error"])
        self.assertEqual(second["error_code"], "paid_request_already_submitted")
        self.assertEqual(run.call_count, 2)

    @patch("calbot.tempo.client._run_process")
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
        self.service_details("parallel")
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
        poll_command = run.call_args_list[2].args[0]
        spend_flag = poll_command.index("--max-spend")
        self.assertEqual(poll_command[spend_flag + 1], "0")

        body_preview = self.client.preview_call(
            "https://parallelmpp.dev/api/task/task_123",
            method="GET",
            body='{"private":"data"}',
        )
        self.assertFalse(body_preview.trusted_nonpaying_poll)

    @patch("calbot.tempo.client._run_process")
    def test_dedicated_task_status_reconstructs_fixed_zero_spend_poll(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"status":"completed"}', stderr=""
        )

        result = json.loads(self.client.task_status("task_123"))

        self.assertEqual(result["status"], "completed")
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "https://parallelmpp.dev/api/task/task_123")
        spend_flag = command.index("--max-spend")
        self.assertEqual(command[spend_flag + 1], "0")

    @patch("calbot.tempo.client._run_process")
    def test_dedicated_task_status_rejects_unbounded_run_id(self, run):
        result = json.loads(self.client.task_status("x" * 129))

        self.assertEqual(result["error_code"], "invalid_task_run_id")
        run.assert_not_called()

    @patch("calbot.tempo.client._run_process")
    def test_request_budget_is_cumulative_across_paid_tool_calls(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"results":[]}', stderr=""
            ),
        ]
        self.service_details("parallel")
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

    @patch("calbot.tempo.client._run_process")
    def test_known_broken_openai_image_session_is_blocked_before_payment(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(OPENAI), stderr=""
        )
        self.service_details("openai")

        result = json.loads(
            self.client.call_service(
                "https://openai.mpp.tempo.xyz/v1/images/generations",
                body='{"model":"dall-e-3","prompt":"a puppy"}',
                max_spend="0.05",
                request_budget=TempoRequestBudget(),
            )
        )

        self.assertEqual(result["error_code"], "incompatible_payment_session")
        self.assertIn("fal", result["error"])
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_fal_flux_image_uses_fixed_three_tenths_cent_charge(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(FAL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"images":[{"url":"https://example.com/puppy.png"}]}',
                stderr="",
            ),
        ]
        self.service_details("fal")

        result = json.loads(
            self.client.call_service(
                "https://fal.mpp.tempo.xyz/fal-ai/flux/schnell",
                body='{"prompt":"an adorable puppy"}',
                request_budget=TempoRequestBudget(),
            )
        )

        self.assertIn("images", result)
        command = run.call_args_list[1].args[0]
        spend_flag = command.index("--max-spend")
        self.assertEqual(command[spend_flag + 1], "0.003")
        self.assertIn("--stream", command)

    @patch("calbot.tempo.client._run_process")
    def test_preview_validates_and_prices_call_without_submitting_payment(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(FAL), stderr=""
        )
        self.service_details("fal")

        preview = self.client.preview_call(
            url="https://fal.mpp.tempo.xyz/fal-ai/flux/schnell",
            method="POST",
            body='{"prompt":"a puppy"}',
            max_spend="",
        )

        self.assertIsNone(preview.error)
        self.assertEqual(preview.amount, Decimal("0.003"))
        self.assertEqual(preview.spend_limit, "0.003")
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_nonfinite_requested_spend_is_rejected_without_crashing(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

        preview = self.client.preview_call(
            url="https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            max_spend="NaN",
        )

        self.assertIn("finite", preview.error["error"])
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_extreme_decimal_exponents_are_rejected_without_expansion(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

        tiny = self.client.preview_call(
            url="https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            max_spend="1e-10000",
        )
        huge = self.client.preview_call(
            url="https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            max_spend="1e10000",
        )

        self.assertIn("at most", tiny.error["error"])
        self.assertIn("at most", huge.error["error"])
        with self.assertRaisesRegex(ValueError, "at most"):
            decimal_text(Decimal("1e-10000"))

    @patch("calbot.tempo.client._run_process")
    def test_spend_cap_precision_cannot_be_rounded_up_by_the_cli(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
        )
        self.service_details("parallel")

        preview = self.client.preview_call(
            url="https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            max_spend="0.0000009",
        )

        self.assertIn("at most", preview.error["error"])
        self.assertEqual(run.call_count, 1)

    @patch("calbot.tempo.client._run_process")
    def test_scientific_spend_cap_is_canonicalized_before_the_cli(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(PARALLEL), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"results":[]}', stderr=""
            ),
        ]
        self.service_details("parallel")

        self.client.call_service(
            "https://parallelmpp.dev/api/search",
            body='{"query":"Tempo"}',
            max_spend="1e-2",
            request_budget=TempoRequestBudget(),
        )

        command = run.call_args_list[1].args[0]
        spend_flag = command.index("--max-spend")
        self.assertEqual(command[spend_flag + 1], "0.01")

    @patch("calbot.tempo.client._run_process")
    def test_nonfinite_discovered_price_is_treated_as_dynamic(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(NONFINITE_PRICE), stderr=""
        )
        self.service_details("untrusted-price")

        preview = self.client.preview_call(
            url="https://service.example/dynamic",
            method="POST",
            body="{}",
            max_spend="0.01",
        )

        self.assertIsNone(preview.error)
        self.assertEqual(preview.amount, Decimal("0.01"))
        self.assertTrue(preview.requires_confirmation)

    @patch("calbot.tempo.client._run_process")
    def test_submitted_payment_timeout_has_unknown_outcome_and_no_retry_advice(
        self, run
    ):
        run.side_effect = subprocess.TimeoutExpired([self.client.bin, "request"], 1)

        result = json.loads(self.client._run(["request", "https://example.com"], 1))

        self.assertEqual(result["error_code"], "payment_submission_outcome_unknown")
        self.assertIn("do not retry", result["error"])


class BoundedProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "process-group isolation requires POSIX")
    def test_timeout_kills_descendant_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant-survived"
            child = (
                "import pathlib,time;"
                "time.sleep(0.4);"
                f"pathlib.Path({str(marker)!r}).write_text('survived')"
            )
            parent = (
                "import subprocess,sys,time;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
                "time.sleep(5)"
            )

            with self.assertRaises(subprocess.TimeoutExpired):
                _run_process(
                    [sys.executable, "-c", parent],
                    timeout=0.1,
                    env=dict(os.environ),
                    max_output_bytes=4096,
                )
            time.sleep(0.5)

            self.assertFalse(marker.exists())

    def test_output_overflow_terminates_command_and_fails_closed(self):
        command = [
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.write('x'*100000);"
            "sys.stdout.flush();time.sleep(5)",
        ]

        with self.assertRaises(ProcessOutputLimitExceeded):
            _run_process(
                command,
                timeout=2,
                env=dict(os.environ),
                max_output_bytes=1024,
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "RLIMIT_DATA containment is Linux-only"
    )
    def test_process_guard_enforces_child_data_allocation_limit(self):
        result = _run_process(
            [sys.executable, "-c", "bytearray(256 * 1024 * 1024)"],
            timeout=5,
            env=dict(os.environ),
            max_output_bytes=4096,
            max_data_memory_bytes=96 * 1024 * 1024,
        )

        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
