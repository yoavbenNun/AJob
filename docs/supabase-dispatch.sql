-- Deployment template; review and run as postgres only AFTER configuring Vault.
-- Vault secret ajob_github_dispatch_token must be a fine-grained GitHub token
-- restricted to yoavbenNun/AJob, with repository Actions: write permission.
-- Never put that token in this file, migrations, chat, or command arguments.
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.dispatch_ajob_scan()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    local_now timestamp := current_timestamp at time zone 'Asia/Jerusalem';
    utc_now timestamp := current_timestamp at time zone 'UTC';
    utc_offset interval;
    intended_slot text;
    dispatch_token text;
    request_id bigint;
begin
    -- Cron is UTC; gate by Israel time so both DST offset candidates are safe.
    if extract(hour from local_now) not in (8, 11, 16)
       or extract(minute from local_now) not in (17, 27, 37, 47, 57) then
        return null;
    end if;
    utc_offset := local_now - utc_now;
    if utc_offset not in (interval '2 hours', interval '3 hours') then
        raise exception 'Unexpected Israel UTC offset';
    end if;
    intended_slot := to_char(date_trunc('hour', local_now) + interval '17 minutes',
                             'YYYY-MM-DD"T"HH24:MI:SS')
        || case when utc_offset = interval '3 hours' then '+03:00' else '+02:00' end;

    select decrypted_secret into strict dispatch_token
      from vault.decrypted_secrets where name = 'ajob_github_dispatch_token';
    if dispatch_token is null or length(dispatch_token) = 0 then
        raise exception 'Missing AJob dispatch credential';
    end if;
    select net.http_post(
        url := 'https://api.github.com/repos/yoavbenNun/AJob/actions/workflows/scheduled-scan.yml/dispatches',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || dispatch_token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'Content-Type', 'application/json'),
        body := jsonb_build_object('ref', 'main', 'inputs',
                    jsonb_build_object('delivery_slot', intended_slot)),
        timeout_milliseconds := 15000
    ) into request_id;
    return request_id;
end;
$$;

-- The public API must not be able to trigger scans or read the dispatch token.
revoke all on function public.dispatch_ajob_scan() from public, anon, authenticated;

-- A named schedule is updated in place on repeat deployment.
select cron.schedule(
    'ajob-cloud-dispatch',
    '17,27,37,47,57 5,6,8,9,13,14 * * *',
    'select public.dispatch_ajob_scan();'
);
