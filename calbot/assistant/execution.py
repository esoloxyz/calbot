"""Typed tool-execution results and payload-free logging classification."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolExecutionResult:
    """Executor response with an optional immediate user-visible boundary."""

    output: str
    user_reply: str | None = None
    halt: bool = False


def _tool_outcome(output: str) -> str:
    """Return a non-sensitive result label suitable for production logs."""
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return "unstructured"
    if not isinstance(payload, dict):
        return "structured"
    if payload.get("error_code"):
        return "has_error_code"
    if payload.get("error"):
        return "error"
    if payload.get("status"):
        return "has_status"
    return "ok"
