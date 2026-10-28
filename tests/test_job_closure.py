"""Synthetic complete-board evidence; no employer requests or production data."""

from dataclasses import replace
from unittest.mock import Mock
from urllib.error import HTTPError
import time

import pytest

from src.models import JobPosting
from src.scrapers.closure_evidence import classify, snapshot
from src.scrapers.budgeted_scan import DurableSummary
from src.storage.closure_review import review_closures, report_after_scan
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.storage.scan_store import ScanStore


SOURCE = 'https://boards.greenhouse.io/synthetic'


def evidence():
    return snapshot(SOURCE, {'meta': {'total': 1}, 'jobs': [dict(id=200, title='Student',
                    absolute_url='https://boards.greenhouse.io/synthetic/jobs/200')]}, 'greenhouse')


def job(number=100, status='new'):
    return JobPosting('Software Student', 'Synthetic', 'Israel',
                      f'https://boards.greenhouse.io/synthetic/jobs/{number}',
                      description='Develop software as a student.', source=SOURCE, status=status)


@pytest.fixture
def store(tmp_path):
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    target = ScanStore(repo)
    target.migrate()
    yield target
    target.close()


def summary(status='full'):
    return DurableSummary(status=status, closure_snapshots={'Synthetic': evidence()})


@pytest.mark.parametrize('status', ['new', 'reviewed', 'applied', 'rejected', 'ignored'])
def test_only_reviewable_jobs_are_dismissed_and_upsert_preserves(store, status):
    item = job(status=status)
    item.id = store.repository.save(item)
    result = review_closures(store, summary(), enabled=True)
    expected = 'ignored' if status in {'new', 'reviewed'} else status
    assert store.repository.get_by_id(item.id).status == expected
    assert result['jobs_auto_dismissed'] == int(status in {'new', 'reviewed'})
    store.repository.save(job())
    assert store.repository.get_by_id(item.id).status == expected
    assert review_closures(store, summary(), enabled=True)['jobs_auto_dismissed'] == 0


def test_open_unchanged_and_url_change_uses_stable_identity(store):
    item = job(200)
    item.job_url = 'https://careers.example.test/renamed-student?gh_jid=200'
    item.id = store.repository.save(item)
    result = review_closures(store, summary(), enabled=True)
    assert result['jobs_open'] == 1 and result['jobs_auto_dismissed'] == 0
    assert store.repository.get_by_id(item.id).status == 'new'


def test_restore_is_permanent_opt_out_and_user_content_unchanged(store, tmp_path):
    item = job()
    item.id = store.repository.save(item)
    before = store.repository.get_by_id(item.id)
    notes = PersonalNotesRepository(tmp_path / 'notes.db')
    notes.initialize()
    notes.save(item.id, 'Synthetic test note')
    review_closures(store, summary(), enabled=True)
    store.repository.update_status(item.id, 'reviewed')
    result = review_closures(store, summary(), enabled=True)
    after = store.repository.get_by_id(item.id)
    assert result['jobs_auto_dismissed'] == 0
    assert replace(before, status='reviewed') == after
    assert notes.for_jobs([item.id])[item.id] == 'Synthetic test note'
    assert store.execute('SELECT previous_status FROM job_closure_dismissals').fetchone()['previous_status'] == 'new'


@pytest.mark.parametrize('status', ['partial', 'failed'])
def test_unsuccessful_scan_never_changes_status(store, status):
    item = job()
    item.id = store.repository.save(item)
    result = review_closures(store, summary(status), enabled=True)
    assert result['deferred'] and result['jobs_checked'] == 0
    assert store.repository.get_by_id(item.id).status == 'new'


def test_dry_run_default_is_read_only_even_without_optional_schema(store, monkeypatch):
    store.execute('DROP TABLE job_closure_dismissals')
    item = job()
    item.id = store.repository.save(item)
    monkeypatch.delenv('AJOB_AUTO_DISMISS_CLOSED', raising=False)
    # SQLite itself enforces read-only throughout the report.
    store.execute('PRAGMA query_only=ON')
    result = report_after_scan(store, summary(), seconds=5, cancelled=lambda: False)
    assert result['mode'] == 'report_only'
    assert result['jobs_closed'] == result['jobs_would_dismiss'] == 1
    assert result['jobs_auto_dismissed'] == result['checks_failed'] == 0
    assert store.repository.get_by_id(item.id).status == 'new'


def test_enable_without_migration_fails_safely(store):
    store.execute('DROP TABLE job_closure_dismissals')
    item = job()
    item.id = store.repository.save(item)
    result = review_closures(store, summary(), enabled=True)
    assert result['checks_failed'] == 1
    assert store.repository.get_by_id(item.id).status == 'new'


def test_failed_maintenance_probe_cannot_break_delivery_path(store):
    def failed_probe():
        raise RuntimeError('Synthetic heartbeat failure')
    result = report_after_scan(store, summary(), seconds=5, cancelled=failed_probe)
    assert result['deferred'] and result['checks_failed'] == 1


@pytest.mark.parametrize('payload', [None, {}, {'jobs': []}, {'jobs': [None]},
    {'jobs': [{'id': 1}]}, {'jobs': [dict(id=1, title='Student', absolute_url='https://example.test/jobs/1')], 'meta': {'total': 10}},
    {'jobs': [dict(id=1, title='Student', absolute_url='https://example.test/jobs/1')], 'next_page': 2}])
def test_partial_empty_or_invalid_feed_is_unknown(payload):
    assert snapshot(SOURCE, payload, 'greenhouse') is None
    assert classify(job(), None) == 'UNKNOWN'


