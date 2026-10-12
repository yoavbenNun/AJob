# Cloud Roadmap

The matching engine does not need an external AI API. It is deterministic Python
code and can run on a laptop, server, scheduled container, or behind a future API.

## Recommended first deployment

1. Package the scanner and Chromium dependencies in a container.
2. Run it on a schedule while the personal computer is off.
3. Store jobs on persistent storage; use managed PostgreSQL if the platform has an
   ephemeral filesystem, or SQLite on a single persistent VM. For Supabase, keep
   the full connection string in a secret store and include `sslmode=require`.
   The PostgreSQL adapter uses a bounded connection timeout and does not perform
   destructive URL-deduplication migrations.
4. SMTP email notifications are available. The optional privacy-reduced
   `CANDIDATE_PROFILE_JSON` secret enables scoring and commute filtering without
   uploading the original resume or contact details.
5. Full resume parsing and approved DOCX generation remain local by default.

## Deferred until needed

- Public dashboard hosting and authentication (the current dashboard is local-only)
- Resume upload or cloud storage
- External LLM provider
- Multi-user accounts
- Queues or Kubernetes
- Automatic application submission

## GitHub Actions notes

The scheduled workflow requires the `DATABASE_URL` repository secret so a hosted
runner cannot fall back to an ephemeral SQLite database. GitHub Actions evaluates
scheduled cron in UTC; it does not support a `timezone` property for a schedule.
The included `05:17 UTC` schedule lands at 07:17 or 08:17 in Israel as daylight
saving time changes. The concurrency group prevents overlapping scans from
competing for the same database rows.

An LLM can later be an optional adapter for better wording. Deterministic matching
must remain available as the private, free, testable baseline.
