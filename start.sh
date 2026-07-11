#!/usr/bin/env bash
set -e

echo "=== calbot startup ==="
echo "HOME=$HOME USER=$(whoami)"

TEMPO_BIN="${TEMPO_BIN:-$HOME/.tempo/bin/tempo}"
if [ ! -x "$TEMPO_BIN" ]; then
    echo "ERROR: Tempo binary is not executable at $TEMPO_BIN" >&2
    exit 1
fi
echo "Tempo ready: $($TEMPO_BIN --version | head -n 1)"

# Restore the current Tempo wallet credentials.
install -d -m 700 "$HOME/.tempo/wallet"
if [ -n "${TEMPO_WALLET_STORE_B64:-}" ]; then
    printf '%s' "$TEMPO_WALLET_STORE_B64" | base64 -d > "$HOME/.tempo/wallet/store.json"
    chmod 600 "$HOME/.tempo/wallet/store.json"
    echo "Wallet store restored"
else
    echo "ERROR: TEMPO_WALLET_STORE_B64 is not configured" >&2
    exit 1
fi

exec python bot.py
