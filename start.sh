#!/usr/bin/env bash
set -e

# Restore wallet credentials from env var
if [ -n "$TEMPO_KEYS_TOML_B64" ]; then
    mkdir -p "$HOME/.tempo/wallet"
    echo "$TEMPO_KEYS_TOML_B64" | base64 -d > "$HOME/.tempo/wallet/keys.toml"
    chmod 600 "$HOME/.tempo/wallet/keys.toml"
fi

exec python bot.py
