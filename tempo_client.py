"""Tempo wallet/MPP client + tool definitions for Claude."""

import json
import logging
import os
import subprocess
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

log = logging.getLogger("tempo-client")

TEMPO_BIN = os.environ.get("TEMPO_BIN", os.path.expanduser("~/.tempo/bin/tempo"))
_WALLET_DIR = os.path.normpath(os.path.join(os.path.dirname(TEMPO_BIN), "..", "wallet"))
_KEYS_PATH = os.path.join(_WALLET_DIR, "keys.toml")


def _setup():
    import base64

    # Restore wallet keys from env var if not already present
    keys_b64 = os.environ.get("TEMPO_KEYS_TOML_B64", "")
    if keys_b64 and not os.path.exists(_KEYS_PATH):
        os.makedirs(_WALLET_DIR, exist_ok=True)
        with open(_KEYS_PATH, "wb") as f:
            f.write(base64.b64decode(keys_b64))
        os.chmod(_KEYS_PATH, 0o600)
        log.info("Wallet keys written to %s", _KEYS_PATH)

    # Log startup state
    bin_ok = os.path.exists(TEMPO_BIN)
    keys_ok = os.path.exists(_KEYS_PATH)
    log.info("Tempo startup: bin=%s keys=%s bin_path=%s", bin_ok, keys_ok, TEMPO_BIN)
    if not bin_ok:
        log.error("Tempo binary missing at %s", TEMPO_BIN)
    if not keys_ok:
        log.warning("Wallet keys missing at %s (TEMPO_KEYS_TOML_B64 set=%s)", _KEYS_PATH, bool(keys_b64))


_setup()


class TempoClient:
    def __init__(self):
        self.bin = TEMPO_BIN
        self.max_spend = os.environ.get("TEMPO_MAX_SPEND", "0.50")
        self._allowed_endpoints: dict[str, set[str]] = {}

    def _run(self, args: list[str], timeout: int = 90) -> str:
        try:
            result = subprocess.run(
                [self.bin, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if result.returncode != 0:
                return json.dumps(
                    {
                        "error": "Tempo command failed",
                        "exit_code": result.returncode,
                        "details": stderr or stdout or "No error details returned",
                    }
                )
            return stdout or stderr or "{}"
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Tempo command timed out"})
        except FileNotFoundError:
            return json.dumps({"error": f"Tempo CLI not found at {self.bin}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def wallet_balance(self) -> str:
        return self._run(["wallet", "whoami", "--format", "json"])

    def discover_services(self, query: str) -> str:
        return self._run(
            ["wallet", "services", "--search", query, "--format", "json"]
        )

    def service_details(self, service_id: str) -> str:
        output = self._run(
            ["wallet", "services", service_id, "--format", "json"]
        )
        try:
            service = json.loads(output)
            if not isinstance(service, dict) or service.get("error"):
                return output
            base_url = service.get("service_url") or service.get("url")
            if not isinstance(base_url, str):
                return output
            for endpoint in service.get("endpoints", []):
                path = endpoint.get("path")
                method = endpoint.get("method")
                if isinstance(path, str) and isinstance(method, str):
                    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
                    self._allowed_endpoints.setdefault(url, set()).add(method.upper())
        except (json.JSONDecodeError, TypeError, AttributeError):
            log.warning("Could not parse service details for endpoint validation")
        return output

    def _spend_limit(self, requested: str) -> Tuple[Optional[str], Optional[str]]:
        value = requested or self.max_spend
        try:
            requested_amount = Decimal(value)
            hard_cap = Decimal(self.max_spend)
        except InvalidOperation:
            return None, "max_spend must be a valid decimal amount"
        if requested_amount <= 0:
            return None, "max_spend must be greater than zero"
        if requested_amount > hard_cap:
            return None, f"max_spend exceeds the configured ${self.max_spend} ceiling"
        return value, None

    def call_service(
        self,
        url: str,
        method: str = "POST",
        body: str = "",
        max_spend: str = "",
    ) -> str:
        method = method.upper()
        allowed_methods = self._allowed_endpoints.get(url)
        if not allowed_methods:
            return json.dumps(
                {
                    "error": (
                        "URL is not a discovered Tempo service endpoint. "
                        "Call tempo_service_details first."
                    )
                }
            )
        if method not in allowed_methods:
            return json.dumps(
                {
                    "error": (
                        f"Discovered endpoint does not support {method}; "
                        f"allowed: {', '.join(sorted(allowed_methods))}"
                    )
                }
            )
        spend_limit, error = self._spend_limit(max_spend)
        if error:
            return json.dumps({"error": error})

        args = ["request", "-t", "-X", method, "--max-spend", spend_limit]
        if body:
            args += ["--json", body]
        args.append(url)
        return self._run(args, timeout=120)

    def run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "tempo_wallet_balance":
                return self.wallet_balance()
            if name == "tempo_discover_services":
                return self.discover_services(args["query"])
            if name == "tempo_service_details":
                return self.service_details(args["service_id"])
            if name == "tempo_call_service":
                return self.call_service(
                    url=args["url"],
                    method=args.get("method", "POST"),
                    body=args.get("body", ""),
                    max_spend=args.get("max_spend", ""),
                )
            return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


TEMPO_TOOLS = [
    {
        "name": "tempo_wallet_balance",
        "description": "Check the Tempo wallet balance and address.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tempo_discover_services",
        "description": (
            "Search for available Tempo services by keyword "
            "(e.g. 'parallel', 'image', 'search', 'audio', 'browser')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "tempo_service_details",
        "description": (
            "Get full details (URL, endpoints, pricing) for a specific Tempo service by its ID. "
            "Always call this before tempo_call_service; it authorizes only the exact discovered "
            "endpoint URLs and methods for payment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string"},
            },
            "required": ["service_id"],
        },
    },
    {
        "name": "tempo_call_service",
        "description": (
            "Call a Tempo service endpoint with automatic stablecoin payment via MPP. "
            "Always use tempo_service_details first to get the exact URL, method, and body schema. "
            "Never guess endpoint paths. Calls are capped by the server's TEMPO_MAX_SPEND setting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full service URL including path, from tempo_service_details",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method, e.g. GET or POST",
                    "default": "POST",
                },
                "body": {
                    "type": "string",
                    "description": "JSON body as a string (for POST requests)",
                },
                "max_spend": {
                    "type": "string",
                    "description": (
                        "Optional per-call spend cap in USDC, e.g. '0.10'. It may be lower than "
                        "but never higher than the bot's configured ceiling."
                    ),
                },
            },
            "required": ["url"],
        },
    },
]
