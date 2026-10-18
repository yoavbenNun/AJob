# Round 2: durable windows, bounded wake-ups and separate delivery retries

All changes in this round are local. No production scan, mail, migration, cron,
secret, permission, push or deployment was performed.

## What changes

Keep the three Israel slots **08:17 / 11:17 / 16:17**. Each has a two-hour start/
recovery window. `scheduled-tick` selects current work from PostgreSQL, regardless
of the triggering cron's age. Expired windows become explicit MissedWindow or
WindowExpired failures/partials, with a notification, rather than replaying an old
full scan after a newer window starts. Existing round-1 full scans are preserved.
The optional legacy dispatch slot is validated for audit only, not replayed.

Reuse the **existing** Supabase pg_cron + http + Vault dispatcher. The SQL template
checks due work every five minutes, dispatches only actionable work, suppresses
requests during healthy worker leases, and applies a 15-minute accepted-dispatch
cooldown / 30-minute HTTP-error backoff. It stores status/error codes, never a
token or HTTP error body. A successful cron SQL call is not proof of HTTP success:
inspect dispatcher_state.error_type and last_accepted_at. The request uses the
existing repository-scoped credential; no new service, secret or paid tier.
Normal Actions wake-ups decrease: GitHub fallback has 12 daily UTC candidates
instead of 30, covering both Israel offsets, and primary dispatch usually needs
three calls/day. Retries still consume existing DB/Actions quotas; no price or
hard timing guarantee is implied.

GitHub concurrency keeps **one running and one pending** wake-up (default queue),
without cancelling the active process. New pending wake-ups replace redundant old
ones. Replacing a wake-up loses no durable work. The job timeout stays **75 min**.
Neither concurrency nor dispatch acceptance solves GitHub runner-capacity delays.
The independent existing watchdog avoids dependence on GitHub cron creation.

## Scan ownership and retries

A nonblocking PostgreSQL transaction advisory lock protects only claiming work.
No session scan lock survives for the entire scan. A scan lease lasts two minutes
and is renewed every 30 seconds through the parent's cancellation/polling path.
Every job insert/relevance update validates its window owner and lease inside the
same transaction. An expired but connected old scanner cannot overwrite a newer
window or block it indefinitely. Idle transactions are limited to 15 seconds;
source HTTP work occurs outside DB transactions. Recovery skips locked scan rows.

At most **two scan attempts per window**, five minutes apart. A retry visits only
sources without persisted full metrics in that window. Full scans are never
repeated because SMTP failed. Source budget is five minutes; scan budget is at
most an hour and is reduced near the window deadline to reserve ten minutes for
finalization. Under a minute of remaining scan budget produces an explicit failure.
Interrupted discoveries/outbox rows survive; run records are marked partial/failed
when leases expire. SIGTERM requests graceful cancellation when possible.

## Delivery state

`delivery_leases` is the authoritative operational state:
**pending / processing / sent / failed / uncertain**. The round-1 batch state
`sending` remains an internal SMTP-boundary marker for compatibility.

A ten-minute processing lease covers bounded preparation plus SMTP. Before SMTP,
a persisted marker is set atomically with the batch sending claim. An expired
preparation without that marker becomes failed and is safely retryable. An expired
attempt after the marker becomes uncertain and is **never auto-replayed**.
A random owner token fences old preparers/senders. Batch then lease lock order is
consistent between claim, completion and recovery to avoid deadlocks/races.

There are at most three automatic mail attempts, with 5/10/20-minute failure
backoff; after exhaustion the failed row stays visible for human action. An approved
operator may fix the cause and reset attempts/retry_at on a **failed** lease and
return its failed batch to pending in one transaction. Never apply that procedure
to processing or uncertain rows; reconcile provider/recipient evidence first. Explicit
SMTP rejection/pre-DATA errors retry; ambiguous DATA failure does not. Rendering
failures remain retryable. Frozen messages and stable Message-ID are retained.
Old round-1 sending without a lease is conservatively uncertain.

Membership is fixed at preparation, including empty batches. Older failure mails
cannot consume future-window discoveries. A partial scan's already-sent summary
is not sent twice when the scan resumes; later discoveries remain durable for the
next window. **Sent means SMTP accepted, not confirmed inbox delivery.**

