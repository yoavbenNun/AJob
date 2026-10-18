from datetime import datetime, timedelta, timezone
import json

import pytest

from src.models import Company, JobPosting
from src.notifications.email_notifier import DeliveryError, SmtpSettings
from src.scrapers.budgeted_scan import DurableSummary
from src.scheduling.window_runner import scheduled_tick
from src.storage.job_repository import JobRepository
from src.storage.window_store import WindowStore, LeaseLost, expected_windows, stamp


class Clock:
    def __init__(self, value='2026-07-01T05:17:00+00:00'):
        self.value = datetime.fromisoformat(value)
    def __call__(self): return self.value
    def advance(self, **kwargs): self.value += timedelta(**kwargs)


SETTINGS = SmtpSettings('localhost', 25, '', '', 'from@example.test', 'to@example.test', False)
COMPANIES = [Company('Synthetic', 'https://example.test')]


def posting(number=1):
    return JobPosting('Intern', 'Synthetic', 'Remote', f'https://example.test/jobs/{number}')


@pytest.fixture
def environment(tmp_path):
    repository = JobRepository(tmp_path / 'round2.db')
    repository.initialize()
    target = WindowStore(repository)
    target.migrate()
    target.close()
    return repository, Clock()


def successful_scanner(calls):
    def run(companies, store, run_id, slot, **options):
        calls.append((slot, [c.name for c in companies], options['scan_seconds']))
        assert not options['cancelled']()
        store.save_discovery(posting(), slot)
        for company in companies:
            store.record_metrics(run_id, company.name, {'status': 'full'})
        return DurableSummary(status='full', jobs_saved=1)
    return run


def tick(environment, calls, sent, sender=None):
    repo, clock = environment
    return scheduled_tick(COMPANIES, repo, SETTINGS, clock=clock,
                          scanner=successful_scanner(calls),
                          sender=sender or (lambda _, message: sent.append(message)))


def test_smtp_retry_never_rescans_completed_window(environment):
    calls, sent = [], []
    def fail(*args): raise DeliveryError(ConnectionError(), False)
    assert tick(environment, calls, sent, fail) == 1
    environment[1].advance(minutes=6)
    assert tick(environment, calls, sent) == 0
    assert len(calls) == len(sent) == 1
    assert tick(environment, calls, sent) == 0
    assert len(calls) == len(sent) == 1


def test_hours_late_wakeup_scans_current_window_and_reports_missed(environment):
    environment[1].value = datetime.fromisoformat('2026-07-01T09:20:00+00:00')
    calls, sent = [], []
    assert tick(environment, calls, sent) == 1  # the missed morning remains explicit
    assert len(calls) == 1 and 'T11:17:' in calls[0][0]
    store = WindowStore(environment[0], environment[1])
    try:
        report = store.report(1)
        assert report[0]['scan'] == 'failed'
        assert report[0]['failure_reason'] == 'MissedWindow'
        assert report[1]['scan'] == 'full'
        assert report[0]['notification'] == report[1]['notification'] == 'sent'
        old_message = next(msg for msg in sent if 'T08:17:' in msg.get_content())
        assert 'Intern at' not in old_message.get_content()
    finally: store.close()


def test_crash_during_smtp_recovers_as_uncertain(environment):
    calls, sent = [], []
    def crash(*args): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): tick(environment, calls, sent, crash)
    environment[1].advance(minutes=11)
    assert tick(environment, calls, sent) == 1
    assert len(calls) == 1 and not sent
    store = WindowStore(environment[0], environment[1])
    try:
        assert store.report(1)[0]['notification'] == 'uncertain'
    finally: store.close()


