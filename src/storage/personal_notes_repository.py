"""Private dashboard-only notes; never part of cloud jobs or notification payloads."""

from pathlib import Path
import sqlite3


MAX_NOTE_LENGTH = 2000


class PersonalNotesRepository:
    def __init__(self, database_path):
        self.database_path = Path(database_path)

    def initialize(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS personal_notes (
                job_id INTEGER PRIMARY KEY,
                note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 2000)
            )''')

    def for_jobs(self, job_ids):
        ids = list(job_ids)
        result = {}
        with sqlite3.connect(self.database_path) as connection:
            for start in range(0, len(ids), 900):
                batch = ids[start:start + 900]
                placeholders = ','.join('?' for _ in batch)
                result.update(connection.execute(
                    f'SELECT job_id,note FROM personal_notes WHERE job_id IN ({placeholders})', batch))
        return result

    def save(self, job_id, note):
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH or '\x00' in note:
            raise ValueError('Notes must be text of at most 2,000 characters.')
        note = note.strip()
        with sqlite3.connect(self.database_path) as connection:
            if note:
                connection.execute('INSERT INTO personal_notes(job_id,note) VALUES (?,?) '
                                   'ON CONFLICT(job_id) DO UPDATE SET note=excluded.note', (job_id, note))
            else:
                connection.execute('DELETE FROM personal_notes WHERE job_id=?', (job_id,))
        return note
