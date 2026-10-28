"""Recovery warning/failure boundaries; fixtures never contact SMTP or employers."""

import json
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import pytest

from test_window_recovery import Clock, SETTINGS, COMPANIES, posting
from src.models import Company
from src.scrapers.budgeted_scan import DurableSummary
from src.scrapers.failure_diagnostics import source_failure
from src.scrapers import careers_scraper
from src.scheduling.outcomes import report_exit_code
from src.scheduling.window_runner import scheduled_tick
from src.storage.job_repository import JobRepository
from src.storage.window_store import WindowStore
from src.storage.closure_review import review_closures


@pytest.fixture
def environment(tmp_path):
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    store = WindowStore(repo, Clock())
    store.migrate()
    store.close()
    return repo, Clock()


def partial_scan(companies, store, run, slot, **options):
    store.save_discovery(posting(), slot)
    for company in companies:
        store.record_metrics(run, company.name, {'status': 'failed'})
    return DurableSummary(status='partial', jobs_saved=1)


def report(environment, **options):
    store = WindowStore(*environment)
    try:
        return store.report(days=1, include_unresolved=True, **options)
    finally:
        store.close()


def test_partial_warning_retains_failure_without_independent_monitor(environment, capsys):
    repo, clock = environment
    sent = []
    assert scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=partial_scan,
                          sender=lambda _, msg: sent.append(msg)) == 1
    row = report(environment)[0]
    assert row['outcome'] == 'PARTIAL_RECOVERABLE' and row['scan'] == 'partial'
    assert row['notification'] == 'sent' and row['recovery_planned']
    assert not row['recovery_monitor_verified']
    assert '::warning::PARTIAL_RECOVERABLE' in capsys.readouterr().out
    assert len(sent) == 1


def test_recovery_full_only_checks_own_source_and_never_resends(environment):
    repo, clock = environment
    companies = COMPANIES + [Company('Second', 'https://example.test')]
    sent, visits, closure_reports = [], [], []
    def scan(remaining, store, run, slot, **options):
        names = [c.name for c in remaining]
        visits.append(names)
        for company in remaining:
            store.record_metrics(run, company.name, {'status': 'full' if company.name == 'Synthetic' or len(visits) == 2 else 'failed'})
        result = DurableSummary(status='partial' if len(visits) == 1 else 'full')
        # A recovery has no snapshots for earlier companies. UNKNOWN is safe,
        # and the closure review cannot promote the first partial result.
        closure_reports.append(review_closures(store, result))
        assert result.status == ('partial' if len(visits) == 1 else 'full')
        return result
    options = dict(clock=clock, scanner=scan, sender=lambda _, msg: sent.append(msg))
    assert scheduled_tick(companies, repo, SETTINGS, **options) == 1
    clock.advance(minutes=6)
    assert scheduled_tick(companies, repo, SETTINGS, **options) == 0
    assert visits == [['Synthetic', 'Second'], ['Second']]
    assert len(sent) == 1
    assert closure_reports[0]['deferred'] and closure_reports[1]['jobs_closed'] == 0
    assert report(environment)[0]['outcome'] == 'FULL'


def test_exhausted_recovery_is_terminal_even_when_mail_was_sent(environment):
    repo, clock = environment
    sent = []
    options = dict(clock=clock, scanner=partial_scan, sender=lambda _, msg: sent.append(msg))
    assert scheduled_tick(COMPANIES, repo, SETTINGS, **options) == 1
    clock.advance(minutes=6)
    assert scheduled_tick(COMPANIES, repo, SETTINGS, **options) == 1
    row = report(environment)[0]
    assert row['scan_attempts'] == 2 and row['outcome'] == 'FINAL_FAILURE'
    assert not row['recovery_planned'] and len(sent) == 1


