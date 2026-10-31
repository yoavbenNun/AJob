"""Cloud undo history and atomic dashboard status changes."""

from src.models import JOB_STATUSES
from src.storage.postgres_job_repository import PostgresJobRepository


class PostgresDismissalRepository(PostgresJobRepository):
    def initialize(self):
        self.verify_schema()

    def verify_schema(self):
        with self._connect() as connection:
            connection.execute(
                'SELECT job_id, previous_status FROM ajob_private.job_dismissal_history LIMIT 0')
            connection.execute('SELECT id, status FROM public.job_postings LIMIT 0')

    def remember(self, job_id, status):
        if status not in JOB_STATUSES or status == 'ignored':
            raise ValueError('Invalid restoration status')
        with self._connect() as connection:
            self._remember(connection, job_id, status)

    @staticmethod
    def _remember(connection, job_id, status):
        connection.execute(
            'INSERT INTO ajob_private.job_dismissal_history(job_id,previous_status) VALUES (%s,%s) '
            'ON CONFLICT(job_id) DO UPDATE SET previous_status=excluded.previous_status',
            (job_id, status))

    def previous(self, job_id):
        with self._connect() as connection:
            row = connection.execute(
                'SELECT previous_status FROM ajob_private.job_dismissal_history WHERE job_id=%s',
                (job_id,)).fetchone()
        return row['previous_status'] if row and row['previous_status'] in JOB_STATUSES and row['previous_status'] != 'ignored' else None

    def change_status(self, job_id, action):
        """Serialize competing dashboards; commit status and undo together."""
        if action not in {'dismiss', 'restore'}:
            raise ValueError('Unknown dismissal action')
        with self._connect() as connection:
            row = connection.execute(
                'SELECT status FROM public.job_postings WHERE id=%s FOR UPDATE', (job_id,)).fetchone()
            if row is None:
                return None
            current = row['status']
            if (action == 'dismiss') == (current == 'ignored'):
                return {'status': current, 'changed': False}
            if action == 'dismiss':
                self._remember(connection, job_id, current)
                target = 'ignored'
            else:
                previous = connection.execute(
                    'SELECT previous_status FROM ajob_private.job_dismissal_history WHERE job_id=%s',
                    (job_id,)).fetchone()
                target = previous['previous_status'] if previous else 'reviewed'
                if target not in JOB_STATUSES or target == 'ignored':
                    target = 'reviewed'
                connection.execute(
                    'DELETE FROM ajob_private.job_dismissal_history WHERE job_id=%s', (job_id,))
            connection.execute('UPDATE public.job_postings SET status=%s WHERE id=%s', (target, job_id))
        return {'status': target, 'changed': True}
