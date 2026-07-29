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

## 4. Configure Claude

Create an Anthropic API key and store it as `ANTHROPIC_API_KEY`. Calbot defaults
to `claude-sonnet-4-6`; override `ANTHROPIC_MODEL` only if needed.

## 5. Configure Railway

Create or select a Railway service and add:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_ID`
- `ALLOWED_USER_IDS` (optional)
- `ANTHROPIC_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `CALENDAR_ID`
- `TIMEZONE`
- `BOT_OWNER`
- `RESPOND_TO_ALL`

Deploy the repository and confirm the logs contain `Bot starting (polling)…`.
Then test `/start`, `/today`, and a calendar change.

## Approval example

```text
User: Dinner at Lilia Saturday at 8
Bot:  Calendar change awaiting approval:
      • Add “Dinner at Lilia” (...)

      Reply approve to continue.
User: approve
Bot:  Done — Dinner at Lilia is on the calendar.
```

Only the person who requested the change can approve it. The approval expires
after ten minutes, is consumed once, and is cancelled by that person's next
unrelated message.

## Scheduled summaries

- Friday at 9:00 AM: weekend preview
- Sunday at 6:00 PM: the following week

Times use the configured `TIMEZONE`. The schedules are defined in
`calbot/telegram_app.py`.
