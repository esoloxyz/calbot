"""Tempo wallet/MPP client + tool definitions for Claude."""

import base64
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

log = logging.getLogger("tempo-client")

TEMPO_BIN = os.environ.get("TEMPO_BIN", os.path.expanduser("~/.tempo/bin/tempo"))
_WALLET_DIR = os.path.normpath(os.path.join(os.path.dirname(TEMPO_BIN), "..", "wallet"))
_STORE_PATH = os.path.join(_WALLET_DIR, "store.json")
_KEYS_PATH = os.path.join(_WALLET_DIR, "keys.toml")


def _decimal_text(value: Decimal) -> str:
    return f"{value:.2f}"


def _canonical_body(body: str) -> str:
    try:
        return json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return (body or "").strip()


def _call_fingerprint(args: dict) -> Tuple[str, str, str, str]:
    max_spend = args.get("max_spend", "")
    try:
        max_spend = str(Decimal(max_spend)) if max_spend else ""
    except InvalidOperation:
        max_spend = str(max_spend)
    return (
        str(args.get("url", "")),
        str(args.get("method", "POST")).upper(),
        _canonical_body(str(args.get("body", ""))),
        max_spend,
    )


@dataclass(frozen=True)
class EndpointPayment:
    amount: Optional[Decimal]
    dynamic: bool
    free: bool = False


class TempoRequestBudget:
    """Cumulative authorization state for one user-visible bot request."""

    def __init__(
        self,
        auto_limit: str = "0.01",
        approved_call: Optional[dict] = None,
        approved_limit: str = "",
    ):
        self.auto_limit = Decimal(auto_limit)
        self.approved_fingerprint = (
            _call_fingerprint(approved_call) if approved_call else None
        )
        self.approved_limit = (
            Decimal(approved_limit) if approved_limit else self.auto_limit
        )
        self.spent = Decimal("0")
        self.paid_request_submitted = False

    def authorize(
        self,
        call_args: dict,
        amount: Decimal,
        requires_confirmation: bool,
    ) -> Optional[dict]:
        if amount == 0:
            return None
        if self.paid_request_submitted:
            return {
                "error": (
                    "A paid request was already submitted for this Telegram message. "
                    "It will not be retried automatically."
                ),
                "error_code": "paid_request_already_submitted",
            }

        approved = (
            self.approved_fingerprint is not None
            and _call_fingerprint(call_args) == self.approved_fingerprint
        )
        limit = self.approved_limit if approved else self.auto_limit
        if requires_confirmation or amount > self.auto_limit:
            if not approved or amount > self.approved_limit:
                amount_text = _decimal_text(amount)
                return {
                    "error": (
                        "This paid request requires explicit user confirmation before "
                        "any payment is submitted."
                    ),
                    "error_code": "confirmation_required",
                    "approval_amount": amount_text,
                    "confirmation_prompt": f"approve ${amount_text}",
                }
        if self.spent + amount > limit:
            return {
                "error": (
                    f"This request would exceed the cumulative ${_decimal_text(limit)} "
                    "budget for the current Telegram message."
                ),
                "error_code": "cumulative_budget_exceeded",
            }
        return None

    def mark_submitted(self, amount: Decimal) -> None:
        if amount > 0:
            self.spent += amount
            self.paid_request_submitted = True


def restore_wallet_credentials(
    wallet_dir: str, store_b64: str, legacy_keys_b64: str
) -> Optional[str]:
    """Restore the current wallet store, with legacy keys.toml as a fallback."""
    if store_b64:
        encoded = store_b64
        path = os.path.join(wallet_dir, "store.json")
    elif legacy_keys_b64:
        encoded = legacy_keys_b64
        path = os.path.join(wallet_dir, "keys.toml")
    else:
        return None

    os.makedirs(wallet_dir, mode=0o700, exist_ok=True)
    os.chmod(wallet_dir, 0o700)
    with open(path, "wb") as credential_file:
        credential_file.write(base64.b64decode(encoded))
    os.chmod(path, 0o600)
    return path


def _setup():
    store_b64 = os.environ.get("TEMPO_WALLET_STORE_B64", "")
    keys_b64 = os.environ.get("TEMPO_KEYS_TOML_B64", "")
    restored_path = restore_wallet_credentials(_WALLET_DIR, store_b64, keys_b64)
    if restored_path:
        log.info("Wallet credentials written to %s", restored_path)

    # Log startup state
    bin_ok = os.path.exists(TEMPO_BIN)
    wallet_ok = os.path.exists(_STORE_PATH) or os.path.exists(_KEYS_PATH)
    log.info(
        "Tempo startup: bin=%s wallet=%s bin_path=%s",
        bin_ok,
        wallet_ok,
        TEMPO_BIN,
    )
    if not bin_ok:
        log.error("Tempo binary missing at %s", TEMPO_BIN)
    if not wallet_ok:
        log.warning(
            "Wallet credentials missing (TEMPO_WALLET_STORE_B64 set=%s, "
            "legacy TEMPO_KEYS_TOML_B64 set=%s)",
            bool(store_b64),
            bool(keys_b64),
        )


_setup()


