# Couple Calendar Bot — Setup Guide

A Telegram bot for you two + one shared Google Calendar, powered by Claude.
Total setup time: ~30 minutes. Running cost: ~$5/mo (Railway) + pennies of Claude API usage.

Say things like:

- `dinner at Lilia saturday 8pm`
- `flight to Miami oct 12, 9am`
- `what do we have this weekend?`
- `move friday dinner to 7:30`
- `cancel the dentist thing`

It also posts a **weekend preview every Friday 9 AM** and a **week-ahead digest every Sunday 6 PM**, and supports `/weekend`, `/week`, `/today` on demand.

---

## Step 1 — The shared Gmail + calendar (5 min)

1. Create the new Gmail account (e.g. `ezraandher@gmail.com`).
2. Go to [calendar.google.com](https://calendar.google.com) signed into that account. The default calendar is fine, or create one named "Us".
3. **Share it with both your personal accounts**: calendar Settings → *Share with specific people* → add both personal emails with **"Make changes to events"**. Now it shows up in both your normal Google Calendars too.
4. Note the **Calendar ID**: Settings → *Integrate calendar* → Calendar ID. For the default calendar it's just the Gmail address itself.

## Step 2 — The Telegram bot (5 min)

1. In Telegram, message **@BotFather** → `/newbot` → pick a name and username. Copy the **token**.
2. Still in BotFather: `/mybots` → your bot → *Bot Settings* → *Group Privacy* → **Turn OFF**. (This lets it read all group messages, not just @mentions.)
3. Create a **new private group** with just you and your girlfriend, then add the bot to it.
4. You'll need the group's **chat ID**. Easiest way: deploy the bot first (Step 4), then type `/id` in the group — it replies with the ID (a negative number like `-1001234567890`). Alternatively, add @RawDataBot to the group temporarily and read the `chat.id` it prints.

## Step 3 — Google Cloud service account (10 min)

This gives the bot its own "robot identity" that can edit the calendar. One-time setup:

1. Go to [console.cloud.google.com](https://console.cloud.google.com) (any Google account works) → create a new project, e.g. `couple-bot`.
2. **Enable the API**: search "Google Calendar API" in the top bar → Enable.
3. **Create the service account**: IAM & Admin → Service Accounts → Create. Name it anything; no roles needed. 
4. Open the new service account → **Keys** tab → Add Key → JSON. A `.json` file downloads — keep it safe, treat it like a password.
5. Copy the service account's **email** (looks like `couple-bot@project.iam.gserviceaccount.com`).
6. Back in the shared Google Calendar's settings → *Share with specific people* → add that service account email with **"Make changes to events"**. ← This step is the one everyone forgets.

## Step 4 — Deploy on Railway (10 min)

1. Push this folder to a **private GitHub repo** (or use Railway's CLI to deploy the folder directly).
2. Sign up at [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. In the service → **Variables**, add everything from `.env.example`:
   - `TELEGRAM_BOT_TOKEN` — from BotFather
   - `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com) (add ~$5 of credit; this bot will take months to burn through it)
   - `ALLOWED_CHAT_ID` — set a placeholder like `0` for now
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — open the downloaded JSON key file and paste its **entire contents** as the value (Railway handles multi-line values fine)
   - `CALENDAR_ID` — from Step 1
   - `TIMEZONE` — e.g. `America/New_York`
   - `COUPLE_NAMES` — e.g. `Ezra and Maya` (used in the bot's personality)
4. Railway auto-detects the `Procfile` and runs `python bot.py`. Check the deploy logs for `Bot starting (polling)…`.
5. In your Telegram group, type `/id` → copy the chat ID → update `ALLOWED_CHAT_ID` in Railway → it redeploys automatically.
6. Type `/start` in the group. You're live. 🎉

## Try it

```
You:  dinner at Lilia saturday 8pm
Bot:  Added ✓ Dinner at Lilia — Sat, Jul 11, 8:00 PM

Her:  I have a hair appt tuesday at 4
Bot:  Added ✓ Hair appointment — Tue, Jul 14, 4:00 PM

You:  /weekend
Bot:  Your weekend: Saturday — Dinner at Lilia at 8 PM. Sunday's wide open 🙌
```

Events also appear instantly in both your normal Google Calendar apps, since the calendar is shared with your personal accounts.

## Tweaks

- **Bot replying to messages meant for each other?** It's prompted to stay quiet (`PASS`) on non-calendar chatter, but if it's still chatty, set `RESPOND_TO_ALL=false` — then it only responds to @mentions and replies.
- **Change digest times**: edit the two `run_daily` lines at the bottom of `bot.py`.
- **Add a daily morning digest**: copy the `scheduled_digest` pattern with a new `data` label.
- **Memory**: the bot remembers the last ~24 messages of context (enough for "actually make that 8:30"), resetting on redeploy.

## Cost summary

| Item | Cost |
|---|---|
| Railway Hobby | $5/mo (includes usage credit that this bot fits within) |
| Claude API (Sonnet) | ~$0.01–0.05/day at couple-usage volume |
| Telegram, Google Calendar | Free |
