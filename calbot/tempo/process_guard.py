"""Exec a child with a hard data-allocation ceiling on Linux."""

from __future__ import annotations

import os
import resource
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit("usage: process_guard.py <max-bytes> <command> [args...]")
    try:
        max_bytes = int(argv[1])
    except ValueError as exc:
        raise SystemExit("max-bytes must be an integer") from exc
    if max_bytes <= 0:
        raise SystemExit("max-bytes must be positive")

    # RLIMIT_DATA also covers anonymous mmap allocations on the production
    # Linux kernel, including buffered response bodies, without constraining
    # V8's much larger reserved (but uncommitted) virtual address ranges.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_DATA, (max_bytes, max_bytes))
    os.execvpe(argv[2], argv[2:], os.environ)
    return 127  # pragma: no cover - exec only returns on failure


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