## Observation and tests

`python -m src.main scan-report` emits a seven-day JSON matrix (21 expected slots):
scheduled time, actual start, full/partial/failed or current running state, unique
persisted discoveries, notification state, duration, scan UUID and failure reason.
Unobserved past slots and expired leases are explicit failures, not green blanks.
The workflow publishes this business report even after a failed worker step.
A successful no-op/healthy in-progress wake-up is technical success only.

Tests use mocked SMTP and a disposable PostgreSQL 17.11 Docker cluster. They cover
hours-late wake-ups, overlapping claims, connected expired owners, source resume,
near-budget finalization, SMTP retry without rescan, crashes before/during SMTP,
pending restart, owner fencing, cross-window deduplication, DST and Israel midnight,
notification-recovery races and an NOBYPASSRLS runtime role. The actual dispatcher
PL/pgSQL is exercised using local http/Vault stubs (no outbound request). Actual
Supabase pg_cron/http deployment and real mail receipt are not validated here.

Final validation: **369 passed, 2 skipped**, including 20 round-2 real-PostgreSQL
cases. Five targeted claiming/recovery/permission cases also passed through
PgBouncer **1.25.2 session mode**. The skips are the Windows symlink-privilege
case and the separately opt-in round-1 pooler test; its transport was validated
in round 1. One existing upstream Starlette/httpx deprecation warning remains.
The suite ran in unique temporary directories with production settings removed.
No Docker/HTTP/SMTP integration in this validation contacted production services.

## Controlled rollout and rollback (requires approval)

1. Pause both existing trigger sources and drain/cancel old workers deliberately;
   reconcile round-1 sending/uncertain outcomes before any replay. Back up the DB.
2. Validate the actual database role, RLS and pooler in a disposable staging clone.
   Apply 001 then **002_window_recovery.sql** under an approved schema owner.
   Grant the runtime only required tables/sequences and explicit RLS policies (or
   retain the approved scanner owner). Anonymous clients get no private policies.
3. Deploy workflow/code together. Review a read-only report before enabling the
   reviewed replacement function and the existing named pg_cron job. Replace,
   do not duplicate, ajob-cloud-dispatch. Preserve token scope/expiry and Vault.
4. Enable one controlled window and inspect business records and actual receipt,
   then all three. Observe dispatch rejection, missing starts and expired leases.
5. Rollback: pause both triggers first, stop/drain workers, preserve all outbox,
   messages and lease records, and reconcile any uncertain outcomes. Keep additive
   tables. Do not blindly enable the round-1 sender against round-2 in-flight mail.
   A code revert alone is insufficient; resume only an explicitly reconciled path.

## Seven-day acceptance

After approved deployment, collect **21 expected windows** over seven full Israel
calendar days. Verify each expected row has a start and terminal scan result; aim
for 21 full scans with start delay <=15 minutes and completion <=75 minutes.
Partial/failed/missed windows must have an actionable reason, not count as success.
Verify 21 SMTP acceptances separately from 21 inbox arrivals using stable Message-ID
and actual recipient/provider evidence. Investigate every missing/duplicate mail,
uncertain outcome, exhausted retry and stale processing lease. Confirm all saved
outbox jobs are represented in a frozen sent message or explicitly pending for the
next window, with no silent orphan older than 24 hours. Track queue/start latency,
source durations and counts, retry counts and per-slot mail outcome daily.

Remaining failures include paused/unavailable Supabase, expired Vault credential,
GitHub API/runner outage, image-build delay, DB/network partitions, exhausted time
budgets, and SMTP filtering after acceptance. Existing infrastructure is sufficient
for this best-effort design; a hard delivery-time SLA during those outages needs
an independent executor/monitor, which requires a separate plan and approval.

References: [GitHub concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency),
[Supabase Cron](https://supabase.com/docs/guides/cron).

## Pre-deployment compatibility

[Overnight review](predeployment-review.md) and the executable/local validation
steps in [deployment-runbook.md](deployment-runbook.md) are prerequisites for rollout.
New legacy email entry points refuse to bypass active managed windows. Historical
binaries still cannot safely coexist with the new outbox; rollback must first pause
triggers and preserve/reconcile pending and uncertain state, not just revert code.
