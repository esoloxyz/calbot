"""Read all stablecoin balances for a Tempo wallet without signing anything."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


TOKEN_LIST_URL = "https://tokenlist.tempo.xyz/list/{chain_id}"
DEFAULT_RPC_URLS = {
    4217: "https://rpc.tempo.xyz",
    42431: "https://rpc.moderato.tempo.xyz",
}
MAX_HTTP_RESPONSE_BYTES = 512 * 1024
MAX_TOKEN_COUNT = 256
HTTP_TIMEOUT_SECONDS = 10

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_SYMBOL = re.compile(r"[A-Za-z0-9._-]{1,32}")
_BALANCE_OF_SELECTOR = "70a08231"
_CURRENCY_SELECTOR = "e5a6b10f"
_FIAT_CURRENCIES = frozenset(
    {
        "AED",
        "AUD",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "DKK",
        "EUR",
        "GBP",
        "HKD",
        "INR",
        "JPY",
        "KRW",
        "MXN",
        "NOK",
        "NZD",
        "PLN",
        "SAR",
        "SEK",
        "SGD",
        "TRY",
        "USD",
        "ZAR",
    }
)

log = logging.getLogger("tempo-balances")


def is_fiat_currency(currency: str) -> bool:
    return currency.upper() in _FIAT_CURRENCIES


def _read_json(request: Request, opener: Callable) -> object:
    with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_HTTP_RESPONSE_BYTES:
            raise ValueError("Tempo balance response is too large")
        body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    if len(body) > MAX_HTTP_RESPONSE_BYTES:
        raise ValueError("Tempo balance response is too large")
    return json.loads(body)


def _wallet_identity(identity_output: str) -> tuple[dict, str, int] | None:
    try:
        payload = json.loads(identity_output)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    wallet = payload.get("wallet")
    key = payload.get("key")
    chain_id = key.get("chain_id") if isinstance(key, dict) else None
    try:
        chain_id = int(chain_id)
    except (TypeError, ValueError):
        return None
    if not isinstance(wallet, str) or not _ADDRESS.fullmatch(wallet):
        return None
    return payload, wallet, chain_id


def _token_catalog(chain_id: int, opener: Callable) -> list[dict]:
    request = Request(
        TOKEN_LIST_URL.format(chain_id=chain_id),
        headers={"Accept": "application/json", "User-Agent": "calbot/1"},
        method="GET",
    )
    payload = _read_json(request, opener)
    if not isinstance(payload, dict) or not isinstance(payload.get("tokens"), list):
        raise ValueError("Tempo token list has an invalid shape")
    tokens = payload["tokens"]
    if len(tokens) > MAX_TOKEN_COUNT:
        raise ValueError("Tempo token list has too many entries")
    validated = []
    seen_addresses = set()
    for token in tokens:
        if not isinstance(token, dict) or token.get("chainId") != chain_id:
            continue
        address = token.get("address")
        symbol = token.get("symbol")
        decimals = token.get("decimals")
        if (
            not isinstance(address, str)
            or not _ADDRESS.fullmatch(address)
            or not isinstance(symbol, str)
            or not _SYMBOL.fullmatch(symbol)
            or type(decimals) is not int
            or not 0 <= decimals <= 18
            or address.casefold() in seen_addresses
        ):
            continue
        seen_addresses.add(address.casefold())
        validated.append(
            {
                "address": address,
                "symbol": symbol,
                "name": str(token.get("name", ""))[:100],
                "decimals": decimals,
            }
        )
    if not validated:
        raise ValueError("Tempo token list has no valid entries")
    return validated


def _decode_abi_string(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]*", value):
        return ""
    try:
        encoded = bytes.fromhex(value[2:])
        if len(encoded) < 64:
            return ""
        offset = int.from_bytes(encoded[:32], "big")
        if offset < 0 or offset + 32 > len(encoded):
            return ""
        length = int.from_bytes(encoded[offset : offset + 32], "big")
        if not 1 <= length <= 12 or offset + 32 + length > len(encoded):
            return ""
        return encoded[offset + 32 : offset + 32 + length].decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return ""


def _looks_like_stablecoin(token: dict, currency: str) -> bool:
    if currency:
        return is_fiat_currency(currency)
    label = f"{token['symbol']} {token['name']}".upper()
    return any(code in label for code in _FIAT_CURRENCIES)


def _rpc_balances(
    *,
    wallet: str,
    rpc_url: str,
    tokens: list[dict],
    opener: Callable,
) -> list[dict]:
    parsed_url = urlsplit(rpc_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ValueError("Tempo RPC URL must be public HTTPS")
    wallet_argument = "0" * 24 + wallet[2:].lower()
    calls = []
    call_index: dict[int, tuple[str, dict]] = {}
    request_id = 1
    for token in tokens:
        calls.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "eth_call",
                "params": [
                    {
                        "to": token["address"],
                        "data": "0x" + _BALANCE_OF_SELECTOR + wallet_argument,
                    },
                    "latest",
                ],
            }
        )
        call_index[request_id] = ("balance", token)
        request_id += 1
        calls.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "eth_call",
                "params": [
                    {"to": token["address"], "data": "0x" + _CURRENCY_SELECTOR},
                    "latest",
                ],
            }
        )
        call_index[request_id] = ("currency", token)
        request_id += 1

    request = Request(
        rpc_url,
        data=json.dumps(calls, separators=(",", ":")).encode(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "calbot/1",
        },
        method="POST",
    )
    payload = _read_json(request, opener)
    if not isinstance(payload, list):
        raise ValueError("Tempo RPC did not return a batch response")

    balances: dict[str, int] = {}
    currencies: dict[str, str] = {}
    valid_responses = 0
    for response in payload:
        if not isinstance(response, dict) or response.get("error"):
            continue
        response_id = response.get("id")
        indexed = call_index.get(response_id) if type(response_id) is int else None
        if indexed is None:
            continue
        kind, token = indexed
        address = token["address"].casefold()
        result = response.get("result")
        if kind == "currency":
            currencies[address] = _decode_abi_string(result)
            continue
        if not isinstance(result, str) or not re.fullmatch(
            r"0x[0-9a-fA-F]{1,64}", result
        ):
            continue
        balances[address] = int(result, 16)
        valid_responses += 1
    if valid_responses == 0:
        raise ValueError("Tempo RPC returned no valid balances")

    rendered = []
    for token in tokens:
        address = token["address"].casefold()
        currency = currencies.get(address, "")
        raw_amount = balances.get(address, 0)
        if raw_amount <= 0 or not _looks_like_stablecoin(token, currency):
            continue
        amount = Decimal(raw_amount) / (Decimal(10) ** token["decimals"])
        rendered.append(
            {
                "symbol": token["symbol"],
                "amount": format(amount, "f"),
                "currency": currency.upper(),
            }
        )
    return rendered


def _legacy_balance(payload: dict) -> dict | None:
    balance = payload.get("balance")
    if not isinstance(balance, dict):
        return None
    symbol = balance.get("symbol")
    amount = balance.get("available", balance.get("total"))
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        return None
    try:
        parsed_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed_amount.is_finite() or parsed_amount <= 0:
        return None
    return {"symbol": symbol, "amount": format(parsed_amount, "f"), "currency": ""}


def read_all_wallet_balances(
    identity_output: str,
    *,
    rpc_url: str = "",
    opener: Callable | None = None,
) -> str:
    """Augment wallet-cli's active balance with official token-list balances."""
    identity = _wallet_identity(identity_output)
    if identity is None:
        return identity_output
    payload, wallet, chain_id = identity
    selected_rpc = rpc_url or DEFAULT_RPC_URLS.get(chain_id, "")
    if not selected_rpc:
        return identity_output
    open_request = opener or urlopen
    try:
        tokens = _token_catalog(chain_id, open_request)
        balances = _rpc_balances(
            wallet=wallet,
            rpc_url=selected_rpc,
            tokens=tokens,
            opener=open_request,
        )
    except Exception:
        log.warning("Could not load all stablecoin balances; using wallet CLI fallback")
        return identity_output

    legacy = _legacy_balance(payload)
    symbols = {balance["symbol"].casefold() for balance in balances}
    if legacy and legacy["symbol"].casefold() not in symbols:
        balances.append(legacy)
    return json.dumps(
        {
            "ready": bool(payload.get("ready", True)),
            "wallet": wallet,
            "chain_id": chain_id,
            "balances": balances,
        },
        separators=(",", ":"),
    )
