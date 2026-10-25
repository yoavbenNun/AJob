import json
import time
import sqlite3

import pytest

from src.models import Company, JobPosting
from src.storage.job_repository import JobRepository
from src.storage.scan_store import ScanStore
from src.scrapers.budgeted_scan import DurableSummary, scan_with_budgets
from src.notifications.durable_scan import deliver_batch, run_durable_scan
from src.notifications.email_notifier import SmtpSettings, DeliveryError, send_prepared_summary


@pytest.fixture
def store(tmp_path):
    repo = JobRepository(tmp_path / 'test.db')
    repo.initialize()
    result = ScanStore(repo)
    result.migrate()
    yield result
    result.close()


@pytest.fixture
def settings():
    return SmtpSettings('localhost', 25, '', '', 'sender@example.test', 'receiver@example.test', False)


def job():
    return JobPosting('Intern', 'Example', 'Remote', 'https://example.test/jobs/1')


def test_atomic_discovery_and_status_preservation(store):
    store.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON notification_outbox BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_discovery(job(), 'slot')
    assert not store.known_urls()
    store.execute('DROP TRIGGER reject_outbox')
    assert store.save_discovery(job(), 'slot')
    store.execute("UPDATE job_postings SET status='applied'")
    assert not store.save_discovery(job(), 'slot')
    assert store.execute('SELECT status FROM job_postings').fetchone()['status'] == 'applied'
    assert len(store.prepare_batch('slot')) == 1


def test_interruption_after_save_recovers_in_next_slot(store, settings):
    run = store.start_run('old')
    store.save_discovery(job(), 'old')
    # Reopen exactly as after process exit; no in-memory new_jobs survives.
    repo = store.repository
    store.close()
    recovered = ScanStore(repo)
    try:
        messages = []
        assert deliver_batch(recovered, 'next', DurableSummary(status='partial'), settings,
                             sender=lambda _, message: messages.append(message)) == 'sent'
        assert 'Intern' in messages[0].get_content()
        row = recovered.execute('SELECT origin_slot,batch_slot FROM notification_outbox').fetchone()
        assert tuple(row) == ('old', 'next')
    finally:
        recovered.close()


def test_pre_send_stop_and_definite_failure_retry(store, settings):
    store.save_discovery(job(), 'slot')
    store.prepare_batch('slot')  # stop before message creation/send
    calls = []
    def fail(*_):
        raise DeliveryError(ConnectionRefusedError(), False)
    summary = DurableSummary(status='partial')
    assert deliver_batch(store, 'slot', summary, settings, sender=fail) == 'failed'
    assert deliver_batch(store, 'slot', summary, settings,
                         sender=lambda _, msg: calls.append(msg)) == 'sent'
    assert len(calls) == 1
    assert 'Intern' in calls[0].get_content()


@pytest.mark.parametrize('interrupt', [False, True])
def test_uncertain_delivery_never_automatically_retried(store, settings, interrupt):
    store.save_discovery(job(), 'slot')
    def fail(*_):
        if interrupt:
            raise KeyboardInterrupt()
        raise DeliveryError(ConnectionResetError(), True)
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            deliver_batch(store, 'slot', DurableSummary(), settings, sender=fail)
        store.start_run('slot')
    else:
        deliver_batch(store, 'slot', DurableSummary(), settings, sender=fail)
    assert store.batch_state('slot') == 'uncertain'
    assert deliver_batch(store, 'slot', DurableSummary(), settings,
                         sender=lambda *_: pytest.fail('duplicate')) == 'uncertain'


def worker(company, timeout, pages, headed, pipe):
    import os
    if os.name != 'nt':
        os.setsid()
    if company.name == 'slow':
        pipe.send(('phase', 'http'))
        time.sleep(20)
    elif company.name == 'saved':
        pipe.send(('job', True, job()))
        time.sleep(20)
    else:
        pipe.send(('found', 1))
        pipe.send(('done',))
    pipe.close()


def corrected_workday_worker(company, timeout, pages, headed, pipe):
    import os
    if os.name != 'nt':
        os.setsid()
    posting = JobPosting('Software Student', company.name, 'Israel',
                          company.careers_url + '/job/Israel/Software_JR123',
                          source=company.careers_url, description='Develop Python software.')
    pipe.send(('collection', {'focused_discovered': 1, 'global_discovered': 1, 'duplicates_avoided': 1}))
    pipe.send(('job', True, posting))
    pipe.send(('done',))
    pipe.close()


def filter_metrics_worker(company, timeout, pages, headed, pipe):
    import os
    if os.name != 'nt':
        os.setsid()
    pipe.send(('found', 3))
    pipe.send(('collection', {'raw_listings': 4, 'duplicates_avoided': 1}))
    pipe.send(('filter_decision', 'preliminary', False, 'role_type'))
    rejected = job()
    rejected.job_url += '/rejected'
    pipe.send(('job', False, rejected))
    for suffix in ('', '/new'):
        posting = job()
        posting.job_url += suffix
        pipe.send(('filter_decision', 'preliminary', True, None))
        pipe.send(('invalid_description',) if suffix else ('filter_decision', 'detail', True, None))
        pipe.send(('job', True, posting))
    pipe.send(('done',))
    pipe.close()


