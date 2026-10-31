# Private iPhone access: cloud readiness

Assessment: 31 October 2026, source snapshot `64554b1`. This is a design and
security audit, not a deployment. No production connection, credential change,
personal-data transfer, scan, tunnel, port forwarding or paid signup was performed.

## Decision

Safari access while the PC is off requires an independently hosted dashboard.
The existing cloud scanner does not host the website. Recommend one small,
always-on HTTPS dashboard behind personal identity authentication, connected to
the existing jobs database through a separate restricted role. Preserve the
scanner, schedules and notification system as a separate deployment.

First release: browse/filter jobs, open details, update lifecycle status and save
private notes. Keep resume upload/tailoring, application packages, outreach and
notification-secret editing disabled until each data transfer and capability is
explicitly approved. Without a resume, the current dashboard cannot reproduce its
local candidate-specific fit/eligibility view; label that limitation rather than
presenting generic relevance as personal matching.

## What is already cloud-backed, and what is not

These are code facts, not claims that production is currently healthy.

| Surface | Current boundary | Cloud consequence |
| --- | --- | --- |
| Job descriptions, relevance and lifecycle status | `repository_factory.py` chooses PostgreSQL when `DATABASE_URL` exists, otherwise SQLite | Reuse approved jobs backend; do not copy `jobs.db` or create another scanner |
| Scan windows, delivery/outbox state | Separate scanner storage and scheduled workflow | Dashboard must not mutate these tables or own SMTP/Actions credentials |
| Personal notes and dismissal undo | SQLite `personal_notes.db`, beside configured local jobs path by default | `DATABASE_URL` does not move them; ephemeral hosting would lose notes and undo history |
| Outreach contacts/drafts/activity | SQLite `data/outreach.db` | Remain local unless a separate migration is approved |
| Candidate application profile and approved packages | SQLite `data/applications.db`; packages contain local file paths and hashes | Paths are not portable; no automatic migration or file transfer |
| Active/uploaded/tailored resumes | Explicit filesystem resume directory; matching cache in process memory | PC files are unavailable with PC off; do not upload them implicitly |
| Cloud matching profile | `SafeCandidateProfile` permits location, canonical skills, section skill evidence and education level | Privacy-reduced, still personal data; existence of exporter does not establish live use or authorize new transfer |
| Notification recipient settings | Web POST calls authenticated `gh secret set SMTP_TO` | Disable updater server-side in cloud; never give web runtime repository-secret write access |

Sources: [app factory/routes](../src/web/app.py),
[repository selection](../src/storage/repository_factory.py),
[notes](../src/storage/personal_notes_repository.py),
[dismissal undo](../src/storage/dismissal_repository.py),
[outreach](../src/storage/outreach_repository.py),
[application packages](../src/storage/application_repository.py),
[resume storage](../src/resume/resume_manager.py),
[safe profile](../src/resume/safe_profile.py),
[recipient updater](../src/notifications/recipient_settings.py),
[scanner workflow](../.github/workflows/scheduled-scan.yml).

Earlier scanner diagnostics reported PostgreSQL 17, TLS, a session pooler and the
scanner role `postgres`. A later separate local dashboard connection could read
jobs but lacked monitoring-table SELECT privileges. This audit did not repeat
either inspection. A read-only query does not make its connecting role read-only.
Actual dashboard/scanner credentials, grants,
owners, RLS policies, backups, run freshness and email delivery remain deployment
verification items. Do not infer them from source configuration or older runbooks.

## Blockers before any external access

1. **Identity and authorization are absent.** Current protection is loopback
   TrustedHost allowlisting, CSRF/origin checks, CSP and other browser headers.
   There is no authenticated user or login session. The process-wide CSRF token
   is embedded in pages; it is not a password. Do not simply broaden hosts or bind
   the existing service to an external interface.
2. **HTTPS/proxy trust must be explicit.** Allow only the selected hostname;
   trust forwarded scheme/host headers only from the hosting proxy. Preserve
   cross-origin/null-origin rejection, anti-framing, no-store and CSRF checks.
   Verify native Safari form POSTs through the real proxy: wrong forwarded scheme
   can break legitimate origin comparisons.
3. **Approval state is process-local.** CSRF and proposal/application/outreach
   dictionaries are created at startup. Multiple workers or restarts invalidate
   them inconsistently. Use one worker initially; replace the shared token with
   session-bound CSRF. Bind any later approval to identity, current fingerprint,
   expiry and single use; never carry approval across deployment or restore.
4. **Private state requires durable storage.** All SQLite paths must resolve to
   one attached private volume, with restrictive filesystem permissions and
   encrypted provider storage/backups. Never expose that directory via static
   routes. Multiple instances need a different state design.
5. **Privileges must be separated.** Dashboard startup already calls
   `verify_schema()` rather than PostgreSQL migration/initialization. Use schema
   usage, job SELECT and only required status-column UPDATE (plus any necessary
   narrowly scoped policy). No ownership, DDL, relevance writes, scheduler,
   outbox, SMTP or secret-store privileges. Test effective rights, including
   inherited/PUBLIC grants; RLS alone cannot constrain an owner/bypass role.
6. **Deployment entry point is wrong for a website.** Current Docker `CMD` runs
   a scan. A later dashboard-specific image/entry point must start only the web
   service, fail closed when cloud DB/auth settings are absent, and disable
   unsupported write routes on the server. Hiding buttons is insufficient.

