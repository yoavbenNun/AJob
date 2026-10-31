# Private cloud notes and undo

The dashboard PostgreSQL adapters use `ajob_private.personal_notes` and
`ajob_private.job_dismissal_history`. Scanner startup never creates these tables.
Dashboard startup only selects required columns, and fails if the manual schema
or read permissions are missing. Cloud dismissal locks the job row and commits
the previous status and new status in one transaction; restore consumes undo
metadata in that same transaction. Repeated dismiss/restore requests are no-ops.

## Deployment preparation (manual; not performed by this change)

1. Back up the job database and local private database. Confirm a local restore.
2. Review `scripts/manual_cloud_private.sql`, then apply it as the trusted schema
   owner. Coordinate it with `deployment/cloud-auth.sql`; both use `ajob_private`
   and the restricted `ajob_dashboard` group role.
3. Provision a dedicated PostgreSQL login with membership in `ajob_dashboard`.
   It must not own schemas/tables, have BYPASSRLS/superuser privileges, or inherit
   any administrative/scanner/API roles. Do not reuse scanner credentials.
4. Keep `ajob_private` outside the platform's API-exposed schema list. The script
   revokes PUBLIC and common Supabase API role grants and enables forced RLS.
   It grants the dashboard private-table CRUD, job SELECT and UPDATE(status).
   Dashboard-specific job SELECT/UPDATE policies support an existing RLS-enabled
   jobs table; its RLS setting and other roles' policies remain unchanged.
   Verify anonymous/API/scanner logins cannot read private rows. Administrators
   and superusers remain trusted and can bypass these boundaries.
5. Test persistence, concurrent dismiss/restore and rollback with synthetic data
   and the actual restricted login. Apply cloud data migration only after separate
   review and authorization. Restart the app to run its read-only preflight.

The RLS policies authorize the single owner's server role, not individual browser
users. Application authentication remains required. These policies are not a
multi-tenant ownership system.

## Offline migration rehearsal

`scripts/migrate_private_data.py` never reads cloud configuration or connects to
any network. Run it with the project's Python environment. All destinations must
be new paths; it refuses to overwrite existing files.

```text
python scripts/migrate_private_data.py backup PATH_TO_PRIVATE.db NEW_BACKUP.db
python scripts/migrate_private_data.py restore NEW_BACKUP.db NEW_RESTORE.db
python scripts/migrate_private_data.py dry-run --jobs LOCAL_JOBS.db --notes LOCAL_PRIVATE.db --target-snapshot TARGET_JOBS.json --output PRIVATE_PLAN.json
python scripts/migrate_private_data.py dry-run --source-snapshot SOURCE_JOBS.json --notes LOCAL_PRIVATE.db --target-snapshot TARGET_JOBS.json --output PRIVATE_PLAN.json
```

Choose the source that actually backs the desktop dashboard. When it uses
PostgreSQL, use a reviewed `SOURCE_JOBS.json` export from that database, not a
possibly stale `data/jobs.db`. The tool never connects to PostgreSQL. Source and
target snapshots are local JSON lists of integer IDs and canonical posting URLs;
obtain any real export only through an approved read-only path and keep it private.
The CLI requires exactly one source and refuses conflicting identity maps.

The target snapshot is a reviewed local JSON export with only target job IDs and
URLs: `[{"id":42,"job_url":"https://example.test/job"}]`. Source and target IDs
need not agree. The planner uses canonical URL identity; duplicates, orphaned
private records, missing target jobs and invalid private values abort the plan.
It never merges jobs, updates statuses, deletes records, or imports cloud data.

The output contains the exact note and undo values plus reviewed ID mapping; it
is sensitive. Keep it and all database backups outside the repository, logs,
container build context and public artifact directories. Console output contains
counts only. Review a plan privately before a separately authorized transactional
import. Preserve target private records and stop on conflicting existing values;
never blindly upsert an old snapshot over newer notes. Recheck target URL identity
and lifecycle state immediately before any later import. A generated plan is not
permission to upload personal data.

Backups use SQLite's online backup API, include committed WAL content and pass
`integrity_check`. Restore creates a separate database; selecting it as the active
application database is an explicit operator action. This tooling does not restore
or roll back the shared cloud job database.

## Verification

`tests/test_cloud_private_storage.py` uses synthetic SQLite fixtures to verify
validation, stable mapping, safe backup/restore and transactional failure paths.
These tests do not prove PostgreSQL locking, permissions or RLS.

`tests/test_cloud_private_postgres.py` is opt-in through
`AJOB_TEST_POSTGRES_DSN`, pointing only to a dedicated disposable loopback test
cluster whose administrator can create databases and roles. It creates a random
test database and random NOLOGIN roles, runs the manual migration there, verifies
restricted persistence, denial, RLS, concurrency and rollback, then removes only
its own database and roles. It never uses `DATABASE_URL`. Docker must already be
running, or the operator must supply another isolated local test cluster.
