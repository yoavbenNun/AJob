# Pre-deployment review: overnight candidate

Date: 2026-10-18 (Asia/Jerusalem). No production change or deployment performed.

## Findings and resolution

- **High: old email path could bypass managed leases.** New legacy-email entry
  point now refuses execution once scan_windows is populated. Regression confirms
  no scan or SMTP call occurs. Historical binaries cannot inherit this guard;
  mixed live versions remain prohibited and are a hard rollout/rollback gate.
- **High: schema compatibility is not delivery compatibility.** Tested unchanged
  legacy repository code against the new schema: jobs/status and pending outbox
  survive, but the old independent delivery ledger can claim a slot and cannot
  drain the new queue. Never re-enable an old sender to finish pending mail.
- **Readiness fix:** invalid connection configuration in scheduled CLI operations
  is handled by the existing sanitized error path rather than escaping before it.
- **Backup/restore:** synthetic pending and uncertain outbox snapshots survive a
  pg_dump/pg_restore round trip; restored uncertain deliveries remain unclaimable.
- New worker + legacy schema fails without DDL/data loss. New schema + legacy
  storage remains structurally usable, subject to the semantic limitation above.

## Read-only production evidence and gaps

Supabase MCP failed OAuth refresh before any SQL executed. Production schema,
RLS/grants, actual scanner/dashboard roles, connection/pooler/TLS and current
pg_cron function/job remain unverified. Reconnect privately in the morning; no
credential or production configuration was read, printed or changed.

GitHub metadata reports repository visibility **Public**. No visibility change
was performed. The private-only push authorization therefore does not apply and
no push will be made. main ref matched 508636f6c433c3a51af753b160d10e14335a63ba.
At the read-only snapshot there were zero queued and zero in-progress Actions
runs. This does not prove later quiescence; check again during approved rollout.

CI/security push jobs run tests/audits and a dry-run container. More importantly,
publishing changed workflow code to default branch main changes future cron
behavior even if no production on:push job exists. Pause triggers before merging
or deploying. No historical run-log rescan was performed.

CI now provisions a disposable pinned PostgreSQL service for integration tests,
so those tests no longer silently skip in the normal CI test job. It has no
production DATABASE_URL or SMTP secrets. The CI definition is prepared locally;
GitHub has not executed the changed workflow because nothing was pushed.

## Morning actions

1. Restore Supabase MCP authentication and complete read-only schema/role/pooler/
   cron verification. Resolve any mismatch before applying migrations.
2. Review the existing Public visibility and explicitly approve any publication;
   no visibility change is needed or performed automatically.
3. Approve one coordinated maintenance/deployment window: pause/drain, verified
   backup, schema/code, existing dispatcher update, controlled first scan/mail,
   then re-enable and observe. Secrets/permissions changes are not preauthorized.
4. Follow [deployment-runbook.md](deployment-runbook.md). Retain pending/uncertain
   data throughout rollback. Seven-day acceptance still requires real observation.


## Final validation and handoff

- Full isolated suite: **375 passed, 2 skipped**, one existing upstream
  Starlette/httpx deprecation warning. Explicit local PostgreSQL 17.11 tests and
  the pg_dump/pg_restore drill ran; no production configuration was inherited.
- Five focused overnight compatibility/backup/guard regressions passed before
  the final suite. Prior round-2 PgBouncer checks remain applicable; they were not
  repeated without a transport change in this overnight review.
- Offline working-tree secret scan: **118 files, zero findings**. No historical
  log/history re-audit, dependency change or external secret verification.
- Workflow YAML parsed successfully; scheduled scan has only schedule/manual
  triggers, CI's PostgreSQL is a disposable loopback service without cloud secrets.
- Candidate is retained locally with a focused commit; no push or production
  operation is performed. AGENTS.md remains uncommitted and unchanged by this task.
- Primary rollout blockers are missing Supabase read-only verification and the
  public/private publication mismatch, plus explicit approval of production steps.
