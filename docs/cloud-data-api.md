# Cloud Data API — implementation and activation gates

This is an inactive implementation, not a deployed service. It builds on PR #24's
direct-SQL guard; neither PR is permission to activate production.

## Identity and isolation

Safari uses the existing AJob password login, secure session cookie and CSRF
token. The server separately signs in a dedicated Supabase Auth machine identity
over HTTPS. A Custom Access Token Hook maps **only its approved immutable UUID**
to `ajob_cloud_api`; editable user metadata never grants privileges. All other
users retain their existing claims. Supabase issues/signs the JWT. The client
first validates it through Auth `/user`, then checks subject, issuer, audience,
role and expiry. PostgREST independently verifies the signature before running
the request. Expiring tokens are replaced by a bounded password sign-in, under
a process lock; cold starts need no writable local refresh-token file.

The machine password is a real, narrow application secret. It authorizes this
owner's dashboard operations, not arbitrary SQL or arbitrary-role JWT issuance.
Compromise still exposes dashboard data and status/notes/session operations;
protect and rotate it accordingly. Browser authentication is a separate layer.
Neither publishable key nor an unverified decoded JWT authenticates the owner.

Render receives **no SQL connection/password, service_role/admin key, management
token or JWT signing key**. `ajob_cloud_api` and the RPC executor are NOLOGIN.
Typed RPCs in `ajob_api` read paged jobs, update only status, save plain-text notes,
atomically dismiss/restore, and maintain durable sessions/throttling. No generic
SQL, table name, network URL or function-name proxy exists. The executor has
minimal jobs/private-table permissions; RLS and explicit subject/role checks
apply. This is deliberately a single-owner project: other identities cannot
read or mutate its shared jobs, even if they obtain the caller role.

PUBLIC's managed `net` privileges remain unchanged. Safety depends on the
**entire** exposed Data API surface: `net`, `vault`, `cron`, `ajob_private`, scanner
and outbox tables must be unexposed, and no existing exposed privileged/generic
RPC may be executable by this role. An added schema alone does not prove that.
The manual script does not change managed extensions or existing role settings.

Supported mechanisms:
- [Supabase's recommendation for data-only clients with pg_net](https://supabase.com/kb/troubleshooting/revoking-access-to-pg_net-objects-has-no-effect)
- [Custom Access Token Hook](https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook)
- [Server-verified Auth user](https://supabase.com/docs/reference/python/auth-getuser)
- [PostgREST JWT roles](https://docs.postgrest.org/en/stable/references/auth.html)

## Configuration names, no real values

`AJOB_CLOUD_ENABLED=1`, `AJOB_CLOUD_BACKEND=data_api`,
`AJOB_PUBLIC_ORIGIN` (exact Render HTTPS origin), `AJOB_OWNER_PASSWORD_HASH`;
`AJOB_SUPABASE_URL`, `AJOB_SUPABASE_PUBLISHABLE_KEY`,
`AJOB_SUPABASE_AUTH_EMAIL`, `AJOB_SUPABASE_AUTH_PASSWORD`,
`AJOB_SUPABASE_AUTH_SUBJECT` (approved UUID).

Absent cloud opt-in, the public factory remains disabled. The desktop factory
does not use these adapters. API mode rejects known SQL/admin secret variables;
the operator must also inventory Render's complete environment rather than rely
on a finite denylist. Legacy `direct_sql` is explicit, retains PR #24's checks,
and is **not an activation option on the reported production project**.

RPC transport uses verified TLS, one fixed Supabase project host, no redirects or
environment proxies, bounded timeouts and response sizes, and generic errors.
Mutations are never automatically replayed after timeout/401: a write may have
committed before the connection failed. Refresh and reconcile the visible state
before retrying. Notes drafts remain in the browser after failed saves.

## Manual deployment sequence — separate approval required

1. Read-only inventory: exposed schemas, every callable RPC and effective grants,
   existing Auth hooks, owner UUID/registration settings, project quotas and
   baseline scanner health. Stop if another application hook would be replaced
   or an exposed generic/privileged RPC remains reachable.
2. Protect an off-repository backup and prove restoration in disposable PostgreSQL.
   Capture grants, policies, API/hook settings separately. Do not replay outbox
   state or replace live jobs while restoring a dashboard-only change.
3. After explicit schema/role approval, review and run `deployment/data-api.sql`
   manually. It is never called at startup, by the scanner or by a deployment.
   Review collision guards and exact approved Auth UUID provisioning first.
   This standalone bootstrap does not require the legacy direct-SQL dashboard
   migration; do not run it merely to grant the existing desktop role new rights.
4. After separate identity/settings approval, create a dedicated Auth identity,
   configure the hook without overwriting an existing hook, and expose `ajob_api`
   while preserving required existing schemas. Audit their complete callable
   surface. Do not expose internal schemas or grant direct SQL LOGIN.
5. Test a real provider-issued token: wrong subject/role/expired/forged JWT,
   anonymous calls, schema overrides, direct tables and net/Vault/Cron/outbox
   denial. Confirm the actual hook, `/user` validation and gateway behavior;
   synthetic tests do not substitute for this activation gate.
6. After separate secret/deploy approval, configure only the listed settings on
   Render Free and deploy manually. Start with **empty cloud notes/undo**; leave
   local SQLite and resumes untouched. Jobs/statuses stay in their existing DB.
7. Approved test write/undo, logout/expiry, Safari and idle wake-up; compare scanner
   health read-only. Never dispatch a scan or email as a dashboard smoke test.

Rollback: disable the Render service and revoke only the new identity/API
authorization; preserve notes and status data. Restore changed hook/API settings
from the captured baseline. Do not drop private tables, alter managed `net`,
replay notifications, or fall back to the unsafe SQL login. A schema rollback
must be a separately reviewed operation after stopping all dashboard writers.

## Evidence and limits

Unit/integration suites use synthetic identities and disposable stores. The
required CI gateway suite runs real PostgreSQL 17 and pinned PostgREST with
test-only signing keys; it checks signature/expiry, roles, RLS, denied schemas,
atomicity and concurrency. Those keys never enter runtime code or configuration.
Auth `/token` and `/user` responses are mocked; no live GoTrue/Supabase project,
production policy, Auth-hook configuration, provider quota or Render cold start
is claimed as verified. Activation is blocked until step 5 passes in the approved
environment. No migration, service, real credential or personal-data transfer is
part of this PR.
