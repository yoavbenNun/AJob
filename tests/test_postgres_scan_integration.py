"""Opt-in real PostgreSQL tests. Only a loopback disposable cluster is accepted.

AJOB_TEST_POSTGRES_DSN points to a local admin database; a new random database is
created and removed. Never uses DATABASE_URL or an application configuration file.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import quote, urlencode
import multiprocessing
import time

import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from src.models import JobPosting
from src.notifications.durable_scan import deliver_batch
from src.notifications.email_notifier import DeliveryError, SmtpSettings
from src.scrapers.budgeted_scan import DurableSummary
from src.storage.postgres_job_repository import PostgresJobRepository
from src.storage.scan_store import ScanStore, SlotBusy


@pytest.fixture(scope='module')
def cluster():
    dsn = os.environ.get('AJOB_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set AJOB_TEST_POSTGRES_DSN to an isolated loopback PostgreSQL cluster')
    values = conninfo_to_dict(dsn)
    if values.get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        pytest.fail('Refusing non-loopback PostgreSQL test connection')
    name = 'ajob_test_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    # The application repository intentionally accepts URI connection strings.
    auth = quote(values.get('user', 'postgres'), safe='')
    if values.get('password'):
        auth += ':' + quote(values['password'], safe='')
    host = values['host']
    host = '[' + host + ']' if ':' in host else host
    options = {k: v for k, v in values.items() if k not in {'user','password','host','port','dbname'}}
    test_dsn = f"postgresql://{auth}@{host}:{values.get('port', '5432')}/{name}"
    if options:
        test_dsn += '?' + urlencode(options)
    with psycopg.connect(test_dsn, autocommit=True) as connection:
        print('REAL_POSTGRES_VERSION=' + connection.execute('SHOW server_version').fetchone()[0])
    try:
        yield test_dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


class ScopedRepository(PostgresJobRepository):
    def __init__(self, dsn, schema, role=None):
        super().__init__(dsn)
        self.schema, self.role = schema, role

    def _connect(self):
        connection = super()._connect()
        connection.autocommit = True
        connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        if self.role:
            connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(self.role)))
        connection.autocommit = False
        return connection


@pytest.fixture
def repository(cluster):
    schema = 'case_' + uuid.uuid4().hex
    with psycopg.connect(cluster, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    repo = ScopedRepository(cluster, schema)
    repo.initialize()
    store = ScanStore(repo)
    store.migrate()
    store.close()
    yield repo
    with psycopg.connect(cluster, autocommit=True) as connection:
        connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def store(repository):
    target = ScanStore(repository)
    yield target
    target.close()


def job(number=1):
    return JobPosting('Intern', 'Synthetic', 'Remote', f'https://example.test/jobs/{number}')


SETTINGS = SmtpSettings('localhost', 25, '', '', 'from@example.test', 'to@example.test', False)


@pytest.mark.parametrize('lifecycle', ['new', 'reviewed', 'applied'])
def test_workday_backfill_preserves_identity_tracking_outbox_and_restores(repository, store, lifecycle):
    from src.storage.workday_backfill import prepare_backfill, apply_backfill, restore_backfill, snapshot
    from src.scrapers.workday_details import WorkdayDetail, workday_urls
    source = 'https://intel.wd1.myworkdayjobs.com/en-US/External'
    original = JobPosting('Software Student', 'Intel', 'Israel',
                          'https://intel.wd1.myworkdayjobs.com/job/Israel/Software_JR123',
                          source=source, description='Page not found.', status=lifecycle)
    store.save_discovery(original, 'original-slot')
    before = snapshot(store.execute('SELECT * FROM job_postings').fetchone())
    outbox = store.execute('SELECT * FROM notification_outbox').fetchall()
    detail = WorkdayDetail(workday_urls(source, original.job_url)[0], original.title, 'JR123',
                           'Software Student\nDevelop Python software.\nRequirements\nCurrently pursuing a degree.')
    plan = prepare_backfill(store.connection, fetch=lambda *args: detail)
    assert snapshot(store.execute('SELECT * FROM job_postings').fetchone()) == before  # Dry run.
    assert apply_backfill(store.connection, plan) == [original.id]
    after = snapshot(store.execute('SELECT * FROM job_postings').fetchone())
    assert after == {**before, **plan['rows'][0]['after']}
    assert store.execute('SELECT * FROM notification_outbox').fetchall() == outbox
    assert store.execute('SELECT count(*) AS n FROM job_postings').fetchone()['n'] == 1
    assert restore_backfill(store.connection, plan) == [original.id]
    assert snapshot(store.execute('SELECT * FROM job_postings').fetchone()) == before


def test_workday_backfill_concurrent_edit_and_identity_error_prevent_writes(repository, store):
    from src.storage.workday_backfill import prepare_backfill, apply_backfill
    from src.scrapers.workday_details import WorkdayDetail, WorkdayIdentityError, workday_urls
    source = 'https://intel.wd1.myworkdayjobs.com/en-US/External'
    for number in (1, 2):
        store.save_discovery(JobPosting('Software Student', 'Intel', 'Israel',
                             f'https://intel.wd1.myworkdayjobs.com/job/Israel/Software_JR{number}',
                             source=source, description='Page not found.'), 'original-slot')
    def fetch(source, url, title):
        return WorkdayDetail(workday_urls(source, url)[0], title, url.rsplit('_', 1)[-1],
                              'Software Student\nDevelop Python software.\nRequirements\nCurrently pursuing a degree.')
    plan = prepare_backfill(store.connection, fetch=fetch)
    store.execute("UPDATE job_postings SET status='reviewed' WHERE id=?", (plan['rows'][1]['before']['id'],))
    with pytest.raises(RuntimeError, match='Row changed'):
        apply_backfill(store.connection, plan)
    # Even the earlier successful UPDATE must have rolled back.
    assert all(row['description'] == 'Page not found.' for row in store.execute('SELECT description FROM job_postings'))
    def mismatch(*args):
        raise WorkdayIdentityError('mismatch')
    plan = prepare_backfill(store.connection, fetch=mismatch)
    with pytest.raises(WorkdayIdentityError):
        apply_backfill(store.connection, plan)


def test_atomic_commit_and_real_rollback(store):
    store.execute("CREATE FUNCTION fail_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
    store.execute('CREATE TRIGGER fail_outbox BEFORE INSERT ON notification_outbox FOR EACH ROW EXECUTE FUNCTION fail_outbox()')
    with pytest.raises(psycopg.errors.RaiseException):
        store.save_discovery(job(), 'slot')
    assert not store.known_urls()
    assert store.execute('SELECT count(*) AS n FROM notification_outbox').fetchone()['n'] == 0
    store.execute('DROP TRIGGER fail_outbox ON notification_outbox')
    assert store.save_discovery(job(), 'slot')
    assert len(store.prepare_batch('slot')) == 1


@pytest.mark.parametrize('durable', [False, True])
def test_bad_detail_refresh_preserves_valid_posting(repository, store, durable):
    from dataclasses import replace
    original = JobPosting('Software Student', 'Example', 'Israel', 'https://example.test/preserve-detail',
                          description='Develop Python services.', requirements='Python required.', status='applied')
    job_id = repository.save(original)
    invalid = replace(original, description='Page not found.', requirements='', status='new')
    if durable:
        assert not store.save_discovery(invalid, 'test-detail-slot')
    else:
        repository.save(invalid)
    saved = repository.get_by_id(job_id)
    assert (saved.description, saved.requirements, saved.status) == (
        'Develop Python services.', 'Python required.', 'applied')


def test_concurrent_discovery_has_one_job_and_one_outbox(repository, store):
    barrier = Barrier(2)
    def discover(index):
        target = ScanStore(repository)
        try:
            barrier.wait(timeout=5)
            return target.save_discovery(job(), f'slot-{index}')
        finally:
            target.close()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(discover, range(2)))
    assert sorted(results) == [False, True]
    assert len(store.known_urls()) == 1
    assert store.execute('SELECT count(*) AS n FROM notification_outbox').fetchone()['n'] == 1


def test_two_claimants_only_one_send(repository, store):
    store.save_discovery(job(), 'slot')
    store.prepare_batch('slot')
    store.freeze_message('slot', '{"subject":"test","body":"test"}')
    barrier = Barrier(2)
    def claim(_):
        target = ScanStore(repository)
        try:
            barrier.wait(timeout=5)
            return target.begin_send('slot')
        finally:
            target.close()
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_parallel_batches_do_not_steal_discoveries(repository, store):
    for number in range(1, 9):
        store.save_discovery(job(number), 'origin')
    barrier = Barrier(2)
    def prepare(index):
        target = ScanStore(repository)
        try:
            barrier.wait(timeout=5)
            return {j.id for j in target.prepare_batch(f'batch-{index}')}
        finally:
            target.close()
    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(prepare, range(2)))
    assert not first & second
    assert len(first | second) == 8


@pytest.mark.parametrize('empty', [False, True])
def test_prepared_membership_cannot_change_before_message_freezes(store, empty):
    if not empty:
        store.save_discovery(job(), 'origin')
    initial = store.prepare_batch('slot')
    store.save_discovery(job(2), 'origin')
    again = store.prepare_batch('slot')
    assert [j.id for j in initial] == [j.id for j in again]
    assert [j.job_url for j in store.prepare_batch('next')] == [job(2).job_url]


def test_restart_after_commit_and_before_mail(repository):
    first = ScanStore(repository)
    first.start_run('old')
    first.save_discovery(job(), 'old')
    first.close()  # simulate connection loss; no summary/new_jobs in memory
    second = ScanStore(repository)
    try:
        second.start_run('old')
        statuses = [r['status'] for r in second.execute('SELECT status FROM scan_runs ORDER BY started_at')]
        assert statuses == ['partial', 'running']
        sent = []
        assert deliver_batch(second, 'next', DurableSummary(status='partial'), SETTINGS,
                             sender=lambda _, message: sent.append(message)) == 'sent'
        assert 'Intern' in sent[0].get_content()
    finally:
        second.close()


@pytest.mark.parametrize('uncertain', [False, True])
def test_failed_and_uncertain_delivery_recovery(repository, store, uncertain):
    store.save_discovery(job(), 'slot')
    def fail(*args):
        raise DeliveryError(ConnectionError(), uncertain)
    assert deliver_batch(store, 'slot', DurableSummary(), SETTINGS, sender=fail) == ('uncertain' if uncertain else 'failed')
    reopened = ScanStore(repository)
    try:
        sent = []
        result = deliver_batch(reopened, 'slot', DurableSummary(), SETTINGS,
                               sender=lambda _, msg: sent.append(msg))
        assert result == ('uncertain' if uncertain else 'sent')
        assert len(sent) == (0 if uncertain else 1)
    finally:
        reopened.close()


def test_interrupted_sending_held_on_restart(repository, store):
    store.start_run('slot')
    store.save_discovery(job(), 'slot')
    store.prepare_batch('slot')
    store.freeze_message('slot', '{}')
    assert store.begin_send('slot')
    store.close()
    reopened = ScanStore(repository)
    try:
        reopened.start_run('slot')
        assert reopened.batch_state('slot') == 'uncertain'
        assert not reopened.begin_send('slot')
    finally:
        reopened.close()


def test_session_lock_owned_until_disconnect(repository, store):
    store.acquire_slot('slot')
    other = ScanStore(repository)
    try:
        with pytest.raises(SlotBusy):
            other.acquire_slot('slot')
        store.close()
        other.acquire_slot('slot')
    finally:
        other.close()


def test_lifecycle_preserved_on_refresh(store):
    store.save_discovery(job(), 'slot')
    store.execute("UPDATE job_postings SET status='applied'")
    assert not store.save_discovery(job(), 'next')
    assert store.execute('SELECT status FROM job_postings').fetchone()['status'] == 'applied'


def test_migration_preserves_existing_data_and_is_idempotent(store):
    store.save_discovery(job(), 'slot')
    store.execute("UPDATE job_postings SET status='reviewed'")
    store.repository.begin_delivery('legacy')
    store.repository.complete_delivery('legacy')
    store.migrate()
    store.migrate()
    assert store.batch_state('legacy') == 'sent'
    assert store.execute('SELECT status FROM job_postings').fetchone()['status'] == 'reviewed'
    assert len(store.prepare_batch('slot')) == 1


def test_rls_and_minimal_scanner_privileges(repository, store):
    scanner = 'scanner_' + uuid.uuid4().hex
    reader = 'dashboard_' + uuid.uuid4().hex
    tables = ['scan_runs', 'scan_source_metrics', 'notification_batches', 'notification_outbox']
    try:
        for role in (scanner, reader):
            store.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOBYPASSRLS').format(sql.Identifier(role)))
            store.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(repository.schema), sql.Identifier(role)))
        store.execute(sql.SQL('GRANT SELECT ON job_postings TO {}').format(sql.Identifier(reader)))
        store.execute(sql.SQL('GRANT UPDATE(status) ON job_postings TO {}').format(sql.Identifier(reader)))
        # Deliberately grant SELECT on private tables: RLS still hides rows.
        for table in tables:
            store.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.Identifier(table), sql.Identifier(reader)))
            store.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON {} TO {}').format(sql.Identifier(table), sql.Identifier(scanner)))
            store.execute(sql.SQL('CREATE POLICY scanner_access ON {} TO {} USING (true) WITH CHECK (true)').format(sql.Identifier(table), sql.Identifier(scanner)))
        store.execute(sql.SQL('GRANT SELECT,INSERT ON job_postings TO {}').format(sql.Identifier(scanner)))
        store.execute(sql.SQL('GRANT UPDATE(title,company,location,description,requirements,source,discovered_at,is_relevant) ON job_postings TO {}').format(sql.Identifier(scanner)))
        store.execute(sql.SQL('GRANT USAGE ON SEQUENCE job_postings_id_seq TO {}').format(sql.Identifier(scanner)))
        limited = ScanStore(ScopedRepository(repository.database_url, repository.schema, scanner))
        try:
            run_id = limited.start_run('limited')
            limited.save_discovery(job(), 'limited')
            limited.record_metrics(run_id, 'Synthetic', {'status': 'full'})
            limited.finish_run(run_id, DurableSummary(status='full'))
            limited.prepare_batch('limited')
            limited.freeze_message('limited', '{}')
            assert limited.begin_send('limited')
            limited.finish_send('limited', 'sent')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute('DELETE FROM notification_outbox')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute('CREATE TABLE forbidden(id int)')
        finally:
            limited.close()
        restricted = ScanStore(ScopedRepository(repository.database_url, repository.schema, reader))
        try:
            assert restricted.execute('SELECT count(*) AS n FROM job_postings').fetchone()['n'] == 1
            restricted.execute("UPDATE job_postings SET status='reviewed'")
            assert restricted.execute('SELECT status FROM job_postings').fetchone()['status'] == 'reviewed'
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute("UPDATE job_postings SET description='forbidden'")
            for table in tables:
                assert restricted.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.save_discovery(job(2), 'forbidden')
        finally:
            restricted.close()
    finally:
        # Roles are cluster-wide, so explicitly remove grants before dropping.
        for role in (scanner, reader):
            store.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            store.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_failed_migration_rolls_back_all_new_tables(cluster):
    schema = 'blank_' + uuid.uuid4().hex
    with psycopg.connect(cluster, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = ScanStore(ScopedRepository(cluster, schema))
    try:
        # This migration intentionally depends on the application's base schema.
        with pytest.raises(psycopg.errors.UndefinedTable):
            target.migrate()
        assert target.execute("SELECT to_regclass('scan_runs') AS relation").fetchone()['relation'] is None
    finally:
        target.close()
        with psycopg.connect(cluster, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_upgrade_previous_round1_schema_preserves_prepared_batch(store):
    store.save_discovery(job(), 'slot')
    store.prepare_batch('slot')
    store.execute('ALTER TABLE notification_batches DROP COLUMN prepared_at')
    store.migrate()
    store.save_discovery(job(2), 'new')
    assert len(store.prepare_batch('slot')) == 1
    assert len(store.prepare_batch('next')) == 1


def process_claim(dsn, schema, barrier, queue):
    target = ScanStore(ScopedRepository(dsn, schema))
    try:
        barrier.wait(timeout=15)
        queue.put(target.begin_send('slot'))
    finally:
        target.close()


def test_separate_processes_claim_same_delivery(repository, store):
    store.prepare_batch('slot')
    store.freeze_message('slot', '{}')
    ctx = multiprocessing.get_context('spawn')
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=process_claim,
        args=(repository.database_url, repository.schema, barrier, queue)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        assert sorted([queue.get(timeout=20), queue.get(timeout=20)]) == [False, True]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        queue.close()


def commit_then_wait(dsn, schema, pipe):
    target = ScanStore(ScopedRepository(dsn, schema))
    target.start_run('crashed')
    target.save_discovery(job(), 'crashed')
    pipe.send('committed')
    time.sleep(60)


def test_real_process_crash_after_commit(repository, store):
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=commit_then_wait,
        args=(repository.database_url, repository.schema, child))
    try:
        process.start()
        child.close()
        assert parent.poll(15)
        assert parent.recv() == 'committed'
        process.kill()
        process.join(timeout=10)
        assert not process.is_alive()
        # PostgreSQL releases the terminated client's session lock on EOF.
        deadline = time.monotonic() + 5
        while True:
            try:
                store.start_run('crashed')
                break
            except SlotBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        assert len(store.prepare_batch('recovered')) == 1
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
        child.close()


def test_pooler_session_mode_preserves_locks_and_resets_connections(repository, store):
    """Select explicitly when AJOB_TEST_POSTGRES_DSN targets PgBouncer session mode."""
    if not os.environ.get('AJOB_TEST_SESSION_POOLER'):
        pytest.skip('Requires the disposable session pooler')
    with psycopg.connect(os.environ['AJOB_TEST_POSTGRES_DSN'], dbname='pgbouncer', autocommit=True, row_factory=dict_row) as admin:
        settings = {row['key']: row['value'] for row in admin.execute('SHOW CONFIG')}
        assert settings['pool_mode'] == 'session'
        print('REAL_POOLER_VERSION=' + admin.execute('SHOW VERSION').fetchone()['version'])
    backend = store.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    store.acquire_slot('pooled')
    for number in range(3):
        store.save_discovery(job(number), 'pooled')
        assert store.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'] == backend
    other = ScanStore(repository)
    try:
        with pytest.raises(SlotBusy):
            other.acquire_slot('pooled')
        store.close()
        deadline = time.monotonic() + 5
        while True:
            try:
                other.acquire_slot('pooled')
                break
            except SlotBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        assert len(other.prepare_batch('pooled')) == 3
    finally:
        other.close()


def test_prepare_failure_rolls_back_claims_and_batch(store):
    store.save_discovery(job(), 'origin')
    store.execute("CREATE FUNCTION fail_preparation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
    store.execute('CREATE TRIGGER fail_preparation BEFORE UPDATE ON notification_batches FOR EACH ROW EXECUTE FUNCTION fail_preparation()')
    with pytest.raises(psycopg.errors.RaiseException):
        store.prepare_batch('slot')
    assert store.batch_state('slot') is None
    assert store.execute('SELECT batch_slot FROM notification_outbox').fetchone()['batch_slot'] is None
    store.execute('DROP TRIGGER fail_preparation ON notification_batches')
    assert len(store.prepare_batch('slot')) == 1


def test_outdated_schema_fails_preflight_without_side_effects(store):
    store.execute('ALTER TABLE notification_batches DROP COLUMN prepared_at')
    with pytest.raises(psycopg.errors.UndefinedColumn):
        store.verify_schema()
    assert store.execute('SELECT count(*) AS n FROM scan_runs').fetchone()['n'] == 0
    store.migrate()
    store.verify_schema()
