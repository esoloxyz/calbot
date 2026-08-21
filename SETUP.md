# Calbot Setup

Calbot connects one private Telegram chat to one shared Google Calendar.

## 1. Create the shared calendar

1. Create or choose a Google Calendar.
2. Share it with both intended people using **Make changes to events**
   permission.
3. In **Settings → Integrate calendar**, copy the calendar ID.

## 2. Create the Telegram bot

1. Message **@BotFather** and run `/newbot`.
2. Copy the token into `TELEGRAM_BOT_TOKEN`.
3. Add the bot to the private chat or group.
4. Turn off BotFather's group privacy mode if Calbot should read messages that
   do not mention it.
5. Start the bot once, run `/id` in the group, and set the returned value as
   `ALLOWED_CHAT_ID`.
6. Optionally set `ALLOWED_USER_IDS` to the two comma-separated Telegram user
   IDs for an additional restriction.
7. Set `ACTOR_NAMES` to trusted ID/name pairs such as `101:Ezra,202:Sarah` so
   Calbot can correctly interpret whose appointment a message refers to.

## 3. Configure Google Calendar access

1. Create a Google Cloud project.
2. Enable the **Google Calendar API**.
3. Create a service account under **IAM & Admin → Service Accounts**.
4. Create a JSON key for that service account.
5. Share the calendar with the service account's email and grant **Make changes
   to events**.
6. Store the complete JSON key as `GOOGLE_SERVICE_ACCOUNT_JSON`.

The service account does not need a project-level IAM role. Calendar sharing is
what grants access.

For native attendee invitations and Google Meet creation on a user-owned
calendar, use Google OAuth instead. Configure `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN`; when all three
are present, Calbot prefers OAuth over the service account. The OAuth grant must
include the Google Calendar events scope. Basic event reads and writes can keep
using the simpler service-account setup.

## 4. Configure OpenAI

Create an OpenAI API key and store it as `OPENAI_API_KEY`. Calbot defaults to
`gpt-5.6-terra`; override `OPENAI_MODEL` only if needed.

## 5. Add durable state

Add a Railway Postgres service to the project. Expose its private connection
string to the Calbot worker as `DATABASE_URL`. Calbot creates three narrowly
scoped tables at startup for conversation turns, verified action receipts, and
processed Telegram requests. Google Calendar remains the source of truth for
events.

## 6. Configure Railway

Create or select a Railway service and add:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_ID`
- `ALLOWED_USER_IDS` (optional)
- `ACTOR_NAMES` (recommended)
- `OPENAI_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
  `GOOGLE_OAUTH_REFRESH_TOKEN` (optional OAuth replacement)
- `CALENDAR_ID`
- `TIMEZONE`
- `BOT_OWNER`
- `RESPOND_TO_ALL`
- `DATABASE_URL`

Deploy the repository and confirm the logs contain `Bot starting (polling)…`.
Then test `/start`, `/today`, and a calendar change.

## Calendar change example

```text
user: dinner at lilia saturday, august 1 at 8
bot:  done. dinner at lilia is on the calendar for saturday, august 1 from 8pm to 10pm.
```

Clear add, update, and delete requests run immediately. When an important detail
is genuinely ambiguous, Calbot asks one concise follow-up question before
changing the calendar.

## Scheduled summaries

- Friday at 9:00 AM: weekend preview
- Sunday at 6:00 PM: the following week

Times use the configured `TIMEZONE`. The schedules are defined in
`calbot/telegram_app.py`.

## Verify semantic routing

Run `python -m scripts.evaluate_planner` with `OPENAI_API_KEY` configured. The
checked-in suite covers social conversation, research boundaries, terse event
creation, reads, status questions, and contextual follow-up edits.
