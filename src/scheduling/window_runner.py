"""One wake-up selects current DB work; late cron events never replay old scans."""
import json
import time
import signal

from src.notifications.durable_scan import deliver_batch
from src.scrapers.budgeted_scan import DurableSummary, scan_with_budgets
from src.storage.window_store import LeaseLost, WindowStore, utcnow
from src.scheduling.alert_reporting import alert_exit_code


def _summary(store, slot):
    row = store.execute('SELECT summary FROM scan_runs WHERE slot_key=? ORDER BY started_at DESC LIMIT 1', (slot,)).fetchone()
    summary = DurableSummary(**json.loads(row['summary'])) if row else DurableSummary()
    window = store.execute('SELECT status,error_type FROM scan_windows WHERE slot_key=?', (slot,)).fetchone()
    if window:
        summary.status = window['status']
        summary.failure_reason = window['error_type']
    count = store.execute('SELECT count(*) AS n FROM notification_outbox WHERE origin_slot=?', (slot,)).fetchone()['n']
    summary.jobs_saved = max(summary.jobs_saved, count)
    return summary


def scheduled_tick(companies, repository, settings, *, recommendations=None,
                   scanner=scan_with_budgets, sender=None, clock=utcnow):
    store = WindowStore(repository, clock)
    started = time.monotonic()
    scan_error = False
    stopping = [False]
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__(0, True))
    try:
        store.verify_schema()
        store.seed_windows()
        store.recover()
        claim = store.claim_window()
        if claim:
            slot, run_id, deadline = claim
            available = (deadline - clock()).total_seconds() - 600
            if available < 60:
                result = DurableSummary(status='failed', failures=1)
            else:
                completed = store.completed_sources(slot)
                remaining = [c for c in companies if c.enabled and c.name not in completed]
                last_heartbeat = [0.0]
                lost_lease = [False]
                def cancelled():
                    if lost_lease[0] or stopping[0] or clock() >= deadline:
                        return True
                    if time.monotonic() - last_heartbeat[0] >= 30:
                        last_heartbeat[0] = time.monotonic()
                        lost_lease[0] = not store.heartbeat()
                        return lost_lease[0]
                    return False
                try:
                    result = scanner(remaining, store, run_id, slot, cancelled=cancelled,
                                     scan_seconds=min(3600, available), source_seconds=300)
                except (RuntimeError, OSError, ValueError) as error:
                    result = DurableSummary(status='partial', failures=1)
                    print(json.dumps(dict(event='scan_error', slot=slot, error_type=type(error).__name__)))
                if not remaining:
                    result.status = 'full'
            try:
                store.finish_window(run_id, result, [c.name for c in companies if c.enabled])
            except LeaseLost:
                scan_error = True
                print(json.dumps(dict(event='scan_ownership_lost', slot=slot, outcome='FINAL_FAILURE')))
            print(json.dumps(dict(event='scan_result', slot=slot, status=result.status,
                                  jobs_saved=result.jobs_saved, failures=result.failures)))
            scan_error = scan_error or result.status != 'full'
        # Notification retries use persisted batches and never call the scanner.
        try:
            mail_settings = settings() if callable(settings) else settings
        except ValueError:
            mail_settings = None
        # Every completed or missed window must have an observable notification.
        for row in store.execute("SELECT slot_key FROM scan_windows WHERE status IN ('full','partial','failed') ORDER BY scheduled_at DESC"):
            store.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,'pending',?) ON CONFLICT(slot_key) DO NOTHING", (row['slot_key'], clock().isoformat()))
        pending = list(reversed(store.pending_batches()))
        for slot in pending:
            if stopping[0] or time.monotonic() - started >= 3900:
                break
            if not store.claim_delivery(slot):
                continue
            if mail_settings is None:
                store.finish_send(slot, 'failed', 'SmtpConfigurationError')
                continue
            try:
                options = dict(recommendations=recommendations,
                               recommendation_seconds=min(300, max(1, 3900 - (time.monotonic() - started))))
                if sender:
                    options['sender'] = sender
                deliver_batch(store, slot, _summary(store, slot), mail_settings, **options)
            except (RuntimeError, OSError, ValueError, EOFError) as error:
                store.finish_send(slot, 'failed', type(error).__name__)
        rows = store.report(days=1, include_unresolved=True)
        print(json.dumps(dict(event='window_report', windows=rows)), flush=True)
        exit_code = alert_exit_code(store, rows, current_failure=scan_error)
        recoverable = sum(r['outcome'] == 'PARTIAL_RECOVERABLE' for r in rows)
        historical = sum(r['alert_scope'] == 'historical_scan_failure' for r in rows)
        terminal = sum(r['outcome'] == 'FINAL_FAILURE' and r['alert_scope'] == 'operational' for r in rows)
        if recoverable:
            print('::warning::PARTIAL_RECOVERABLE: persisted retryable work; '
                  'business status is separate from the incident alert decision.')
        if terminal:
            level = 'error' if exit_code else 'warning'
            print(f'::{level}::FINAL_FAILURE: inspect window_report; '
                  'window history remains failed independently of alert deduplication.')
        if historical:
            print(f'::warning::HISTORICAL_SCAN_FAILURE: {historical} expired scan incident(s) '
                  'remain FINAL_FAILURE in window_report; mail was sent, no normal recovery remains, '
                  'and the seven-day failing-check period has elapsed. Current failures still fail.')
        # Legacy mail without a window still requires attention, including after
        # midnight. Neither sent mail nor recovery planning clears a scan failure.
        return exit_code

    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        store.close()
