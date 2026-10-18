# Engineering Decisions

## Local browser boundary (2026-10-18)

Keep the existing single-user, loopback-only architecture. Add Host allowlisting,
same-origin mutation checks, CSRF, and anti-framing headers rather than presenting
the dashboard as a hosted multi-user product. This preserves the launcher and
forms; public hosting would need a separate authentication/authorization design.

## Portfolio and audit tooling (2026-10-18)

Generate screenshots in a disposable synthetic workspace and block external
browser requests. Install audit tooling in an isolated environment and report
redacted locations. Keep tooling out of the scanner's runtime requirements.
Security workflow proposals do not change the active cloud deployment until an
explicitly approved push.

## Scan reliability, round 1

Use an atomic PostgreSQL outbox and independent scan/mail records instead of the
in-memory discovery list. Isolate sources in stdlib child processes to enforce
wall deadlines even when browser/HTTP code hangs. Keep parent-only DB writes and
short transactions. Prefer conservative uncertain SMTP state over automatic
replay. See [deployment gate and limitations](scan-reliability.md). This round is
local only; migration and deployment require separate approval.


## Durable windows and independent wake-ups (round 2)

Reuse the existing Supabase pg_cron/http/Vault dispatcher as a due-work watchdog,
with GitHub cron as backup. A wake-up never owns an old slot; PostgreSQL selects
current work and records missed windows. Coalesce pending Actions runs instead of
building a FIFO of retries. Use a short transaction advisory lock for claims,
renewable scan leases with write fencing, and separate fenced notification leases.
This avoids an expired session lock blocking later windows and separates SMTP
retries from scans. No new cloud service/tier is introduced. See
[rollout, limitations and seven-day acceptance](round2-reliability.md).
