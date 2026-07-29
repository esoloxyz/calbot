# Calbot

Calbot is a small private Telegram bot for managing a shared Google Calendar.
It is intentionally limited to scheduling for two people.

Examples:

- `Dinner at Lilia Saturday at 8`
- `What do we have this weekend?`
- `Move Friday's dinner to 7:30`
- `Delete the dentist appointment`

## What it does

- Creates, lists, updates, and deletes Google Calendar events.
- Understands conversational dates and follow-up edits through Claude.
- Posts a Friday weekend preview and a Sunday week-ahead summary.
- Provides `/today`, `/week`, and `/weekend` calendar summaries.
- Restricts access to one Telegram chat and, optionally, specific users.
- Executes clear calendar requests immediately and asks a short follow-up only
  when an important date, time, or event is ambiguous.
- Keeps Telegram replies conversational and suppresses internal tool data.

Calbot does not include payments, wallets, paid APIs, web search, food ordering,
or any non-calendar integrations.

## How it works

```text
Telegram message
      |
      v
Claude calendar tool loop
      |
      +----> calendar read
      |
      +----> validate change ----> Google Calendar write ----> friendly confirmation
```

Writes use deterministic request IDs and duplicate checks so retrying the same
Telegram message does not create another copy.

## Run locally

Requirements:

- Python 3.12+
- A Telegram bot token
- An Anthropic API key
- A Google Cloud service account with access to the shared calendar

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
python -m calbot
```

The application reads configuration from environment variables; `.env` is only
a convenient local reference and is not loaded automatically.

## Configuration

| Variable | Required | Purpose |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from BotFather |
| `ALLOWED_CHAT_ID` | Yes | The only Telegram chat Calbot accepts |
| `ALLOWED_USER_IDS` | No | Comma-separated user IDs allowed in that chat |
| `ANTHROPIC_API_KEY` | Yes | Claude API authentication |
| `ANTHROPIC_MODEL` | No | Defaults to `claude-sonnet-4-6` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Complete service-account JSON on one line |
| `CALENDAR_ID` | Yes | Shared calendar ID |
| `TIMEZONE` | No | Defaults to `America/New_York` |
| `BOT_OWNER` | No | Greeting/name used in the prompt |
| `RESPOND_TO_ALL` | No | Set `false` to require a mention or reply |

See [SETUP.md](SETUP.md) for the complete Telegram, Google Calendar, and Railway
setup.

## Commands

| Command | Description |
|---|---|
| `/start` | Show example requests |
| `/id` | Show the current Telegram chat ID |
| `/today` | Summarize today |
| `/week` | Summarize the next seven days |
| `/weekend` | Summarize Friday through Sunday |

## Deploy

The Docker image runs as an unprivileged user and installs dependencies from the
hash-locked `requirements.lock`.

When Railway is connected to this repository, pushes to the configured branch
deploy automatically. Configure the variables above in the Railway service and
use `bash start.sh` as the start command if Railway does not use the Docker
`CMD`.
