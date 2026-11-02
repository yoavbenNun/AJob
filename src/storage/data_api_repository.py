"""Dashboard-only repositories over a fixed, owner-authorized RPC surface.

No SQL connection, DDL, scanner storage, or automatic retry of writes lives here.
"""
from datetime import datetime

from src.models import JOB_STATUSES, JobPosting
from src.storage.personal_notes_repository import MAX_NOTE_LENGTH
from src.web.cloud_auth import AuthSession


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('Invalid job identifier')
    return value


def _job(row):
    if row is None:
        return None
    try:
        return JobPosting(
            id=_identifier(row['id']), title=row['title'], company=row['company'],
            location=row['location'], job_url=row['job_url'],
            description=row['description'], requirements=row['requirements'],
            source=row['source'], discovered_at=datetime.fromisoformat(row['discovered_at']),
            is_relevant=row['is_relevant'], status=row['status'],
        )
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Invalid dashboard API response') from None


class DataApiJobRepository:
    def __init__(self, client):
        self.client = client

    def initialize(self):
        # Read-only readiness, deliberately no migration or local SQLite fallback.
        self.client.preflight()

    verify_schema = initialize

    def list_all(self):
        jobs, after = [], 0
        # Finite reads; hitting the bound fails rather than silently losing rows.
        for _ in range(100):
            rows = self.client.rpc('ajob_jobs', {'p_after_id': after, 'p_limit': 200})
            if not isinstance(rows, list) or len(rows) > 200:
                raise RuntimeError('Invalid dashboard API page')
            page = [_job(row) for row in rows]
            if any(job is None or job.id <= after for job in page):
                raise RuntimeError('Invalid dashboard API cursor')
            ids = [job.id for job in page]
            if ids != sorted(set(ids)):
                raise RuntimeError('Invalid dashboard API ordering')
            jobs.extend(page)
            if len(page) < 200:
                return jobs
            after = page[-1].id
        raise RuntimeError('Dashboard API read limit exceeded')

    def get_by_id(self, job_id):
        return _job(self.client.rpc('ajob_job', {'p_job_id': _identifier(job_id)}))

    def update_status(self, job_id, status, *, expected_status=None):
        if status not in JOB_STATUSES or (expected_status is not None and expected_status not in JOB_STATUSES):
            raise ValueError('Invalid job status')
        return _job(self.client.rpc('ajob_status', {
            'p_job_id': _identifier(job_id), 'p_status': status,
            'p_expected_status': expected_status,
        }))


class DataApiPersonalNotesRepository:
    def __init__(self, client):
        self.client = client

    def initialize(self):
        self.client.preflight()

    def for_jobs(self, job_ids):
        ids = list(dict.fromkeys(_identifier(value) for value in job_ids))
        notes = {}
        for offset in range(0, len(ids), 200):
            batch = ids[offset:offset + 200]
            result = self.client.rpc('ajob_notes', {'p_job_ids': batch})
            if not isinstance(result, dict):
                raise RuntimeError('Invalid dashboard notes response')
            try:
                parsed = {int(key): value for key, value in result.items()}
                if not set(parsed) <= set(batch) or any(not isinstance(note, str) for note in parsed.values()):
                    raise ValueError
            except (TypeError, ValueError):
                raise RuntimeError('Invalid dashboard notes response') from None
            notes.update(parsed)
        return notes

    def save(self, job_id, note):
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH or '\x00' in note:
            raise ValueError('Notes must be text of at most 2,000 characters.')
        result = self.client.rpc('ajob_note_save', {'p_job_id': _identifier(job_id), 'p_note': note.strip()})
        if not isinstance(result, str):
            raise RuntimeError('Invalid dashboard notes response')
        return result


class DataApiDismissalRepository:
    def __init__(self, client):
        self.client = client

    def initialize(self):
        self.client.preflight()

    def change_status(self, job_id, action):
        if action not in {'dismiss', 'restore'}:
            raise ValueError('Unknown dismissal action')
        result = self.client.rpc('ajob_dismiss', {'p_job_id': _identifier(job_id), 'p_action': action})
        if result is not None and (not isinstance(result, dict)
                or result.get('status') not in JOB_STATUSES or type(result.get('changed')) is not bool):
            raise RuntimeError('Invalid dashboard dismissal response')
        return result


class DataApiAuthStore:
    def __init__(self, client):
        self.client = client

    def verify_schema(self):
        self.client.preflight()

    def reserve_login(self, now):
        result = self.client.rpc('ajob_login_reserve', {'p_now': now})
        if type(result) is not bool:
            raise RuntimeError('Invalid authentication API response')
        return result

    def create_session(self, session_hash, csrf_token, now, expires_at):
        self.client.rpc('ajob_session_create', {
            'p_session_hash': session_hash, 'p_csrf_token': csrf_token,
            'p_now': now, 'p_expires_at': expires_at,
        })

    def get_session(self, session_hash, now, idle_seconds):
        result = self.client.rpc('ajob_session_get', {
            'p_session_hash': session_hash, 'p_now': now, 'p_idle_seconds': idle_seconds,
        })
        if result is None:
            return None
        if not isinstance(result, dict) or not isinstance(result.get('csrf_token'), str):
            raise RuntimeError('Invalid authentication API response')
        return AuthSession(result['csrf_token'])

    def revoke_session(self, session_hash):
        self.client.rpc('ajob_session_revoke', {'p_session_hash': session_hash})
