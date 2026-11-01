# Cloud data access: direct SQL blocked, Data API proposed

## Evidence and official remedy

Production metadata supplied by the operator reports pg_net 0.20.4, net objects
owned by supabase_admin, PUBLIC schema USAGE/table read-write/function EXECUTE,
RLS off on the queue tables, and a pg_net worker configured as postgres.
No queue payload or secret was read, and no production query was executed by this
change. These capabilities apply to future SQL logins too, regardless of their
otherwise restricted jobs grants. NOINHERIT/search_path/read-only defaults do not
remove PUBLIC privileges.

Supabase explicitly documents that changing managed pg_net grants per project is
not supported. Project postgres revokes can do nothing while SQL Editor hides the
warning. The postgres worker, webhooks and Cron callers can depend on PUBLIC table
grants. A major-version upgrade or revoking only HTTP-function EXECUTE does not
prove isolation: a login with queue INSERT can still queue a request directly.

Official supported choices are: keep direct logins only for trusted network-capable
services and use the Data API for data-only clients; or drop pg_net only if it is
unused. Dropping deletes queued requests/responses and can break webhooks/Cron.
No removal, upgrade, global revoke or key rotation is proposed for this scanner.

References (reviewed 31 October 2026):
- [Managed pg_net grants and supported choices](https://supabase.com/kb/troubleshooting/revoking-access-to-pg_net-objects-has-no-effect)
- [PUBLIC inheritance limitations](https://supabase.com/kb/troubleshooting/custom-role-inherits-privileges-that-were-not-explicitly-granted)
- [Data API security](https://supabase.com/docs/guides/api/securing-your-api)

## Implemented now: fail-closed startup

The dedicated cloud SQL-role check now rejects effective net relation, column,
sequence or function privileges. It uses catalog inspection only, never invokes a
network function or reads queue rows, and handles databases with no net schema.
Tests reproduce the PUBLIC grants with harmless fake objects in a disposable local
PostgreSQL database. Existing desktop/scanner code is unchanged. This is a guard,
not a workaround that makes direct SQL safe on the reported project.

## Recommended architecture — requires approval before implementation

Keep the existing Supabase project and Render Free, but remove SQL credentials
from the public web service entirely:

`Safari -> authenticated AJob -> HTTPS Data API -> narrow ajob_api RPC -> private tables`

- Use a fresh custom **NOLOGIN** API role, never ajob_dashboard/postgres. Grant the
  gateway authenticator permission to assume only that API role as required.
- Render holds a publishable project key and narrowly scoped API authorization;
  **no service_role/secret admin API key, project JWT signing secret/private key,
  management token or PostgreSQL password**. It must not mint arbitrary roles.
- Prefer a dedicated Supabase Auth principal with an owner-ID-bound Custom Access
  Token Hook issuing the custom role. Use Supabase's supported token issuance and
  renewal, not a homemade signature or a globally shared admin key. The hook must
  preserve every other principal's existing claims and must never trust editable
  user_metadata for privileges. Any existing hook/settings must be inventoried
  before approval; this identity configuration has NOT been verified or created.
- The exact server-side API credential/renewal lifecycle is an implementation
  decision: a dedicated Auth credential is different from an SQL password, but is
  still a real secret needing separate approval and safe persistence/rotation.
  Do not introduce it silently or rely on an unexportable signing key.
- Add only a dedicated ajob_api exposed schema. Keep ajob_private, net, cron,
  vault and internal helpers unexposed; preserve legitimate existing API settings.
- Audit the custom role against the ENTIRE exposed API surface, including public,
  not just the new schema. A client-chosen profile header is not authorization.
- Expose only typed operations: paged job reads, status updates, note operations,
  atomic dismiss/restore, and bounded session/throttle operations. No arbitrary
  SQL, table/column/function names, URLs, generic database proxy or network calls.
- RLS and RPC authorization enforce the API role and approved subject. Use fixed
  SQL, a controlled search_path and narrowly privileged NOLOGIN function owners
  where SECURITY DEFINER is necessary. Revoke function EXECUTE from PUBLIC/anon
  on these NEW functions; grant only the custom caller. Existing roles stay intact.
- A queue-capable role inside PostgreSQL is not a public raw-SQL interface: the
  boundary here is authenticated PostgREST's exposed surface plus audited RPC.
  If net or a generic SQL/privileged wrapper can be reached through that surface,
  activation remains blocked.
- Preserve the existing browser Host/Origin/CSRF, single-owner login/session expiry
  and logout behavior. RPC transactions must preserve current atomic undo and
  global throttle/session limits. Do not replace an atomic operation with separate
  HTTP read/write requests or automatically retry uncertain mutation results.
- Start with empty notes; jobs/statuses stay in the same database. No scanner,
  Cron, SMTP or pg_net changes. Free-tier quotas still apply; account-specific
  availability is unverified, and no additional resource has been created.

Custom Access Token hooks are listed in the Free plan, but actual configuration,
Auth rate limits and existing usage must be checked before activation:
[Auth hook](https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook),
[signing-key limitations](https://supabase.com/docs/guides/auth/signing-keys),
[pricing](https://supabase.com/pricing).

Alternative: an isolated database/project without pg_net avoids that permission
domain, but needs a reviewed jobs/status synchronization design and quota/account
approval. It adds complexity and is not the first recommendation. Do not change
scanner writes to implement it during this work.

## Staged execution, tests and rollback

1. **Safe now:** merge only the guard if separately approved; prepare an inactive
   API-adapter/RPC/auth design in a new PR after the architectural choice is
   approved. Test against mocks and disposable PostgreSQL/PostgREST; no production
   roles, secrets, Auth settings, exposed schemas or service creation.
2. **Before any production change:** read-only inventory of exposed schemas,
   callable RPC, API/authenticator roles and existing Auth hook. Capture protected
   backup and grants/policy/settings baseline; prove restore in isolation. Do not
   display signing keys or function bodies containing secrets.
3. **Before activation:** integration tests for unauthorized/expired/wrong-role
   tokens; net/cron/vault/other schemas and generic RPC denial; no SQL login; no
   escalation via forged claims/profile headers/user_metadata; session rotation,
   cold-start renewal, logout, CSRF and failed/uncertain requests; note persistence;
   atomic concurrent status/undo/throttle; complete pagination and safe HTTP errors.
4. **After explicit role/schema/Auth approval:** provision only the reviewed new
   API objects/principal/grants, preserve old roles and scanner policies, add the
   approved schema without exposing internal schemas, then verify the actual
   signed token's permissions over HTTPS. A publishable key alone is not auth.
5. **After separate deploy approval:** configure only the narrowed API identity on
   Render, leave SQL mode/credentials absent, start empty notes, and verify real
   Safari, idle wake-up and an explicitly approved write/undo smoke test. Compare
   scanner/delivery health before and after without dispatching a scan.
6. **Rollback:** disable the web service and revoke only its API authorization;
   preserve notes/status/audit data. Restore only newly changed API/hook settings
   from the baseline, preserving other callers. Keep pg_net/PUBLIC/scanner roles
   unchanged; never fall back to the unsafe SQL login or replay mail/outbox state.

Readiness: **NOT READY for the existing direct-SQL deployment**. The recommended
Data API architecture is a proposal, not an implemented or tested production
access path. Approve that architectural direction before building its adapter and
identity integration; no further net ownership query is needed to justify it.
