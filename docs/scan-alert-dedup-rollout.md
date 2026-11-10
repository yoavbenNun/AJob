# AJ-008: evidence and gated durable alert rollout

Status: review/test plan only. **No Production SQL, grants, repository variables,
notification settings, dispatches, scans or SMTP tests were executed.** No new
dedup mechanism is needed: PR #23 already implements the required exit policy.

## What run 42789814970 actually establishes

[Scheduled scan, November 10](https://github.com/yoavbenNun/AJob/actions/runs/42789814970)
ran main `23a125de610360dc4af8b5bcb494294955bbd833`, created at
09:20:01 UTC / **11:20:01 Asia/Jerusalem (UTC+02)**. Reconcile exited 1 at
09:28:06 UTC; Publish exited 1 at 09:28:18 UTC. The flag was empty in both
steps; a read-only repository-variable lookup also returned not found.

The two aged scan-only failures (October 18 and October 31) remain FINAL_FAILURE,
but are already `historical_scan_failure`: **they do not cause this exit**.
The report also contains:

- Ten operational expired FINAL_FAILURE windows: three each on November 7, 8
  and 9, plus November 10 08:17 Israel time. These are still inside seven days
  of their individual deadlines.
- A **new** November 10 11:17 partial scan:
  `SourceFailureOrBudget / PARTIAL_RECOVERABLE`, one failed source,
  `notification=sent`. This is a real collection failure, not historical noise.
- The future 16:17 window was SCHEDULED/pending, not a missed delivery.

All completed windows in this report have mail state sent. This establishes
recorded SMTP acceptance, not inbox arrival. No Production queries were made,
so this audit does not certify the absence of orphan delivery failures outside
the report. Adapter diagnosis belongs to Developer 1 and is not changed here.

The legacy exit path correctly reports the operational/new failures; it never
consults alert receipts while the flag is unset. Repeated unchanged operational
incidents can therefore keep unrelated subsequent workflow runs red until aging
or explicit durable acknowledgement. Merely sending their business mail is not
that acknowledgement.

## Three independent channels

| Channel | Durable state | What dedup can establish |
| --- | --- | --- |
| AJob business summary SMTP | notification_batches, outbox and delivery leases | Sent batches are not replayed; interrupted SMTP may be uncertain, never assumed unsent |
| AJob operational incident reporting | optional scan_alert_receipts | An identical incident was present in a separately verified completed GitHub reporting failure |
| GitHub Actions failure emails | GitHub run/attempt conclusion and account preferences | GitHub decides notifications; the application cannot certify inbox delivery |

The incident ledger does **not** send a separate incident email, change SMTP
state, acknowledge receipt of business mail or turn a failed window FULL.
PR #23 controls both Reconcile and Publish exit decisions. For known verified
incidents only, they can return zero while the window history remains failed.
That can prevent an unnecessary workflow failure, and consequently its automatic
GitHub failure notification. It cannot stop emails for new collection/Recovery/
SMTP/lease failures, setup/build failures, receipt-check failures or other jobs.
No claim of exactly-once GitHub notification delivery is made.

## Existing implementation and acceptance criteria

1. New incident: persist pending receipt and fail. Both steps in that same
   run/attempt must remain red.
2. Later run: verify the exact prior attempt through GitHub APIs. Repository,
   main branch, schedule/manual event, workflow path, completed failure, scan
   job and named failed business step must match. Cancellation/unrelated failure
   or a pending database row is not proof.
3. Confirmed identical snapshot: report zero for alert eligibility, retain
   FINAL_FAILURE in window_report and keep strict `scan-report --check` nonzero
   for operational failures.
4. A changed state/reason, scan owner/attempt, delivery attempt, orphan delivery
   timestamp or new slot creates a new incident. A failed current collection
   always fails, even if another incident has a confirmed receipt.
5. Missing/corrupt receipts, schema/version, database, context, token or API
   failure: fail closed. Restore loss causes re-alerting rather than silence.
6. Seven-day aging remains separate: only expired scan-only incidents with sent
   mail qualify. Unknown, lease and unfinished/uncertain delivery stay actionable.

Receipts persist in PostgreSQL across hosted runners and restarts, with composite
PK and short transactions. Two unconfirmed concurrent runs may both fail:
conservative duplicates are preferable to a lost alert. Network verification is
outside transactions; bounds remain three prior attempts, ten receipts/event,
30 seconds cooperative budget and five-second HTTP timeouts.

## Production activation checklist — ALL steps require operator approval

### A. Evidence, environment and backup — before any change

- Record approved main SHA, actual Scheduled workflow path and its existing
  contents:read/actions:read permission. Record current flag state (unset/0/1).
  Do not alter permissions, Cron, secrets or personal notification preferences.
- Inventory currently operational windows with existing read-only reports.
  Preserve slot, deadline, outcome, scan/mail attempts, scope and current run IDs.
  Do not manually mark a window successful or a receipt confirmed.
- Confirm the actual scanner login and search_path using its normal connection,
  not an administrator's successful query. Production receipt schema and grants
  have **not** been validated by this task.
- Obtain a provider snapshot or consistent encrypted backup, validate restore to
  a disposable database, record retention/access restrictions. If using pg_dump,
  use an already approved private libpq service, not credentials on the command
  line. Example for an approved operator, **not run by this PR**:

  `pg_dump --dbname=service=ajob_backup --format=custom --file=backups/aj008-before.dump`

  The service name is an example requiring local configuration. This backup
  contains personal data; use approved encrypted storage and never commit it.
  Stop if backup/restore validation or identity verification is unavailable.
- Verify no Scheduled job is currently in flight; wait for it to finish rather
  than cancelling it. A variable change is not retroactive to an existing job.

### B. Schema gate — additive and manual only

Review and test `src/storage/alert_migrations/001_scan_alert_receipts.sql` in a
disposable PostgreSQL instance first. Normal scanner migrations never apply it.
It creates only `scan_alert_schema` and `scan_alert_receipts`, with RLS enabled.
There are **no automatic grants or policies**. IF NOT EXISTS is not proof that a
preexisting table has the required shape.

Only after separate approval, apply using the approved installer identity:

```sh
psql "service=ajob_alert_installer" -X --set=ON_ERROR_STOP=1 --single-transaction --file=src/storage/alert_migrations/001_scan_alert_receipts.sql
```

Alternatively the existing explicit `python -m src.main prepare-scan-alert-storage`
command can be used with approved scanner connection handling. Do not execute
both methods blindly, run normal migrations, or expose DATABASE_URL in arguments.

Under the **actual runtime scanner login**, inspect and record:

```sql
SELECT current_user, session_user, current_schemas(true);
SELECT to_regclass('scan_alert_schema'), to_regclass('scan_alert_receipts');
SELECT id, version FROM public.scan_alert_schema; -- exactly one row, 1 / 1
SELECT c.relname, pg_get_userbyid(c.relowner) AS owner,
       c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public'
  AND c.relname IN ('scan_alert_schema','scan_alert_receipts');
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN ('scan_alert_schema','scan_alert_receipts')
ORDER BY table_name, ordinal_position;
SELECT conrelid::regclass AS relation, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid IN ('public.scan_alert_schema'::regclass,
                   'public.scan_alert_receipts'::regclass);
SELECT tablename, roles, cmd, qual, with_check FROM pg_policies
WHERE schemaname='public'
  AND tablename IN ('scan_alert_schema','scan_alert_receipts');
SELECT has_schema_privilege(current_user,'public','USAGE') AS schema_usage,
       has_table_privilege(current_user,'public.scan_alert_schema','SELECT') AS version_read,
       has_table_privilege(current_user,'public.scan_alert_receipts','SELECT') AS receipt_read,
       has_table_privilege(current_user,'public.scan_alert_receipts','INSERT') AS receipt_insert,
       has_table_privilege(current_user,'public.scan_alert_receipts','UPDATE') AS receipt_update;
```

Confirm unqualified names resolve to these approved tables, no untrusted writable
schema shadows them, and the shape matches the migration:
schema singleton id/version; receipts' four-part PK
`(fingerprint,repository,run_id,run_attempt)`, positive run_attempt and valid
pending/confirmed/timestamp-nullability checks. Do not publish event_json contents.

Required runtime access: schema USAGE, version SELECT, receipt SELECT/INSERT/UPDATE.
No DELETE, TRUNCATE, sequence, dashboard-role or new BYPASSRLS access is needed.
The installer needs schema CREATE and table-owner ALTER authority. An owner may
already bypass non-forced RLS; a non-owner needs both privileges **and applicable
SELECT/INSERT/UPDATE policies**. Table grants alone are insufficient.

If runtime rights/policies are missing, **stop for a separately reviewed
role-specific grant/policy change**; do not grant PUBLIC, anon or dashboard access.
Do not disable RLS or broaden a shared role just to make the feature work.

After approving schema/permissions, prove read/write/transaction behavior with a
fully rolled-back probe under that same login. Use no real incident or mail row:

```sql
BEGIN;
DO $probe$
DECLARE observed integer;
BEGIN
  INSERT INTO public.scan_alert_receipts
    (fingerprint,repository,run_id,run_attempt,event_json,state,created_at)
  VALUES (repeat('0',64),'yoavbenNun/AJob','1',1,
          '{"kind":"preflight"}','pending',CURRENT_TIMESTAMP::text);
  SELECT count(*) INTO observed FROM public.scan_alert_receipts
    WHERE fingerprint=repeat('0',64) AND repository='yoavbenNun/AJob'
      AND run_id='1' AND run_attempt=1;
  IF observed <> 1 THEN RAISE EXCEPTION 'Receipt SELECT policy failed'; END IF;
  UPDATE public.scan_alert_receipts SET state='confirmed',
    confirmed_at=CURRENT_TIMESTAMP::text
    WHERE fingerprint=repeat('0',64) AND repository='yoavbenNun/AJob'
      AND run_id='1' AND run_attempt=1;
  GET DIAGNOSTICS observed = ROW_COUNT;
  IF observed <> 1 THEN RAISE EXCEPTION 'Receipt UPDATE policy failed'; END IF;
  SELECT count(*) INTO observed FROM public.scan_alert_receipts
    WHERE fingerprint=repeat('0',64) AND repository='yoavbenNun/AJob'
      AND run_id='1' AND run_attempt=1 AND state='confirmed';
  IF observed <> 1 THEN RAISE EXCEPTION 'Confirmed receipt is not readable'; END IF;
END;
$probe$;
ROLLBACK;
```

Stop on any error, including unsupported DO execution or conflicting probe key;
do not grant extra privileges to bypass a failed preflight. Verify no probe row
remains. This SQL is a future approved probe, **not executed against Production**.
It must not be replaced by pre-confirming actual historical incidents.
The probe uses this repository identity so a correctly repository-scoped RLS
policy can pass; it is not a receipt for an actual Actions run.

### C. Separate flag approval and natural-run verification

- Only after gate B passes, explicitly approve setting repository variable
  `AJOB_SCAN_ALERT_DEDUP=1`. Keep all other settings unchanged. Do not dispatch
  a scan, rerun a delivery workflow or send a real test email.
- Observe the next **naturally scheduled** wake-up that actually received value1.
  With no ledger history, operational incidents deliberately fail once and
  produce pending receipts. Historical aged scan-only rows remain visible warnings.
- Observe a later distinct completed run: it can confirm the previous failure
  and output `incident_alert_check` with `previously_reported>0`. A healthy
  duplicate-only run should have both business steps zero, not merely a hidden
  annotation. Check the overall workflow conclusion, not just the step summary.
- New or changed events must still have `new_or_unverified>0`, or a current
  execution failure, and nonzero exit. Build/DB/API/receipt-check failures remain
  red. Do not trigger a genuine Production failure just to test; use the synthetic
  suite for these cases and review the next naturally occurring event.
- Compare business window/mail history before/after: failures remain failed,
  sent batches are not replayed, attempts change only due to genuine scheduled
  work. Confirm durable receipts exist after the runner exits. GitHub receipt
  verification does not prove user inbox delivery.
- Multiple new failures or API/permission problems can legitimately keep every
  wake-up red. **Do not promise that enabling the flag stops all GitHub emails.**
  Investigate new source failures with their owner, not by relaxing this policy.

### D. Rollback / abort

- With approval, restore the prior variable state (unset or0) if schema, role,
  context, API validation or suppression behavior is wrong.
- Leave both optional tables/receipts and all window/mail history intact.
  Legacy strict exit behavior resumes; repeated failure emails may resume too.
- Do not drop tables, delete events, reset batches or replay sent/uncertain mail.
  Restoring the entire backup is **not** normal rollback: it can rewind delivery
  state and replay mail. Any disaster restore requires a separate reconciled
  recovery plan, including uncertain sends and manual statuses.
- Schema preparation without flag activation is inert. A failed validation means
  no flag enablement. Lost receipts must re-alert, never be silently fabricated.

## Review and tests

AJ-008 adds synthetic acceptance tests for the exact historical-vs-fresh
distinction, repeated acknowledged events, rollback, changed Recovery/SMTP
attempts, seven-day expiry and delivery retries. Existing tests cover restart,
concurrent PostgreSQL writers, failed GitHub verification, missing schema and
single business-message delivery. All tests use temporary stores and stubbed
APIs/SMTP; no Production connection or actual email is required.
The added PostgreSQL role test proves table grants alone are denied by RLS,
then permits only the intended repository with explicit row policies.
