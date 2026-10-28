# Conservative job closure review

Scheduled scans now emit one `closure_review` JSON event after collection and
persistence. The default is **report_only**: no job updates, DDL, audit inserts,
extra HTTP requests, email or notification mutations. Partial/failed scans defer
the entire review. Recovery may lack earlier snapshots; those jobs remain UNKNOWN.

## Evidence and limits

- Direct Greenhouse boards: reuse the successful public complete-board response
  before role filtering. Validate every listing, identity and advertised total;
  reject empty, malformed or explicitly paginated responses. Match the original
  board and stable ATS ID, including custom employer URLs with `gh_jid`.
  The [official Job Board API](https://docs.greenhouse.io/job-board.html#list-jobs)
  documents this endpoint as the list of all published job posts.
- Elbit: reuse the successful complete official jobs feed, with validated active
  integer job IDs and no unknown status values or invalid listings. Match `jid`,
  independent of URL slash changes. No staff metadata is retained.
- Presence means OPEN; absence from these validated, nonempty complete snapshots
  means CLOSED. A changed title/URL with the same ATS ID remains OPEN. Unknown
  identity/source, failed access, parser errors and missing snapshots mean UNKNOWN.
- Workday, Amazon, Fives/FIL, other APIs and custom HTML currently remain UNKNOWN:
  their keyword/location/page-limited collections do not prove closure. No 404,
  403, 429, CAPTCHA, timeout, redirect or 5xx alone causes dismissal.
- No detail HTTP calls, extra concurrency, rate-limit retries or new services.
  The local review has at most five seconds **inside** the existing scan budget
  and 1,000 jobs. Exhaustion/cancellation reports `deferred`. Database statement
  and lock timeouts remain in force; an in-flight statement may finish after the
  local deadline. Delivery keeps its existing reserved budget.

Metrics: jobs_checked/open/closed/unknown, jobs_would_dismiss,
jobs_auto_dismissed, jobs_skipped_due_to_manual_status, checks_failed,
check_duration, supported_sources (validated snapshots available this run), deferred.
Logs contain aggregate counts only. CLOSED is evidence of listing closure, not a
change to technical fit or personal eligibility.

## Separate activation, after report review

Do **not** enable automatically on release. Review scheduled dry-run counts and
official evidence first. Separately approve/apply migration
`003_job_closure_audit.sql` and explicitly set `AJOB_AUTO_DISMISS_CLOSED=true` in
the scanner runtime. No workflow, secret or Production migration is changed by
this release. Without that exact value, status writes remain disabled. Without
the optional table, an enabled review fails safely and reports checks_failed.

When enabled, only new/reviewed can become ignored, using a compare-and-set
transaction with a unique audit row containing previous status, timestamp and
evidence type. Applied/rejected/ignored and all other job fields are preserved.
Upsert already preserves status. Personal Notes are never accessed by the worker.

**Restore policy after automatic dismissal:** automatic dismissal is once per stable database job ID. Keep
the audit row forever; after the user restores a job, later reviews report its
official closure but never auto-dismiss it again. Manual dismissals remain
ordinary ignored rows without an automatic audit entry. This intentionally
favors user control over repeated automatic hiding. Existing Restore/Undo and
filter behavior are unchanged; the audit preserves the pre-dismissal status for
operators without changing dashboard UI.

Rollback: unset the flag to stop writes; retain audit rows, jobs and outbox.
Do not bulk restore ignored rows (some are manual). Review only recorded automatic
dismissals and restore individually with expected-status guards if needed.
