# Security Policy

## Supported versions

Security fixes are applied to the latest version on `main`. Older commits and
forks are not supported.

## Reporting a vulnerability

Do not open a public GitHub issue for a suspected vulnerability. Use the
repository's **Security → Report a vulnerability** flow to submit a private
report. If private vulnerability reporting is unavailable, contact the
maintainer privately through their GitHub profile.

Include the affected version, impact, reproduction steps, and any suggested
mitigation. Please avoid accessing other users' data or spending wallet funds
while validating a report.

## Credential exposure

Never include real Telegram tokens, Anthropic keys, Google service-account
credentials, or Tempo wallet stores in an issue, pull request, log, or
screenshot. If a credential may have been exposed, revoke or rotate it before
reporting the incident; deleting it from the latest commit is not sufficient.

## Deployment responsibility

Each operator is responsible for restricting `ALLOWED_CHAT_ID`, protecting
deployment secrets, using a limited Tempo access key, and setting conservative
`TEMPO_AUTO_SPEND` and `TEMPO_MAX_SPEND` values. The repository contains safety
checks, but they are not a substitute for wallet-level limits and secret
management.
