"""Business outcomes are independent of SMTP acceptance and workflow exit codes."""

from datetime import datetime, timedelta


def window_outcome(row, reference):
    scan, mail = row['scan'], row['notification']
    if row.get('failure_reason') == 'NotObserved':
        return 'UNKNOWN'
    if mail == 'uncertain' or row.get('lease_expired'):
        return 'FINAL_FAILURE'
    if scan == 'full' and mail == 'sent':
        return 'FULL'
    if ((scan != 'full' and row.get('failure_reason') == 'ScanLeaseExpired')
            or (mail != 'sent' and row.get('failure_reason') == 'DeliveryLeaseExpired')):
        return 'FINAL_FAILURE'
    if row.get('retry_exhausted'):
        return 'FINAL_FAILURE'
    scheduled = datetime.fromisoformat(row['scheduled'])
    deadline = datetime.fromisoformat(row['deadline'])
    if scan != 'full' and deadline <= reference:
        return 'FINAL_FAILURE'
    if scan == 'scheduled' and scheduled > reference:
        return 'SCHEDULED'
    if scan == 'running':
        return 'RUNNING'
    if scan != 'full':
        retry = datetime.fromisoformat(row['retry_at']) if row.get('retry_at') else reference
        if max(reference, retry) + timedelta(minutes=11) >= deadline:
            return 'FINAL_FAILURE'
    return 'PARTIAL_RECOVERABLE'


def report_exit_code(rows):
    # There is no separately verified final-failure monitor. Retain failure for
    # recoverable partials too; a successful email is never proof of a full scan.
    return int(any(row.get('alert_scope') != 'historical_scan_failure'
                   and row['outcome'] in {'PARTIAL_RECOVERABLE', 'FINAL_FAILURE', 'UNKNOWN'}
                   for row in rows))


def window_alert_scope(row, reference):
    """Age only finalized scan-only incidents; never age unresolved delivery.

    Keep seven days of failing checks after the deadline. An expired scan cannot
    be replayed by the normal recovery path; its actual outcome remains failed.
    Missing records, active/stale leases, and mail that is not sent stay actionable.
    """
    if (row['outcome'] == 'FINAL_FAILURE'
            and row['scan'] in {'partial', 'failed'}
            and row['notification'] == 'sent'
            and not row.get('lease_expired')
            and row.get('failure_reason') in {'WindowExpired', 'MissedWindow'}
            and datetime.fromisoformat(row['deadline']) <= reference - timedelta(days=7)):
        return 'historical_scan_failure'
    return 'operational'
