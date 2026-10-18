# AJob deployment and rollback runbook

Status: **LOCAL CANDIDATE; PRODUCTION CHANGES NOT AUTHORIZED OR PERFORMED**.
Use together with round2-reliability.md. Never paste credentials or backup data
into chat, Git, workflow summaries or this document.

## Verified state and gates

- Candidate covers reliability rounds 1/2 and the pre-deployment regressions.
- Remote main was 508636f6c433c3a51af753b160d10e14335a63ba at the review snapshot.
- GitHub API reports the repository **Public**, not Private. No visibility change
  was made. Publishing any candidate branch requires explicit approval now.
- Supabase MCP OAuth refresh failed. Production schema/roles/policies, connection
  mode and live cron contents remain **unverified**. Reconnect before rollout.
- GitHub read-only snapshot: zero running and zero queued Actions runs. This is
  time-sensitive evidence, not permission to deploy later without checking again.
- Code/migrations were exercised in disposable PostgreSQL 17.11; round-2 claims
  and recovery were also verified through PgBouncer 1.25.2 session mode.

STOP if any gate is unresolved: schema/role/pooler unknown; triggers not paused;
workers active; backup not restored successfully; candidate checks red; pending
or uncertain mail not reconciled; public publication not explicitly approved.
Never deploy old and new email workers concurrently.

Final overnight validation: 375 tests passed, two skipped; zero secret findings
across 118 candidate working-tree files. Backup/restore and compatibility drills
used synthetic data only. These checks do not waive production gates above.

## 1. Preflight (read-only)

1. Reconnect Supabase MCP privately. Confirm project sfsuwtpnkixjkbdzopbp, DB name,
   server version and that the connection is the approved direct/session endpoint.
   Do not infer the pooler from the provider name. Verify TLS and the real role.
2. Inspect job_postings and notification_deliveries, then any existing 001/002
   tables. Inspect owners, RLS/policies, table/sequence privileges and active client
   sessions. No row content, credentials, resume or contact information is needed.
3. Inspect the existing ajob-cloud-dispatch cron entry/function and current GitHub
   scheduled-scan workflow. Verify all trigger sources, including manual clients.
4. Check no unreviewed changes entered the candidate. Verify the public repository
   decision and candidate commit before publishing. Keep AGENTS.md out of this
   reliability commit; its prior local change is preserved separately.

## 2. Pause triggers safely (requires fresh production approval)

1. Disable Scheduled job scan in GitHub Actions using the authorized account.
   Disabling a workflow is separate from cancelling or finishing existing runs.
2. Pause the existing independent dispatcher using its named entry:

```sql
select cron.unschedule('ajob-cloud-dispatch');
```

3. Verify the cron entry is absent/inactive and the Actions workflow is disabled.
   Do not create a second dispatcher under a new name. Do not rotate secrets.
4. Stop external/manual launchers from starting cloud scans during the maintenance
   window. Do not turn off the user's local dashboard unnecessarily.

## 3. Drain workers and reconcile delivery

Wait for running workers to finish (the Actions job limit is 75 minutes). Inspect
both queued and running runs again, plus database sessions and processing/sending
records. Never rely on a green workflow alone. If cancellation is needed, obtain
explicit approval; prefer draining because cancellation during SMTP is uncertain.

Take a read-only inventory of pending/failed/sending/uncertain deliveries and
outbox counts. Investigate ambiguous SMTP outcomes with actual provider/recipient
records. Do not reset uncertain or sending to pending on a timer. Old released
code does not know the new outbox or leases; its legacy ledger can independently
claim a slot. Mixed live versions are therefore **semantically incompatible**.
The new legacy-email entry point refuses to run once managed windows are active,
but this cannot retrofit a guard into an already-running historical binary.

## 4. Obtain and verify a backup

Use the approved Supabase/operator backup procedure and encrypted storage outside
the repository. Record snapshot time, database/schema version and a checksum, then
restore to an isolated PostgreSQL instance before proceeding. Verify jobs/status,
legacy ledger, scan_runs/metrics, pending snapshots, frozen messages and lease
states. Re-check RLS/grants after restore; logical data restore alone is insufficient.
Supabase-managed schemas, platform credentials and Vault restoration require the
provider-approved process. Never restore a production backup into an unprotected
public/local demo or start mail workers against both original and restored DBs.

The tested local drill uses pg_dump 17.11 custom format and pg_restore inside an
explicitly named disposable Docker container, with synthetic data only. To repeat
that drill against such a container (never set these to production):

```powershell
$env:AJOB_TEST_POSTGRES_DSN='postgresql://postgres@127.0.0.1:LOCAL_PORT/postgres'
$env:AJOB_TEST_DOCKER_CONTAINER='ajob-pg-YOUR_DISPOSABLE_CONTAINER'
.venv\Scripts\python.exe -m pytest tests/test_postgres_window_recovery.py::test_backup_restore_retains_pending_and_uncertain_outbox -q -p no:cacheprovider
```