class TempoClient:
    def __init__(self):
        self.bin = TEMPO_BIN
        self.max_spend = os.environ.get("TEMPO_MAX_SPEND", "0.50")
        self.auto_spend = os.environ.get("TEMPO_AUTO_SPEND", "0.01")
        self._allowed_endpoints: dict[str, set[str]] = {}
        self._endpoint_payments: dict[Tuple[str, str], EndpointPayment] = {}

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
                    method = method.upper()
                    self._allowed_endpoints.setdefault(url, set()).add(method)
                    payment = endpoint.get("payment") or {}
                    dynamic = bool(payment.get("dynamic"))
                    amount = None
                    raw_amount = payment.get("amount")
                    decimals = payment.get("decimals", 0)
                    try:
                        if raw_amount not in (None, ""):
                            amount = Decimal(str(raw_amount)) / (
                                Decimal(10) ** int(decimals)
                            )
                    except (InvalidOperation, TypeError, ValueError):
                        amount = None
                    # Missing price metadata is treated like dynamic pricing.
                    self._endpoint_payments[(url, method)] = EndpointPayment(
                        amount=amount,
                        dynamic=dynamic or amount is None,
                    )
        except (json.JSONDecodeError, TypeError, AttributeError):
            log.warning("Could not parse service details for endpoint validation")
        return output

    def _spend_limit(self, requested: str) -> Tuple[Optional[str], Optional[str]]:
        value = requested or self.auto_spend
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

    def _parallel_task_price(self, url: str, body: str) -> Tuple[Optional[Decimal], Optional[str]]:
        if url.rstrip("/") != "https://parallelmpp.dev/api/task":
            return None, None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None, "Parallel task body must be valid JSON"
        if not isinstance(payload, dict) or not isinstance(payload.get("input"), str):
            return None, (
                "Parallel task body requires a non-empty 'input' string and "
                "processor 'pro' or 'ultra'"
            )
        if not payload["input"].strip():
            return None, "Parallel task body requires a non-empty 'input' string"
        processor = payload.get("processor")
        prices = {"pro": Decimal("0.10"), "ultra": Decimal("0.30")}
        if processor not in prices:
            return None, "Parallel task body requires processor 'pro' or 'ultra'"
        return prices[processor], None

    def _authorize_task_polling(self, task_url: str, output: str) -> None:
        if task_url.rstrip("/") != "https://parallelmpp.dev/api/task":
            return
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            return
        if not isinstance(result, dict) or result.get("error"):
            return
        run_id = result.get("run_id") or result.get("task_run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        if not all(character.isalnum() or character in "_-" for character in run_id):
            return
        poll_url = f"{task_url.rstrip('/')}/{run_id}"
        self._allowed_endpoints.setdefault(poll_url, set()).add("GET")
        self._endpoint_payments[(poll_url, "GET")] = EndpointPayment(
            amount=Decimal("0"), dynamic=False, free=True
        )

    def call_service(
        self,
        url: str,
        method: str = "POST",
        body: str = "",
        max_spend: str = "",
        request_budget: Optional[TempoRequestBudget] = None,
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
        payment = self._endpoint_payments.get((url, method))
        if payment is None:
            payment = EndpointPayment(amount=None, dynamic=True)

        spend_limit, error = self._spend_limit(max_spend)
        if error:
            return json.dumps({"error": error})
        declared_cap = Decimal(spend_limit)

        task_price, schema_error = self._parallel_task_price(url, body)
        if schema_error:
            return json.dumps(
                {"error": schema_error, "error_code": "invalid_request_schema"}
            )

        if payment.free:
            effective_amount = Decimal("0")
            spend_limit = ""
        elif task_price is not None:
            effective_amount = task_price
            spend_limit = str(task_price)
        elif payment.amount is not None:
            effective_amount = payment.amount
            spend_limit = str(payment.amount)
        else:
            effective_amount = Decimal(spend_limit)

        if not payment.free and effective_amount > declared_cap:
            return json.dumps(
                {
                    "error": (
                        f"max_spend ${declared_cap} is below the service price "
                        f"${_decimal_text(effective_amount)}"
                    ),
                    "error_code": "spend_cap_too_low",
                }
            )

        call_args = {
            "url": url,
            "method": method,
            "body": body,
            "max_spend": max_spend,
        }
        budget = request_budget or TempoRequestBudget(auto_limit=self.auto_spend)
        authorization_error = budget.authorize(
            call_args,
            effective_amount,
            requires_confirmation=payment.dynamic,
        )
        if authorization_error:
            return json.dumps(authorization_error)

        args = ["request", "-t", "-X", method]
        if spend_limit:
            args += ["--max-spend", spend_limit]
        if body:
            args += ["--json", body]
        args.append(url)
        # Submission is the point of no return: even a lost response may have paid.
        budget.mark_submitted(effective_amount)
        output = self._run(args, timeout=120)
        self._authorize_task_polling(url, output)
        return output

    def run_tool(
        self,
        name: str,
        args: dict,
        request_budget: Optional[TempoRequestBudget] = None,
    ) -> str:
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
                    request_budget=request_budget,
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
            "Never guess endpoint paths. Fixed-price calls up to $0.01 can run automatically. "
            "Dynamic-price or higher-priced calls return an exact confirmation phrase that the "
            "user must send before the same call can run. Never retry a submitted paid call."
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
                        "Per-call spend cap in USDC, required for dynamic pricing. It may be lower "
                        "than but never higher than the bot's configured ceiling."
                    ),
                },
            },
            "required": ["url"],
        },
    },
]
