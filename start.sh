#!/usr/bin/env bash
set -e

# Install Tempo CLI if not already present
if [ ! -f "$HOME/.tempo/bin/tempo" ]; then
    echo "Installing Tempo CLI..."
    curl -fsSL https://tempo.xyz/install | bash
    echo "Tempo installed: $("$HOME/.tempo/bin/tempo" --version)"
fi

# Restore wallet credentials from env var
if [ -n "$TEMPO_KEYS_TOML_B64" ]; then
    mkdir -p "$HOME/.tempo/wallet"
    echo "$TEMPO_KEYS_TOML_B64" | base64 -d > "$HOME/.tempo/wallet/keys.toml"
    chmod 600 "$HOME/.tempo/wallet/keys.toml"
    echo "Wallet keys restored"
fi

exec python bot.py
