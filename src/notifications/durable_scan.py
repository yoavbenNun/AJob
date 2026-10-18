"""Persistent scan and notification stages. No schema creation on startup."""
import json
import hashlib
import multiprocessing
import signal
import time
import uuid
from email.message import EmailMessage

from src.notifications.email_notifier import (
    DeliveryError, compose_scan_summary, send_prepared_summary,
)
from src.scrapers.budgeted_scan import DurableSummary, scan_with_budgets, stop_worker
from src.storage.scan_store import ScanStore


def recommendation_worker(callback, summary, pipe):
    import os
    if os.name != 'nt':
        os.setsid()
    try:
        pipe.send(('ok', callback(summary)))
    except Exception as error:
        pipe.send(('error', type(error).__name__))
    finally:
        pipe.close()


def bounded_recommendations(callback, summary, seconds):
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=recommendation_worker, args=(callback, summary, child))
    try:
        process.start()
        child.close()
        if not parent.poll(max(0, seconds)):
            raise TimeoutError('Recommendation budget exceeded; notification remains pending')
        kind, result = parent.recv()
        if kind != 'ok':
            raise RuntimeError('Recommendation processing failed')
        return result
    finally:
        if process.pid is not None:
            stop_worker(process)
            process.join(timeout=2)
        parent.close()
        child.close()


def deliver_batch(store, slot, summary, settings, recommendations=None,
                  sender=send_prepared_summary, recommendation_seconds=300):
    if store.batch_state(slot) in {'sent', 'sending', 'uncertain'}:
        return store.batch_state(slot)
    summary.new_jobs = store.prepare_batch(slot)
    frozen = store.execute('SELECT message FROM notification_batches WHERE slot_key=?', (slot,)).fetchone()['message']
    replace_dismissed = frozen is not None and any(
        url in json.loads(frozen)['body'] for url in store.dismissed_batch_urls(slot))
    if frozen is None or replace_dismissed:
        ranked = (bounded_recommendations(recommendations, summary, recommendation_seconds)
                  if recommendations else None)
        message = compose_scan_summary(settings, summary, ranked)
        message.set_content(f'Delivery slot: {slot}\n' + message.get_content())
        frozen = store.freeze_message(slot, json.dumps({
            'subject': str(message['Subject']), 'body': message.get_content()}),
            previous_message=frozen if replace_dismissed else None)
    payload = json.loads(frozen)
    message = EmailMessage()
    message['From'], message['To'] = settings.sender, settings.recipient
    message['Subject'] = payload['subject']
    # Stable identifier helps reconciliation; SMTP still cannot promise exactly once.
    message['Message-ID'] = f'<ajob-{hashlib.sha256(slot.encode()).hexdigest()}@ajob.local>'
    message.set_content(payload['body'])
    if not store.begin_send(slot):
        return store.batch_state(slot)
    try:
        sender(settings, message)
    except DeliveryError as error:
        state = 'uncertain' if error.uncertain else 'failed'
        store.finish_send(slot, state, str(error))
    except Exception as error:
        # Unknown senders/errors must fail conservatively, never risk duplicates.
        store.finish_send(slot, 'uncertain', type(error).__name__)
    else:
        store.finish_send(slot, 'sent')
    return store.batch_state(slot)


def run_durable_scan(companies, repository, settings, slot=None, *,
                     recommendations=None, scanner=scan_with_budgets,
                     sender=send_prepared_summary, **options):
    slot = slot or 'manual-' + str(uuid.uuid4())
    store = ScanStore(repository)
    previous_handler = None
    cancelled = [False]
    started = time.monotonic()
    try:
        store.verify_schema()
        if store.managed_windows_exist():
            raise RuntimeError('Managed windows are active; use scheduled-tick, not the legacy email scanner')
        # Full completed slots are no-ops, but partial scans may continue even
        # when their summary has already been sent.
        store.acquire_slot(slot)
        previously_full = store.scan_was_full(slot)
        legacy_sent = store.execute(
            "SELECT slot_key FROM notification_batches WHERE slot_key=? "
            "AND state='sent' AND error_type='LegacyDeliveryRequiresReconciliation'",
            (slot,),
        ).fetchone()
        if store.batch_state(slot) == 'sent' and (previously_full or legacy_sent):
            print(json.dumps(dict(event='slot_noop', slot=slot, mail='sent',
                                  scan='full' if previously_full else 'legacy_unknown')))
            return 0
        run_id = store.start_run(slot)
        previous_handler = signal.signal(signal.SIGTERM, lambda *_: cancelled.__setitem__(0, True))
        try:
            summary = scanner(companies, store, run_id, slot, cancelled=lambda: cancelled[0], **options)
        except BaseException:
            store.finish_run(run_id, DurableSummary(status='partial', failures=1))
            raise
        store.finish_run(run_id, summary)
        try:
            settings = settings() if callable(settings) else settings
        except ValueError:
            store.prepare_batch(slot)
            print(json.dumps(dict(event='delivery_result', slot=slot, scan=summary.status,
                                  mail='pending', error_type='SmtpConfigurationError')))
            return 1
        states = []
        for pending in dict.fromkeys([*store.pending_batches(), slot]):
            # At most five minutes per recommendation worker, within a shared
            # 65-minute cutoff; reserve remaining time for SMTP and cleanup.
            remaining = max(0, 3900 - (time.monotonic() - started))
            if remaining <= 0 or cancelled[0]:
                break
            batch_summary = summary
            if pending != slot:
                row = store.execute('SELECT summary,status FROM scan_runs WHERE slot_key=? ORDER BY started_at DESC LIMIT 1', (pending,)).fetchone()
                batch_summary = DurableSummary(**json.loads(row['summary'])) if row else DurableSummary()
            try:
                state = deliver_batch(store, pending, batch_summary, settings, recommendations,
                                      sender, recommendation_seconds=min(300, remaining))
            except (RuntimeError, TimeoutError, EOFError, ValueError) as error:
                state = 'pending'
                print(json.dumps(dict(event='notification_preparation_failed', slot=pending, error_type=type(error).__name__)))
            states.append(state)
            print(json.dumps(dict(event='delivery_result', slot=pending, scan=summary.status, mail=state)))
        return 0 if summary.status == 'full' and states and all(s == 'sent' for s in states) else 1
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
        store.close()