The test creates/removes its own random databases. It validates pending/uncertain
preservation and no replay of uncertain messages, not Supabase platform backups.
No exact executable production backup command is supplied: that endpoint, client,
storage and privileges were unavailable for verification overnight.

## 5. Apply additive schema (requires approval)

After backup verification and confirming the approved DATABASE_URL privately:

```powershell
.venv\Scripts\python.exe -m src.main prepare-scan-storage
```

This explicitly applies 001_scan_reliability.sql then 002_window_recovery.sql,
each transactionally. It requires an initialized base job/legacy-ledger schema;
it is not a blank-database bootstrap. Reapplication was tested on existing data
and the earlier round-1 schema. Startup checks fail closed on old schema without
creating tables. The runtime never auto-migrates.

Use an approved schema owner for DDL. Verify runtime policies/privileges separately:
scanner needs SELECT/INSERT/UPDATE on the relevant state tables and sequence
USAGE (no DELETE/DDL), while the dashboard retains its read/status-only access.
Private state has RLS enabled; anon/authenticated get no private access policies.
Do not grant BYPASSRLS broadly to fix a missing policy.

## 6. Publish and deploy code (requires approval)

Review the final commit and all checks. A push to **main changes the live scheduled
workflow**, even without an on:push production job; do not confuse CI-only push
triggers with safe default-branch publication. Keep trigger pause in force through
publishing/merging the candidate and verify the resulting workflow stays disabled.
Do not force-push or rewrite history. The repository is currently Public, so obtain
explicit publication approval before any push, including a candidate branch.

Deploy code and schema as a matched version. Do not start a historical image
against the new active delivery system. Keep candidate and rollback image/commit
identities available. The local user's unrelated AGENTS.md change is not included.

## 7. Update existing Supabase dispatcher (requires approval)

Review docs/supabase-dispatch.sql and apply it under the approved owner only after
code/schema are ready. It replaces the existing named pg_cron entry and function,
uses the existing Vault credential and preserves repository-scoped Actions access.
The named cron.schedule call enables the job, so do not execute that final line
until smoke checks and re-enable approval are complete. Do not create a duplicate
job, change secrets or add pg_net credential-bearing requests. Inspect
cron.job_run_details AND dispatcher_state.error_type; HTTP 204 is not scan success.

## 8. Smoke checks

Before any real scan/mail, run the read-only business report with the approved
connection environment:

```powershell
.venv\Scripts\python.exe -m src.main scan-report
```

Verify all required tables/columns, role access, planned Israel slots and clear
predeployment NotObserved/missed states. Verify no unexpected worker is active.
Do not call scheduled-tick as a harmless smoke command: it performs real scans
and sends mail. A controlled first production window needs explicit authorization.
Use dry-run only for source configuration, not to prove DB delivery semantics.

## 9. Re-enable and observe

With approval, enable the existing named Supabase dispatcher and then the GitHub
fallback workflow. Confirm one active dispatcher, bounded pending Actions runs,
healthy leases and no live old workers. Observe one controlled window, inspect
business status and actual receipt, then continue all three daily windows.
No additional cloud service or paid tier is required by this candidate.

## 10. Seven-day acceptance

Capture scan-report daily over seven complete Israel calendar days (21 expected
slots). Require starts and explicit terminal results; target full scans starting
within 15 minutes and completing within 75 minutes. Treat partial/missed/failed
as exceptions, not success. Separately reconcile 21 SMTP acceptances and actual
inbox arrivals using stable Message-ID evidence; sent is not inbox delivery.
Investigate every expired processing lease, failed/exhausted retry, uncertain mail
and outbox snapshot unrepresented by sent or explicitly pending future delivery.
No pending discovery should disappear or remain a silent orphan beyond 24 hours.

## 11. Stop/rollback criteria and procedure

Stop on duplicate send evidence, outbox loss, wrong recipient/profile, ownership
fencing failure, unexpected privileges, migration/restore failure, or repeated
missing windows. Pause both trigger sources first; drain workers and capture state.
Keep all additive tables, snapshots, messages and leases. Do not drop them as a
schema rollback or return uncertain outcomes to pending.

A safe rollback is initially a **paused system with preserved state**. Revert code
only after reconciliation, and do not re-enable an old sender while new outbox
rows or uncertain attempts exist: old code cannot drain that queue and may send a
second message through its independent legacy ledger. Prefer a fixed compatible
worker/roll-forward. If a DB restore is required, choose a verified snapshot and
reconcile deliveries accepted after it before resuming; restoring a snapshot cannot
unsend mail. Production restore and any cancellation/publication require approval.
