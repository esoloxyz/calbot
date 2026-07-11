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

# Restore wallet credentials from env var
if [ -n "$TEMPO_KEYS_TOML_B64" ]; then
    install -d -m 700 "$HOME/.tempo/wallet"
    printf '%s' "$TEMPO_KEYS_TOML_B64" | base64 -d > "$HOME/.tempo/wallet/keys.toml"
    chmod 600 "$HOME/.tempo/wallet/keys.toml"
    echo "Wallet keys restored"
else
    echo "ERROR: TEMPO_KEYS_TOML_B64 is not configured" >&2
    exit 1
fi

exec python bot.py
