"""Synthetic transaction/validation tests; SQLite is not PostgreSQL/RLS proof."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from src.storage.postgres_personal_notes_repository import PostgresPersonalNotesRepository
from src.storage.postgres_dismissal_repository import PostgresDismissalRepository
from src.storage.private_data_migration import backup_sqlite, restore_sqlite, build_plan, write_plan


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class SyntheticConnection:
    def __init__(self, db):
        self.db = db
        self.fail_update = False
        self.statements = []

    def execute(self, query, parameters=()):
        self.statements.append(query)
        if self.fail_update and query.startswith('UPDATE public.job_postings'):
            raise RuntimeError('Injected status transaction failure')
        if 'ANY(%s)' in query:
            parameters = parameters[0]
            query = query.replace('= ANY(%s)', 'IN (' + ','.join('?' for _ in parameters) + ')')
        query = query.replace('%s', '?').replace(' FOR UPDATE', '')
        return Cursor(self.db.execute(query, parameters))

    @contextmanager
    def connect(self):
        with self.db:
            yield self


@pytest.fixture
def repositories():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute("ATTACH DATABASE ':memory:' AS public")
    db.execute("ATTACH DATABASE ':memory:' AS ajob_private")
    db.execute('CREATE TABLE public.job_postings(id INTEGER PRIMARY KEY,status TEXT)')
    db.execute("INSERT INTO public.job_postings VALUES (1,'applied'),(2,'new')")
    db.execute('CREATE TABLE ajob_private.personal_notes(job_id INTEGER PRIMARY KEY,note TEXT)')
    db.execute('CREATE TABLE ajob_private.job_dismissal_history(job_id INTEGER PRIMARY KEY,previous_status TEXT)')
    db.commit()
    connection = SyntheticConnection(db)
    notes = PostgresPersonalNotesRepository('postgresql://localhost/synthetic')
    undo = PostgresDismissalRepository('postgresql://localhost/synthetic')
    notes._connect = undo._connect = connection.connect
    yield notes, undo, connection
    db.close()


def test_schema_preflight_is_read_only(repositories):
    notes, undo, connection = repositories
    notes.initialize()
    undo.initialize()
    assert all(query.startswith('SELECT ') for query in connection.statements)


def test_notes_edit_restart_and_delete(repositories):
    notes, _, connection = repositories
    assert notes.save(1, '  synthetic note  ') == 'synthetic note'
    notes.save(2, '<script>synthetic text</script>')
    assert notes.for_jobs([1, 2]) == {1: 'synthetic note', 2: '<script>synthetic text</script>'}
    replacement = PostgresPersonalNotesRepository('postgresql://localhost/synthetic')
    replacement._connect = connection.connect
    replacement.save(1, 'edited')
    assert notes.for_jobs([1]) == {1: 'edited'}
    notes.save(1, ' ')
    assert notes.for_jobs([1]) == {}
    assert notes.for_jobs([]) == {}
    for invalid in (None, 'x' * 2001, 'x\x00x'):
        with pytest.raises(ValueError):
            notes.save(2, invalid)


def test_atomic_dismiss_undo_noop_and_missing(repositories):
    _, undo, connection = repositories
    assert undo.change_status(1, 'dismiss') == {'status': 'ignored', 'changed': True}
    assert undo.previous(1) == 'applied'
    assert undo.change_status(1, 'dismiss') == {'status': 'ignored', 'changed': False}
    assert undo.previous(1) == 'applied'
    assert undo.change_status(1, 'restore') == {'status': 'applied', 'changed': True}
    assert undo.previous(1) is None
    assert undo.change_status(1, 'restore') == {'status': 'applied', 'changed': False}
    assert undo.change_status(900, 'dismiss') is None
    with pytest.raises(ValueError):
        undo.change_status(1, 'forged')
    with pytest.raises(ValueError):
        undo.remember(1, 'ignored')
    connection.db.execute("UPDATE public.job_postings SET status='ignored' WHERE id=2")
    connection.db.commit()
    assert undo.change_status(2, 'restore') == {'status': 'reviewed', 'changed': True}


def test_transaction_failure_rolls_back_history_and_status(repositories):
    _, undo, connection = repositories
    connection.fail_update = True
    with pytest.raises(RuntimeError):
        undo.change_status(1, 'dismiss')
    assert undo.previous(1) is None
    assert connection.db.execute('SELECT status FROM public.job_postings WHERE id=1').fetchone()[0] == 'applied'
    connection.fail_update = False
    undo.change_status(1, 'dismiss')
    connection.fail_update = True
    with pytest.raises(RuntimeError):
        undo.change_status(1, 'restore')
    assert undo.previous(1) == 'applied'
    assert connection.db.execute('SELECT status FROM public.job_postings WHERE id=1').fetchone()[0] == 'ignored'


def source_files(tmp_path):
    jobs, private = tmp_path / 'jobs.db', tmp_path / 'notes.db'
    with sqlite3.connect(jobs) as db:
        db.execute('CREATE TABLE job_postings(id INTEGER PRIMARY KEY,job_url TEXT)')
        db.execute("INSERT INTO job_postings VALUES (1,'https://example.test/job/?b=2&a=1')")
    with sqlite3.connect(private) as db:
        db.execute('CREATE TABLE personal_notes(job_id INTEGER PRIMARY KEY,note TEXT)')
        db.execute('CREATE TABLE job_dismissal_history(job_id INTEGER PRIMARY KEY,previous_status TEXT)')
        db.execute("INSERT INTO personal_notes VALUES (1,'synthetic private note')")
        db.execute("INSERT INTO job_dismissal_history VALUES (1,'applied')")
    return jobs, private


def test_offline_plan_maps_identity_and_refuses_ambiguity(tmp_path):
    jobs, private = source_files(tmp_path)
    before = private.read_bytes()
    target = [{'id': 42, 'job_url': 'https://example.test/job?a=1&b=2'}]
    plan = build_plan(jobs, private, target)
    assert plan['notes'][0]['job_id'] == 42
    assert plan['dismissals'][0]['previous_status'] == 'applied'
    assert private.read_bytes() == before
    write_plan(plan, tmp_path / 'private-plan.json')
    with pytest.raises(FileExistsError):
        write_plan(plan, tmp_path / 'private-plan.json')
    for invalid in ([], target * 2, [{'id': 42, 'job_url': 'https://example.test/other'}]):
        with pytest.raises(ValueError):
            build_plan(jobs, private, invalid)


def test_backup_restore_new_path_only_includes_wal(tmp_path):
    _, private = source_files(tmp_path)
    with sqlite3.connect(private) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("UPDATE personal_notes SET note='committed WAL note'")
        db.commit()
        backup = tmp_path / 'private-backup.db'
        backup_sqlite(private, backup)
    restored = tmp_path / 'restored.db'
    restore_sqlite(backup, restored)
    with sqlite3.connect(restored) as db:
        assert db.execute('SELECT note FROM personal_notes').fetchone()[0] == 'committed WAL note'
    with pytest.raises(FileExistsError):
        restore_sqlite(backup, private)
    with pytest.raises(FileExistsError):
        backup_sqlite(private, backup)


def test_missing_source_is_not_created(tmp_path):
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        backup_sqlite(missing, tmp_path / 'backup.db')
    assert not missing.exists()
    assert not (tmp_path / 'backup.db').exists()


def test_postgres_source_snapshot_avoids_unrelated_local_sqlite_ids(tmp_path):
    jobs, private = source_files(tmp_path)
    # The same numeric ID can identify a different posting in a stale local DB.
    authoritative = [{'id': 1, 'job_url': 'https://example.test/actual-postgres-job'}]
    target = [{'id': 7, 'job_url': 'https://example.test/job?a=1&b=2'},
              {'id': 42, 'job_url': 'https://example.test/actual-postgres-job'}]
    before_jobs, before_private = jobs.read_bytes(), private.read_bytes()
    plan = build_plan(None, private, target, source_snapshot=authoritative)
    assert plan['notes'][0]['job_id'] == 42
    assert plan['dismissals'][0]['job_id'] == 42
    assert jobs.read_bytes() == before_jobs and private.read_bytes() == before_private
    with pytest.raises(ValueError, match='exactly one'):
        build_plan(jobs, private, target, source_snapshot=authoritative)
    with pytest.raises(ValueError, match='exactly one'):
        build_plan(None, private, target)
    for invalid in (authoritative * 2,
                    [{'id': True, 'job_url': authoritative[0]['job_url']}],
                    authoritative + [{'id': 1, 'job_url': 'https://example.test/different'}]):
        with pytest.raises(ValueError):
            build_plan(None, private, target, source_snapshot=invalid)


def test_snapshot_cli_dry_run_prints_counts_only_and_never_changes_source(tmp_path):
    _, private = source_files(tmp_path)
    source = tmp_path / 'source.json'
    target = tmp_path / 'target.json'
    output = tmp_path / 'plan.json'
    source.write_text(json.dumps([{'id': 1, 'job_url': 'https://example.test/job?a=1&b=2'}]))
    target.write_text(json.dumps([{'id': 42, 'job_url': 'https://example.test/job?a=1&b=2'}]))
    before = private.read_bytes()
    script = Path(__file__).resolve().parents[1] / 'scripts/migrate_private_data.py'
    result = subprocess.run([sys.executable, '-B', str(script), 'dry-run',
        '--source-snapshot', str(source), '--notes', str(private),
        '--target-snapshot', str(target), '--output', str(output)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '1 notes, 1 undo records' in result.stdout
    assert 'synthetic private note' not in result.stdout + result.stderr
    assert json.loads(output.read_text())['notes'][0]['job_id'] == 42
    assert private.read_bytes() == before
