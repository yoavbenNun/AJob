# Independent cloud dispatch (round 2, deployment pending)

GitHub cron creation can be delayed for hours. Reuse the existing Supabase
pg_cron/http/Vault dispatcher as the primary due-work watchdog, with GitHub cron as
backup. `supabase-dispatch.sql` is a reviewed deployment template only; this round
does not apply it or change cloud configuration.

The watchdog checks every five minutes but calls GitHub only for actionable DB
work, with healthy-lease suppression and dispatch cooldown/error backoff. It seeds
three canonical Israel windows daily and dispatches a generic wake-up. The worker
selects current windows and pending deliveries; a delayed event cannot replay an
old window. The existing named cron job is replaced in place, not duplicated.

Before an approved rollout:

1. Pause triggers, drain old workers and back up the DB. Apply 001 and 002 locally
   in a disposable clone, then apply production only with explicit approval.
2. Deploy the round-2 workflow/code before replacing the dispatch function.
3. Reuse the existing fine-grained token restricted to `yoavbenNun/AJob` with
   **Actions: write**, stored in Vault as `ajob_github_dispatch_token`. Never place
   its value in SQL, source, command arguments, logs or chat. No new permission or
   secret is required. The documented existing expiry is 2027-01-15; verify it
   privately before rollout and arrange renewal separately.
4. Review/run the template as the approved owner using the existing pg_cron/http
   extensions. It uses synchronous http, not pg_net's credential-bearing queue.
5. Inspect cron.job_run_details **and dispatcher_state**. HTTP rejection is stored
   as an error code with backoff; a technically successful cron invocation is not
   proof of dispatch, execution, scanning or inbox delivery. Run `scan-report` and
   verify expected DB outcomes and actual email receipt.

To pause this dispatcher without changing GitHub fallback, an authorized operator
can run `select cron.unschedule('ajob-cloud-dispatch');`. For rollback pause both
trigger sources, retain outbox/leases and reconcile uncertain delivery first.

See [round-2 design, rollout and seven-day acceptance](round2-reliability.md).
The watchdog reuses existing infrastructure; no extra cloud service or paid tier
is introduced. Actions/DB usage remains metered under the existing plans. GitHub
API and runner availability still limit timeliness; no hard delivery SLA is claimed.
