"""Round-2 fault tests on real PostgreSQL; imports shared scenarios, not mocks of SQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from test_postgres_scan_integration import cluster, repository
from test_window_recovery import (
    Clock, posting,
    test_smtp_retry_never_rescans_completed_window,
    test_hours_late_wakeup_scans_current_window_and_reports_missed,
    test_crash_during_smtp_recovers_as_uncertain,
    test_restart_before_smtp_can_retry_after_lease,
    test_stale_preparer_cannot_send_after_new_owner,
    test_scan_lease_fences_old_writes,
    test_partial_retry_only_visits_unfinished_sources,
    test_scan_budget_reserves_notification_time,
    test_late_old_window_never_replaces_new_window,
    test_israel_midnight_does_not_replay_yesterday,
    test_delivery_attempts_bounded_and_backoff,
    test_scan_near_hour_budget_preserves_time_for_notification,
    test_round1_completed_scan_is_not_repeated_after_upgrade,
    test_old_uncertain_mail_remains_visible_after_midnight,
)
from src.storage.window_store import WindowStore
from test_recovery_outcomes import (
    test_partial_warning_retains_failure_without_independent_monitor,
    test_recovery_full_only_checks_own_source_and_never_resends,
    test_exhausted_recovery_is_terminal_even_when_mail_was_sent,
    test_stopped_recovery_and_watchdog_cannot_hide_failure_after_midnight,
    test_lease_expiry_is_visible_without_running_recovery,
)


@pytest.fixture
def environment(repository):
    return repository, Clock()


def test_overlapping_scan_claims_do_not_queue(repository):
    clock = Clock()
    store = WindowStore(repository, clock)
    store.seed_windows()
    store.close()
    barrier = Barrier(2)
    def claim(_):
        target = WindowStore(repository, clock)
        try:
            barrier.wait(timeout=5)
            return bool(target.claim_window())
        finally:
            target.close()
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_overlapping_delivery_leases_have_single_owner(repository):
    clock = Clock()
    barrier = Barrier(2)
    def claim(_):
        target = WindowStore(repository, clock)
        try:
            barrier.wait(timeout=5)
            return target.claim_delivery('2026-07-01T08:17:00+03:00')
        finally:
            target.close()
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_dispatch_template_without_network(repository):
    """Execute the actual PL/pgSQL function with local HTTP and Vault stubs."""
    # This test owns an entire disposable database, including its public schema.
    # No extension, scheduler or HTTP client capable of network access is loaded.
    store = WindowStore(repository)
    try:
        store.execute('CREATE SCHEMA IF NOT EXISTS extensions')
        store.execute('CREATE SCHEMA IF NOT EXISTS vault')
        store.execute('CREATE TABLE vault.decrypted_secrets(name text,decrypted_secret text)')
        store.execute("INSERT INTO vault.decrypted_secrets VALUES ('ajob_github_dispatch_token','synthetic-test-credential')")
        store.execute('CREATE TYPE extensions.http_header AS (field text,value text)')
        store.execute('CREATE TYPE extensions.http_request AS (method text,uri text,headers extensions.http_header[],content_type text,content text)')
        store.execute('CREATE TYPE extensions.http_response AS (status int,content_type text,headers extensions.http_header[],content text)')
        store.execute('CREATE TABLE extensions.calls(number int)')
        store.execute('CREATE TABLE extensions.result(status int)')
        store.execute('INSERT INTO extensions.result VALUES (204)')
        store.execute("CREATE FUNCTION extensions.http_set_curlopt(text,text) RETURNS boolean LANGUAGE sql AS 'SELECT true'")
        store.execute("""CREATE FUNCTION extensions.http(extensions.http_request) RETURNS extensions.http_response
            LANGUAGE plpgsql AS $$ BEGIN INSERT INTO extensions.calls VALUES (1);
            RETURN ROW((SELECT status FROM extensions.result),NULL,NULL,NULL)::extensions.http_response; END $$""")
        # Function uses public-qualified runtime tables, so point those names at
        # this test schema through simple updatable views with matching columns.
        for table in ('scan_windows','notification_batches','delivery_leases','dispatcher_state'):
            # ON CONFLICT requires actual tables, not views. Move test tables to
            # public only inside this uniquely named disposable database.
            store.execute(f'ALTER TABLE {table} SET SCHEMA public')
        template = Path('docs/supabase-dispatch.sql').read_text()
        function = re.search(r'create or replace function public.dispatch_ajob_scan\(\).*?\n\$\$;', template, re.S).group(0)
        store.execute(function)
        assert store.execute('SELECT public.dispatch_ajob_scan() AS status').fetchone()['status'] in (None, 204)
        # Force a due notification irrespective of the local wall clock.
        store.execute("INSERT INTO public.notification_batches(slot_key,state,updated_at) VALUES ('synthetic','pending','2026-01-01')")
        store.execute("UPDATE public.dispatcher_state SET next_try=NULL")
        assert store.execute('SELECT public.dispatch_ajob_scan() AS status').fetchone()['status'] == 204
        count = store.execute('SELECT count(*) AS n FROM extensions.calls').fetchone()['n']
        assert store.execute('SELECT public.dispatch_ajob_scan() AS status').fetchone()['status'] is None
        assert store.execute('SELECT count(*) AS n FROM extensions.calls').fetchone()['n'] == count
        # Healthy work suppresses dispatch even after the cooldown is cleared.
        store.execute("INSERT INTO public.scan_windows(slot_key,scheduled_at,deadline_at,status,lease_until) VALUES ('busy',CURRENT_TIMESTAMP::text,(CURRENT_TIMESTAMP+interval '1 hour')::text,'running',(CURRENT_TIMESTAMP+interval '2 minutes')::text)")
        store.execute('UPDATE public.dispatcher_state SET next_try=NULL')
        assert store.execute('SELECT public.dispatch_ajob_scan() AS status').fetchone()['status'] is None
        assert store.execute('SELECT count(*) AS n FROM extensions.calls').fetchone()['n'] == count
        store.execute("UPDATE public.scan_windows SET status='full',lease_until=NULL WHERE slot_key='busy'")
        # HTTP rejection is persisted without a token/error body and backs off.
        store.execute('UPDATE extensions.result SET status=403')
        store.execute('UPDATE public.dispatcher_state SET next_try=NULL')
        assert store.execute('SELECT public.dispatch_ajob_scan() AS status').fetchone()['status'] == 403
        row = store.execute('SELECT error_type,next_try FROM public.dispatcher_state').fetchone()
        assert row['error_type'] == 'GitHubHTTP_403'
        assert row['next_try'] is not None
    finally:
        # Undo only objects created by this case; other tests use fresh schemas.
        for table in ('scan_windows','notification_batches','delivery_leases','dispatcher_state'):
            store.execute(f'DROP TABLE IF EXISTS public.{table} CASCADE')
        store.execute('DROP FUNCTION IF EXISTS public.dispatch_ajob_scan()')
        store.execute('DROP SCHEMA IF EXISTS extensions CASCADE')
        store.execute('DROP SCHEMA IF EXISTS vault CASCADE')
        store.close()


def test_send_completion_and_expired_lease_recovery_are_serialized(repository):
    clock = Clock()
    sender = WindowStore(repository, clock)
    recovery = WindowStore(repository, clock)
    slot = '2026-07-01T08:17:00+03:00'
    try:
        assert sender.claim_delivery(slot)
        sender.prepare_batch(slot)
        sender.freeze_message(slot, '{}')
        assert sender.begin_send(slot)
        clock.advance(minutes=11)
        barrier = Barrier(2)
        def complete():
            barrier.wait(timeout=5)
            sender.finish_send(slot, 'sent')
        def recover():
            barrier.wait(timeout=5)
            recovery.recover()
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(complete), pool.submit(recover)]
            for future in futures: future.result(timeout=10)
        batch = sender.batch_state(slot)
        lease = sender.execute('SELECT state FROM delivery_leases WHERE slot_key=?', (slot,)).fetchone()['state']
        assert batch == lease and batch in {'sent','uncertain'}
        assert not recovery.claim_delivery(slot)
    finally:
        sender.close(); recovery.close()


def test_round2_minimum_runtime_role(repository):
    from psycopg import sql, errors
    import uuid
    from test_postgres_scan_integration import ScopedRepository
    from src.scrapers.budgeted_scan import DurableSummary
    role = 'round2_' + uuid.uuid4().hex
    store = WindowStore(repository, Clock())
    tables = ('job_postings','scan_runs','scan_source_metrics','notification_batches',
              'notification_outbox','scan_windows','delivery_leases','dispatcher_state')
    try:
        store.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOBYPASSRLS').format(sql.Identifier(role)))
        store.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(repository.schema),sql.Identifier(role)))
        for table in tables:
            store.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON {} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
            if table != 'job_postings':
                store.execute(sql.SQL('CREATE POLICY runtime_access ON {} TO {} USING(true) WITH CHECK(true)').format(sql.Identifier(table),sql.Identifier(role)))
        store.execute(sql.SQL('GRANT USAGE ON SEQUENCE job_postings_id_seq TO {}').format(sql.Identifier(role)))
        limited = WindowStore(ScopedRepository(repository.database_url,repository.schema,role),Clock())
        try:
            limited.verify_schema()
            limited.seed_windows()
            slot, run, _ = limited.claim_window()
            limited.save_discovery(posting(),slot)
            limited.record_metrics(run,'Synthetic',{'status':'full'})
            limited.finish_window(run,DurableSummary(status='full'),['Synthetic'])
            assert limited.claim_delivery(slot)
            limited.prepare_batch(slot)
            limited.freeze_message(slot,'{}')
            assert limited.begin_send(slot)
            limited.finish_send(slot,'sent')
            assert limited.report(1)[0]['notification'] == 'sent'
            with pytest.raises(errors.InsufficientPrivilege): limited.execute('DELETE FROM delivery_leases')
        finally: limited.close()
    finally:
        store.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
        store.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
        store.close()


def test_expired_connected_scanner_cannot_block_newer_window(repository):
    from src.storage.window_store import LeaseLost
    clock = Clock()
    old = WindowStore(repository,clock)
    new = WindowStore(repository,clock)
    try:
        old.seed_windows()
        previous = old.claim_window()
        clock.advance(hours=3)
        # Old connection intentionally stays open: no session lock may hold
        # the newer window hostage. Its expired owner is fenced from writes.
        new.seed_windows()
        new.recover()
        current = new.claim_window()
        assert previous and current and previous[0] != current[0]
        new.save_discovery(posting(),current[0])
        with pytest.raises(LeaseLost): old.save_discovery(posting(),previous[0])
    finally:
        old.close(); new.close()


def test_new_worker_rejects_legacy_schema_without_ddl(cluster):
    import uuid
    import psycopg
    from psycopg import sql
    from test_postgres_scan_integration import ScopedRepository
    schema = 'legacy_' + uuid.uuid4().hex
    with psycopg.connect(cluster,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    repo = ScopedRepository(cluster,schema)
    repo.initialize()
    repo.save(posting())
    store = WindowStore(repo,Clock())
    try:
        with pytest.raises(psycopg.errors.UndefinedTable): store.verify_schema()
        assert store.execute("SELECT to_regclass('scan_windows') AS name").fetchone()['name'] is None
        assert len(repo.list_all()) == 1
    finally:
        store.close()
        with psycopg.connect(cluster,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_legacy_storage_preserves_pending_outbox_but_cannot_drain_it(repository):
    from src.storage.scan_store import ScanStore
    store = WindowStore(repository,Clock())
    try:
        slot = '2026-07-01T08:17:00+03:00'
        store.save_discovery(posting(),slot)
        store.prepare_batch(slot)
        # Existing legacy adapter is unchanged from HEAD: schema access still
        # works, but its private delivery ledger does not consume the new outbox.
        repository.initialize()
        existing = repository.get_by_url(posting().job_url)
        existing.title = 'Refreshed synthetic intern'
        existing.status = 'applied'
        repository.update_status(existing.id,'applied')
        repository.save(existing)
        assert len(store.prepare_batch(slot)) == 1
        assert store.batch_state(slot) == 'pending'
        assert repository.get_by_id(existing.id).status == 'applied'
        assert repository.begin_delivery(slot)  # demonstrates semantic incompatibility
        assert store.batch_state(slot) == 'pending'
    finally: store.close()


def test_backup_restore_retains_pending_and_uncertain_outbox(repository):
    import os, subprocess, uuid
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from test_postgres_scan_integration import ScopedRepository
    container = os.environ.get('AJOB_TEST_DOCKER_CONTAINER')
    if not container or not container.startswith('ajob-pg-'):
        pytest.skip('Requires an explicitly selected disposable PostgreSQL container')
    database = conninfo_to_dict(repository.database_url)['dbname']
    assert database.startswith('ajob_test_')
    store = WindowStore(repository,Clock())
    restored = 'ajob_restore_' + uuid.uuid4().hex
    try:
        store.save_discovery(posting(),'2026-07-01T08:17:00+03:00')
        store.prepare_batch('2026-07-01T08:17:00+03:00')
        later = posting(); later.job_url += '/second'
        store.save_discovery(later,'2026-07-01T11:17:00+03:00')
        slot = '2026-07-01T11:17:00+03:00'
        store.claim_delivery(slot); store.prepare_batch(slot)
        store.freeze_message(slot,'{}'); assert store.begin_send(slot)
        store.finish_send(slot,'uncertain','SyntheticDisconnect')
        store.execute("UPDATE job_postings SET status='applied' WHERE job_url=?", (posting().job_url,))
        archive = subprocess.run(['docker','exec',container,'pg_dump','-U','postgres','-Fc','-d',database],capture_output=True,check=True,timeout=30).stdout
        assert archive.startswith(b'PGDMP')
        with psycopg.connect(repository.database_url,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored)))
        subprocess.run(['docker','exec','-i',container,'pg_restore','-U','postgres','--exit-on-error','--no-owner','--no-acl','-d',restored],input=archive,capture_output=True,check=True,timeout=30)
        # URI is synthetic loopback only; never read DATABASE_URL.
        dsn = repository.database_url.rsplit('/',1)[0] + '/' + restored
        copy = WindowStore(ScopedRepository(dsn,repository.schema),Clock())
        try:
            copy.verify_schema()
            assert len(copy.known_urls()) == 2
            assert copy.repository.get_by_url(posting().job_url).status == 'applied'
            assert copy.execute("SELECT count(*) AS n FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname IN ('notification_outbox','scan_windows') AND relrowsecurity").fetchone()['n'] == 2
            assert len(copy.prepare_batch('2026-07-01T08:17:00+03:00')) == 1
            assert copy.batch_state(slot) == 'uncertain'
            assert not copy.claim_delivery(slot)
            assert copy.execute('SELECT count(*) AS n FROM notification_outbox').fetchone()['n'] == 2
        finally: copy.close()
    finally:
        store.close()
        with psycopg.connect(repository.database_url,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH(FORCE)').format(sql.Identifier(restored)))
