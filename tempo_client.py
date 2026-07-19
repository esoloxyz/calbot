"""Tempo service orchestration facade and Claude tool definitions."""

import json
import logging
import os
import re
import subprocess
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple
from urllib.parse import urljoin, urlsplit

from tempo_catalog import (
    MAX_DISCOVERED_SERVICES,
    MAX_SERVICE_ENDPOINTS,
    SAFE_DISCOVERY_WORDS,
    SERVICE_ID_PATTERN,
    _safe_public_https_url,
    _service_ids,
)
from tempo_payments import (
    EndpointPayment,
    TempoCallPreview,
    TempoRequestBudget,
    _bounded_money_decimal,
    _unknown_payment_submission,
    decimal_text,
)
from tempo_tools import TEMPO_TOOLS as TEMPO_TOOLS
from tempo_process import (
    MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES,
    ProcessOutputLimitExceeded,
    _run_process,
    _tempo_process_env,
)
from tempo_wallet import (
    _validate_wallet_store,
    restore_wallet_credentials,
)

log = logging.getLogger("tempo-client")

MAX_PAYMENT_TOKEN_DECIMALS = 18
MAX_AUTHORIZED_TASK_POLLS = 50
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")

_INCOMPATIBLE_PAYMENT_ENDPOINTS = {
    "https://openai.mpp.tempo.xyz/v1/images/generations": (
        "OpenAI image generation currently advertises an MPP session voucher that "
        "is incompatible with this access-key wallet. Use Tempo service 'fal' and "
        "the fixed-price /fal-ai/flux/schnell endpoint instead."
    )
}


