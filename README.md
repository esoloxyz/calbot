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
- Fetches scheduled digests directly from Google Calendar, independent of chat history.
- Discovers MPP services at runtime instead of hard-coding providers.
- Pays for approved service calls from a Tempo wallet.
- Turns Tempo and web-search results into plain-English Telegram replies instead
  of exposing provider JSON or HTTP call data.
- Lists every registry-known Tempo stablecoin balance above `$0.50`.
- Restricts access to one configured Telegram chat.
- Optionally restricts access to specific Telegram user IDs within that chat.
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

For paid calls, Claude first searches the Tempo service directory and loads the
selected service's exact endpoint metadata. Calbot validates and prices the call
without submitting it, then asks the initiating Telegram user to approve an
exact one-shot action by replying `approve`. Only that actor can approve, and
only the internally stored call can reach `tempo request`. The Tempo CLI handles
the MPP challenge and payment signature. Calbot rejects guessed endpoints and
HTTP methods.

Approved provider output is never inserted into conversational history. Search
results are reduced to useful titles, excerpts, and source links, then answered
in a separate tool-free model turn with a deterministic plain-text fallback.
This keeps web content from authorizing tools while avoiding raw provider data
in Telegram.

For `/balance`, Calbot reads the wallet identity through the Tempo CLI, loads
Tempo's official token list for that chain, and makes read-only `balanceOf` RPC
calls. It displays stablecoin balances strictly above `$0.50` and falls back to
the CLI's active balance if the registry or RPC is unavailable.

### Code layout

- `calbot/telegram_app.py` is the Telegram adapter; `calbot/runtime.py` owns
  application state and approvals.
- `calbot/assistant/` contains model policy, tool-round orchestration, typed tool
  execution, and response postconditions.
- `calbot/tempo/` contains the Tempo facade, catalog validation, payment policy,
  read-only balance discovery, plain-text rendering, subprocess isolation, tool
  schemas, and wallet validation.
- `calbot/calendar/` contains Google Calendar validation, mutations, and
  deterministic digest rendering.
- The root `bot.py` is only a compatibility launcher for existing host overrides.

## Setup

The full Telegram, Google Cloud, Calendar, and Railway walkthrough is in
[SETUP.md](SETUP.md).

### Requirements

- Python 3.12+
- A Telegram bot token
- An Anthropic API key
- A Google Cloud service account with Calendar access
- A Tempo wallet and access key for MPP calls
- Railway or another always-on container host

### Install locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
```

Install the Tempo CLI with the pinned, checksum-verified procedure in
[SETUP.md](SETUP.md#4-prepare-a-tempo-wallet-for-mpp), then authenticate the
dedicated Calbot wallet.

Set the required environment variables, then start Calbot:

```bash
python -m calbot
```

## Configuration

See [.env.example](.env.example) for sample values.

| Variable | Required | Purpose |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token issued by BotFather |
| `ALLOWED_CHAT_ID` | Yes | Only this Telegram chat can use the bot |
| `ALLOWED_USER_IDS` | No | Comma-separated Telegram user IDs allowed within the configured chat |
| `ANTHROPIC_API_KEY` | Yes | Claude API authentication |
| `ANTHROPIC_MODEL` | No | Claude model; defaults to `claude-sonnet-4-6` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Google service-account JSON on one line |
| `CALENDAR_ID` | Yes | Calendar the bot can manage |
| `TEMPO_WALLET_STORE_B64` | Yes | Base64-encoded current Tempo wallet store |
| `TEMPO_AUTO_SPEND` | No | Default spend cap when a tool omits one; use at most 6 decimal places; approval is still required |
| `TEMPO_MAX_SPEND` | No | Absolute ceiling for an explicitly approved call; at most 6 decimal places; defaults to `0.50` |
| `TEMPO_BIN` | No | Tempo binary path; defaults to `~/.tempo/bin/tempo` |
| `TEMPO_HOME` | No | Tempo launcher/extension home; does not relocate wallet-cli's `~/.tempo/wallet` store |
| `TEMPO_RPC_URL` | No | Optional HTTPS RPC override for all-token balance reads; known Tempo networks use their official public RPC by default |
| `TIMEZONE` | No | IANA timezone; defaults to `America/New_York` |
| `BOT_OWNER` | No | Name used in the assistant prompt |
| `RESPOND_TO_ALL` | No | Set `false` to require mentions or replies |

## Configure the Tempo wallet on Railway

`store.json` contains a signing key. Use a dedicated, low-balance wallet or a
limited access key, and treat the encoded value as a production secret.

From a Railway-linked checkout:

```bash
base64 < "$HOME/.tempo/wallet/store.json" |
  railway variable set TEMPO_WALLET_STORE_B64 --stdin \
    --service worker --environment production

