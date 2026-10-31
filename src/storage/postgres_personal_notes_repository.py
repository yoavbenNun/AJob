"""Private cloud notes. Deployment migrations are deliberately manual."""

from src.storage.personal_notes_repository import MAX_NOTE_LENGTH
from src.storage.postgres_job_repository import PostgresJobRepository


class PostgresPersonalNotesRepository(PostgresJobRepository):
    def initialize(self):
        self.verify_schema()

    def verify_schema(self):
        with self._connect() as connection:
            connection.execute('SELECT job_id, note FROM ajob_private.personal_notes LIMIT 0')

    def for_jobs(self, job_ids):
        ids = list(job_ids)
        if not ids:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT job_id, note FROM ajob_private.personal_notes WHERE job_id = ANY(%s)',
                (ids,),
            ).fetchall()
        return {row['job_id']: row['note'] for row in rows}

    def save(self, job_id, note):
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH or '\x00' in note:
            raise ValueError('Notes must be text of at most 2,000 characters.')
        note = note.strip()
        with self._connect() as connection:
            if note:
                connection.execute(
                    'INSERT INTO ajob_private.personal_notes(job_id,note) VALUES (%s,%s) '
                    'ON CONFLICT(job_id) DO UPDATE SET note=excluded.note', (job_id, note))
            else:
                connection.execute('DELETE FROM ajob_private.personal_notes WHERE job_id=%s', (job_id,))
        return note
