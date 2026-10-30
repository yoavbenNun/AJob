# Scan reliability: round 1 (not deployed)

Round-2 operational behavior and rollout now live in
[round2-reliability.md](round2-reliability.md). The historical round-1 guarantees
and validation below remain applicable to its storage layer; scheduled execution
uses the round-2 fenced leases rather than a long-lived scan session lock.

## Design and guarantees

The scheduled `scan --email-summary` path uses a PostgreSQL outbox. A new job and
its notification snapshot commit in the **same short transaction**. Existing job
lifecycle status is preserved. A single scanner connection replaces per-candidate
connections; unknown rejected URLs cause no DB writes. No Redis or queue service.

Each source runs in an isolated child process with no DB access. The parent writes
discoveries as IPC results arrive, and terminates the worker process tree after
its budget (default 300 seconds/source, 3600 seconds/scan). Source startup and
fetch/enrichment count toward the wall budget. DB statements have a 5-second
limit and lock waits a 2-second limit. Worker cleanup adds a small bounded grace.
A hung source does not abort later sources unless the total budget is exhausted.
SIGTERM requests graceful cancellation; SIGKILL can prevent final status updates,
but cannot undo committed jobs/outbox entries. Retrying the same slot marks its
abandoned running scan partial. Overall statuses are full / partial / failed.
SMTP configuration is validated after scanning; invalid mail settings leave a
pending batch without preventing job discovery. A failed scan has no completed source and no saved jobs; partial means some useful
work completed. Source metrics explain individual timeouts and errors.

Discovery origin slots remain immutable. Pending unassigned discoveries are
included in a later batch, even after a crash before batch creation. Batch membership is fixed on first preparation (`prepared_at`), under a batch
row lock. Later discoveries stay unassigned for the next batch, even when the
first batch was empty. Pending or
failed batches retain their frozen message and are retried on subsequent runs.
A partial scan can later complete without resending its already-sent summary;
additional discoveries remain available for the next slot. Scan and mail states
are independent, and logs report both. A workflow success is not inbox evidence.

Before SMTP, the message is persisted and the batch becomes sending. Explicit
SMTP rejection or pre-DATA connection failure is retryable. Disconnection during
DATA is uncertain. Interrupted sending is held, never reset automatically; retry
of the original slot changes it to uncertain under a PostgreSQL advisory lock.
Unretried interrupted slots may remain sending and also require reconciliation.
A positive SMTP DATA acknowledgement means accepted, **not delivered to inbox**.
A stable Message-ID aids investigation but does not guarantee exactly once.

Recommendation processing has a hard child-process budget (up to five minutes,
within a shared 65-minute orchestration cutoff). Failure leaves the batch pending;
it never sends unfiltered fallback recommendations. SMTP socket operations have
30-second timeouts. The cutoff stops starting further batches, not an operation
already in progress; leave the remaining 10 minutes for SMTP, DB, cleanup and
workflow overhead. These are starting budgets, to tune using real metrics.

## Migration and deployment gate

No production migration, scan, mail, secrets, workflow or cron change was made.
`src/storage/migrations/001_scan_reliability.sql` adds scan_runs,
scan_source_metrics, notification_batches and notification_outbox, their indexes,
and enables RLS. Legacy sent slots import as sent; other legacy slots import as
uncertain to avoid replaying possibly accepted messages. Existing job rows and
legacy ledger remain unchanged. Previously saved jobs with no historical outbox
cannot be reliably identified as undelivered: reconcile separately, never bulk
backfill them blindly.

After approval, first back up and test migration on a disposable PostgreSQL clone.
Against the existing initialized job database, apply explicitly using `python -m src.main prepare-scan-storage` with the
approved connection environment. Startup verifies schema and fails closed if it
is absent. Use a direct PostgreSQL or **session pooler** connection: session-level
advisory locks are not compatible with transaction pooling. The scanner role
needs table/sequence privileges and RLS bypass/appropriate policies; anonymous
clients must have no access to these tables. The local dashboard is unchanged.
Do not run old and new scanner binaries together: they use different mail ledgers.
Rollback requires pausing scans and reconciling mail state first; retain outbox
and frozen messages, and never switch blindly back to the legacy sender.

## Metrics and round 2

Structured `source_metrics` logs and persisted JSON contain elapsed_seconds,
http_seconds (HTTP/browser/feed decoding), processing_seconds (filter CPU stages),
db_seconds, db_calls, no_op_updates_avoided, status, error_type and company.
These are stage timings, not pure network wire timing; interrupted stages use
elapsed time until cancellation. scan_runs records slot, times, status and counts;
notification_batches records mail state and exception type without credentials.

