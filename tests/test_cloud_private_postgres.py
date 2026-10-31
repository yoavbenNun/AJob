"""Opt-in PostgreSQL/RLS verification in a random disposable loopback database.

Never reads DATABASE_URL. AJOB_TEST_POSTGRES_DSN must name an isolated local
test cluster whose administrator can create/drop databases and temporary roles.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import uuid
from urllib.parse import quote, urlencode

import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from src.models import JobPosting
from src.storage.postgres_job_repository import PostgresJobRepository
from src.storage.postgres_personal_notes_repository import PostgresPersonalNotesRepository
from src.storage.postgres_dismissal_repository import PostgresDismissalRepository


@pytest.fixture(scope='module')
def cloud_database():
    dsn = os.environ.get('AJOB_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Isolated loopback PostgreSQL test cluster unavailable')
    values = conninfo_to_dict(dsn)
    if values.get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        pytest.fail('Refusing non-loopback PostgreSQL test connection')
    # hostaddr must not silently override the loopback host safety boundary.
    if values.get('hostaddr') not in {None, '127.0.0.1', '::1'} or values.get('service'):
        pytest.fail('Refusing PostgreSQL test service or non-loopback hostaddr')
    suffix = uuid.uuid4().hex
    name, role, denied = ('ajob_test_' + suffix, 'ajob_test_dashboard_' + suffix, 'ajob_test_denied_' + suffix)
    auth = quote(values.get('user', 'postgres'), safe='')
    if values.get('password'):
        auth += ':' + quote(values['password'], safe='')
    host = values['host']
    host = '[' + host + ']' if ':' in host else host
    options = {key: value for key, value in values.items() if key not in {'user','password','host','port','dbname'}}
    target = f"postgresql://{auth}@{host}:{values.get('port', '5432')}/{name}"
    if options:
        target += '?' + urlencode(options)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        jobs = PostgresJobRepository(target)
        jobs.initialize()
        migration = (Path(__file__).resolve().parents[1] / 'scripts/manual_cloud_private.sql').read_text()
        # A fresh role per run avoids altering any existing application's roles.
        migration = migration.replace('ajob_dashboard', role)
        with psycopg.connect(target, autocommit=True) as connection:
            # Prove the dashboard also works when existing jobs enforce RLS.
            connection.execute('ALTER TABLE public.job_postings ENABLE ROW LEVEL SECURITY')
            connection.execute(migration)
            # Manual re-application must preserve schema and permissions.
            connection.execute(migration)
            connection.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(denied)))
        yield target, role, denied, jobs
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            for owned_role in (role, denied):
                admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(owned_role)))


def scoped(repository_type, target, role):
    repository = repository_type(target)
    original = repository._connect
    def connect():
        connection = original()
        connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
        connection.commit()
        return connection
    repository._connect = connect
    return repository


def test_real_notes_persistence_least_privilege_and_rls(cloud_database):
    target, role, denied, jobs = cloud_database
    job_id = jobs.save(JobPosting('Student', 'Synthetic', 'Israel', 'https://example.test/cloud-note'))
    notes = scoped(PostgresPersonalNotesRepository, target, role)
    notes.initialize()
    notes.save(job_id, 'synthetic cloud note')
    assert scoped(PostgresPersonalNotesRepository, target, role).for_jobs([job_id]) == {job_id: 'synthetic cloud note'}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        scoped(PostgresPersonalNotesRepository, target, denied).for_jobs([job_id])
    with notes._connect() as connection:
        policies = connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='ajob_private.personal_notes'::regclass").fetchone()
        assert policies == {'relrowsecurity': True, 'relforcerowsecurity': True}
        assert connection.execute('SELECT count(*) AS n FROM public.job_postings').fetchone()['n'] >= 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('CREATE TABLE ajob_private.forbidden(id integer)')
    with notes._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("UPDATE public.job_postings SET title='forbidden' WHERE id=%s", (job_id,))
    # Scanner does not gain access to private records just because it reads jobs.
    with psycopg.connect(target) as connection:
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(denied)))
        connection.execute(sql.SQL('GRANT SELECT ON public.job_postings TO {}').format(sql.Identifier(denied)))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        scoped(PostgresPersonalNotesRepository, target, denied).for_jobs([job_id])


def test_real_atomic_concurrent_dismiss_restart_restore(cloud_database):
    target, role, _, jobs = cloud_database
    job_id = jobs.save(JobPosting('Student', 'Synthetic', 'Israel', 'https://example.test/cloud-dismiss'))
    jobs.update_status(job_id, 'applied')
    undo = scoped(PostgresDismissalRepository, target, role)
    undo.initialize()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scoped(PostgresDismissalRepository, target, role).change_status(job_id, 'dismiss'), range(2)))
    assert sorted(result['changed'] for result in results) == [False, True]
    assert undo.previous(job_id) == 'applied'
    assert scoped(PostgresDismissalRepository, target, role).change_status(job_id, 'restore') == {'status': 'applied', 'changed': True}
    assert jobs.get_by_id(job_id).status == 'applied'
    assert undo.previous(job_id) is None


def test_real_injected_status_failure_rolls_back_undo(cloud_database):
    target, role, _, jobs = cloud_database
    job_id = jobs.save(JobPosting('Student', 'Synthetic', 'Israel', 'https://example.test/cloud-rollback'))
    # Reject only this synthetic row's ignored transition, after undo was inserted.
    constraint = 'test_reject_' + uuid.uuid4().hex
    with psycopg.connect(target) as connection:
        connection.execute(sql.SQL('ALTER TABLE public.job_postings ADD CONSTRAINT {} CHECK (id <> {} OR status <> \'ignored\')').format(sql.Identifier(constraint), sql.Literal(job_id)))
    undo = scoped(PostgresDismissalRepository, target, role)
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            undo.change_status(job_id, 'dismiss')
        assert undo.previous(job_id) is None
        assert jobs.get_by_id(job_id).status == 'new'
    finally:
        with psycopg.connect(target) as connection:
            connection.execute(sql.SQL('ALTER TABLE public.job_postings DROP CONSTRAINT {}').format(sql.Identifier(constraint)))
