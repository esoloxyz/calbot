"""Tempo wallet/MPP client + tool definitions for Claude."""

import json
import logging
import os
import subprocess

log = logging.getLogger("tempo-client")

TEMPO_BIN = os.environ.get("TEMPO_BIN", os.path.expanduser("~/.tempo/bin/tempo"))


def _install_cli():
    """Install the Tempo CLI if it's not already present."""
    if os.path.exists(TEMPO_BIN):
        return
    log.info("Tempo CLI not found at %s — installing...", TEMPO_BIN)
    result = subprocess.run(
        ["bash", "-c", "curl -fsSL https://tempo.xyz/install | bash"],
        timeout=120,
    )
    if result.returncode != 0:
        log.error("Tempo CLI install failed (exit %d)", result.returncode)
    else:
        # Find where it actually installed
        found = subprocess.run(
            ["find", "/root", "/home", "/app", "-name", "tempo", "-type", "f"],
            capture_output=True, text=True, timeout=15,
        )
        log.info("Tempo binaries found after install: %s", found.stdout.strip() or "(none)")
        if os.path.exists(TEMPO_BIN):
            log.info("Tempo CLI installed successfully at %s", TEMPO_BIN)
        else:
            log.error("Tempo CLI install ran but binary not found at expected path %s", TEMPO_BIN)


def _restore_keys():
    """Write wallet keys from env var if the keys file doesn't exist."""
    keys_b64 = os.environ.get("TEMPO_KEYS_TOML_B64", "")
    if not keys_b64:
        return
    keys_path = os.path.normpath(
        os.path.join(os.path.dirname(TEMPO_BIN), "..", "wallet", "keys.toml")
    )
    if os.path.exists(keys_path):
        return
    os.makedirs(os.path.dirname(keys_path), exist_ok=True)
    import base64
    with open(keys_path, "wb") as f:
        f.write(base64.b64decode(keys_b64))
    os.chmod(keys_path, 0o600)
    log.info("Wallet keys restored to %s", keys_path)


_install_cli()
_restore_keys()


class TempoClient:
    def __init__(self):
        self.bin = TEMPO_BIN

    def _run(self, args: list[str], timeout: int = 90) -> str:
        try:
            result = subprocess.run(
                [self.bin, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Tempo command timed out"})
        except FileNotFoundError:
            return json.dumps({"error": f"Tempo CLI not found at {self.bin}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def wallet_balance(self) -> str:
        return self._run(["wallet", "-t", "whoami"])

    def discover_services(self, query: str) -> str:
        return self._run(["wallet", "-t", "services", "--search", query])

    def service_details(self, service_id: str) -> str:
        return self._run(["wallet", "-t", "services", service_id])

    def call_service(
        self,
        url: str,
        method: str = "POST",
        body: str = "",
        max_spend: str = "",
    ) -> str:
        args = ["request", "-t", "-X", method.upper()]
        if max_spend:
            args += ["--max-spend", max_spend]
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
            "Always call this before tempo_call_service to get the exact URL and body schema."
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
            "Never guess endpoint paths."
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
                    "description": "Optional spend cap in USDC, e.g. '0.50'",
                },
            },
            "required": ["url"],
        },
    },
]
