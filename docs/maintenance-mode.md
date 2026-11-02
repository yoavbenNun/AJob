# AJob maintenance mode

Product decision, 2 November 2026: freeze the iPhone/cloud dashboard indefinitely.
Maintain the existing job-search system; do not add features to resume Cloud work.

## Active system

- Windows shortcut → the isolated published runtime → `src.web.app:app`, bound
  only to `127.0.0.1:8001`. Keep local notes, resumes, settings and status data.
- The updater selects only main commits with successful CI/Security, stages and
  probes a candidate, and retains its previous version on failure/offline use.
  It does not switch to `src.web.cloud`, create services or run Cloud migrations.
- Existing scheduled cloud **scanners** and mail delivery remain active. This
  dashboard freeze does not disable scanners, SMTP, Cron or the durable outbox.

## Frozen system and evidence limits

- Cloud is disabled without explicit `AJOB_CLOUD_ENABLED=1`; the retained Render
  example now sets `0` and keeps automatic deployment off. Cloud schema scripts
  remain manual and are not invoked by launcher/application startup. No approved
  dashboard service has been deployed in this work.
- Keep the Data API adapters, SQL scripts, Docker configuration and tests for
  future review. Their presence in main does not activate them.
- The last user-supplied production metadata shows no AJob Data API roles,
  ajob_api/ajob_private schemas or private tables. This is a dated observation,
  not live inventory of Supabase or Render.
- Supabase's pre-existing platform Data API may remain enabled; it is distinct
  from the unprovisioned AJob dashboard adapter. Do not change platform settings
  or the existing `ajob_dashboard` SQL role as part of this freeze.
- Local startup has no new Cloud dependency or required Cloud credential. The
  Local entry point and its published dependency set remain unchanged.

## Any future activation needs new approval

Previous development, merge and preflight approvals are not deployment approval.
Resume only after a new product decision and explicit authorization for each
production identity/grant/schema/secret/data-transfer/deployment operation.
Recheck managed Auth-schema grantability, all REST/GraphQL surfaces, real
provider-issued identity, backup/isolated restoration and physical Safari.
Do not treat prior synthetic tests as proof of managed-service readiness.

Until then, no Render service creation/deployment, production Cloud migrations,
new Cloud credentials, personal-data transfer or automatic activation. Preserve
the implementation, branches, private local work and audit evidence; no rollback
or deletion is required to operate the existing Local Dashboard.
