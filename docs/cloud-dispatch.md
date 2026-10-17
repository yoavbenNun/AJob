# Independent cloud dispatch

GitHub scheduled runs can be created hours late. Supabase `pg_cron` can dispatch
the existing GitHub workflow without waiting for GitHub's cron service. This
still depends on Supabase availability, GitHub API availability, and runner
capacity; dispatch acceptance is not proof of scan completion or email receipt.

## Deployment

1. Deploy the workflow and scheduling helper on `main` first.
2. Create a fine-grained GitHub token restricted to `yoavbenNun/AJob`, granting
   repository **Actions: write**, with an expiry and renewal reminder. No source
   code write permission is needed. Store it using Supabase Vault's UI under
   `ajob_github_dispatch_token`; never paste it into a committed SQL file or chat.
3. Review and execute `supabase-dispatch.sql` as the database administrator. It
   installs one named job and restricts the dispatch function to its owner.
   Keep access to Vault and `net` request tables administrator-only: pending HTTP
   requests contain an Authorization header.
4. Verify `cron.job` contains `ajob-cloud-dispatch`. At the next intended time,
   inspect `cron.job_run_details` and `net._http_response` for a successful
   dispatch (GitHub returns HTTP 204), then inspect the matching Actions run and
   the existing notification delivery ledger. Verify the actual email arrives.
   A token expiry, 401/403, or a paused project must be investigated explicitly.

The intended Israel scan slots are 08:17, 11:17, and 16:17, with attempts at
:27, :37, :47, and :57 in the same hour. UTC candidate hours cover both Israel
offsets; the SQL function gates on `Asia/Jerusalem`. Every attempt sends the
same canonical slot string as GitHub's original cron, so the persistent delivery
ledger suppresses duplicate sends. The workflow rejects noncanonical, future,
or older-than-24-hour supplied slots. Manual dispatch with no input retains its
existing immediate-scan behavior and does not participate in scheduled deduplication.

GitHub cron remains as a fallback. The workflow concurrency queue and delivery
claims still apply. Retries are bounded; a failing scan that occupies all retry
attempts can exhaust the window. SMTP is at-least-once, so an ambiguous connection
failure after acceptance may still result in duplicate mail.

To disable this additional dispatcher without affecting GitHub's fallback:

```sql
select cron.unschedule('ajob-cloud-dispatch');
```

References: [Supabase Cron](https://supabase.com/docs/guides/cron),
[Vault](https://supabase.com/docs/guides/database/vault),
[pg_net](https://supabase.com/docs/guides/database/extensions/pg_net), and
[GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
