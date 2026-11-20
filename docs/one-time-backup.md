# One-time encrypted AJob backup

Manual procedure only; not yet executed or certified against Production.
No scanner, SMTP, migrations, roles or grants are changed.

## Scope and prerequisites

The existing DATABASE_URL must reach PostgreSQL from GitHub's IPv4 runner.
Supabase recommends the **session pooler** for hosted Actions where the direct
endpoint is IPv6-only. Do not silently replace the secret or use transaction
pooling; report an incompatible existing connection as a blocker.

The connection needs complete SELECT access and ability to bypass RLS for every
application table. pg_dump's default row_security=off fails rather than accepting
an RLS-filtered export. PostgreSQL 17 tools cannot dump newer major servers.
If denied, do not create users, grant permissions, omit tables or retry with
--enable-row-security: stop and report the blocker.

The URI is passed explicitly with --dbname inside each container; PGDATABASE is
not used to carry a URI. Inventory runs BEGIN READ ONLY, independent of pooler
startup options. No PGOPTIONS read-only guarantee is assumed. The URI exists in
the private client process arguments inside the ephemeral runner container, not
host Docker arguments; shell tracing and raw client stderr remain disabled.
Do not enable debug tracing or collect process listings.

Preflight requires eight core scan/job/notification tables. The closure audit
table job_closure_dismissals and alert receipt tables are optional, not inventory
requirements. Schema-wide pg_dump still includes them whenever present, and fails
if any included table cannot be read. A connection/query failure is distinguished
from a missing/inaccessible mandatory inventory without printing raw SQL errors.

One custom-format pg_dump transaction includes schema, data and sequences from
public and all ajob_* schemas, including job history, scan runs/source metrics,
windows, delivery leases, dispatcher state, notification outbox/batches and optional
alert receipts. Optional application-private schemas are included if present.
This is an application backup, **not a whole Supabase project backup**: managed
auth/storage schemas, roles/grants, pg_cron/Vault settings, external files,
local SQLite/notes/resumes and GitHub secrets are outside scope. If the actual
installation stores AJob state outside those schemas, stop before execution and
review scope. No auto-expansion into managed Supabase internals.

## Key and manual run (after merge and explicit approval)

Install age locally. On a trusted machine, generate the recovery key:

```sh
age-keygen -o ajob-recovery.key
age-keygen -y ajob-recovery.key
```

Store the private key securely offline/separately from the backup; do not upload,
commit or put it in GitHub. Only the printed age1 public recipient goes to Actions.
Losing the private key makes recovery impossible.

1. Actions → **One-time encrypted AJob backup** → Run workflow → main.
2. Paste the public recipient. Never paste the private key or DATABASE_URL.
3. Require a successful run. Download its ajob-encrypted-backup-RUN_ID artifact,
   unzip locally into a private directory. The artifact expires after one day;
   delete it after successful transfer. Artifact limits/storage quotas still apply.
4. Verify transfer with `sha256sum -c database.dump.age.sha256`.
   This checksum detects transfer errors; age authenticates ciphertext on decryption.
5. Keep only encrypted backup copies and the separately held recovery key.

The dump streams straight into age; there is no plaintext dump file on the runner.
Dump stderr is suppressed to prevent leaking values; failure logs intentionally
report only a sanitized blocker. Do not enable shell tracing or Actions debug.

## Isolated Docker restoration check

Use a trusted local machine with Docker and age. Bash commands below never connect
to Production. No host port is published; the container has no network access.
Choose a fresh container name if ajob-restore-check already exists.

```sh
docker run -d --name ajob-restore-check --network none \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=ajob_restore postgres:17-alpine
until docker exec ajob-restore-check pg_isready -U postgres -d ajob_restore; do sleep 1; done
set -o pipefail
age -d -i ajob-recovery.key database.dump.age | \
  docker exec -i ajob-restore-check pg_restore -U postgres -d ajob_restore \
    --no-owner --no-acl --clean --if-exists --single-transaction --exit-on-error
docker exec ajob-restore-check psql -U postgres -d ajob_restore -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) FROM public.job_postings;" \
  -c "SELECT status,count(*) FROM public.scan_windows GROUP BY status;" \
  -c "SELECT state,count(*) FROM public.notification_batches GROUP BY state;" \
  -c "SELECT count(*) FROM public.notification_outbox;"
```

Check restored table inventory (psql \dt *.*), optional receipt/private tables,
sequence values and expected recent windows without printing personal rows.
A failed restore is NOT a verified backup. Application RLS policies/functions can
reference pre-existing roles or Supabase extensions outside this archive. If those
dependencies are missing, stop: do not ignore restore errors or claim success.
Review minimal disposable-only prerequisites separately; never recreate privileges
in Production. Original ownership/ACLs are intentionally not restored.

Do not start AJob, dispatchers or senders against the test database.
After inspection, `docker rm -f -v ajob-restore-check` removes ONLY this disposable
container and its test volume. Do not use that name for anything else.
Restoring to Production is a separate, unapproved operation.

Reference: [Supabase GitHub Actions backup guidance](https://supabase.com/docs/guides/deployment/ci/backups).
We follow its connection and restore-validation cautions, but intentionally use
PG17 custom dumps, public-key encryption and temporary artifacts instead of
committing plaintext SQL or enabling automatic triggers.
