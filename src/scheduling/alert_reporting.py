"""Opt-in persistent alert receipts; never rewrite business window outcomes."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from src.scheduling.github_failure_receipt import verify_failure
from src.scheduling.outcomes import report_exit_code


@dataclass(frozen=True)
class AlertContext:
    repository: str
    run_id: str
    attempt: int

    def validate(self):
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', self.repository)
                or not re.fullmatch(r'[1-9][0-9]*', self.run_id)
                or type(self.attempt) is not int or self.attempt < 1):
            raise RuntimeError('Invalid alert context')

    @classmethod
    def environment(cls):
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        if (os.environ.get('GITHUB_ACTIONS') != 'true'
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'
                or os.environ.get('GITHUB_EVENT_NAME') not in {'schedule', 'workflow_dispatch'}
                or os.environ.get('GITHUB_WORKFLOW_REF') != repo + '/.github/workflows/scheduled-scan.yml@refs/heads/main'):
            raise RuntimeError('Untrusted alert context')
        context = cls(repo, os.environ.get('GITHUB_RUN_ID', ''), int(os.environ.get('GITHUB_RUN_ATTEMPT', '0')))
        context.validate()
        return context


def prepare_alert_schema(store):
    """Explicit manual command only; not part of ScanStore.migrate()."""
    path = Path(__file__).parents[1] / 'storage/alert_migrations/001_scan_alert_receipts.sql'
    store._apply_migration(path)


def _canonical(event):
    return json.dumps(event, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _fingerprint(payload):
    return hashlib.sha256(payload.encode()).hexdigest()


def failure_events(store, rows):
    """Ignore volatile report timestamps; new attempts/states produce new events."""
    events = []
    for row in rows:
        if row.get('outcome') in {'FULL', 'SCHEDULED', 'RUNNING'}:
            continue
        if row.get('alert_scope') == 'historical_scan_failure':
            continue  # Preserve existing seven-day history policy, not window state.
        if row.get('outcome') not in {'PARTIAL_RECOVERABLE', 'FINAL_FAILURE', 'UNKNOWN'}:
            raise RuntimeError('Unknown alert outcome')
        event = {key: row[key] for key in (
            'slot', 'outcome', 'scan', 'notification', 'failure_reason', 'lease_expired',
            'scan_attempts', 'notification_attempts', 'run_id',
        )}
        event['kind'] = 'window'
        events.append(event)
    # Includes legacy/orphan SMTP failures not represented by window reports.
    for row in store.execute("SELECT slot_key,state,error_type,updated_at FROM notification_batches WHERE state IN ('failed','uncertain')"):
        events.append(dict(kind='delivery', slot=row['slot_key'], state=row['state'],
                           failure_reason=row['error_type'], attempt_updated_at=row['updated_at']))
    return events


class AlertLedger:
    def __init__(self, store, context, verifier):
        self.store, self.context, self.verifier = store, context, verifier

    def _observe(self, digest, payload):
        with self.store.transaction():
            self.store.execute("INSERT INTO scan_alert_receipts(fingerprint,repository,run_id,run_attempt,event_json,state,created_at) VALUES (?,?,?,?,?,'pending',?) ON CONFLICT(fingerprint,repository,run_id,run_attempt) DO NOTHING",
                (digest, self.context.repository, self.context.run_id, self.context.attempt, payload, datetime.now(timezone.utc).isoformat()))

    def check(self, events):
        self.context.validate()
        version = self.store.execute('SELECT version FROM scan_alert_schema WHERE id=1').fetchone()
        if not version or version['version'] != 1:
            raise RuntimeError('Alert schema not approved')
        self.store.execute('SELECT fingerprint,repository,run_id,run_attempt,event_json,state,created_at,confirmed_at FROM scan_alert_receipts LIMIT 0')
        cache, new_count, known_count = {}, 0, 0
        deadline = time.monotonic() + 30
        for event in events:
            payload = _canonical(event)
            digest = _fingerprint(payload)
            receipts = self.store.execute(
                'SELECT * FROM scan_alert_receipts WHERE fingerprint=? AND repository=? ORDER BY created_at DESC LIMIT 10',
                (digest, self.context.repository)).fetchall()
            known = False
            for receipt in receipts:
                # Corrupt or partially restored receipts never authorize suppression.
                if (receipt['event_json'] != payload or _fingerprint(receipt['event_json']) != receipt['fingerprint']
                        or receipt['state'] not in {'pending', 'confirmed'}):
                    raise RuntimeError('Corrupt alert receipt')
                prior = AlertContext(receipt['repository'], receipt['run_id'], receipt['run_attempt'])
                prior.validate()
                if datetime.fromisoformat(receipt['created_at']).tzinfo is None:
                    raise RuntimeError('Corrupt alert creation timestamp')
                if (prior.run_id, prior.attempt) == (self.context.run_id, self.context.attempt):
                    continue  # Both failing steps in one workflow must remain red.
                if receipt['state'] == 'confirmed':
                    if not receipt['confirmed_at'] or datetime.fromisoformat(receipt['confirmed_at']).tzinfo is None:
                        raise RuntimeError('Corrupt alert confirmation')
                    known = True
                    break
                if receipt['confirmed_at'] is not None:
                    raise RuntimeError('Corrupt pending alert')
                key = (prior.run_id, prior.attempt)
                if key not in cache:
                    try:
                        if len(cache) >= 3 or time.monotonic() >= deadline:
                            raise RuntimeError('Alert verification budget exhausted')
                        cache[key] = self.verifier(prior.repository, prior.run_id, prior.attempt)
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Alert verification budget exhausted')
                    except Exception:
                        # Preserve this failed reporting attempt too. Otherwise
                        # cancelled/deleted prior runs can starve confirmation
                        # forever, even after GitHub access is restored.
                        self._observe(digest, payload)
                        raise
                if cache[key] is True:
                    # No network calls or long locks inside this short transaction.
                    with self.store.transaction():
                        changed = self.store.execute("UPDATE scan_alert_receipts SET state='confirmed',confirmed_at=COALESCE(confirmed_at,?) WHERE fingerprint=? AND repository=? AND run_id=? AND run_attempt=? AND event_json=? RETURNING fingerprint",
                            (datetime.now(timezone.utc).isoformat(), digest, prior.repository, prior.run_id, prior.attempt, payload)).fetchone()
                        if not changed:
                            raise RuntimeError('Alert receipt disappeared during confirmation')
                    known = True
                    break
            if known:
                known_count += 1
            else:
                self._observe(digest, payload)
                new_count += 1
        print(json.dumps(dict(event='incident_alert_check', new_or_unverified=new_count,
                              previously_reported=known_count, window_outcomes_unchanged=True)))
        return int(new_count > 0)


def alert_exit_code(store, rows, *, current_failure=False, context=None, verifier=None):
    """Exit describes new/unverified incidents only when explicitly enabled.

    Legacy read-only health checks still use report_exit_code directly. Every
    failure in this opt-in reporting mechanism fails closed, even for empty rows.
    """
    try:
        enabled = os.environ.get('AJOB_SCAN_ALERT_DEDUP', '')
        if enabled in {'', '0'}:
            orphan = store.execute("SELECT slot_key FROM notification_batches WHERE state IN ('failed','uncertain') LIMIT 1").fetchone()
            return int(bool(current_failure or orphan or report_exit_code(rows)))
        if enabled != '1':
            raise RuntimeError('Invalid alert activation setting')
        context = context or AlertContext.environment()
        if verifier is None:
            token = os.environ.get('GITHUB_TOKEN', '')
            if not token:
                raise RuntimeError('Missing failure receipt access')
            verifier = lambda repo, run_id, attempt: verify_failure(repo, run_id, attempt, token)
        code = AlertLedger(store, context, verifier).check(failure_events(store, rows))
        return int(bool(current_failure or code))
    except Exception as error:
        print(f'::error::ALERT_CHECK_FAILED: {type(error).__name__}; failure remains visible.')
        return 1
