# Free cloud activation checklist

Prepared against main `101ac9057fbc8e0acd6325b0fa2d0b1b1059cefb` on 31 October 2026.
This checklist does not create a service, authorize data transfer, establish a live
production login or prove resource capacity on Render. No Render account connector
is available in this session; account eligibility, region and remaining quotas
are unverified. Stop if verification requires a card or paid resource.

## Evidence and remaining gates

| Item | Preparation result | Real activation gate |
|---|---|---|
| Source | Separate worktree; no launcher/scanner changes | Merge approved preparation PR, pin tested SHA |
| Render Free | Official docs show free web, 0.1 CPU / 512 MB, HTTPS | Confirm Free selectable for this account, no card |
| Idle/restarts | State kept in PostgreSQL; synthetic fresh-app tests | Measure actual wake-up and memory on free instance |
| Authentication | One owner, no signup, scrypt, server-side sessions | Generate a new deployment credential privately |
| Browser boundary | Exact Host/HTTPS Origin, CSRF, secure cookies | Verify actual HTTPS edge and physical Safari |
| Database | Manual private-schema SQL, forced RLS, restricted-role gate | Approved role, grants, TLS and backups on actual project |
| Private data | Offline-only backup/restore and explicit source mapping | Separate approval of exact dataset, import and writer |
| Scanner isolation | Separate Dockerfile/entry point, no SMTP settings | Verify service command/image; no shared scanner env group |

Render sleeps after 15 idle minutes and wake-up takes about one minute. Files are
lost on restart/sleep/deploy; do not add SQLite, resume uploads or a persistent
disk to this free deployment. The 750 instance-hour monthly allowance is shared
across the workspace. Bandwidth/build limits and unusually high outbound traffic
can suspend service; with a payment method overages may be billed. Keep the
no-card policy and confirm the account's current allowance before activation.
Existing Supabase plan/quota/availability remain dependencies, not a promised
zero-cost unlimited database. Do not use the 30-day Render Free Postgres trial.