class TempoClient:
    def __init__(
        self,
        *,
        bin_path: str = "",
        tempo_home: str = "",
        max_spend: str = "",
        auto_spend: str = "",
    ):
        user_tempo_dir = os.path.join(os.path.expanduser("~"), ".tempo")
        self.bin = (
            bin_path
            or os.environ.get("TEMPO_BIN")
            or os.path.join(user_tempo_dir, "bin", "tempo")
        )
        self.tempo_home = tempo_home or os.environ.get("TEMPO_HOME") or user_tempo_dir
        # wallet-cli v0.6.7 uses HOME/.tempo/wallet and ignores TEMPO_HOME.
        self.wallet_dir = os.path.join(user_tempo_dir, "wallet")
        self.store_path = os.path.join(self.wallet_dir, "store.json")
        self.max_spend = max_spend or os.environ.get("TEMPO_MAX_SPEND", "0.50")
        self.auto_spend = auto_spend or os.environ.get("TEMPO_AUTO_SPEND", "0.01")
        self._allowed_endpoints: dict[str, set[str]] = {}
        self._endpoint_payments: dict[Tuple[str, str], EndpointPayment] = {}
        self._discovered_service_ids: set[str] = set()
        self._service_catalog: OrderedDict[
            str, dict[Tuple[str, str], EndpointPayment]
        ] = OrderedDict()
        self._task_polls: OrderedDict[Tuple[str, str], EndpointPayment] = OrderedDict()

    def _rebuild_endpoint_authorization(self) -> None:
        allowed: dict[str, set[str]] = {}
        payments: dict[Tuple[str, str], EndpointPayment] = {}
        for endpoints in self._service_catalog.values():
            for (url, method), payment in endpoints.items():
                allowed.setdefault(url, set()).add(method)
                payments[(url, method)] = payment
        for (url, method), payment in self._task_polls.items():
            allowed.setdefault(url, set()).add(method)
            payments[(url, method)] = payment
        self._allowed_endpoints = allowed
        self._endpoint_payments = payments

    def prepare_wallet(self, store_b64: str, *, required: bool = True) -> None:
        """Restore wallet credentials and validate startup paths explicitly."""
        restored_path = restore_wallet_credentials(self.wallet_dir, store_b64)
        if restored_path:
            log.info("Tempo wallet credentials restored")
        bin_ok = os.path.isfile(self.bin) and os.access(self.bin, os.X_OK)
        wallet_ok = os.path.isfile(self.store_path)
        if wallet_ok:
            try:
                with open(self.store_path, "rb") as credential_file:
                    _validate_wallet_store(credential_file.read())
            except (OSError, ValueError) as exc:
                raise RuntimeError("Tempo wallet credential store is invalid") from exc
        log.info("Tempo startup check: bin=%s wallet=%s", bin_ok, wallet_ok)
        if not bin_ok:
            raise RuntimeError(
                f"Tempo binary is missing or not executable at {self.bin}"
            )
        if required and not wallet_ok:
            raise RuntimeError("Tempo wallet credentials are not configured")

    def _run(self, args: list[str], timeout: float = 90) -> str:
        try:
            result = _run_process(
                [self.bin, *args],
                timeout=timeout,
                env=_tempo_process_env(self.tempo_home),
                max_data_memory_bytes=(
                    MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES
                    if args and args[0] == "request"
                    else None
                ),
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if result.returncode != 0:
                if args and args[0] == "request":
                    return _unknown_payment_submission()
                return json.dumps(
                    {
                        "error": "Tempo command failed",
                        "exit_code": result.returncode,
                        "details": stderr or stdout or "No error details returned",
                    }
                )
            return stdout or stderr or "{}"
        except (subprocess.TimeoutExpired, ProcessOutputLimitExceeded) as exc:
            if args and args[0] == "request":
                return _unknown_payment_submission()
            error_code = (
                "tempo_command_timed_out"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "tempo_output_limit_exceeded"
            )
            return json.dumps(
                {
                    "error": (
                        "Tempo command timed out"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "Tempo command output exceeded the safe limit"
                    ),
                    "error_code": error_code,
                }
            )
        except FileNotFoundError:
            return json.dumps({"error": f"Tempo CLI not found at {self.bin}"})
        except Exception as exc:
            if args and args[0] == "request":
                return _unknown_payment_submission()
            return json.dumps({"error": str(exc)})

    def wallet_balance(self) -> str:
        return self._run(["wallet", "whoami", "--format", "json"])

    def discover_services(self, query: str) -> str:
        if not isinstance(query, str):
            return json.dumps({"error": "service search must be a string"})
        normalized = " ".join(query.casefold().split())
        words = normalized.split()
        if (
            not words
            or len(words) > 3
            or any(word not in SAFE_DISCOVERY_WORDS for word in words)
        ):
            return json.dumps(
                {
                    "error": (
                        "service search must use up to three supported capability "
                        "keywords"
                    ),
                    "supported_keywords": sorted(SAFE_DISCOVERY_WORDS),
                }
            )
        output = self._run(
            ["wallet", "services", "--search", normalized, "--format", "json"]
        )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return output
        discovered = _service_ids(payload)
        if discovered:
            self._discovered_service_ids.update(discovered)
            if len(self._discovered_service_ids) > MAX_DISCOVERED_SERVICES:
                self._discovered_service_ids = set(
                    sorted(self._discovered_service_ids)[-MAX_DISCOVERED_SERVICES:]
                )
            for stale_id in set(self._service_catalog) - self._discovered_service_ids:
                self._service_catalog.pop(stale_id, None)
            self._rebuild_endpoint_authorization()
        return output

    def service_details(self, service_id: str) -> str:
        if not isinstance(service_id, str):
            return json.dumps({"error": "service_id must be a string"})
        service_id = service_id.casefold()
        if (
            not SERVICE_ID_PATTERN.fullmatch(service_id)
            or service_id not in self._discovered_service_ids
        ):
            return json.dumps(
                {
                    "error": (
                        "service_id was not returned by a service search in this "
                        "process; call tempo_discover_services first"
                    )
                }
            )
        output = self._run(["wallet", "services", service_id, "--format", "json"])
        try:
            service = json.loads(output)
            if not isinstance(service, dict) or service.get("error"):
                return output
            returned_id = service.get("id") or service.get("service_id")
            if not isinstance(returned_id, str) or returned_id.casefold() != service_id:
                raise ValueError("service details returned a mismatched identifier")
            base_url = service.get("service_url") or service.get("url")
            if not isinstance(base_url, str) or not _safe_public_https_url(base_url):
                raise ValueError("service details returned an unsafe base URL")
            endpoints = service.get("endpoints")
            if not isinstance(endpoints, list) or not endpoints:
                raise ValueError("service details returned no endpoints")
            if len(endpoints) > MAX_SERVICE_ENDPOINTS:
                raise ValueError("service details returned too many endpoints")
            parsed_endpoints: dict[Tuple[str, str], EndpointPayment] = {}
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    raise ValueError("service details returned an invalid endpoint")
                path = endpoint.get("path")
                method = endpoint.get("method")
                if (
                    not isinstance(path, str)
                    or not path.startswith("/")
                    or urlsplit(path).scheme
                    or urlsplit(path).netloc
                    or not isinstance(method, str)
                    or method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                ):
                    raise ValueError("service details returned an invalid endpoint")
                url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                if not _safe_public_https_url(url):
                    raise ValueError("service details returned an unsafe endpoint URL")
                method = method.upper()
                payment = endpoint.get("payment") or {}
                if not isinstance(payment, dict):
                    payment = {}
                dynamic = bool(payment.get("dynamic"))
                amount = None
                raw_amount = payment.get("amount")
                decimals = payment.get("decimals", 0)
                try:
                    if raw_amount not in (None, ""):
                        token_decimals = int(decimals)
                        if not 0 <= token_decimals <= MAX_PAYMENT_TOKEN_DECIMALS:
                            raise ValueError("unsupported payment token decimals")
                        raw_value = _bounded_money_decimal(
                            raw_amount,
                            label="service price",
                        )
                        amount = _bounded_money_decimal(
                            raw_value / (Decimal(10) ** token_decimals),
                            label="service price",
                        )
                        if amount < 0:
                            amount = None
                except (InvalidOperation, TypeError, ValueError):
                    amount = None
                # Missing/invalid price metadata is treated as dynamic pricing.
                parsed_endpoints[(url, method)] = EndpointPayment(
                    amount=amount,
                    dynamic=dynamic or amount is None,
                )
            # Commit atomically only after the complete response validates. This
            # also evicts stale endpoints when a service definition changes.
            self._service_catalog.pop(service_id, None)
            self._service_catalog[service_id] = parsed_endpoints
            while len(self._service_catalog) > MAX_DISCOVERED_SERVICES:
                self._service_catalog.popitem(last=False)
            self._rebuild_endpoint_authorization()
        except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
            log.warning(
                "Could not validate service details for endpoint authorization: %s",
                exc,
            )
        return output

    def _spend_limit(self, requested: str) -> Tuple[Optional[str], Optional[str]]:
        value = requested or self.auto_spend
        try:
            requested_amount = _bounded_money_decimal(value, label="max_spend")
            hard_cap = _bounded_money_decimal(
                self.max_spend,
                label="configured spend ceiling",
            )
        except (InvalidOperation, ValueError) as exc:
            return None, str(exc)
        if requested_amount <= 0:
            return None, "max_spend must be greater than zero"
        if requested_amount > hard_cap:
            return None, f"max_spend exceeds the configured ${self.max_spend} ceiling"
        return decimal_text(requested_amount), None

    def _parallel_task_price(
        self, url: str, body: str
    ) -> Tuple[Optional[Decimal], Optional[str]]:
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
        if not RUN_ID_PATTERN.fullmatch(run_id):
            return
        self._register_task_poll(run_id)

    def _register_task_poll(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("task run_id must contain 1-128 letters, digits, _ or -")
        poll_url = f"https://parallelmpp.dev/api/task/{run_id}"
        key = (poll_url, "GET")
        self._task_polls[key] = EndpointPayment(
            amount=Decimal("0"), dynamic=False, free=True
        )
        self._task_polls.move_to_end(key)
        while len(self._task_polls) > MAX_AUTHORIZED_TASK_POLLS:
            self._task_polls.popitem(last=False)
        self._rebuild_endpoint_authorization()
        return poll_url

    def task_status(
        self,
        run_id: str,
        request_budget: Optional[TempoRequestBudget] = None,
    ) -> str:
        """Poll one validated Parallel run ID at a fixed zero-spend endpoint."""
        try:
            poll_url = self._register_task_poll(run_id)
        except ValueError as exc:
            return json.dumps({"error": str(exc), "error_code": "invalid_task_run_id"})
        return self.call_service(
            poll_url,
            method="GET",
            max_spend="0",
            request_budget=request_budget,
        )

    def call_service(
        self,
        url: str,
        method: str = "POST",
        body: str = "",
        max_spend: str = "",
        request_budget: Optional[TempoRequestBudget] = None,
    ) -> str:
        preview = self.preview_call(
            url=url,
            method=method,
            body=body,
            max_spend=max_spend,
        )
        if preview.error:
            return json.dumps(preview.error)

        budget = request_budget or TempoRequestBudget(auto_limit=self.auto_spend)
        authorization_error = budget.authorize(
            preview.call_args,
            preview.amount,
            requires_confirmation=preview.requires_confirmation,
        )
        if authorization_error:
            return json.dumps(authorization_error)

        # Stream into the parent's bounded pipe reader. Without this flag the
        # pinned CLI buffers response.text() internally before writing stdout,
        # so a hostile response could exhaust the child process before our
        # 64-KiB output limit has a chance to terminate it.
        args = ["request", "--stream", "-t", "-X", preview.call_args["method"]]
        if preview.spend_limit:
            args += ["--max-spend", preview.spend_limit]
        if preview.call_args["body"]:
            args += ["--json", preview.call_args["body"]]
        args.append(preview.call_args["url"])
        # Submission is the point of no return: even a lost response may have paid.
        budget.mark_submitted(preview.amount)
        output = self._run(args, timeout=120)
        self._authorize_task_polling(preview.call_args["url"], output)
        return output

    def preview_call(
        self,
        url: str,
        method: str = "POST",
        body: str = "",
        max_spend: str = "",
    ) -> TempoCallPreview:
        """Validate and price a service call without invoking ``tempo request``."""
        method = method.upper()
        call_args = {
            "url": url,
            "method": method,
            "body": body,
            "max_spend": max_spend,
        }
        allowed_methods = self._allowed_endpoints.get(url)
        if not allowed_methods:
            return TempoCallPreview(
                call_args=call_args,
                error={
                    "error": (
                        "URL is not a discovered Tempo service endpoint. "
                        "Call tempo_service_details first."
                    )
                },
            )
        if method not in allowed_methods:
            return TempoCallPreview(
                call_args=call_args,
                error={
                    "error": (
                        f"Discovered endpoint does not support {method}; "
                        f"allowed: {', '.join(sorted(allowed_methods))}"
                    )
                },
            )
        incompatible_reason = _INCOMPATIBLE_PAYMENT_ENDPOINTS.get(url)
        if incompatible_reason:
            return TempoCallPreview(
                call_args=call_args,
                error={
                    "error": incompatible_reason,
                    "error_code": "incompatible_payment_session",
                },
            )
        payment = self._endpoint_payments.get((url, method))
        if payment is None:
            payment = EndpointPayment(amount=None, dynamic=True)

        if payment.free:
            spend_limit = "0"
            declared_cap = Decimal("0")
        else:
            spend_limit, error = self._spend_limit(max_spend)
            if error:
                return TempoCallPreview(call_args=call_args, error={"error": error})
            declared_cap = _bounded_money_decimal(spend_limit, label="max_spend")

        task_price, schema_error = self._parallel_task_price(url, body)
        if schema_error:
            return TempoCallPreview(
                call_args=call_args,
                error={
                    "error": schema_error,
                    "error_code": "invalid_request_schema",
                },
            )

        if payment.free:
            effective_amount = Decimal("0")
            # The CLI has no default spend cap. Keep an explicit zero cap on
            # previously authorized polling calls so compromised metadata or a
            # changed remote endpoint can never turn a status check into a charge.
            spend_limit = "0"
        elif task_price is not None:
            effective_amount = task_price
            spend_limit = decimal_text(task_price)
        elif payment.amount is not None:
            effective_amount = payment.amount
            spend_limit = decimal_text(payment.amount)
        else:
            effective_amount = Decimal(spend_limit)

        if not payment.free and effective_amount > declared_cap:
            return TempoCallPreview(
                call_args=call_args,
                error={
                    "error": (
                        f"max_spend ${declared_cap} is below the service price "
                        f"${decimal_text(effective_amount)}"
                    ),
                    "error_code": "spend_cap_too_low",
                },
            )
        return TempoCallPreview(
            call_args=call_args,
            amount=effective_amount,
            spend_limit=spend_limit,
            requires_confirmation=payment.dynamic,
            price_is_maximum=(
                not payment.free and task_price is None and payment.amount is None
            ),
            trusted_nonpaying_poll=(
                payment.free and method == "GET" and not body and max_spend in {"", "0"}
            ),
        )

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
            if name == "tempo_task_status":
                return self.task_status(args["run_id"], request_budget=request_budget)
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