def test_stopped_recovery_and_watchdog_cannot_hide_failure_after_midnight(environment):
    repo, clock = environment
    scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=partial_scan, sender=lambda *_: None)
    # No tick/recover/dispatch occurs here. The separate SELECT-only report must
    # derive terminal failure, even outside its normal one-day report window.
    clock.advance(days=10)
    store = WindowStore(repo, clock)
    try:
        store.execute('BEGIN READ ONLY' if store.postgres else 'PRAGMA query_only=ON')
        rows = store.report(1, include_unresolved=True)
        old = next(row for row in rows if row['slot'].startswith('2026-07-01T08:17'))
        assert old['outcome'] == 'FINAL_FAILURE'
        assert report_exit_code(rows) == 1
        assert store.execute('SELECT status FROM scan_windows ORDER BY scheduled_at LIMIT 1').fetchone()['status'] == 'partial'
    finally:
        store.close()


def test_lease_expiry_is_visible_without_running_recovery(environment):
    repo, clock = environment
    store = WindowStore(repo, clock)
    try:
        store.seed_windows()
        store.claim_window()  # Simulate runner disappearance, no finalization.
        clock.advance(minutes=3)
        row = store.report(1)[0]
        assert row['outcome'] == 'FINAL_FAILURE' and row['lease_expired']
        assert row['failure_reason'] == 'ScanLeaseExpired'
        store.recover()
        assert store.report(1)[0]['outcome'] == 'FINAL_FAILURE'
    finally:
        store.close()


def test_single_source_recovery_never_closes_other_company(environment):
    from src.scrapers.closure_evidence import snapshot
    repo, clock = environment
    first, second = posting(1), posting(2)
    first.source = 'https://boards.greenhouse.io/synthetic'
    first.job_url = first.source + '/jobs/100'
    second.company = 'Second'
    second.source = 'https://boards.greenhouse.io/second'
    second.job_url = second.source + '/jobs/100'
    for item in (first, second):
        repo.save(item)
    evidence = snapshot(first.source, {'jobs': [dict(id=200, title='Student',
        absolute_url=first.source + '/jobs/200')], 'meta': {'total': 1}}, 'greenhouse')
    store = WindowStore(repo, clock)
    try:
        result = review_closures(store, DurableSummary(status='full', closure_snapshots={'Synthetic': evidence}))
        assert result['jobs_closed'] == 1 and result['jobs_unknown'] == 1
        assert result['jobs_auto_dismissed'] == 0
        assert all(item.status == 'new' for item in repo.list_all())
    finally:
        store.close()


@pytest.mark.parametrize('status,transient', [(403, False), (429, True), (500, True)])
def test_source_http_diagnostics_are_precise_and_private(monkeypatch, status, transient):
    error = HTTPError('https://example.test/private-path', status, 'sensitive test text', {}, None)
    monkeypatch.setattr(careers_scraper, 'fetch_greenhouse_jobs', Mock(side_effect=error))
    result = careers_scraper.CareersScraper(Mock()).scan_company(
        Company('Synthetic', 'https://boards.greenhouse.io/synthetic'))
    details = result.failure_details
    assert details['http_status'] == status and details['transient'] is transient
    assert details['exception_type'] == 'HTTPError' and details['stage'] == 'fetch'
    assert 'private-path' not in json.dumps(details) and 'sensitive test text' not in result.error
    assert result.closure_snapshot is None


def test_wrapped_timeout_diagnostics_do_not_serialize_exception():
    details = source_failure(URLError(TimeoutError('sensitive test text')))
    assert details['timeout'] and details['transient'] and details['http_status'] is None
    assert 'sensitive test text' not in json.dumps(details)


def test_cli_check_is_read_only_and_reports_unrecovered_old_failure(environment, monkeypatch, capsys):
    from src import main
    from src.storage import window_store
    repo, clock = environment
    scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=partial_scan, sender=lambda *_: None)
    clock.advance(days=10)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(window_store, 'WindowStore', lambda repository: WindowStore(repository, clock))
    before = repo.database_path.read_bytes()
    assert main.main(['scan-report', '--database', str(repo.database_path), '--check']) == 1
    assert 'FINAL_FAILURE' in capsys.readouterr().out
    assert repo.database_path.read_bytes() == before