def test_restart_before_smtp_can_retry_after_lease(environment):
    repo, clock = environment
    store = WindowStore(repo, clock)
    store.seed_windows()
    slot, run, deadline = store.claim_window()
    store.save_discovery(posting(), slot)
    store.record_metrics(run, 'Synthetic', {'status': 'full'})
    store.finish_window(run, DurableSummary(status='full'), ['Synthetic'])
    assert store.claim_delivery(slot)
    store.prepare_batch(slot)
    store.close()
    clock.advance(minutes=11)
    store = WindowStore(repo, clock)
    store.recover()
    assert store.batch_state(slot) == 'failed'
    store.close()
    clock.advance(minutes=6)
    calls, sent = [], []
    assert tick(environment, calls, sent) == 0
    assert not calls and len(sent) == 1
    assert 'Intern' in sent[0].get_content()


def test_stale_preparer_cannot_send_after_new_owner(environment):
    repo, clock = environment
    first, second = WindowStore(repo, clock), WindowStore(repo, clock)
    try:
        slot = next(expected_windows(clock()))[0]
        assert first.claim_delivery(slot)
        first.prepare_batch(slot)
        first.freeze_message(slot, '{}')
        clock.advance(minutes=11)
        second.recover()
        clock.advance(minutes=6)
        assert second.claim_delivery(slot)
        assert not first.begin_send(slot)
        first.finish_send(slot, 'sent')
        assert second.begin_send(slot)
        second.finish_send(slot, 'sent')
        assert second.batch_state(slot) == 'sent'
    finally:
        first.close(); second.close()


def test_scan_lease_fences_old_writes(environment):
    repo, clock = environment
    store = WindowStore(repo, clock)
    try:
        store.seed_windows()
        slot, run, _ = store.claim_window()
        clock.advance(minutes=3)
        store.recover()
        with pytest.raises(LeaseLost): store.save_discovery(posting(), slot)
        assert not store.known_urls()
        assert store.report(1)[0]['failure_reason'] == 'ScanLeaseExpired'
    finally: store.close()


def test_partial_retry_only_visits_unfinished_sources(environment):
    repo, clock = environment
    companies = COMPANIES + [Company('Second', 'https://example.test')]
    calls = []
    def scan(remaining, store, run, slot, **options):
        calls.append([c.name for c in remaining])
        for company in remaining:
            store.record_metrics(run, company.name, {'status': 'full' if company.name == 'Synthetic' or len(calls) == 2 else 'failed'})
        return DurableSummary(status='partial' if len(calls) == 1 else 'full')
    sender = lambda *args: None
    assert scheduled_tick(companies, repo, SETTINGS, clock=clock, scanner=scan, sender=sender) == 1
    clock.advance(minutes=6)
    assert scheduled_tick(companies, repo, SETTINGS, clock=clock, scanner=scan, sender=sender) == 0
    assert calls == [['Synthetic','Second'], ['Second']]


def test_scan_budget_reserves_notification_time(environment):
    environment[1].advance(minutes=100)
    calls, sent = [], []
    tick(environment, calls, sent)
    assert 0 < calls[0][2] <= 600  # only 20 minutes left; keep 10 for finalization


def test_late_old_window_never_replaces_new_window(environment):
    calls, sent = [], []
    tick(environment, calls, sent)
    environment[1].advance(hours=3)
    tick(environment, calls, sent)
    tick(environment, calls, sent)  # represents a very late older cron wake-up
    assert len(calls) == 2
    assert 'T08:17:' in calls[0][0] and 'T11:17:' in calls[1][0]
    assert len(sent) == 2
    # Same job in the later window is refreshed, not rediscovered or re-notified.
    assert sum('Intern at' in msg.get_content() for msg in sent) == 1


@pytest.mark.parametrize('value,offset', [('2026-03-27T05:17:00+00:00','+03:00'), ('2026-10-25T06:17:00+00:00','+02:00')])
def test_dst_expected_windows(value, offset):
    reference = datetime.fromisoformat(value)
    slots = list(expected_windows(reference))
    assert len(slots) == 3
    assert all(slot.endswith(offset) for slot, _, _ in slots)
    assert slots[0][1] == stamp(reference)