Official references checked 31 October 2026:
[Free](https://render.com/docs/free),
[compute](https://render.com/docs/compute-plans),
[HTTPS/hostname](https://render.com/docs/web-services),
[health Host](https://render.com/docs/health-checks),
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Exact service configuration, after approval only

- Service: Web Service, Docker, **Free**, single instance, no attached disk.
- Source: reviewed main SHA; use `Dockerfile.dashboard`, not `Dockerfile`.
- Command: image default `python -m src.web.cloud_server`; no scanner/predeploy
  command, scheduled job, worker, automatic migration or keep-alive monitor.
- Automatic deploy: **off**. Health check: `/healthz` (liveness only).
- Use provider-assigned `onrender.com` hostname and managed HTTPS; no paid domain.
- Choose a region supported by Free close to the existing database. Verify its
  availability in the account before selection; no account was created here.
- Do not attach scanner environment groups, SMTP values, GitHub tokens, raw
  resumes or development `.env` files. Restrict access to provider logs/settings.

| Setting | Required content, never paste the real value into Git/chat |
|---|---|
| `AJOB_CLOUD_ENABLED` | `1` only for the explicitly approved cloud service |
| `AJOB_PUBLIC_ORIGIN` | Exact `https://` provider hostname, no trailing slash/path |
| `AJOB_DASHBOARD_DATABASE_URL` | New restricted LOGIN, shared Session Pooler, `sslmode=verify-full`; approved CA |
| `AJOB_OWNER_PASSWORD_HASH` | Newly generated salted scrypt hash, unrelated to development fixtures/accounts |
| `PORT` | Provider-supplied; do not override unless required by provider |

If the database requires an explicit CA file, provision its official certificate
through an approved secret-file/config-file mechanism and set `sslrootcert` in the
connection settings. Do not disable certificate or hostname verification. Do not
put credentials in command arguments or reuse `postgres`/scanner credentials.
The app refuses missing settings, unverified TLS and privileged roles; it does
not fall back to desktop `DATABASE_URL` or SQLite.

Sessions use Secure/HttpOnly/SameSite=Strict host-only cookies, 30-minute idle and
12-hour absolute limits, login rotation and server-side logout. Login attempts
share five attempts per 15-minute window in PostgreSQL. It is password-only,
without MFA; an attacker can consume the budget and temporarily block new logins.
Provider TLS and host routing still require live validation; synthetic tests are
not that evidence. `/healthz` staying green during DB downtime is expected: verify
authenticated readiness separately, without logging credentials or job content.

## Data boundaries and rehearsal

Jobs and statuses already in PostgreSQL stay in place; do not reimport a jobs
snapshot. The proposed transfer is only local Notes and dismissal Undo. Keep raw
resumes, generated documents, outreach/application packages and desktop settings
on the PC. Personal resume matching is not enabled in this cloud scope.

Before any real transfer:

1. Identify the actual jobs backend used by the desktop process. For PostgreSQL,
   use a reviewed local ID/URL snapshot from that backend; **never infer identity
   from an old `data/jobs.db`**. For an actual SQLite backend, `--jobs` is valid.
2. Freeze Notes/Undo writers, make a consistent protected SQLite backup, restore
   to a new path and verify it. Never delete or overwrite the original database.
3. Produce a target ID/URL snapshot through an approved read-only export. Run the
   offline Dry Run using `--source-snapshot` or `--jobs`, not both. Resolve every
   duplicate, orphan and conflict before proceeding. Plans/backups contain private
   data and must stay outside Git/build contexts and in a protected directory.
4. A later approved importer must compare existing target note/undo values, recheck
   ID/URL identity immediately before the transaction and abort conflicts. There
   is intentionally no automatic cloud import command in this preparation.
5. Select one authoritative writer. Starting cloud Notes empty is also an option;
   do not promise automatic desktop/cloud two-way synchronization.

## Authorized activation sequence and stop points

These steps **have not been executed** and require the approval package below.

1. Approve Free service creation, region, hostname and external HTTPS exposure;
   confirm no-card policy and account quotas. Stop on any payment requirement.
2. Verify a protected production PostgreSQL backup by restoring it to an isolated
   database. Approve destination/encryption/retention. Do not use a jobs/outbox
   restore to roll back only a dashboard mistake.
3. Approve and apply `scripts/manual_cloud_private.sql`, then
   `deployment/cloud-auth.sql`; separately provision a restricted LOGIN. Verify
   actual grants/RLS and ensure `ajob_private` is not an API-exposed schema. Stop
   on broad privileges, missing schema or failed restore evidence.
4. Generate the new owner credential privately and set only the settings above.
   Deploy the reviewed pinned web image. Prefer synthetic staging first; creation
   and deployment of staging are also external actions requiring approval.
5. Verify HTTPS, exact Host/Origin, anonymous denial, failed login throttle,
   session rotation/expiry/logout, invalid-CSRF rejection and private-API denial.
   Test on physical Safari and from the Home Screen. A liveness check alone does
   not approve login or database readiness.
6. After any separately approved Notes/Undo import, verify status/notes identity,
   restart persistence and a controlled write/Undo with explicit authorization.
   Keep the original SQLite backup and do not overwrite current job statuses.
7. Verify actual idle wake-up and access with the PC off. Review RAM/CPU/quota
   consumption, confirm scanning/mail are unchanged, then enable personal use.

Rollback: stop web writes, retain post-cutover notes/status changes, redeploy the
last tested web image/SHA and retain additive private tables. Revoke sessions after
any state restore. Never replay mail or restore old shared job/outbox state merely
to undo a web deployment. Desktop can remain available while the PC is on, but is
not an always-on fallback. Backups need an approved encrypted off-host destination;
ephemeral Render files and a free plan are not backup evidence.

## Explicit approvals still required

Approve separately: merging the preparation PR; creating the free account/service
and exposing HTTPS; provisioning real credentials and minimum production grants;
backup destination/retention and manual schema changes; any precise Notes/Undo
dataset transfer (or choose empty cloud notes); and deployment plus the controlled
production write test. No resume transfer, paid resource or scanner change is
included. Stop rather than infer consent when any of these approvals is missing.
