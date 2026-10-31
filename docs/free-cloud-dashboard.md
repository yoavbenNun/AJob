# Personal cloud dashboard — implementation, not a live deployment

Opt-in, single-owner web service. Nothing here provisions a service, changes the
scanner, migrates production or transfers private data. Desktop remains the
default. Do not expose the desktop `src.web.app:app` entry point publicly.

## Free-tier decision (official documentation checked 31 October 2026)

Use **Render Free web + the existing Supabase PostgreSQL**, with application-owned
authentication. A free `onrender.com` HTTPS address avoids buying a domain.
Cloudflare Access self-hosted public applications require a domain in an active
Cloudflare zone; we cannot assume ownership of Render's domain. Access is therefore
not a prerequisite or an unverified protection layer in this implementation.

Render Free provides 0.1 CPU/512 MB RAM, a single instance, 750 workspace instance
hours/month, and no persistent disk. It sleeps after 15 idle minutes; wake-up can
take around a minute. Instances can restart at any time. Use one web worker and
PostgreSQL for all durable state, never runner-local SQLite. This is a personal
hobby deployment, not an uptime guarantee; Render cautions against production use
of Free instances. Resource adequacy still needs a real staging measurement.

The Hobby allowance currently includes 5 GB outbound bandwidth and 500 build
minutes; account allowances must be checked before creating anything. Without a
payment method, exceeding free allowances can suspend service/builds rather than
provide unlimited capacity. Render may also suspend unusually high outbound use,
including database traffic. No keep-alive pings are proposed.

Supabase Free currently includes 500 MB database size and 5 GB egress; inactive
projects may pause after a week. Use the existing project's *actual* plan/quota,
not an assumption that it is Free. Its shared Session Pooler supports IPv4; the
dashboard opens short-lived connections with connection/statement timeouts. No
new Render Postgres: its free databases expire after 30 days. A $0 incremental
deployment is plausible within quotas without a card, not guaranteed availability
or proof that the existing database account costs $0.

