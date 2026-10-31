"""Synthetic receipt persistence only; no GitHub, SMTP or production access."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from threading import Barrier

import pytest

from src.scheduling.alert_reporting import (
    AlertContext, AlertLedger, alert_exit_code, failure_events, prepare_alert_schema,
)
from src.storage.job_repository import JobRepository
from src.storage.window_store import WindowStore


def context(number=101, attempt=1):
    return AlertContext('yoavbenNun/AJob', str(number), attempt)


def incident(**changes):
    row = dict(slot='2026-10-31T08:17:00+02:00', outcome='FINAL_FAILURE',
               scan='partial', notification='sent', failure_reason='WindowExpired',
               lease_expired=False, scan_attempts=2, notification_attempts=1,
               run_id='scan-owner-1', alert_scope='operational')
    row.update(changes)
    return row


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('AJOB_SCAN_ALERT_DEDUP', '1')
    repository = JobRepository(tmp_path / 'alerts.db')
    repository.initialize()
    store = WindowStore(repository)
    store.migrate()
    prepare_alert_schema(store)
    yield repository, store
    store.close()


def check(store, rows, number=101, verifier=lambda *args: True, **options):
    return alert_exit_code(store, rows, context=context(number), verifier=verifier, **options)


def test_new_failure_fails_and_same_workflow_cannot_acknowledge_itself(database):
    _, store = database
    calls = []
    verify = lambda *args: calls.append(args) or True
    assert check(store, [incident()], verifier=verify) == 1
    assert check(store, [incident()], verifier=verify) == 1
    assert calls == []
    assert store.execute('SELECT count(*) AS n FROM scan_alert_receipts').fetchone()['n'] == 1


def test_verified_failure_is_suppressed_across_repeated_reports(database):
    _, store = database
    assert check(store, [incident()]) == 1
    calls = []
    verify = lambda *args: calls.append(args) or True
    assert check(store, [incident()], 102, verify) == 0
    for number in range(103, 108):
        assert check(store, [incident()], number, verify) == 0
    assert calls == [('yoavbenNun/AJob', '101', 1)]
    receipt = store.execute('SELECT * FROM scan_alert_receipts').fetchone()
    assert receipt['state'] == 'confirmed' and receipt['confirmed_at']


@pytest.mark.parametrize('changes', [
    dict(outcome='FINAL_FAILURE', failure_reason='WindowExpired'),
    dict(scan_attempts=2, run_id='recovery-owner-2'),
    dict(notification='failed', notification_attempts=2, failure_reason='SmtpFailure'),
    dict(notification='uncertain', failure_reason='DeliveryLeaseExpired', lease_expired=True),
])
def test_worsening_recovery_or_delivery_failure_is_a_new_event(database, changes):
    _, store = database
    previous = incident(outcome='PARTIAL_RECOVERABLE', failure_reason='SourceFailureOrBudget',
                        scan_attempts=1)
    assert check(store, [previous]) == 1
    assert check(store, [previous], 102) == 0
    changed = dict(previous, **changes)
    assert check(store, [changed], 103) == 1
    assert check(store, [changed], 104) == 0
    assert store.execute('SELECT count(*) AS n FROM scan_alert_receipts').fetchone()['n'] == 2


def test_new_slot_failure_is_not_hidden_by_confirmed_old_incident(database):
    _, store = database
    assert check(store, [incident()]) == 1
    assert check(store, [incident()], 102) == 0
    new = incident(slot='2026-10-31T11:17:00+02:00')
    assert check(store, [incident(), new], 103) == 1


def test_current_execution_failure_always_fails_even_with_known_history(database):
    _, store = database
    assert check(store, [incident()]) == 1
    assert check(store, [incident()], 102) == 0
    assert check(store, [incident()], 103, current_failure=True) == 1
    assert check(store, [], 104, current_failure=True) == 1


def test_later_full_window_succeeds_without_rewriting_failed_window(database):
    _, store = database
    rows = [incident(), incident(slot='later', outcome='FULL', scan='full')]
    original = deepcopy(rows)
    assert check(store, rows) == 1
    assert check(store, rows, 102) == 0
    assert rows == original and rows[0]['outcome'] == 'FINAL_FAILURE'
    assert check(store, [rows[1]], 103) == 0


def test_restart_retains_confirmation_without_process_memory(database):
    repository, store = database
    assert check(store, [incident()]) == 1
    assert check(store, [incident()], 102) == 0
    store.close()
    reopened = WindowStore(repository)
    try:
        def unavailable(*args):
            raise AssertionError('Confirmed receipt must survive without a new API lookup')
        assert check(reopened, [incident()], 103, unavailable) == 0
    finally:
        reopened.close()


def test_parallel_connections_never_treat_pending_receipts_as_reported(database):
    repository, store = database
    barrier = Barrier(2)
    def run(number):
        own_store = WindowStore(repository)
        try:
            barrier.wait(timeout=5)
            return check(own_store, [incident()], number, lambda *args: False)
        finally:
            own_store.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, [101, 102]))
    # Fail-safe duplicates are allowed until a completed failure is verified.
    assert results == [1, 1]
    receipts = store.execute('SELECT * FROM scan_alert_receipts').fetchall()
    assert len(receipts) == 2 and all(row['state'] == 'pending' for row in receipts)
    assert check(store, [incident()], 103) == 0


def test_cancelled_first_attempt_does_not_permanently_pin_event(database):
    _, store = database
    assert check(store, [incident()], 101) == 1
    assert check(store, [incident()], 102, lambda *args: False) == 1
    # Only the second workflow became a verified failed reporting run.
    verify = lambda repo, run, attempt: run == '102'
    assert check(store, [incident()], 103, verify) == 0
    receipts = store.execute('SELECT * FROM scan_alert_receipts').fetchall()
    assert {row['run_id']: row['state'] for row in receipts} == {
        '101': 'pending', '102': 'confirmed'}


def test_rerun_attempt_is_distinct_from_same_run_id(database):
    _, store = database
    events = failure_events(store, [incident()])
    assert AlertLedger(store, context(), lambda *args: True).check(events) == 1
    assert AlertLedger(store, context(attempt=2), lambda *args: True).check(events) == 0


@pytest.mark.parametrize('state', ['failed', 'uncertain'])
def test_orphan_mail_failure_is_deduplicated_but_new_attempt_alerts(database, state):
    _, store = database
    store.execute("INSERT INTO notification_batches(slot_key,state,error_type,updated_at) VALUES (?,?,?,?)",
                  ('legacy', state, 'SyntheticSmtpError', '2026-10-31T10:00:00+00:00'))
    assert check(store, []) == 1
    assert check(store, [], 102) == 0
    store.execute("UPDATE notification_batches SET updated_at=? WHERE slot_key='legacy'",
                  ('2026-10-31T10:05:00+00:00',))
    assert check(store, [], 103) == 1
    assert store.execute("SELECT state FROM notification_batches WHERE slot_key='legacy'").fetchone()['state'] == state


def test_volatile_report_values_do_not_create_new_incidents(database):
    _, store = database
    old = incident(duration_seconds=10, retry_at='earlier')
    assert check(store, [old]) == 1
    changed = dict(old, duration_seconds=999, retry_at='later', jobs_discovered=999)
    assert check(store, [changed], 102) == 0


def test_api_failure_is_fail_closed_and_does_not_confirm(database):
    _, store = database
    assert check(store, [incident()]) == 1
    def unavailable(*args):
        raise RuntimeError('synthetic unavailable')
    assert check(store, [incident()], 102, unavailable) == 1
    assert store.execute('SELECT state FROM scan_alert_receipts').fetchone()['state'] == 'pending'


def test_cancelled_receipts_cannot_starve_later_verified_reporting_failure(database):
    _, store = database
    for number in range(101, 105):
        assert check(store, [incident()], number, lambda *args: False) == 1
    verify = lambda repo, run, attempt: int(run) >= 105
    assert check(store, [incident()], 105, verify) == 1  # Budget exhaustion.
    assert store.execute("SELECT count(*) AS n FROM scan_alert_receipts WHERE run_id='105'").fetchone()['n'] == 1
    assert check(store, [incident()], 106, verify) == 0


def test_unreadable_old_run_does_not_pin_event_after_access_recovers(database):
    _, store = database
    assert check(store, [incident()]) == 1
    def unavailable(*args):
        raise RuntimeError('synthetic deleted run')
    assert check(store, [incident()], 102, unavailable) == 1
    assert check(store, [incident()], 103, lambda repo, run, attempt: run == '102') == 0


def test_database_failure_is_fail_closed_even_when_rows_are_full(database, monkeypatch):
    _, store = database
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic database failure')
    monkeypatch.setattr(store, 'execute', unavailable)
    assert check(store, [incident(outcome='FULL')]) == 1


def test_missing_schema_fails_closed_and_normal_migrate_does_not_install_it(database):
    _, store = database
    store.execute('DROP TABLE scan_alert_receipts')
    store.execute('DROP TABLE scan_alert_schema')
    store.migrate()
    assert check(store, []) == 1
    assert store.execute("SELECT name FROM sqlite_master WHERE name='scan_alert_receipts'").fetchone() is None


def test_missing_version_is_fail_closed(database):
    _, store = database
    store.execute('DELETE FROM scan_alert_schema')
    assert check(store, []) == 1


def test_corrupt_payload_cannot_suppress_failure(database):
    _, store = database
    assert check(store, [incident()]) == 1
    store.execute("UPDATE scan_alert_receipts SET event_json='{}'")
    assert check(store, [incident()], 102) == 1


def test_corrupt_confirmation_is_fail_closed(database):
    _, store = database
    assert check(store, [incident()]) == 1
    assert check(store, [incident()], 102) == 0
    store.execute("UPDATE scan_alert_receipts SET confirmed_at='invalid-date'")
    assert check(store, [incident()], 103) == 1


def test_default_off_uses_legacy_strict_check_without_schema(database, monkeypatch):
    _, store = database
    monkeypatch.delenv('AJOB_SCAN_ALERT_DEDUP')
    store.execute('DROP TABLE scan_alert_receipts')
    store.execute('DROP TABLE scan_alert_schema')
    assert check(store, [incident()]) == 1
    assert check(store, [incident(outcome='FULL')]) == 0


def test_invalid_activation_context_or_unknown_outcome_fails_closed(database, monkeypatch):
    _, store = database
    monkeypatch.setenv('AJOB_SCAN_ALERT_DEDUP', 'invalid')
    assert check(store, []) == 1
    monkeypatch.setenv('AJOB_SCAN_ALERT_DEDUP', '1')
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert alert_exit_code(store, [], verifier=lambda *args: True) == 1
    assert check(store, [incident(outcome='unexpected')]) == 1


def test_archived_history_is_retained_after_seven_days_without_failing_new_report(database):
    repository, store = database
    store.clock = lambda: datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
    slot = '2026-10-18T08:17:00+03:00'
    store.execute("INSERT INTO scan_windows(slot_key,scheduled_at,deadline_at,status,attempts,error_type) VALUES (?,?,?,'partial',2,'WindowExpired')",
                  (slot, '2026-10-18T05:17:00+00:00', '2026-10-18T07:17:00+00:00'))
    store.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,'sent',?)",
                  (slot, '2026-10-18T06:00:00+00:00'))
    rows = [row for row in store.report(1, include_unresolved=True) if row['slot'] == slot]
    assert len(rows) == 1 and rows[0]['outcome'] == 'FINAL_FAILURE'
    assert rows[0]['alert_scope'] == 'historical_scan_failure'
    before = json.dumps(rows, sort_keys=True)
    assert check(store, rows) == 0
    assert json.dumps(rows, sort_keys=True) == before
    persisted = store.execute('SELECT status,attempts FROM scan_windows WHERE slot_key=?', (slot,)).fetchone()
    assert persisted['status'] == 'partial' and persisted['attempts'] == 2
