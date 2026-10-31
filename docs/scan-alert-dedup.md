# Scheduled scan incident reporting

## Cause and semantics

Previously both scheduled-tick and scan-report --check returned nonzero for every
unresolved operational window for seven days. GitHub failure notifications follow
workflow failure, not whether its annotation describes a new incident. Repeated
wake-ups therefore repeated an old failure even without scanning or sending mail.

The opt-in reporting path separates:

1. Business window: FULL/PARTIAL_RECOVERABLE/FINAL_FAILURE/UNKNOWN remain unchanged.
2. Incident: stable operational snapshot and persisted GitHub failure receipt.
3. Reporting invocation: nonzero for a new/unverified incident, new failed work,
   or any error in alert checking. Zero may mean only already-reported incidents
   remain; it never means those windows became FULL.

The window_report and Actions summary still contain actual failed outcomes.
`scan-report --check` remains a strict, read-only business health check, including
when deduplication is enabled. The explicit `--check --alert-check` reporting path
may write receipts and has the new operational exit semantics. Both workflow
commands use that decision when activated; changing annotations alone would not
stop workflow-failure notifications. There is no blanket continue-on-error.

## Durable evidence, not an in-memory suppression cache

The optional scan_alert_receipts table stores canonical operational snapshots,
SHA256 fingerprints, repository, Actions run ID/attempt, and pending/confirmed
timestamps. It stores no job text, profile data, tokens, headers or API responses.
Only optional alert tables are created; job/window/mail schemas are not altered.

A fingerprint includes slot, outcome, scan/mail state, failure reason, lease-expiry
state, scan owner ID and scan/mail attempt counts. Legacy orphan mail failures
include their persisted delivery-state update timestamp. Durations, retry times
and discovery counts are excluded. New Recovery/delivery attempts, worsening or
expired leases therefore alert again without timestamp-only churn.

A new observation is pending and fails the invocation. It is NOT marked reported
before exit: the process could crash or the workflow could be cancelled. A later
distinct run/attempt confirms the prior run through GitHub's exact-attempt REST
endpoint and jobs endpoint. Identity, main branch, scheduled/manual event, workflow
path, completed failure and failed scan business step must all match. A DB pending
row, unrelated build failure, cancellation, timeout or an API error is not proof.
This proves workflow failure-report eligibility, not delivery to a user's inbox.

Only verified receipts authorize duplicate suppression. Same-run tick/report keep
failing for an unverified incident. Confirmed receipts survive restart. Short DB
transactions and composite primary keys protect concurrent writes; network calls
are outside transactions. Concurrent *unconfirmed* attempts may both fail, by
design: conservative duplicate alerts are safer than hiding an unreported failure.
Existing workflow serialization is unchanged. No exactly-once GitHub email claim.

Lookup is bounded to ten recent receipts per event, three distinct prior attempts
per invocation, a 30-second cooperative verification budget, five-second HTTP
timeouts and 256KiB API response bounds. API errors/budget exhaustion also persist
the current pending observation before failing, avoiding starvation by cancelled
or unreadable old attempts. Schema/version/context/receipt corruption, DB errors,
missing tokens or failed confirmation all fail closed, even without active rows.
Lost receipts cause re-alerting, never silent acknowledgement.

## Separate approval gate: not deployed by this PR

Default: AJOB_SCAN_ALERT_DEDUP is unset/empty/0, preserving existing behavior with
no optional-schema access or GitHub API calls. No repository variable is changed
by this implementation. GitHub Actions/notifications and cron remain enabled.

The additive migration is deliberately outside normal storage migrations:
`src/storage/alert_migrations/001_scan_alert_receipts.sql`. Neither startup nor
`prepare-scan-storage` applies it. Both alert tables enable PostgreSQL RLS, without
changing dashboard permissions or granting access to cloud dashboard roles.

After separate approval, an operator must:

1. Review the migration and retained-state backup; validate it against a disposable
   PostgreSQL instance and confirm the scanner's intended role can access it.
2. Explicitly run `python -m src.main prepare-scan-alert-storage` with the approved
   scanner connection. This PR does NOT run it on Production or change any secret.
3. Verify version=1 and read/write/transaction behavior under the scanner role;
   do not grant dashboard roles receipt access. RLS denial must fail closed.
4. Separately approve setting the repository variable AJOB_SCAN_ALERT_DEDUP to1.
   The existing Actions token gets actions:read only for receipt verification;
   no additional credential or write permission is introduced.
5. Observe the next naturally scheduled wake-up. Existing incidents without
   receipts deliberately fail once; the following completed run can acknowledge
   them. New source/SMTP/lease/recovery failures must continue to fail.

Do not manually pre-confirm historical receipts or run a Production scan to test.
If disabled again (variable0/unset), legacy strict exit semantics return immediately.
Retain optional tables, receipts and all original incident/window history; rollback
does not delete data or replay email. At seven days the existing historical
scan-only aging policy still applies; this feature does not change or erase it.

## Acceptance checks

Synthetic tests cover new/known/repeated events, worsening, failed Recovery,
SMTP failed/uncertain and new delivery attempts, later FULL windows, restart,
parallel connections, missing/corrupt state, DB/API errors, schema/version loss,
cancelled first attempts, rerun attempt identity, budget starvation, read-only
health checks, feature-off behavior and retained history beyond seven days.
An actual tick test confirms a failed recovery alerts again while the business
message is sent only once. PostgreSQL concurrency uses the existing disposable,
loopback-only CI cluster; it never uses DATABASE_URL.

Production acceptance remains gated: verify an incident's original failed run,
subsequent deduplicated wake-up, unchanged window failure, and distinct handling
of the next genuine failure. No real SMTP is used in the test suite.

GitHub API contracts: https://docs.github.com/en/rest/actions/workflow-runs and
https://docs.github.com/en/rest/actions/workflow-jobs .