Sources: [Render Free](https://render.com/docs/free),
[compute](https://render.com/docs/compute-plans), [pricing](https://render.com/pricing),
[Cloudflare prerequisites](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/),
[Supabase pricing](https://supabase.com/pricing),
[connections](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Authentication and browser boundary

There is no registration, account enumeration, reset-email flow, external identity
header trust or public API token. The owner uses a unique strong password, with
only its salted scrypt hash stored in the provider's secret settings. Generate the
hash locally via `hash_password(getpass.getpass())`; never put the password in
command arguments, chat, source or logs. The supported scrypt profile uses 32 MiB
and parallelization 3 (OWASP's memory/CPU tradeoff). No new auth protocol or custom
encryption is introduced: standard random bearer sessions, scrypt and HMAC are
used. This password-only version has **no MFA**; do not claim otherwise.

The login form has a short-lived signed anti-CSRF token and a Secure HttpOnly
`__Host-` prelogin cookie. Login attempts share a PostgreSQL atomic global limit
of five attempts per 15 minutes, including successful attempts. It survives cold
starts and cannot be bypassed with spoofed IP headers. An attacker can consume
this budget and temporarily block login; existing sessions remain usable. There
is no public reset endpoint; credential rotation and session revocation are
operator actions. A maximum of five sessions is retained.

Successful login rotates the opaque token; only its SHA-256 digest is stored in
PostgreSQL. Sessions have 30-minute idle and 12-hour absolute expiry and a
per-session CSRF token. Logout revokes the server-side record. Cookies are Secure,
HttpOnly, SameSite=Strict, Path=/ and have no Domain. Every mutation requires the
exact configured HTTPS Origin and session CSRF; null/missing/foreign origins are
rejected. Host is exact, duplicate security headers are rejected, and forwarded
identity/proxy headers establish no authorization. Render is the approved HTTPS
termination boundary; Uvicorn proxy header trust and access logging are disabled.

All private pages/APIs require authentication. A positive cloud route allowlist
permits only browsing/detail/status/notes/dismissal; desktop resume/email/outreach
and document-generation endpoints remain unreachable. Health reveals only
`{"status":"ok"}` and is liveness, not proof of DB health. Public manifest/icons
contain no private data. Responses use no-store, CSP, anti-framing and HSTS.
No service worker caches personal pages. Errors omit connection strings and
credentials; platform operational logs still require restricted access/retention.

## Data, least privilege and migration

`src.web.cloud:create_cloud_app` is the dedicated opt-in factory. It requires
verified database TLS, schema preflight and a restricted role; it performs no DDL.
`Dockerfile.dashboard` has no browser download or scanner command, and
`src.web.cloud_server` starts only Uvicorn. There is no SMTP, scheduler or worker.

Jobs and lifecycle/application statuses remain in the existing PostgreSQL rows.
The dashboard role receives jobs SELECT and UPDATE(status) only. New private
tables hold notes, dismissal restoration history, hashed sessions and the login
throttle, with exact-role RLS and no public/anonymous API access. Keep the private
schema out of exposed Supabase API schemas. A non-owner LOGIN role inherits
`ajob_dashboard`; it must not inherit scanner/admin/DDL privileges. Runtime startup
rejects obvious overprivileged credentials. A security review of actual grants,
RLS, TLS and Session Pooler is still required before live activation.

Manual migration order, **only after separate production approval**:

1. Back up the database, verify restoration in an isolated database and drain any
   conflicting web writers. Scanner timing is unaffected by this preparation.
2. Review/apply `scripts/manual_cloud_private.sql`, then
   `deployment/cloud-auth.sql` as the approved schema owner.
3. Provision the restricted LOGIN credential securely; set no password in SQL
   committed to Git. Verify privileges/RLS with that actual role.
4. Start with empty cloud notes, or separately approve an explicit transfer.

Local SQLite is never deleted or silently switched. The offline
`scripts/migrate_private_data.py` supports consistent backup, restoration to a
new file and dry-run mapping by canonical job URL. It rejects duplicate identities,
orphans and invalid notes/statuses. Plans contain private text: keep them in a
protected directory outside Git. It does not connect to a cloud DB or apply a
transfer. Actual import/reconciliation remains an approved operator step; do not
guess that desktop IDs match production IDs. Do not replay a jobs snapshot over
current statuses. Select one notes writer at cutover; no automatic two-way sync.

Dismissal and its undo record share one PostgreSQL transaction and row lock.
Restore preserves the recorded lifecycle status and clears the undo record.
Concurrent explicit status edits serialize in PostgreSQL; later explicit status
writes win. Notes are independent of ranking, scanning and mail. Raw resumes,
outreach contacts and document packages remain on the PC. Cloud browsing does not
pretend to calculate a personalized match without an approved profile; private
resume-matching functions are deliberately unavailable in this first cloud scope.

## Settings (names only)

| Variable | Purpose |
|---|---|
| `AJOB_CLOUD_ENABLED=1` | Explicit opt-in; otherwise factory refuses startup |
| `AJOB_PUBLIC_ORIGIN` | Exact HTTPS onrender origin, no path/wildcards |
| `AJOB_DASHBOARD_DATABASE_URL` | Dedicated restricted credential, `sslmode=verify-full` and approved CA |
| `AJOB_OWNER_PASSWORD_HASH` | Supported salted scrypt hash, secret setting |
| `PORT` | Provider-selected listening port, default 10000 |

No scanner `DATABASE_URL`, SMTP credentials, GitHub token or resume directory is
required by this entry point. Session settings are fixed conservative defaults;
changing them currently requires a reviewed code change.

## Backup, rollback and activation gates

Test local synthetic login/security/mobile/backup flows before any account setup.
CI runs the new PostgreSQL tests in its disposable cluster. Verify private table
RLS, concurrency, rollback and session persistence there; a skip is not success.
No real Render cold-start, provider TLS, physical Safari or resource measurement
has been established by local tests.

Before activation, approve the free account/provider region, no-card policy, exact
hostname, password setup, dedicated role/migrations, and whether any private data
may be transferred. Deploy synthetic staging first; verify real HTTPS Host, login,
Safari, cold start/restart, resource use and DB connectivity. Keep automatic deploys
off. Only then approve real credentials, data cutover and a status/notes smoke test.

Back up private PostgreSQL tables to protected encrypted storage with an approved
destination/retention; free hosting is not a backup. The repository tool verifies
synthetic SQLite backup/restore only, not a production PostgreSQL restore. Prove
the latter before any production migration. Revoke sessions after a restore.
Pin the last good image/SHA for rollback, leave additive private tables in place,
and preserve notes/status writes made after cutover. Do not restore an old full
jobs/outbox snapshot or replay notifications. During outage, desktop remains
usable when the PC is on, but offline web mutations are not queued or silently
retried; note drafts remain visible for manual retry.
