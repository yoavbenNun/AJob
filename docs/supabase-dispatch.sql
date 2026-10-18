-- Review/deploy only after 001 and 002 and the round-2 workflow are deployed.
-- Reuses the EXISTING Supabase pg_cron/http/Vault and repository-scoped token.
create extension if not exists pg_cron;
create extension if not exists http with schema extensions;

create or replace function public.dispatch_ajob_scan()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    clock_now timestamptz := current_timestamp;
    local_today date := (clock_now at time zone 'Asia/Jerusalem')::date;
    scheduled timestamptz;
    intended_slot text;
    dispatch_token text;
    response extensions.http_response;
    dispatch_error text := '';
    http_status bigint := 0;
begin
    -- A short transaction lock prevents two watchdogs dispatching together.
    if not pg_catalog.pg_try_advisory_xact_lock(714021886) then return null; end if;
    for scheduled in
        select (local_today + make_time(hour,17,0)) at time zone 'Asia/Jerusalem'
        from unnest(array[8,11,16]) as hour
    loop
        intended_slot := to_char(scheduled at time zone 'Asia/Jerusalem','YYYY-MM-DD"T"HH24:MI:SS')
            || case when extract(hour from ((scheduled at time zone 'Asia/Jerusalem') - (scheduled at time zone 'UTC'))) = 3
                    then '+03:00' else '+02:00' end;
        insert into public.scan_windows(slot_key,scheduled_at,deadline_at)
        values (intended_slot,
            to_char(scheduled at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS') || '+00:00',
            to_char((scheduled + interval '2 hours') at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS') || '+00:00')
        on conflict(slot_key) do nothing;
    end loop;

    -- No new dispatch while a worker has a healthy lease. Pending wake-ups are
    -- coalesced in GitHub as well; an accepted API call is not business success.
    if exists(select 1 from public.scan_windows where status='running' and lease_until::timestamptz>clock_now)
       or exists(select 1 from public.delivery_leases where state='processing' and lease_until::timestamptz>clock_now) then
        return null;
    end if;
    if not (
        exists(select 1 from public.scan_windows where scheduled_at::timestamptz<=clock_now and (
            (status in ('scheduled','partial','failed') and attempts<2 and deadline_at::timestamptz>clock_now
                and (retry_at is null or retry_at::timestamptz<=clock_now))
            or (status='scheduled' and deadline_at::timestamptz<=clock_now)
            or (status='running' and lease_until::timestamptz<=clock_now)
            or (status in ('full','partial','failed') and not exists(
                select 1 from public.notification_batches b where b.slot_key=scan_windows.slot_key))
        ))
        or exists(select 1 from public.notification_batches b left join public.delivery_leases d using(slot_key)
            where b.state in ('pending','failed') and (d.slot_key is null or
                (d.state='failed' and d.attempts<3 and (d.retry_at is null or d.retry_at::timestamptz<=clock_now))))
        or exists(select 1 from public.delivery_leases where state='processing' and lease_until::timestamptz<=clock_now)
        or exists(select 1 from public.notification_batches b where b.state='sending' and not exists(
            select 1 from public.delivery_leases d where d.slot_key=b.slot_key))
    ) then return null; end if;

    insert into public.dispatcher_state(name) values ('github') on conflict(name) do nothing;
    if exists(select 1 from public.dispatcher_state where name='github' and next_try::timestamptz>clock_now) then return null; end if;
    update public.dispatcher_state set next_try=(clock_now + interval '15 minutes')::text,
        attempts=attempts+1 where name='github';
    begin
        select decrypted_secret into strict dispatch_token
          from vault.decrypted_secrets where name='ajob_github_dispatch_token';
        if dispatch_token is null or length(dispatch_token)=0 then
            raise exception 'Missing dispatch credential';
        end if;
        perform extensions.http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS','3000');
        perform extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS','15000');
        select * into response from extensions.http(row(
            'POST',
            'https://api.github.com/repos/yoavbenNun/AJob/actions/workflows/scheduled-scan.yml/dispatches',
            array[
                row('Authorization','Bearer ' || dispatch_token)::extensions.http_header,
                row('User-Agent','AJob-Supabase-Dispatcher')::extensions.http_header,
                row('Accept','application/vnd.github+json')::extensions.http_header,
                row('X-GitHub-Api-Version','2022-11-28')::extensions.http_header
            ], 'application/json', jsonb_build_object('ref','main')::text
        )::extensions.http_request);
        http_status := response.status;
        if http_status is distinct from 204 then
            dispatch_error := 'GitHubHTTP_' || coalesce(http_status::text,'NoStatus');
        end if;
    exception when others then
        -- Never persist SQLERRM: an HTTP/library error could contain credentials.
        dispatch_error := 'DispatchSQLSTATE_' || sqlstate;
    end;
    update public.dispatcher_state set error_type=dispatch_error,
        last_accepted_at=case when dispatch_error='' then clock_now::text else last_accepted_at end,
        next_try=case when dispatch_error='' then next_try else (clock_now + interval '30 minutes')::text end
    where name='github';
    return http_status;
end;
$$;
revoke all on function public.dispatch_ajob_scan() from public, anon, authenticated;

-- Update the EXISTING named job; no extra cloud service or new secret.
select cron.schedule('ajob-cloud-dispatch','*/5 * * * *','select public.dispatch_ajob_scan();');
