"""AJ-008 acceptance contract: synthetic incidents, no GitHub/SMTP/Production."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from test_alert_reporting import database, check, incident
from src.scheduling.alert_reporting import failure_events
from src.scheduling.outcomes import report_exit_code, window_alert_scope


REFERENCE = datetime(2026, 11, 10, 9, 28, tzinfo=timezone.utc)


def expired(slot, deadline):
    row = incident(slot=slot, deadline=deadline)
    row["alert_scope"] = window_alert_scope(row, REFERENCE)
    return row


def historical_pair():
    return [
        expired("2026-10-18T08:17:00+03:00", "2026-10-18T07:17:00+00:00"),
        expired("2026-10-31T08:17:00+02:00", "2026-10-31T08:17:00+00:00"),
    ]


def test_42789814970_old_pair_is_not_the_cause_of_a_fresh_failure(database, monkeypatch):
    _, store = database
    history = historical_pair()
    assert {row["alert_scope"] for row in history} == {"historical_scan_failure"}
    recent = expired("2026-11-10T08:17:00+02:00", "2026-11-10T08:17:00+00:00")
    fresh = incident(slot="2026-11-10T11:17:00+02:00", outcome="PARTIAL_RECOVERABLE",
                     scan_attempts=1, failure_reason="SourceFailureOrBudget")
    monkeypatch.delenv("AJOB_SCAN_ALERT_DEDUP")
    assert check(store, history) == 0
    assert check(store, history + [recent]) == 1
    assert check(store, history + [fresh]) == 1
    assert store.execute("SELECT count(*) AS n FROM scan_alert_receipts").fetchone()["n"] == 0


def test_acknowledgement_changes_reporting_exit_not_incident_or_mail(database, monkeypatch):
    _, store = database
    rows = historical_pair() + [
        expired("2026-11-07T08:17:00+02:00", "2026-11-07T08:17:00+00:00")
    ]
    original = deepcopy(rows)
    assert check(store, rows, 101) == 1
    assert check(store, rows, 101) == 1  # Both steps in the first workflow fail.
    for run in range(102, 108):
        assert check(store, rows, run) == 0
    assert rows == original and all(row["outcome"] == "FINAL_FAILURE" for row in rows)
    assert report_exit_code(rows) == 1  # Strict business health is still red.
    assert store.execute("SELECT count(*) AS n FROM notification_batches").fetchone()["n"] == 0
    assert store.execute("SELECT count(*) AS n FROM scan_alert_receipts").fetchone()["n"] == 1
    monkeypatch.setenv("AJOB_SCAN_ALERT_DEDUP", "0")
    assert check(store, rows, 108) == 1  # Rollback restores legacy reporting.


@pytest.mark.parametrize("change", [
    {"scan_attempts": 3, "run_id": "new-recovery-owner"},
    {"notification": "failed", "notification_attempts": 2, "failure_reason": "SmtpFailure"},
    {"notification": "uncertain", "lease_expired": True, "failure_reason": "DeliveryLeaseExpired"},
    {"slot": "2026-11-10T11:17:00+02:00", "outcome": "PARTIAL_RECOVERABLE",
     "scan_attempts": 1, "failure_reason": "SourceFailureOrBudget"},
])
def test_confirmed_history_cannot_hide_new_attempts_or_changed_failures(database, change):
    _, store = database
    old = incident(slot="2026-11-07T08:17:00+02:00")
    assert check(store, [old], 101) == 1
    assert check(store, [old], 102) == 0
    changed = dict(old, **change)
    assert check(store, [old, changed], 103) == 1
    assert check(store, [old, changed], 103) == 1
    assert check(store, [old, changed], 104) == 0
    assert check(store, [old, changed], 105, current_failure=True) == 1


def test_expiry_ignores_only_scan_history_not_an_unresolved_delivery(database):
    _, store = database
    row = incident(deadline=(REFERENCE - timedelta(days=7)).isoformat())
    row["alert_scope"] = window_alert_scope(row, REFERENCE)
    original = deepcopy(row)
    assert failure_events(store, [row]) == []
    assert check(store, [row]) == 0
    row["notification"], row["failure_reason"] = "uncertain", "DeliveryLeaseExpired"
    row["alert_scope"] = window_alert_scope(row, REFERENCE)
    assert row["alert_scope"] == "operational"
    assert check(store, [row], 102) == 1
    assert original["outcome"] == row["outcome"] == "FINAL_FAILURE"


def test_delivery_retry_is_distinct_and_reporting_never_replays_or_resolves_mail(database):
    _, store = database
    values = ("orphan-slot", "failed", "SyntheticSmtpError", "2026-11-10T09:20:00+00:00")
    store.execute("INSERT INTO notification_batches(slot_key,state,error_type,updated_at) VALUES (?,?,?,?)", values)
    assert check(store, [], 101) == 1
    assert check(store, [], 102) == 0
    row = store.execute("SELECT * FROM notification_batches WHERE slot_key='orphan-slot'").fetchone()
    store.execute("UPDATE notification_batches SET updated_at=? WHERE slot_key='orphan-slot'",
                  ("2026-11-10T09:25:00+00:00",))
    assert check(store, [], 103) == 1
    after = store.execute("SELECT * FROM notification_batches WHERE slot_key='orphan-slot'").fetchone()
    assert row["state"] == after["state"] == "failed"
    assert check(store, [], 104) == 0
    assert store.execute("SELECT state FROM notification_batches WHERE slot_key='orphan-slot'").fetchone()["state"] == "failed"


def test_lost_receipts_realert_instead_of_silently_acknowledging(database):
    _, store = database
    assert check(store, [incident()], 101) == 1
    assert check(store, [incident()], 102) == 0
    store.execute("DELETE FROM scan_alert_receipts")  # Synthetic restore-loss simulation only.
    assert check(store, [incident()], 103) == 1
