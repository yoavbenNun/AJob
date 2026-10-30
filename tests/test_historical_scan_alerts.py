"""Historical scan failures stay visible without poisoning unrelated windows."""
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling.outcomes import report_exit_code, window_alert_scope
from src.scheduling.window_runner import scheduled_tick
from src.scrapers.budgeted_scan import DurableSummary
from src.storage.job_repository import JobRepository
from src.storage.window_store import WindowStore, expected_windows, stamp
from test_window_recovery import Clock, COMPANIES, SETTINGS
from test_recovery_outcomes import partial_scan


REFERENCE = datetime(2026, 10, 30, 9, 30, tzinfo=timezone.utc)


def incident(**changes):
    row = dict(outcome='FINAL_FAILURE', scan='partial', notification='sent',
               lease_expired=False, failure_reason='WindowExpired',
               deadline=(REFERENCE - timedelta(days=12)).isoformat())
    row.update(changes)
    row['alert_scope'] = window_alert_scope(row, REFERENCE)
    return row


@pytest.mark.parametrize('scan,reason', [('partial', 'WindowExpired'), ('failed', 'MissedWindow')])
def test_historical_outcome_is_not_rewritten_and_does_not_fail_current_success(scan, reason):
    old = incident(scan=scan, failure_reason=reason)
    assert old['alert_scope'] == 'historical_scan_failure'
    assert old['outcome'] == 'FINAL_FAILURE' and old['scan'] == scan
    assert report_exit_code([old, dict(outcome='FULL')]) == 0


@pytest.mark.parametrize('age,historical', [
    (timedelta(days=6, hours=23, minutes=59), False),
    (timedelta(days=7), True),
    (timedelta(days=40), True),
])
def test_seven_day_alert_period_uses_deadline(age, historical):
    row = incident(deadline=(REFERENCE - age).isoformat())
    assert (row['alert_scope'] == 'historical_scan_failure') is historical
    assert report_exit_code([row]) == int(not historical)


@pytest.mark.parametrize('changes', [
    dict(notification='pending'), dict(notification='failed'),
    dict(notification='uncertain'), dict(notification='processing'),
    dict(notification='sending'), dict(lease_expired=True),
    dict(scan='running'), dict(scan='scheduled'), dict(scan='full'),
    dict(failure_reason='ScanLeaseExpired'),
    dict(failure_reason='DeliveryLeaseExpired'),
    dict(failure_reason='NotObserved', outcome='UNKNOWN'),
    dict(failure_reason='SourceFailureOrBudget'),
    dict(outcome='PARTIAL_RECOVERABLE'),
])
def test_unfinished_or_unverified_incidents_never_age_out(changes):
    row = incident(**changes)
    assert row['alert_scope'] == 'operational'
    assert report_exit_code([row]) == 1


def test_fresh_failure_still_fails_beside_historical_incident():
    assert report_exit_code([incident(), incident(deadline=REFERENCE.isoformat())]) == 1
    # Missing classification is conservative for older callers.
    assert report_exit_code([dict(outcome='FINAL_FAILURE')]) == 1


@pytest.fixture
def environment(tmp_path):
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    store = WindowStore(repo, Clock())
    store.migrate()
    store.close()
    return repo, Clock()


def expire_incident(environment):
    repo, clock = environment
    sent = []
    assert scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock,
                          scanner=partial_scan, sender=lambda _, msg: sent.append(msg)) == 1
    # Other first-day slots completed normally outside this incident scenario.
    store = WindowStore(repo, clock)
    try:
        store.execute("UPDATE scan_windows SET status='full' WHERE status='scheduled'")
        store.execute("INSERT INTO notification_batches(slot_key,state,updated_at) "
                      "SELECT slot_key,'sent',? FROM scan_windows WHERE status='full'",
                      (clock().isoformat(),))
    finally:
        store.close()
    clock.advance(days=10)
    return sent


