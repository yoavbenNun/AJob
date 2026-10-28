"""Bounded report-only closure review; optional atomic, once-per-job dismissal.

Enabling writes requires both explicit opt-in and the separately applied 003
migration. A retained audit record protects every later manual Restore forever.
"""

import json
import os
import time

from src.models import JobPosting
from src.scrapers.closure_evidence import classify
from src.storage.scan_store import now


def review_closures(store, summary, *, enabled=False, seconds=5, cancelled=lambda: False):
    started = time.monotonic()
    metrics = dict(event='closure_review', mode='apply' if enabled else 'report_only',
                   jobs_checked=0, jobs_open=0, jobs_closed=0, jobs_unknown=0,
                   jobs_auto_dismissed=0, jobs_would_dismiss=0,
                   jobs_skipped_due_to_manual_status=0, checks_failed=0,
                   supported_sources=len(getattr(summary, 'closure_snapshots', {})),
                   deferred=False)
    if summary.status != 'full' or seconds <= 0 or cancelled():
        metrics['deferred'] = True
    else:
        try:
            has_audit = store.execute(
                "SELECT to_regclass('job_closure_dismissals') AS name" if store.postgres else
                "SELECT name FROM sqlite_master WHERE type='table' AND name='job_closure_dismissals'"
            ).fetchone()
            has_audit = bool(has_audit and has_audit['name'])
            # Hard cap also bounds work for unusually large historical datasets.
            rows = store.execute(
                'SELECT j.id,j.company,j.source,j.job_url,j.status, '
                + ('a.job_id AS auto_dismissed_before ' if has_audit else 'NULL AS auto_dismissed_before ')
                + 'FROM job_postings j '
                + ('LEFT JOIN job_closure_dismissals a ON a.job_id=j.id ' if has_audit else '')
                + 'ORDER BY j.id LIMIT 1001').fetchall()
            metrics['deferred'] = len(rows) > 1000
            for row in rows[:1000]:
                if time.monotonic() - started >= seconds or cancelled():
                    metrics['deferred'] = True
                    break
                job = JobPosting('', row['company'], '', row['job_url'], source=row['source'],
                                 id=row['id'], status=row['status'])
                evidence = getattr(summary, 'closure_snapshots', {}).get(job.company)
                state = classify(job, evidence)
                metrics['jobs_checked'] += 1
                metrics['jobs_' + state.lower()] += 1
                if job.status not in {'new', 'reviewed'}:
                    metrics['jobs_skipped_due_to_manual_status'] += 1
                    continue
                if state != 'CLOSED':
                    continue
                if row['auto_dismissed_before'] is not None:
                    metrics['jobs_skipped_due_to_manual_status'] += 1
                    continue
                if enabled:
                    # Both writes roll back together. CAS preserves concurrent
                    # user changes; the UNIQUE audit row fences duplicate owners.
                    with store.transaction():
                        claimed = store.execute(
                            'INSERT INTO job_closure_dismissals(job_id,previous_status,checked_at,evidence) '
                            'VALUES (?,?,?,?) ON CONFLICT(job_id) DO NOTHING RETURNING job_id',
                            (job.id, job.status, now(), 'complete_official_' + evidence['kind'])).fetchone()
                        if claimed:
                            changed = store.execute(
                                'UPDATE job_postings SET status=\'ignored\' WHERE id=? AND status=? RETURNING id',
                                (job.id, job.status)).fetchone()
                            if not changed:
                                store.execute('DELETE FROM job_closure_dismissals WHERE job_id=?', (job.id,))
                            metrics['jobs_auto_dismissed'] += int(changed is not None)
                else:
                    metrics['jobs_would_dismiss'] += 1
        except Exception:
            # Fail closed: no exception text, URLs, credentials or user content.
            metrics['checks_failed'] += 1
            metrics['deferred'] = True
    metrics['check_duration'] = round(time.monotonic() - started, 4)
    return metrics


def report_after_scan(store, summary, *, seconds, cancelled):
    enabled = os.environ.get('AJOB_AUTO_DISMISS_CLOSED', '') == 'true'
    try:
        metrics = review_closures(store, summary, seconds=min(5, seconds), cancelled=cancelled,
                                 enabled=enabled)
    except Exception:
        # Even a failed cancellation/heartbeat probe must not break delivery.
        metrics = review_closures(store, summary, seconds=0, enabled=enabled)
        metrics['checks_failed'] = 1
    print(json.dumps(metrics), flush=True)
    return metrics