printf '0.50' |
  railway variable set TEMPO_MAX_SPEND --stdin \
    --service worker --environment production

printf '0.01' |
  railway variable set TEMPO_AUTO_SPEND --stdin \
    --service worker --environment production
```

Confirm the deployed wallet from Telegram with `/balance`.

## MPP safety controls

Calbot applies several controls before a paid request:

1. The service must come from Tempo's live service directory.
2. Calbot must load the service details before calling it.
3. The URL and HTTP method must exactly match a discovered endpoint.
4. Calbot validates and prices a call without submitting payment.
5. Every new service call requires the initiating user to reply `approve` within
   ten minutes. The approval is actor-bound, one-shot, and consumed before the
   call runs. Replies such as `yes`, `approve please`, or `do it` are rejected.
   Zero-cost external reads require approval too. Only a status poll derived
   from a previously approved task can run without another prompt.
6. Approval is bound to the initiating `(chat ID, user ID)` and the exact URL,
   method, body, and spend cap. It is consumed before one direct submission; an
   unrelated message from that user cancels it. The prompt shows the provider,
   human-readable request, and exact price or maximum price without exposing
   raw URL, method, body, or JSON. Oversized requests are rejected.
7. A different paid call is blocked while confirmation is pending or during an
   approved turn, even when it is below `TEMPO_AUTO_SPEND`.
8. A paid submission is never retried automatically, even when its response is
   lost or reports an error.
9. A caller cannot raise the configured `TEMPO_MAX_SPEND` ceiling or silently
   raise a lower caller-provided cap to match a service price.
10. Service-directory searches accept only bounded capability keywords, service
    IDs must have appeared in a search result, and endpoint metadata is accepted
    only for public HTTPS URLs. Refreshes atomically evict stale endpoints.
11. Parallel task calls require a valid `input` and an explicit `pro` or `ultra`
    processor before payment; returned task IDs authorize only their exact,
    zero-spend status URL. A user can restore polling after a restart by sending
    the exact run ID back to the bot.
12. CLI failures are returned as structured errors instead of looking successful.

For ordinary web research, Calbot prefers Parallel's fixed-price `$0.01` search
endpoint. If deeper research needs a `$0.10` `pro` task or `$0.30` `ultra` task,
Calbot states the price and asks for approval, then stops. The same rule applies
to fixed-price calls, including ordinary search and image generation; the reply
is always simply `approve`.

For ordinary image generation, Calbot prefers fal.ai's FLUX Schnell endpoint,
which uses a fixed one-time `$0.003` MPP charge. The OpenAI DALL-E MPP endpoint
is blocked for now because its live server requests a session voucher even
though the service directory advertises a one-time charge; that session flow is
not compatible with Calbot's access-key wallet.

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
| `/balance` | List every Tempo stablecoin balance above `$0.50` |

Calbot can also perform these actions through normal conversation.

Every calendar write is proposed first and executes only after the initiating
user replies `approve`. The stored tool name and arguments execute directly
without asking the model to reconstruct them. Before creating
an event, Calbot performs a paginated calendar lookup and treats an overlapping
event with the same normalized title as already existing. Creates also use a
deterministic Google Calendar event ID scoped by both Telegram message and event
identity, preventing retries and multi-event messages from colliding.

## Deploying

The multi-stage Docker image installs hash-locked, wheel-only Python
dependencies, the SQLite runtime required by Tempo, and a version-pinned,
GPG-verified Tempo core. Its wallet and request extensions are separately
version-pinned and verified by Tempo's signed-manifest installer during the
build. All three executables are root-owned and read-only; CI exercises the two
extensions with networking disabled so first-use downloads cannot hide a
missing build artifact. The Ubuntu base image is pinned to an immutable digest;
build/download tooling is excluded from the runtime, which runs as the
unprivileged `calbot` user. The wallet key is restored once from
`TEMPO_WALLET_STORE_B64` during Python application startup, so the app works
whether Railway uses the Docker `CMD` or an explicit `python bot.py`
start-command override.

Some hosted builders inject `OTEL_EXPORTER_*` variables that point to their own
telemetry sockets. The Dockerfile clears those values around Tempo installation
and its build-time smoke tests so they cannot be mistaken for Tempo endpoints.
This safeguard is automatic; no Railway variable change is required.

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
python3 -m compileall -q -f .
bash -n start.sh
docker build --platform linux/amd64 --tag calbot:local .
```