def test_missing_total_or_duplicate_ids_never_establish_completeness():
    item = dict(id=200, title='Student', absolute_url=SOURCE + '/jobs/200')
    assert snapshot(SOURCE, {'jobs': [item]}, 'greenhouse') is None
    assert snapshot(SOURCE, {'jobs': [item, item], 'meta': {'total': 2}}, 'greenhouse') is None


@pytest.mark.parametrize('reason', ['403', '429', 'Timeout', '500', 'CAPTCHA', 'ParserError', '404'])
def test_failed_or_unsupported_sources_remain_unknown(store, reason, monkeypatch):
    from src.models import Company
    from src.scrapers import careers_scraper
    error = HTTPError(SOURCE, int(reason), 'synthetic', {}, None) if reason.isdigit() else (
        TimeoutError() if reason == 'Timeout' else ValueError(reason))
    monkeypatch.setattr(careers_scraper, 'fetch_greenhouse_jobs', Mock(side_effect=error))
    fetched = careers_scraper.CareersScraper(Mock()).scan_company(Company('Synthetic', SOURCE))
    assert fetched.error and fetched.closure_snapshot is None
    store.repository.save(job())
    result = review_closures(store, DurableSummary(status='full'), enabled=True)
    assert result['jobs_unknown'] == 1 and result['jobs_auto_dismissed'] == 0


def test_collector_keeps_closure_evidence_out_of_logged_metrics(monkeypatch):
    from src.models import Company
    from src.scrapers import careers_scraper
    def fetch(company, timeout, metrics):
        metrics.update(raw_listings=1, _closure_snapshot=evidence())
        return [job(200)]
    monkeypatch.setattr(careers_scraper, 'fetch_greenhouse_jobs', fetch)
    result = careers_scraper.CareersScraper(Mock()).scan_company(Company('Synthetic', SOURCE))
    assert result.closure_snapshot == evidence()
    assert result.collection_metrics == {'raw_listings': 1}


def test_source_boundary_and_unknown_id_are_never_closed():
    item = job()
    item.source = 'https://boards.greenhouse.io/different'
    assert classify(item, evidence()) == 'UNKNOWN'
    item.job_url = 'https://boards.greenhouse.io/different/jobs/100'
    assert classify(item, evidence()) == 'UNKNOWN'
    item.source = SOURCE
    item.job_url = 'https://careers.example.test/role-without-id'
    assert classify(item, evidence()) == 'UNKNOWN'


def test_elbit_strict_active_snapshot():
    source = 'https://elbitsystemscareer.com/'
    data = [dict(jobId=10, status=1, jobTitle='Student')]
    feed = snapshot(source, data, 'elbit')
    item = JobPosting('Student', 'Elbit', 'Israel',
                      'https://elbitsystemscareer.com/job/?jid=10', source=source)
    assert classify(item, feed) == 'OPEN'
    item.job_url = 'https://elbitsystemscareer.com/job?jid=11'
    assert classify(item, feed) == 'CLOSED'
    for status in [0, False, 2, None]:
        assert snapshot(source, [dict(data[0], status=status)], 'elbit') is None


def test_atomic_rollback_on_write_failure(store, monkeypatch):
    item = job()
    item.id = store.repository.save(item)
    execute = store.execute
    def failing(query, values=()):
        if query.startswith('UPDATE job_postings'):
            raise RuntimeError('Synthetic failure')
        return execute(query, values)
    monkeypatch.setattr(store, 'execute', failing)
    assert review_closures(store, summary(), enabled=True)['checks_failed'] == 1
    assert not execute('SELECT * FROM job_closure_dismissals').fetchall()
    assert store.repository.get_by_id(item.id).status == 'new'


def test_hundreds_of_jobs_no_http_and_bounded_defer(store):
    for number in range(1, 501):
        store.repository.save(job(number))
    started = time.monotonic()
    result = review_closures(store, summary())
    assert result['jobs_checked'] == 500
    assert result['jobs_open'] == 1 and result['jobs_closed'] == 499
    assert time.monotonic() - started < 5
    assert review_closures(store, summary(), seconds=0)['deferred']
    assert review_closures(store, summary(), cancelled=lambda: True)['jobs_checked'] == 0


def closure_source_worker(company, timeout_ms, max_pages, headless, pipe):
    pipe.send(('closure_snapshot', evidence()))
    pipe.send(('done',))
    pipe.close()


def failed_closure_source_worker(company, timeout_ms, max_pages, headless, pipe):
    pipe.send(('closure_snapshot', evidence()))
    pipe.send(('error', 'SyntheticSourceError'))
    pipe.close()


@pytest.mark.parametrize('worker,expected', [(closure_source_worker, 'full'),
                                           (failed_closure_source_worker, 'partial')])
def test_budgeted_scan_reuses_snapshot_only_after_source_success(store, worker, expected, capsys):
    from src.models import Company
    from src.scrapers.budgeted_scan import scan_with_budgets
    store.repository.save(job())
    run = store.start_run('synthetic')
    result = scan_with_budgets([Company('Synthetic', SOURCE)], store, run, 'synthetic',
                              worker=worker, source_seconds=10, scan_seconds=15)
    # With no completed source or discoveries the scan is failed, not partial.
    assert result.status == ('full' if expected == 'full' else 'failed')
    assert bool(result.closure_snapshots) == (expected == 'full')
    assert 'closure_review' in capsys.readouterr().out
    assert store.repository.list_all()[0].status == 'new'
