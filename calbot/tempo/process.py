"""Bounded subprocess execution for Tempo CLI extensions."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional


MAX_PROCESS_OUTPUT_BYTES = 64 * 1024
# The x86_64 single-executable V8 runtime needs more than 384 MiB even for
# startup. 512 MiB leaves bounded working headroom without allowing an
# untrusted response body to consume the container unchecked.
MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES = 512 * 1024 * 1024
PROCESS_TERMINATION_GRACE_SECONDS = 1.0
TEMPO_PROCESS_ENV_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NODE_EXTRA_CA_CERTS",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
)


class ProcessOutputLimitExceeded(RuntimeError):
    """Raised after a command exceeds the bounded stdout/stderr budget."""


def _tempo_process_env(tempo_home: str) -> dict[str, str]:
    """Build the minimal runtime environment needed by the pinned Tempo CLI."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in TEMPO_PROCESS_ENV_ALLOWLIST
    }
    environment["TEMPO_HOME"] = tempo_home
    return environment


def _terminate_process_group(process: subprocess.Popen) -> None:
    """Terminate the command and every descendant that inherited its process group."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # The group leader may have exited while a child remains. Always make one
        # final group-wide attempt so a submitted payment cannot survive timeout.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:  # pragma: no cover - the production image is POSIX
        process.terminate()
        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive OS failure
        process.kill()
        process.wait()


def _run_process(
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str],
    max_output_bytes: int = MAX_PROCESS_OUTPUT_BYTES,
    max_data_memory_bytes: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a command in an isolated process group with bounded captured output."""
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")

    launched_command = command
    if max_data_memory_bytes is not None:
        guard = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "process_guard.py"
        )
        launched_command = [
            sys.executable,
            guard,
            str(max_data_memory_bytes),
            *command,
        ]
    process = subprocess.Popen(
        launched_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    output_lock = threading.Lock()
    output_size = 0
    output_exceeded = threading.Event()

    def read_stream(stream, name: str) -> None:
        nonlocal output_size
        try:
            while chunk := stream.read(16 * 1024):
                with output_lock:
                    remaining = max_output_bytes - output_size
                    if remaining <= 0:
                        output_exceeded.set()
                        return
                    accepted = chunk[:remaining]
                    buffers[name].extend(accepted)
                    output_size += len(accepted)
                    if len(accepted) < len(chunk):
                        output_exceeded.set()
                        return
        finally:
            stream.close()

    readers = [
        threading.Thread(
            target=read_stream,
            args=(process.stdout, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=(process.stderr, "stderr"),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    while process.poll() is None:
        if output_exceeded.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue

    if timed_out or output_exceeded.is_set():
        _terminate_process_group(process)

    for reader in readers:
        reader.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
    if any(reader.is_alive() for reader in readers):
        # A descendant can keep inherited pipe descriptors open after its parent
        # exits. Kill that process group and fail closed instead of hanging.
        timed_out = True
        _terminate_process_group(process)
        for reader in readers:
            reader.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)

    stdout = buffers["stdout"].decode("utf-8", errors="replace")
    stderr = buffers["stderr"].decode("utf-8", errors="replace")
    if timed_out:
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    if output_exceeded.is_set():
        raise ProcessOutputLimitExceeded(
            f"Tempo command output exceeded {max_output_bytes} bytes"
        )
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