The explicit Docker platform matches Railway and GitHub Actions, including when
the local machine is an Apple Silicon Mac.

`requirements.lock` is compiled with uv 0.11.28 for Python 3.12. CI resolves the
manifest under the checked-in lock constraints, then compares every pinned
version and artifact hash. This rejects manifest-only dependency updates,
including incomplete Dependabot pull requests, without failing merely because a
new transitive release appeared after the lock was generated. CI also runs
pinned Ruff checks, scans the exact lock with a checksum-verified OSV-Scanner,
compiles the sources, and smoke-tests the unprivileged container offline.

The acceptance tests cover current Tempo CLI argument order, service discovery,
endpoint authorization, structured failures, fixed and dynamic pricing,
cumulative budgets, confirmation matching, container payment dependencies,
retry prevention, and free task polling.

## Repository layout

| File | Purpose |
|---|---|
| `calbot/telegram_app.py` | Telegram handlers, application factory, and scheduled summaries |
| `calbot/runtime.py` | Dependency-injected assistant runtime and side-effect executor |
| `calbot/authorization.py` | Actor-bound, expiring, one-shot action approvals |
| `calbot/messages.py` | Telegram-to-model message boundaries and reply extraction |
| `calbot/assistant/` | Model policy, tool execution, and response verification |
| `calbot/calendar/` | Google Calendar operations and deterministic digests |
| `calbot/tempo/` | Tempo discovery, payments, wallet, tools, and process isolation |
| `bot.py` | Compatibility launcher for `python bot.py` host overrides |
| `Dockerfile` | Railway/container image with Tempo installed |
| `start.sh` | Startup validation and process launch |
| `requirements.lock` | Fully resolved, hash-locked production dependencies |
| `tests/` | Unit, acceptance, policy, and container regression tests |
| `SETUP.md` | Detailed first-time deployment guide |
| `SECURITY.md` | Vulnerability-reporting and secret-handling policy |

## Security

- Never commit `.env`, Google credentials, Telegram tokens, or Tempo keys.
- Keep the bot in a private chat and configure `ALLOWED_CHAT_ID`.
- Set `ALLOWED_USER_IDS` when only particular members of the chat should be able
  to initiate calendar writes or payments.
- Rotate the Telegram token if it appears in logs or screenshots.
- Keep HTTP client logging at warning level; Telegram API URLs contain the token.
- Keep mutable Telegram display names out of model-visible message content.
- Use the smallest practical Tempo wallet balance and access-key allowance.
- Keep `TEMPO_AUTO_SPEND` and `TEMPO_MAX_SPEND` conservative; they are additional
  caps and do not replace one-shot confirmation.
- Review dynamic-price endpoints before raising `TEMPO_MAX_SPEND`.

Please report vulnerabilities privately rather than opening a public issue. See
[SECURITY.md](SECURITY.md) for the reporting process.

## License

Calbot is available under the [MIT License](LICENSE).
