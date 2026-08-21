# Calbot

Calbot is a private Telegram companion for a couple's shared Google Calendar.
It can chat naturally, but its only external capability is calendar management.

Examples:

- `Dinner at Lilia Saturday at 8`
- `What do we have this weekend?`
- `Move Friday's dinner to 7:30`
- `Delete the dentist appointment`

## What it does

- Creates, lists, updates, and deletes Google Calendar events.
- Semantically distinguishes conversation, calendar reads, calendar writes,
  write-status questions, and unsupported research requests through OpenAI.
- Understands conversational dates and follow-up edits such as `actually 8`,
  `where is dinner?`, and `did you add them?`.
- Supports native event location, description, ownership, timezone, recurrence,
  attendees, reminders, Google Meet, busy/free state, status, source, visibility,
  and color fields.
- Posts a Friday weekend preview and a Sunday week-ahead summary.
- Provides `/today`, `/week`, and `/weekend` calendar summaries.
- Restricts access to one Telegram chat and, optionally, specific users.
- Executes clear calendar requests immediately and asks a short follow-up only
  when an important date, time, or event is ambiguous.
- Keeps Telegram replies conversational and suppresses internal tool data.
- Stores conversation context, action receipts, and processed Telegram request
  IDs in Postgres so deploys and retries do not erase Calbot's memory.

Calbot can participate in normal social conversation. It does not answer general
knowledge or research questions and has no web search, payments, ordering, wallet,
or non-calendar integrations.

## Personality

Edit [`PERSONALITY.md`](PERSONALITY.md) to define Calbot's voice. Its contents
are loaded when the bot starts, so restart or redeploy Calbot after changing it.
Personality guidance controls model-generated tone only and cannot override
calendar scope, access controls, immediate writes, or conversational-output
safeguards. Verified write confirmations remain deterministic so the bot cannot
invent a successful calendar change.

## How it works

```text
Telegram adapter
      |
      v
Semantic planner (strict structured output)
      |
      +--> conversation / unsupported --> natural reply, no tools
      |
      +--> calendar read/status --------> required Google Calendar read
      |
      +--> calendar write --------------> required mutation executor
                                                |
                                  validation + version check
                                                |
                                       Google Calendar write
                                                |
                                   verified receipt + confirmation
                                                |
                                      Postgres state ledger
```

Terra first creates a strict semantic plan; regular expressions do not decide
calendar access. Calendar lanes then have enforced postconditions: a read/status
answer requires a real calendar read, and a clear write cannot end in an
unverified refusal or invented success. Writes also use deterministic event IDs,
processed-request records, and duplicate checks so retrying the same Telegram
message does not create another copy.

The package is organized by responsibility:

| Module | Responsibility |
|---|---|
| `calbot/telegram_app.py` | Telegram handlers, authorization, and scheduled jobs |
| `calbot/runtime.py` | Conversation history and bounded assistant orchestration |
| `calbot/assistant/planner.py` | Strict semantic routing and conversational boundary |
| `calbot/mutations.py` | Immediate validation and verified mutation execution |
| `calbot/calendar/contracts.py` | Canonical tool schemas and field limits |
| `calbot/calendar/client.py` | Google Calendar API reads and writes |
| `calbot/assistant/` | Tool loop, policy, execution results, and reply safeguards |
| `calbot/config.py` | Environment parsing and validation |
| `calbot/personality.py` | Bounded loading of `PERSONALITY.md` |
| `calbot/state.py` | Postgres conversation, action-receipt, and idempotency ledger |

## Run locally

Requirements:

- Python 3.12+
- A Telegram bot token
- An OpenAI API key
- A Google service account or OAuth grant with access to the shared calendar
- Postgres for durable production state (in-memory state is used when omitted)

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
| `ACTOR_NAMES` | No | Trusted `user_id:name` pairs, e.g. `101:Ezra,202:Sarah` |
| `OPENAI_API_KEY` | Yes | OpenAI API authentication |
| `OPENAI_MODEL` | No | Defaults to `gpt-5.6-terra` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | One auth method | Complete service-account JSON on one line |
| `GOOGLE_OAUTH_CLIENT_ID` | One auth method | OAuth client ID; preferred for invitations/Meet |
| `GOOGLE_OAUTH_CLIENT_SECRET` | With OAuth | OAuth client secret |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | With OAuth | Long-lived user authorization refresh token |
| `CALENDAR_ID` | Yes | Shared calendar ID |
| `TIMEZONE` | No | Defaults to `America/New_York` |
| `BOT_OWNER` | No | Greeting/name used in the prompt |
| `RESPOND_TO_ALL` | No | Set `false` to require a mention or reply |
| `DATABASE_URL` | Production | Postgres connection used for durable context and receipts |

See [SETUP.md](SETUP.md) for the complete Telegram, Google Calendar, and Railway
setup.

Run the live semantic routing suite with configured credentials using
`python -m scripts.evaluate_planner`.

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