def test_new_full_window_succeeds_without_replaying_or_relabeling_old_scan(environment, capsys):
    repo, clock = environment
    sent = expire_incident(environment)
    calls = []
    def full(companies, store, run, slot, **options):
        calls.append(slot)
        for company in companies:
            store.record_metrics(run, company.name, {'status': 'full'})
        return DurableSummary(status='full')
    assert scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=full,
                          sender=lambda _, msg: sent.append(msg)) == 0
    assert len(calls) == 1 and calls[0].startswith('2026-07-11')
    assert len(sent) == 2  # One old summary and one new summary; no replay.
    store = WindowStore(repo, clock)
    try:
        rows = store.report(1, include_unresolved=True)
        old = next(r for r in rows if r['slot'].startswith('2026-07-01'))
        assert old['outcome'] == 'FINAL_FAILURE' and old['scan'] == 'partial'
        assert old['alert_scope'] == 'historical_scan_failure'
        assert not old['recovery_planned']
    finally:
        store.close()
    assert '::warning::HISTORICAL_SCAN_FAILURE' in capsys.readouterr().out


def test_new_partial_window_still_fails_alongside_old_incident(environment, capsys):
    repo, clock = environment
    sent = expire_incident(environment)
    assert scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=partial_scan,
                          sender=lambda _, msg: sent.append(msg)) == 1
    output = capsys.readouterr().out
    assert 'PARTIAL_RECOVERABLE' in output and 'HISTORICAL_SCAN_FAILURE' in output
    assert len(sent) == 2


@pytest.mark.parametrize('mail', ['failed', 'uncertain', 'pending'])
def test_old_unsent_delivery_still_fails_report(environment, mail):
    repo, clock = environment
    expire_incident(environment)
    store = WindowStore(repo, clock)
    try:
        store.recover()
        store.execute('UPDATE notification_batches SET state=?', (mail,))
        store.execute('DELETE FROM delivery_leases')  # Test-only fixture, no production.
        rows = store.report(1, include_unresolved=True)
        old = next(r for r in rows if r['slot'].startswith('2026-07-01'))
        assert old['alert_scope'] == 'operational'
        assert report_exit_code([old]) == 1
    finally:
        store.close()


@pytest.mark.parametrize('orphan_state', [None, 'failed', 'uncertain'])
def test_cli_historical_report_remains_read_only_and_visible(environment, monkeypatch, capsys, orphan_state):
    from src import main
    from src.storage import window_store
    repo, clock = environment
    expire_incident(environment)
    def full(companies, store, run, slot, **options):
        for company in companies:
            store.record_metrics(run, company.name, {'status': 'full'})
        return DurableSummary(status='full')
    assert scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock, scanner=full,
                          sender=lambda *_: None) == 0
    store = WindowStore(repo, clock)
    # Complete the otherwise missing seven-day reporting baseline in this fixture.
    for slot, scheduled, deadline in expected_windows(clock(), days=7):
        if scheduled <= stamp(clock()):
            store.execute("INSERT INTO scan_windows(slot_key,scheduled_at,deadline_at,status) "
                          "VALUES (?,?,?,'full') ON CONFLICT(slot_key) DO NOTHING",
                          (slot, scheduled, deadline))
            store.execute("INSERT INTO notification_batches(slot_key,state,updated_at) "
                          "VALUES (?,'sent',?) ON CONFLICT(slot_key) DO NOTHING",
                          (slot, stamp(clock())))
    store.recover()
    if orphan_state:
        store.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,?,?)",
                      ('legacy-orphan', orphan_state, stamp(clock())))
    store.close()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(window_store, 'WindowStore', lambda repository: WindowStore(repository, clock))
    before = repo.database_path.read_bytes()
    assert main.main(['scan-report', '--database', str(repo.database_path), '--check']) == int(orphan_state is not None)
    output = capsys.readouterr().out
    assert 'FINAL_FAILURE' in output and 'historical_scan_failure' in output
    assert repo.database_path.read_bytes() == before
