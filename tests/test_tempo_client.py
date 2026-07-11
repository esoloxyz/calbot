import json
import subprocess
import unittest
from unittest.mock import patch

from tempo_client import TempoClient


PARALLEL = {
    "id": "parallel",
    "service_url": "https://parallelmpp.dev",
    "endpoints": [
        {"method": "POST", "path": "/api/search"},
        {"method": "POST", "path": "/api/extract"},
        {"method": "POST", "path": "/api/task"},
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
                "0.50",
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


if __name__ == "__main__":
    unittest.main()