def test_israel_midnight_does_not_replay_yesterday(environment):
    repo, clock = environment
    clock.value = datetime.fromisoformat('2026-07-01T21:30:00+00:00')
    calls, sent = [], []
    assert tick(environment, calls, sent) == 0
    assert not calls and not sent
    store = WindowStore(repo, clock)
    try:
        report = store.report(7)
        assert len(report) == 21
        assert report[-3]['slot'].startswith('2026-07-02')
        assert report[-3]['scan'] == 'scheduled'
        assert report[0]['failure_reason'] == 'NotObserved'
    finally: store.close()


def test_delivery_attempts_bounded_and_backoff(environment):
    repo, clock = environment
    store = WindowStore(repo, clock)
    try:
        slot = next(expected_windows(clock()))[0]
        for attempt in range(3):
            assert store.claim_delivery(slot)
            store.finish_send(slot, 'failed', 'SyntheticFailure')
            assert not store.claim_delivery(slot)
            clock.advance(minutes=21)
        assert not store.claim_delivery(slot)
        assert store.batch_state(slot) == 'failed'
    finally: store.close()


def test_scan_near_hour_budget_preserves_time_for_notification(environment):
    repo, clock = environment
    sent = []
    def bounded_scan(companies, store, run, slot, **options):
        assert options['scan_seconds'] == 3600
        store.save_discovery(posting(), slot)
        # Simulate work and healthy heartbeats; never wait or contact job sites.
        for minute in range(59):
            clock.advance(minutes=1)
            assert store.heartbeat()
        store.record_metrics(run, 'Synthetic', {'status':'partial'})
        return DurableSummary(status='partial', jobs_saved=1, failures=1)
    assert scheduled_tick(COMPANIES,repo,SETTINGS,clock=clock,scanner=bounded_scan,
                          sender=lambda _,message: sent.append(message)) == 1
    assert len(sent) == 1 and 'partial' in sent[0].get_content()


def test_round1_completed_scan_is_not_repeated_after_upgrade(environment):
    from src.storage.scan_store import ScanStore
    repo, clock = environment
    slot = next(expected_windows(clock()))[0]
    previous = ScanStore(repo)
    run = previous.start_run(slot)
    previous.save_discovery(posting(),slot)
    previous.finish_run(run,DurableSummary(status='full',jobs_saved=1))
    previous.close()
    calls, sent = [], []
    assert tick(environment,calls,sent) == 0
    assert not calls and len(sent) == 1


def test_old_uncertain_mail_remains_visible_after_midnight(environment):
    repo, clock = environment
    store = WindowStore(repo,clock)
    slot = next(expected_windows(clock()))[0]
    store.claim_delivery(slot)
    store.prepare_batch(slot)
    store.freeze_message(slot,'{}')
    assert store.begin_send(slot)
    store.finish_send(slot,'uncertain','SyntheticDisconnect')
    store.close()
    clock.advance(hours=18)
    calls, sent = [], []
    assert tick(environment,calls,sent) == 1
    assert not calls and not sent


def test_legacy_email_path_is_blocked_after_managed_activation(environment):
    from src.notifications.durable_scan import run_durable_scan
    repo, clock = environment
    store = WindowStore(repo,clock)
    store.seed_windows()
    store.close()
    with pytest.raises(RuntimeError,match='Managed windows'):
        run_durable_scan([],repo,SETTINGS,'manual-test',
            scanner=lambda *a,**k: pytest.fail('legacy scan bypassed leases'),
            sender=lambda *a: pytest.fail('legacy mail bypassed leases'))


def test_new_worker_rejects_old_schema_without_modifying_it(tmp_path):
    repo = JobRepository(tmp_path/'legacy.db')
    repo.initialize()
    repo.save(posting())
    with pytest.raises(Exception):
        scheduled_tick(COMPANIES,repo,SETTINGS,clock=Clock(),
            scanner=lambda *a,**k: pytest.fail('scan started'),sender=lambda *a:pytest.fail('mail sent'))
    assert len(repo.list_all()) == 1
    with repo._connect() as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='scan_windows'").fetchone()
