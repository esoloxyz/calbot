#!/usr/bin/env bash
set -e

echo "=== calbot startup ==="
echo "HOME=$HOME USER=$(whoami)"

# Install Tempo CLI if not already present
if [ ! -f "$HOME/.tempo/bin/tempo" ]; then
    echo "Downloading Tempo install script..."
    TMPFILE=$(mktemp)
    if curl -fsSL https://tempo.xyz/install -o "$TMPFILE"; then
        echo "Running Tempo install..."
        bash "$TMPFILE"
        rm -f "$TMPFILE"
        if [ -f "$HOME/.tempo/bin/tempo" ]; then
            echo "Tempo installed: $("$HOME/.tempo/bin/tempo" --version)"
        else
            echo "ERROR: Tempo binary not found after install"
            ls -la "$HOME/.tempo/" 2>/dev/null || echo "No .tempo dir"
        fi
    else
        echo "ERROR: Failed to download Tempo install script (curl exit $?)"
        rm -f "$TMPFILE"
    fi
fi

# Restore wallet credentials from env var
if [ -n "$TEMPO_KEYS_TOML_B64" ]; then
    mkdir -p "$HOME/.tempo/wallet"
    echo "$TEMPO_KEYS_TOML_B64" | base64 -d > "$HOME/.tempo/wallet/keys.toml"
    chmod 600 "$HOME/.tempo/wallet/keys.toml"
    echo "Wallet keys restored"
fi

exec python bot.py
