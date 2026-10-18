"""Local restoration metadata; the shared job status remains authoritative."""

import sqlite3
from pathlib import Path

from src.models import JOB_STATUSES


class DismissalRepository:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS job_dismissal_history '
                               '(job_id INTEGER PRIMARY KEY, previous_status TEXT NOT NULL)')

    def remember(self, job_id, status):
        if status not in JOB_STATUSES or status == 'ignored':
            raise ValueError('Invalid restoration status')
        with sqlite3.connect(self.path) as connection:
            connection.execute('INSERT INTO job_dismissal_history VALUES (?,?) '
                               'ON CONFLICT(job_id) DO UPDATE SET previous_status=excluded.previous_status',
                               (job_id, status))

    def previous(self, job_id):
        with sqlite3.connect(self.path) as connection:
            row = connection.execute('SELECT previous_status FROM job_dismissal_history WHERE job_id=?',
                                     (job_id,)).fetchone()
        return row[0] if row and row[0] in JOB_STATUSES and row[0] != 'ignored' else None
