# Calbot

Calbot is a private Telegram assistant powered by Claude. It manages a shared
Google Calendar and can discover and call paid APIs through Tempo and the
[Machine Payments Protocol](https://mpp.dev/).

Examples:

- `Dinner at Lilia Saturday at 8`
- `What do we have this weekend?`
- `Move Friday's dinner to 7:30`
- `Use Parallel to search for today's Tempo news`
- `/balance`

## What it does

- Creates, lists, updates, and deletes Google Calendar events.
- Understands conversational dates and follow-up edits.
- Posts scheduled weekend and week-ahead summaries.
- Discovers MPP services at runtime instead of hard-coding providers.
- Pays for approved service calls from a Tempo wallet.
- Restricts access to one configured Telegram chat.
- Keeps short, in-memory conversation history per chat.

## How it works

```text
Telegram message
      |
      v
Claude tool-use loop
      |
      +----> Google Calendar API
      |
      +----> Tempo service directory
                 |
                 v
          discovered endpoint
                 |
                 v
        MPP HTTP 402 payment flow
                 |
                 v
           paid API response
```

For paid calls, Claude first searches the Tempo service directory, loads the
selected service's exact endpoint metadata, and then calls that endpoint through
`tempo request`. The Tempo CLI handles the MPP challenge, payment signature, and
retry. Calbot rejects guessed endpoints and HTTP methods.

## Setup

The full Telegram, Google Cloud, Calendar, and Railway walkthrough is in
[SETUP.md](SETUP.md).

### Requirements

- Python 3.9+
- A Telegram bot token
- An Anthropic API key
- A Google Cloud service account with Calendar access
- A Tempo wallet and access key for MPP calls
- Railway or another always-on container host

### Install locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install and authenticate the Tempo CLI:

```bash
curl -fsSL https://tempo.xyz/install | bash
"$HOME/.tempo/bin/tempo" wallet login
"$HOME/.tempo/bin/tempo" wallet whoami --format json
```

Set the required environment variables, then start Calbot:

```bash
python bot.py
```

## Configuration

See [.env.example](.env.example) for sample values.

| Variable | Required | Purpose |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token issued by BotFather |
| `ALLOWED_CHAT_ID` | Yes | Only this Telegram chat can use the bot |
| `ANTHROPIC_API_KEY` | Yes | Claude API authentication |
| `ANTHROPIC_MODEL` | No | Claude model; defaults to `claude-sonnet-4-6` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Google service-account JSON on one line |
| `CALENDAR_ID` | Yes | Calendar the bot can manage |
| `TEMPO_KEYS_TOML_B64` | Yes | Base64-encoded Tempo access-key file |
| `TEMPO_MAX_SPEND` | No | Hard per-request ceiling; defaults to `0.50` |
| `TEMPO_BIN` | No | Tempo binary path; defaults to `~/.tempo/bin/tempo` |
| `TIMEZONE` | No | IANA timezone; defaults to `America/New_York` |
| `BOT_OWNER` | No | Name used in the assistant prompt |
| `RESPOND_TO_ALL` | No | Set `false` to require mentions or replies |

## Configure the Tempo wallet on Railway

`keys.toml` contains a signing key. Use a dedicated, low-balance wallet or a
limited access key, and treat the encoded value as a production secret.

From a Railway-linked checkout:

```bash
base64 < "$HOME/.tempo/wallet/keys.toml" |
  railway variable set TEMPO_KEYS_TOML_B64 --stdin \
    --service worker --environment production

printf '0.50' |
  railway variable set TEMPO_MAX_SPEND --stdin \
    --service worker --environment production
```

Confirm the deployed wallet from Telegram with `/balance`.

## MPP safety controls

Calbot applies several controls before a paid request:

1. The service must come from Tempo's live service directory.
2. Calbot must load the service details before calling it.
3. The URL and HTTP method must exactly match a discovered endpoint.
4. Every request receives a `--max-spend` limit.
5. A caller cannot raise the configured `TEMPO_MAX_SPEND` ceiling.
6. CLI failures are returned as structured errors instead of looking successful.

The application ceiling is defense in depth; it does not replace the wallet
access key's on-chain spending limit. If the host or signing key is compromised,
revoke the key from the Tempo wallet.

## Telegram commands

| Command | Description |
|---|---|
| `/start` | Show Calbot's capabilities |
| `/id` | Show the current chat ID |
| `/today` | Summarize today's calendar |
| `/week` | Summarize the next seven days |
| `/weekend` | Summarize Friday through Sunday |
| `/balance` | Show the deployed Tempo wallet status |

Calbot can also perform these actions through normal conversation.

## Deploying

The Docker image installs Python dependencies and a GPG-verified Tempo CLI. The
wallet key is restored from `TEMPO_KEYS_TOML_B64` at startup or import time, so
the app works whether Railway uses the Docker `CMD` or an explicit `python
bot.py` start-command override.

Pushes to `main` deploy automatically when the Railway service is connected to
this GitHub repository. A manual deployment can be started with:

```bash
railway up --service worker --environment production
```

Healthy logs include `Bot starting (polling)…` and should not include `Tempo
binary missing`, `Wallet keys missing`, or `Tempo command failed`.

## Testing

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile tempo_client.py bot.py calendar_client.py
bash -n start.sh
```

The acceptance tests cover current Tempo CLI argument order, service discovery,
endpoint authorization, structured failures, and the spend ceiling.

## Repository layout

| File | Purpose |
|---|---|
| `bot.py` | Telegram handlers, Claude tool loop, and scheduled summaries |
| `calendar_client.py` | Google Calendar operations and Claude tool definitions |
| `tempo_client.py` | Tempo wallet, service discovery, and MPP calls |
| `Dockerfile` | Railway/container image with Tempo installed |
| `start.sh` | Wallet restoration and process startup |
| `tests/test_tempo_client.py` | Tempo/MPP acceptance tests |
| `SETUP.md` | Detailed first-time deployment guide |

## Security notes

- Never commit `.env`, Google credentials, Telegram tokens, or Tempo keys.
- Keep the bot in a private chat and configure `ALLOWED_CHAT_ID`.
- Rotate the Telegram token if it appears in logs or screenshots.
- Keep HTTP client logging at warning level; Telegram API URLs contain the token.
- Keep mutable Telegram display names out of model-visible message content.
- Use the smallest practical Tempo wallet balance and access-key allowance.
- Review dynamic-price endpoints before raising `TEMPO_MAX_SPEND`.
