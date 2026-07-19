"""Validation helpers for Tempo service-directory metadata."""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlsplit


MAX_DISCOVERED_SERVICES = 100
MAX_SERVICE_ENDPOINTS = 100
SERVICE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
LEGACY_NUMERIC_HOST_PATTERN = re.compile(
    r"(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)"
    r"(?:\.(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)){0,3}",
    re.IGNORECASE,
)
SAFE_DISCOVERY_WORDS = frozenset(
    {
        "ai",
        "audio",
        "browser",
        "code",
        "data",
        "fal",
        "finance",
        "image",
        "openai",
        "parallel",
        "research",
        "search",
        "speech",
        "text",
        "translation",
        "video",
        "vision",
        "voice",
        "weather",
        "web",
    }
)


def _safe_public_https_url(url: str) -> bool:
    """Reject endpoint metadata that could address local infrastructure."""
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or any(ord(character) < 32 for character in url)
    ):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    raw_authority = parsed.netloc
    raw_hostname = parsed.hostname or ""
    if (
        not raw_authority
        or any(ord(character) > 127 for character in raw_authority)
        or "%" in raw_authority
        or "\\" in raw_authority
    ):
        return False
    hostname = raw_hostname.casefold()
    canonical_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical_authority = canonical_host + (f":{port}" if port is not None else "")
    if (
        parsed.scheme != "https"
        or not hostname
        or not hostname.isascii()
        or hostname.endswith(".")
        or raw_authority.casefold() != canonical_authority
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    if LEGACY_NUMERIC_HOST_PATTERN.fullmatch(hostname):
        return False
    try:
        return ip_address(hostname).is_global
    except ValueError:
        # DNS can change after validation. Submission still requires an exact
        # actor-bound approval and a zero/finite spend cap.
        return True


def _service_ids(payload) -> set[str]:
    """Extract bounded service identifiers from directory JSON."""
    found: set[str] = set()

    def visit(value) -> None:
        if len(found) >= MAX_DISCOVERED_SERVICES:
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"id", "service_id"} and isinstance(nested, str):
                    candidate = nested.casefold()
                    if SERVICE_ID_PATTERN.fullmatch(candidate):
                        found.add(candidate)
                else:
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    return found