Sources: [browser boundary](../src/web/security.py),
[PostgreSQL schema check/status write](../src/storage/postgres_job_repository.py),
[Dockerfile](../Dockerfile), [.dockerignore](../.dockerignore).

## Smallest practical architecture

`iPhone Safari -> HTTPS + personal identity gate -> one dashboard worker`

`dashboard -> TLS-verified restricted jobs connection + private notes volume`

Preferred access gate: Cloudflare Access with an exact personal identity allowlist
and MFA at the identity provider; no PC tunnel. Verify the Access JWT at the
dashboard on every protected request: approved signing key/algorithm, issuer,
audience, expiry and user identity. Reject absent/invalid tokens even through the
provider's direct origin URL, so bypassing the gateway cannot bypass login.
Cloudflare's [JWT validation guide](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
documents origin verification. A direct OIDC implementation is an alternative,
but adds callback/state/nonce and session maintenance to the app.

Use a short-lived, signed or server-side application session bound to the verified
identity; Secure, HttpOnly, SameSite=Lax cookies, rotation at login and explicit
expiry/logout. Keep CSRF tokens per session. Restrict request/upload sizes and
timeouts; rate-limit authentication and mutations. Use `verify-full` with the
approved database CA, rather than assuming connection-string presence enforces
certificate verification. Keep secret values in the provider secret store.

## Hosting costs and limits

Official public pages checked on 31 October 2026; USD before taxes. These are
planning estimates, not a quote or measured usage. Recheck before purchase.

| Option | Observed public terms | Planning implication |
| --- | --- | --- |
| Render paid small web instance | [Pricing](https://render.com/pricing) lists $7/month for 512 MB and persistent disks at $0.25/GB-month | About $7.25/month for 1 GB disk, plus domain, backup storage, excess traffic and any existing DB charges; benchmark memory first |
| Railway Hobby | [Official plans](https://docs.railway.com/pricing/plans): $5/month includes $5 usage; overage adds the difference | $5 is a floor, not a guaranteed fixed bill; estimate RAM/CPU/volume/egress and configure spending controls |
| Personal access gate | [Cloudflare pricing](https://www.cloudflare.com/sase/products/access/) lists Free for up to 50 users | Fits one owner; domain and hosting remain separate costs |

Recommend Render's paid small service plus private disk for predictable initial
cost. Its [free service](https://render.com/docs/free) sleeps after 15 idle minutes
and loses local filesystem changes on restart/redeploy; it cannot attach a
persistent disk. That is unsuitable for the present SQLite notes design.
[Disk documentation](https://render.com/docs/disks) limits a disk-backed service
to one instance and describes deployment interruptions: accept brief maintenance
downtime for this personal app. No always-on or latency guarantee is established
until an approved synthetic staging benchmark passes. No new account was created.

## State transfer, backup and rollback plan

- Prefer keeping private notes on a persistent volume for the first single-owner
  release; PostgreSQL notes migration is optional later, not a prerequisite.
  Decide whether to begin with empty cloud notes or copy existing notes explicitly.
- Before an approved copy, quiesce writes and take consistent SQLite backups using
  its backup API; do not copy an active database without its journal/WAL. Preserve
  job identity: notes/undo currently key by numeric job ID, so confirm every ID
  maps to the same canonical URL in the selected jobs DB. Abort on mismatches.
- Treat desktop and cloud notes as separate until one authoritative writer is
  selected. No background two-way synchronization or silent conflict overwrites.
  Any later PostgreSQL migration needs versioned additive schema, explicit
  ownership/access policies, count/content checks and a reversible cutover.
- Encrypt off-host backups with access restricted to the owner; propose daily
  snapshots, seven daily copies and a weekly restore drill initially. Confirm
  retention, recovery-point tolerance and storage cost before enabling. A provider
  snapshot without a tested restore is insufficient.
- Restore into an isolated environment with synthetic mail/network sinks. Validate
  notes, job mappings and grants. Keep scanner delivery state out of dashboard
  restore operations; never replay notifications after restoring website state.
- Roll back by stopping cloud writes and reverting the pinned dashboard image;
  preserve volume and an export of post-cutover writes. Restore data only after
  reconciling newer notes/status changes. The existing desktop launcher remains
  an independent fallback, available only when the PC is on.

## Readiness checks and approvals still required

Before deployment, demonstrate with synthetic data: Safari login/logout and
expiry; unauthenticated/direct-origin rejection; denied foreign/null-origin and
invalid-CSRF writes; jobs/details/status/notes working at mobile width; persistence
after restart/redeploy; successful isolated restore; no secret/contact/resume data
in logs; rejected forbidden DB operations; no scan or mail side effect.

Provide cheap liveness and bounded DB readiness separately. Current `/health`
loads all jobs and exposes a job ID: replace with a minimal probe before external
hosting. Log request ID, status, latency and redacted error category only; omit
cookies/JWTs, DSNs, form bodies, note text, resume paths and personal details.
Monitor web availability separately from scan freshness and mail delivery.

Future explicit approvals must name: provider/region/domain and monthly budget;
creation/billing of hosting and access accounts; external HTTPS exposure; allowed
identity and session policy; creation/storage of dashboard credentials and any
grants/RLS/schema changes; exact personal-data categories transferred and retention;
notes source of truth/cutover/backup destination; any production write test.
Scanner schedules, retries and email settings require separate scoped approval.
Resume rewriting and application submission retain their separate human approval
requirements. This document authorizes none of those actions.
