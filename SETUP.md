# Calbot Setup Guide

Calbot is a Telegram assistant for a shared Google Calendar, powered by Claude.
Allow about 30 minutes for the initial setup. Running costs depend on your host,
Claude usage, and any paid MPP services you call.

It understands messages such as:

- `Dinner at Lilia Saturday at 8`
- `Flight to Miami October 12 at 9 AM`
- `What do we have this weekend?`
- `Move Friday's dinner to 7:30`
- `Use Parallel to research today's Tempo news`

It also posts a weekend preview every Friday at 9 AM and a week-ahead digest
every Sunday at 6 PM. The `/weekend`, `/week`, and `/today` commands provide
on-demand summaries.

## 1. Create and share a Google Calendar

1. Create a Google account for the shared calendar, or choose an existing
   calendar that Calbot can manage.
2. In [Google Calendar](https://calendar.google.com), open the calendar's
   settings and share it with each intended user using **Make changes to
   events** permission.
3. Copy the **Calendar ID** from **Settings → Integrate calendar**. For a default
   calendar, this is usually an email address such as
   `sharedcalendar@example.com`.

## 2. Create the Telegram bot

1. Message **@BotFather** in Telegram, run `/newbot`, and copy the token.
2. In BotFather, open **Bot Settings → Group Privacy** and turn privacy mode off
   if Calbot should read unmentioned messages in a group.
3. Add the bot to a private chat or group containing only the intended users.
4. After deployment, send `/id` to Calbot and copy the returned chat ID. Use it
   as `ALLOWED_CHAT_ID`; Calbot ignores every other chat.

## 3. Create a Google Cloud service account

1. Open [Google Cloud Console](https://console.cloud.google.com) and create a
   project, such as `calbot`.
2. Enable the **Google Calendar API**.
3. Open **IAM & Admin → Service Accounts** and create a service account. It does
   not need a project-level role.
4. Open the service account, choose **Keys → Add key → Create new key → JSON**,
   and store the downloaded file securely.
5. In Google Calendar, share the managed calendar with the service account's
   email using **Make changes to events** permission.

## 4. Prepare a Tempo wallet for MPP

Use a dedicated, low-balance wallet or a limited access key. The wallet store is
a signing credential and must be handled like a password.

```bash
curl -fsSL https://tempo.xyz/install | bash
"$HOME/.tempo/bin/tempo" wallet login
"$HOME/.tempo/bin/tempo" wallet whoami --format json
```

Encode the current wallet store for your deployment platform:

```bash
base64 < "$HOME/.tempo/wallet/store.json"
```

Save the output as the secret `TEMPO_WALLET_STORE_B64`. Start with conservative
limits such as `TEMPO_AUTO_SPEND=0.01` and `TEMPO_MAX_SPEND=0.50`. The access
key's wallet-level spending limit is an additional safeguard.

## 5. Deploy

1. Push the repository to GitHub, or deploy the checkout directly with your
   hosting provider's CLI.
2. Create a Railway project and connect the repository.
3. Add the variables from [.env.example](.env.example):
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_CHAT_ID` (use `0` until you can run `/id`)
   - `ANTHROPIC_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (the complete downloaded JSON document)
   - `CALENDAR_ID`
   - `TEMPO_WALLET_STORE_B64`
   - `TEMPO_AUTO_SPEND` and `TEMPO_MAX_SPEND`
   - `TIMEZONE` and `BOT_OWNER`
4. Confirm the logs contain `Bot starting (polling)…`.
5. Send `/id`, replace the temporary `ALLOWED_CHAT_ID`, and let Railway
   redeploy.
6. Send `/start`, `/today`, and `/balance` to verify Telegram, Calendar, and
   Tempo connectivity.

## Example

```text
User: Dinner at Lilia Saturday at 8
Bot:  Added ✓ Dinner at Lilia — Sat, Jul 11, 8:00 PM

Another user: I have an appointment Tuesday at 4
Bot:          Added ✓ Appointment — Tue, Jul 14, 4:00 PM

User: /weekend
Bot:  Your weekend: Saturday — Dinner at Lilia at 8 PM. Sunday is open.
```

Calendar events appear in every account with which the calendar is shared.

## Operational notes

- Set `RESPOND_TO_ALL=false` if Calbot should respond only to mentions and
  replies.
- The bot keeps a short in-memory conversation history for follow-up requests;
  it resets on redeploy.
- Scheduled digest times are configured near the bottom of `bot.py`.
- Keep secrets in the hosting platform's secret store, never in Git or `.env`
  files committed to the repository.
- See [SECURITY.md](SECURITY.md) for vulnerability reporting and incident
  guidance.
