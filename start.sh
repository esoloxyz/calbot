#!/usr/bin/env bash
set -euo pipefail

echo "=== calbot startup ==="
echo "HOME=$HOME USER=$(whoami)"

TEMPO_HOME="${TEMPO_HOME:-${HOME}/.tempo}"
TEMPO_BIN="${TEMPO_BIN:-${TEMPO_HOME}/bin/tempo}"
export TEMPO_HOME TEMPO_BIN
if [ ! -x "$TEMPO_BIN" ]; then
    echo "ERROR: Tempo binary is not executable at $TEMPO_BIN" >&2
    exit 1
fi
echo "Tempo ready: $("$TEMPO_BIN" --version | head -n 1)"

if [ -z "${TEMPO_WALLET_STORE_B64:-}" ]; then
    echo "ERROR: TEMPO_WALLET_STORE_B64 is not configured" >&2
    exit 1
fi

# Python performs the single wallet restore during application startup. Keeping
# restoration there also supports hosts that override this container command.
exec python bot.py
