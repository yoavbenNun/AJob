# Historical relevance recovery

Recovery is a manual, bounded review of explicitly chosen stored job IDs. It
changes only the automated professional-relevance flag, never the job's status,
contents, notes or notification records. It does not send or enqueue email.
Existing pending notifications remain governed by the normal delivery process.

First select the target explicitly and preview up to 100 IDs. For SQLite, ensure
`DATABASE_URL` is unset; for PostgreSQL, use the approved database connection in
the environment. These commands do not initialize schemas:

```text
python -m src.main recover-relevance --target sqlite --database data/jobs.db --job-id 21
python -m src.main recover-relevance --target postgres --job-id 21 --resume path/to/resume.pdf
```

Review the professional decision, location and eligibility for each entry. When
no resume is supplied, eligibility is **not assessed**. Unknown locations remain
reviewable; jobs clearly outside Israel, explicitly ineligible jobs, and jobs
with ignored, applied or rejected status are not recovery candidates. A job's
visibility and recommendations still depend on the existing dashboard filters,
matching and delivery rules. This is not a user override: a later scan can
update automated relevance based on its evidence.

Only after the operator has reviewed the plan and separately approved production
changes, rerun the exact command with `--apply --approve <approval_digest>` from
the dry-run output. The digest binds the target and job snapshots; changed plans
are rejected. A conditional update skips a job if its status or source fields
change during application. Each record is committed separately; if an error
interrupts the operation, preview again before deciding whether to continue.
No recovery email is sent: any historical re-notification requires a separate
design and authorization.
