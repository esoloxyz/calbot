# Security Policy

## Supported versions

Security fixes are applied to the latest version on `main`.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's
**Security → Report a vulnerability** flow or contact the maintainer privately.

Include the affected version, impact, reproduction steps, and any suggested
mitigation. Do not access calendar data that is not yours while validating a
report.

## Credentials

Never include real Telegram tokens, Anthropic keys, or Google service-account
credentials in an issue, pull request, log, or screenshot. If a credential may
have been exposed, revoke or rotate it; deleting it from the latest commit is
not sufficient.

Operators should restrict `ALLOWED_CHAT_ID`, set `ALLOWED_USER_IDS`, protect
deployment secrets, and give the Google service account access only to the
calendar Calbot manages.