Round 2: schedule/concurrency/backlog policy and source fairness (a consistently
exhausted global budget can defer sources at the end of the configured list); bounded retry/backoff and stale-slot
reconciliation; provider delivery evidence; operational dashboards/alerts and
retention of snapshots/messages. Check three intended slots per day separately
from cron no-ops, completion latency, mail acceptance and actual receipt. The local PostgreSQL validation below covers DB transactions and session pooling.
Before production approval, verify the actual deployment role, pooler and grants,
then observe several days of all three slots. Local tests do not establish actual
cloud delivery, scheduling or production performance.


## PostgreSQL validation (2026-10-18)

Verified against disposable Docker PostgreSQL **17.11** (`postgres:17-alpine`,
image digest `sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`).
The DB used tmpfs and a random loopback-only published port. No production DSN,
job site, SMTP server or persistent database volume was used.

`tests/test_postgres_scan_integration.py`: **20 direct PostgreSQL cases passed**,
including atomic job/outbox commit and trigger-injected rollback, competing job
inserts, competing batch assignments, competing sender claims in independent OS
processes, process kill after commit, pending and uncertain recovery, lifecycle
preservation, session advisory locks, schema preflight, migration rollback and
idempotent upgrade. Migration was exercised on freshly initialized base schema,
existing data/legacy delivery rows and the previous round-1 schema.

The migration is additive to the **existing application base schema**, not a
standalone bootstrap script. On an entirely blank schema without job_postings it
fails and rolls back all its DDL; initialize the base schema first in a new test
DB. Reapplication adds prepared_at and preserves prior payloads, job status,
legacy mail outcomes and existing assigned batch membership. Old-schema startup
is rejected before scan execution. Production migration remains unapproved.

Three tests also passed through actual **PgBouncer 1.25.2 in session mode**:
stable backend PID across commits and session-lock lifetime/release, minimum-role
RLS operations, and competing OS-process delivery claims. PgBouncer ran only in a
second disposable container, with packages installed inside that container.
**Supabase Supavisor, transaction pooling, production TLS, deployed role grants,
and production default privileges were not tested.** Session pooling evidence
must not be presented as Supavisor-specific certification.

RLS was exercised with synthetic restricted roles in the local cluster: a scanner
with only SELECT/INSERT/UPDATE, sequence USAGE and explicit policies could perform
the workflow but could not DELETE or create tables. A dashboard-equivalent role
could read jobs/update lifecycle status; even when deliberately granted SELECT on
the private tables, RLS hid their rows and writes were denied. The migration does
not create/grant deployment roles or scanner policies automatically. Before rollout,
confirm that the actual scanner is an appropriate owner/BYPASSRLS role or provide
approved grants and policies, and ensure anon/authenticated lack private access.

To reproduce, start the official PostgreSQL image with a random loopback port and
no persistent volume, then run from PowerShell (use its actual published port):

```powershell
$env:AJOB_TEST_POSTGRES_DSN='postgresql://postgres@127.0.0.1:PORT/postgres'
python -m pytest tests/test_postgres_scan_integration.py -k 'not pooler_session' -q -s -p no:cacheprovider
```

The dedicated test user needs CREATEDB/CREATEROLE. The fixture creates a random
ajob_test_* database and isolated schemas/roles, then removes them. It rejects
non-loopback hosts and never reads DATABASE_URL. Use a disposable cluster only.
For pooler tests, point that same explicit test DSN at a local PgBouncer configured
with `pool_mode=session`, wildcard database forwarding and postgres admin access,
set `AJOB_TEST_SESSION_POOLER=1`, and select
`pooler_session or rls_and_minimal_scanner_privileges or separate_processes_claim`.
Pooler's DISCARD ALL session reset must remain enabled for lock cleanup. Test
containers are stopped/removed after verification; cached Docker images may remain.

## Historical scan alerts

`window_report` and `scan-report` retain unresolved history without deleting or
marking scans full. A finalized `partial`/`failed` scan with `WindowExpired` or
`MissedWindow`, sent mail, no expired lease, and a deadline at least seven days ago
is labeled `alert_scope=historical_scan_failure`. Scheduled runs emit a warning;
the unchanged `FINAL_FAILURE` outcome remains visible in JSON and Actions summaries.
Such history alone no longer fails `scheduled-tick` or `scan-report --check`.

Fresh failures still fail for seven days after deadline. Pending/failed/uncertain
delivery, unknown records, running/expired leases and unfinished recovery never
qualify for this historical exception. This is an alert-retention policy, not
acknowledgement, successful recovery or proof of inbox delivery. No migration or
Production data change is needed. The watchdog and recovery claim rules remain
unchanged: expired scan windows are not replayed and sent mail is not resent.

## Rollback boundary

See [deployment-runbook.md](deployment-runbook.md). A legacy repository may still
read/upsert jobs on the additive schema, but its independent delivery ledger does
not consume the outbox. Structural SQL compatibility is not mail safety. Never
restore an old sender to service while new pending/uncertain mail exists.
