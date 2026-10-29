# Email policy and translations

`domains.json` is the default signup allowlist and email routing configuration:

| Group | Email locale |
| --- | --- |
| DEFAULT | en (English, canonical fallback) |
| KR | ko (Korean) |
| JP | ja (Japanese) |

Edit this file to change bundled domains. An instance can replace each group with
`MIROVEN_EMAIL_DOMAINS_DEFAULT`, `MIROVEN_EMAIL_DOMAINS_KR`, or `MIROVEN_EMAIL_DOMAINS_JP`
(comma-separated exact ASCII DNS names). Empty groups are allowed, but the whole policy
cannot be empty. Duplicates, overlapping memberships, wildcards, and `@` prefixes are
rejected at startup. Match the whole normalized domain; subdomains are not implicit.
All API replicas must use the same configuration. Configuration changes require restart.

Domain restrictions apply only to new accounts. Existing accounts can still sign in,
synchronize and request account deletion. A removed domain uses English for future mail.
Requests and failed verifications retain generic responses to avoid account enumeration.
`GET /api/v1/auth/policy` exposes the public groups for advisory client UI only.

## Address restrictions

New signup addresses must not contain `+` or `.` in the local part (before `@`). Domain
dots remain valid. The server enforces this at code request and account creation; older
outstanding challenges cannot bypass the rule. The public policy includes
`blocked_local_part_characters: ["+", "."]` for advisory UI. Existing exact addresses retain
sign-in/deletion access. Never strip these characters or merge an alias into another user.

## Editing translations

1. Edit `locales/en.json` first. It defines the canonical message keys and placeholders.
2. Update the corresponding keys in `locales/ko.json` and `locales/ja.json`.
3. Preserve `{code}` and `{expires_at}` exactly. Do not translate placeholder names or add
   formatting/conversion expressions. Expiry is an absolute date/time explicitly in UTC.
4. Run `uv run pytest tests/test_email_policy.py -q`. It checks all shipped catalogs are
   complete, validates placeholders, and exercises unknown locales and partial fallbacks.

At startup the English catalog must be complete. Translated catalogs may omit keys and
fall back to the English value per key. Unknown keys, empty values, or wrong placeholders
fail validation rather than risking broken transactional mail. Shipped-catalog tests require
complete translations so omissions are visible in CI. To add a language, add its catalog,
register it in `SUPPORTED_LOCALES`, and update the domain-group mapping and policy contract
if adding a group. Do not branch on language in the SMTP worker or duplicate email layout.

New encrypted job payloads contain `locale` and `expires_at`. The queued locale survives
retries and domain policy changes. Missing/unknown locale uses English; legacy jobs without
an expiry keep the generic expiry sentence. No DB migration is needed. Deploy the worker
before the API to enable translated delivery during rolling upgrades. Old workers can read
new jobs, but will send their original English text until upgraded.

Development Compose explicitly uses `example.com` in DEFAULT and sends only to Mailpit.
Do not copy that override or run tests against external SMTP/real recipient accounts.