def test_filter_metrics_aggregate_without_affecting_storage_or_status(store):
    store.save_discovery(job(), 'old-slot')
    store.execute("UPDATE job_postings SET status='applied'")
    run = store.start_run('metrics-slot')
    summary = scan_with_budgets([Company('Example', 'https://example.test')], store, run,
                               'metrics-slot', source_seconds=10, scan_seconds=20,
                               worker=filter_metrics_worker)
    metrics = summary.metrics[0]
    assert summary.status == 'full'
    assert metrics['new_jobs_saved'] == 1 and metrics['existing_jobs_seen'] == 1
    filtering = metrics['filtering']
    assert filtering['preliminary_passed'] == 2 and filtering['preliminary_rejected'] == 1
    assert filtering['detail_passed'] == 1 and filtering['invalid_description_retained'] == 1
    assert filtering['rejected_by_reason'] == dict(role_type=1, seniority_marker=0,
                                                  missing_early_career_evidence=0, other_unknown=0)
    assert filtering['location_rejected'] is None
    assert filtering['experience_education_rejected'] is None
    assert metrics['collection']['duplicates_avoided'] == 1
    rows = store.execute('SELECT job_url,status FROM job_postings').fetchall()
    assert len(rows) == 2
    assert next(row for row in rows if row['job_url'] == job().job_url)['status'] == 'applied'


def test_corrected_workday_url_reuses_legacy_row_without_new_notification(store):
    source = 'https://intel.wd1.myworkdayjobs.com/en-US/External'
    original = JobPosting('Software Student', 'Intel', 'Israel',
                          'https://intel.wd1.myworkdayjobs.com/job/Israel/Software_JR123',
                          source=source, description='Page not found.', status='applied')
    store.save_discovery(original, 'old-slot')
    outbox = [dict(row) for row in store.execute('SELECT * FROM notification_outbox')]
    run = store.start_run('test-workday-slot')
    result = scan_with_budgets([Company('Intel', source)], store, run, 'test-workday-slot',
                               source_seconds=10, scan_seconds=20, worker=corrected_workday_worker)
    assert result.status == 'full' and result.new_jobs == []
    assert result.metrics[0]['collection']['duplicates_avoided'] == 1
    assert result.metrics[0]['new_jobs_saved'] == 0
    rows = store.execute('SELECT * FROM job_postings').fetchall()
    assert len(rows) == 1 and rows[0]['id'] == original.id and rows[0]['status'] == 'applied'
    assert [dict(row) for row in store.execute('SELECT * FROM notification_outbox')] == outbox


def test_slow_source_isolated_and_metrics(store):
    run = store.start_run('slot')
    companies = [Company('slow', 'https://example.test'), Company('fast', 'https://example.test')]
    started = time.monotonic()
    summary = scan_with_budgets(companies, store, run, 'slot', source_seconds=8,
                                scan_seconds=25, worker=worker)
    assert time.monotonic() - started < 25
    assert summary.status == 'partial'
    assert summary.metrics[0]['error_type'] == 'BudgetExceeded'
    assert summary.metrics[1]['status'] == 'full'
    assert summary.metrics[0]['http_seconds'] > 0


def test_cancel_after_commit_retains_notification(store, settings):
    run = store.start_run('slot')
    summary = scan_with_budgets([Company('saved', 'https://example.test')], store, run, 'slot',
        source_seconds=5, scan_seconds=10, worker=worker,
        cancelled=lambda: bool(store.known_urls()))
    assert summary.status == 'partial'
    assert len(store.prepare_batch('slot')) == 1
    assert summary.metrics[0]['db_calls'] >= 2
    assert summary.metrics[0]['new_jobs_saved'] == 1


def test_partial_scan_can_complete_without_duplicate_summary(store, settings):
    repo = store.repository
    store.close()
    messages = []
    def scanner(status):
        def run(companies, target, run_id, slot, **options):
            target.save_discovery(job(), slot)
            return DurableSummary(status=status, jobs_saved=1)
        return run
    sender = lambda _, msg: messages.append(msg)
    assert run_durable_scan([], repo, settings, 'slot', scanner=scanner('partial'), sender=sender) == 1
    assert run_durable_scan([], repo, settings, 'slot', scanner=scanner('full'), sender=sender) == 0
    assert len(messages) == 1


def test_smtp_disconnect_versus_rejection(settings):
    import smtplib
    from email.message import EmailMessage
    class Fake:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_message(self, msg): raise ConnectionResetError()
    with pytest.raises(DeliveryError) as error:
        send_prepared_summary(settings, EmailMessage(), Fake)
    assert error.value.uncertain
    class Rejected(Fake):
        def send_message(self, msg): raise smtplib.SMTPDataError(550, b'rejected')
    with pytest.raises(DeliveryError) as error:
        send_prepared_summary(settings, EmailMessage(), Rejected)
    assert not error.value.uncertain


