"""Offline migration planning and SQLite backup/restore. Never opens a network."""

import json
import os
from pathlib import Path
import sqlite3

from src.models import JOB_STATUSES
from src.storage.personal_notes_repository import MAX_NOTE_LENGTH
from src.urls import canonical_url


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def backup_sqlite(source, destination):
    """Consistent backup, including committed WAL content; never overwrite."""
    destination = Path(destination)
    with read_only(source) as connection:
        # Exclusive creation prevents overwriting any existing backup or user file.
        with os.fdopen(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb'):
            pass
        try:
            with sqlite3.connect(destination) as target:
                connection.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity check failed')
        except Exception:
            destination.unlink()
            raise


def restore_sqlite(backup, destination):
    """Restore only to a new path. Switching an application path is manual."""
    backup_sqlite(backup, destination)


def build_plan(jobs_path, notes_path, target_snapshot):
    """Map local IDs to reviewed target IDs by canonical URL, without writes.

    target_snapshot is a local JSON export containing [{"id": ..., "job_url": ...}].
    Conflicts/orphans abort the whole plan; no implicit merge or deletion occurs.
    """
    targets = {}
    target_ids = set()
    for row in target_snapshot:
        url = canonical_url(row['job_url'])
        job_id = row['id']
        if not url or type(job_id) is not int or job_id <= 0 or url in targets or job_id in target_ids:
            raise ValueError('Invalid or ambiguous target job identities')
        targets[url] = job_id
        target_ids.add(job_id)
    with read_only(jobs_path) as jobs:
        identities = {}
        seen = set()
        for job_id, raw_url in jobs.execute('SELECT id, job_url FROM job_postings'):
            url = canonical_url(raw_url)
            if not url or url in seen:
                raise ValueError('Invalid or ambiguous source job identities')
            identities[job_id] = url
            seen.add(url)
    with read_only(notes_path) as private:
        tables = {row[0] for row in private.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        notes = list(private.execute('SELECT job_id,note FROM personal_notes')) if 'personal_notes' in tables else []
        undo = list(private.execute('SELECT job_id,previous_status FROM job_dismissal_history')) if 'job_dismissal_history' in tables else []
    def mapped(job_id):
        url = identities.get(job_id)
        if url not in targets:
            raise ValueError('Private row has no unambiguous target job; resolve before migration')
        return {'source_job_id': job_id, 'job_id': targets[url], 'job_url': url}
    plan = {'version': 1, 'notes': [], 'dismissals': []}
    for job_id, note in notes:
        if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_LENGTH or '\x00' in note:
            raise ValueError('Invalid source note; resolve before migration')
        plan['notes'].append({**mapped(job_id), 'note': note})
    for job_id, status in undo:
        if status not in JOB_STATUSES or status == 'ignored':
            raise ValueError('Invalid source restoration status')
        plan['dismissals'].append({**mapped(job_id), 'previous_status': status})
    return plan


def write_plan(plan, destination):
    """Sensitive local artifact; never printed or committed. Never overwrite."""
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
        json.dump(plan, target, ensure_ascii=False, indent=2)
