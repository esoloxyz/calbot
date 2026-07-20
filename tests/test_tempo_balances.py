import json
import unittest

from calbot.tempo.balances import read_all_wallet_balances
from calbot.tempo.rendering import render_wallet_balances


def abi_string(value: str) -> str:
    encoded = value.encode()
    padding = b"\0" * ((32 - len(encoded) % 32) % 32)
    return (
        "0x"
        + (
            (32).to_bytes(32, "big")
            + len(encoded).to_bytes(32, "big")
            + encoded
            + padding
        ).hex()
    )


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class WalletBalanceReaderTests(unittest.TestCase):
    def setUp(self):
        self.wallet = "0x1111111111111111111111111111111111111111"
        self.identity = json.dumps(
            {
                "ready": True,
                "wallet": self.wallet,
                "balance": {"available": "10", "symbol": "USDC"},
                "key": {"chain_id": 4217},
                "privateKey": "never-return-this",
            }
        )

    def test_official_token_balances_are_queried_and_rendered_above_threshold(self):
        token_list = {
            "tokens": [
                {
                    "chainId": 4217,
                    "address": "0x20c0000000000000000000000000000000000000",
                    "name": "PathUSD",
                    "symbol": "pathUSD",
                    "decimals": 6,
                },
                {
                    "chainId": 4217,
                    "address": "0x20c0000000000000000000000000000000000001",
                    "name": "USD Coin",
                    "symbol": "USDC",
                    "decimals": 6,
                },
                {
                    "chainId": 4217,
                    "address": "0x20c0000000000000000000000000000000000002",
                    "name": "Coinbase Wrapped BTC",
                    "symbol": "cbBTC",
                    "decimals": 8,
                },
                {
                    "chainId": 4217,
                    "address": "0x20c0000000000000000000000000000000000003",
                    "name": "Dust USD",
                    "symbol": "dustUSD",
                    "decimals": 6,
                },
            ]
        }
        rpc_response = [
            {"jsonrpc": "2.0", "id": 1, "result": hex(20_000_000)},
            {"jsonrpc": "2.0", "id": 2, "result": abi_string("USD")},
            {"jsonrpc": "2.0", "id": 3, "result": hex(10_000_000)},
            {"jsonrpc": "2.0", "id": 4, "result": abi_string("USD")},
            {"jsonrpc": "2.0", "id": 5, "result": hex(100_000_000)},
            {"jsonrpc": "2.0", "id": 6, "result": abi_string("BTC")},
            {"jsonrpc": "2.0", "id": 7, "result": hex(500_000)},
            {"jsonrpc": "2.0", "id": 8, "result": abi_string("USD")},
        ]
        responses = iter([FakeResponse(token_list), FakeResponse(rpc_response)])
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return next(responses)

        output = read_all_wallet_balances(self.identity, opener=opener)
        payload = json.loads(output)
        reply = render_wallet_balances(output)

        self.assertEqual(
            payload["balances"],
            [
                {"symbol": "pathUSD", "amount": "20", "currency": "USD"},
                {"symbol": "USDC", "amount": "10", "currency": "USD"},
                {"symbol": "dustUSD", "amount": "0.5", "currency": "USD"},
            ],
        )
        self.assertEqual(
            reply,
            "Your Tempo wallet balances are:\n• $20 pathUSD\n• $10 USDC",
        )
        self.assertNotIn("cbBTC", output)
        self.assertNotIn("privateKey", output)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][0].method, "POST")

    def test_network_failure_falls_back_to_wallet_cli_balance(self):
        def failed_opener(_request, _timeout):
            raise TimeoutError

        output = read_all_wallet_balances(self.identity, opener=failed_opener)

        self.assertEqual(output, self.identity)
        self.assertEqual(
            render_wallet_balances(output),
            "Your Tempo wallet balance is $10 USDC.",
        )


if __name__ == "__main__":
    unittest.main()