def test_legacy_slots_imported_conservatively(store):
    repo = store.repository
    repo.begin_delivery('legacy')
    store.migrate()
    assert store.batch_state('legacy') == 'uncertain'


def test_total_budget_and_unstarted_sources(store):
    run = store.start_run('slot')
    companies = [Company('slow', 'https://example.test'), Company('fast', 'https://example.test')]
    summary = scan_with_budgets(companies, store, run, 'slot', source_seconds=5,
                                scan_seconds=1, worker=worker)
    assert summary.status == 'failed'
    assert summary.companies_scanned == 1
    assert summary.metrics[0]['error_type'] == 'BudgetExceeded'


def test_retry_old_pending_batch_before_current_slot(store, settings):
    store.save_discovery(job(), 'old')
    store.prepare_batch('old')
    repo = store.repository
    store.close()
    messages = []
    def scanner(*args, **kwargs):
        return DurableSummary(status='full')
    assert run_durable_scan([], repo, settings, 'next', scanner=scanner,
                            sender=lambda _, msg: messages.append(msg)) == 0
    assert len(messages) == 2  # old discovery plus current slot's empty summary
    assert sum('Intern' in msg.get_content() for msg in messages) == 1


def test_recommendation_failure_retains_pending_discoveries(store, settings, monkeypatch):
    from src.notifications import durable_scan
    store.save_discovery(job(), 'slot')
    def fail(*args):
        raise TimeoutError()
    monkeypatch.setattr(durable_scan, 'bounded_recommendations', fail)
    with pytest.raises(TimeoutError):
        deliver_batch(store, 'slot', DurableSummary(), settings, recommendations=lambda _: ())
    assert store.batch_state('slot') == 'pending'
    assert len(store.prepare_batch('slot')) == 1


def test_postgres_session_lock_and_transactions_contract():
    from unittest.mock import MagicMock
    from src.storage.scan_store import SlotBusy
    repo = MagicMock()
    connection = repo._connect.return_value
    connection.execute.return_value.fetchone.return_value = {'acquired': False}
    target = ScanStore(repo)
    with pytest.raises(SlotBusy):
        target.start_run('slot')
    assert connection.autocommit is True
    assert any('pg_try_advisory_lock' in str(call) for call in connection.execute.call_args_list)
    target.close()
    connection.close.assert_called_once()


def test_noop_does_not_manufacture_scan_success(store, settings):
    store.repository.begin_delivery('legacy-sent')
    store.repository.complete_delivery('legacy-sent')
    store.migrate()
    repo = store.repository
    store.close()
    assert run_durable_scan([], repo, settings, 'legacy-sent',
                            scanner=lambda *a, **kw: pytest.fail('legacy replay')) == 0
    reopened = ScanStore(repo)
    try:
        assert reopened.execute('SELECT run_id FROM scan_runs').fetchone() is None
    finally:
        reopened.close()


def test_partial_recipient_acceptance_is_uncertain(settings):
    from email.message import EmailMessage
    class Fake:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_message(self, msg): return {'refused@example.test': (550, b'refused')}
    with pytest.raises(DeliveryError) as error:
        send_prepared_summary(settings, EmailMessage(), Fake)
    assert error.value.uncertain


def recommendations_callback(summary):
    return ()


def test_recommendation_worker_is_bounded_and_returns_result():
    from src.notifications.durable_scan import bounded_recommendations
    assert bounded_recommendations(recommendations_callback, DurableSummary(), 5) == ()


@pytest.mark.parametrize('source,total', [(float('nan'), 5), (2, float('inf')), (0, 5), (2, 3601)])
def test_invalid_budgets_rejected(store, source, total):
    with pytest.raises(ValueError):
        scan_with_budgets([], store, 'run', 'slot', source_seconds=source, scan_seconds=total)


def test_invalid_smtp_configuration_does_not_prevent_scan(store):
    repo = store.repository
    store.close()
    def scanner(companies, target, run_id, slot, **kwargs):
        target.save_discovery(job(), slot)
        return DurableSummary(status='full', jobs_saved=1)
    def missing_settings():
        raise ValueError('missing settings')
    assert run_durable_scan([], repo, missing_settings, 'slot', scanner=scanner) == 1
    reopened = ScanStore(repo)
    try:
        assert reopened.scan_was_full('slot')
        assert reopened.batch_state('slot') == 'pending'
        assert len(reopened.prepare_batch('slot')) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize('empty', [False, True])
def test_preparation_freezes_membership_even_before_message(store, empty):
    if not empty:
        store.save_discovery(job(), 'origin')
    original = store.prepare_batch('slot')
    later = job()
    later.job_url += '/later'
    store.save_discovery(later, 'origin')
    assert [j.id for j in store.prepare_batch('slot')] == [j.id for j in original]
    assert [j.job_url for j in store.prepare_batch('next')] == [later.job_url]
