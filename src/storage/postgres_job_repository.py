"""PostgreSQL storage adapter for scheduled cloud runs."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from src.models import JOB_STATUSES, JobPosting
from src.urls import canonical_url


class PostgresJobRepository:
    """Persist jobs in PostgreSQL while matching the SQLite repository API."""

    def __init__(self, database_url: str) -> None:
        database_url = database_url.strip()
        parsed = urlsplit(database_url)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or parsed.path in {"", "/"}
        ):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL connection string with a host and database name"
            )
        self.database_url = database_url

    def initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS job_postings (
                    id BIGSERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    job_url TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    requirements TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    discovered_at TIMESTAMPTZ NOT NULL,
                    is_relevant BOOLEAN NOT NULL DEFAULT FALSE,
                    status TEXT NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'reviewed', 'applied', 'ignored', 'rejected'))
                )
                """
            )
            cursor.execute(
                "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS "
                "is_relevant BOOLEAN NOT NULL DEFAULT FALSE"
            )
            cursor.execute(
                "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS "
                "status TEXT NOT NULL DEFAULT 'new'"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS job_postings_relevant_status_discovered_idx "
                "ON job_postings (is_relevant, status, discovered_at DESC, id DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    slot_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('processing', 'sent', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    claimed_at TIMESTAMPTZ NOT NULL,
                    sent_at TIMESTAMPTZ,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._canonicalize_existing_urls(cursor)

    def begin_delivery(self, slot_key: str) -> bool:
        """Atomically claim a delivery slot, allowing failed or stale retries."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notification_deliveries (
                    slot_key, status, attempts, claimed_at, sent_at, last_error
                ) VALUES (%s, 'processing', 1, NOW(), NULL, '')
                ON CONFLICT(slot_key) DO UPDATE SET
                    status = 'processing',
                    attempts = notification_deliveries.attempts + 1,
                    claimed_at = NOW(),
                    last_error = ''
                WHERE notification_deliveries.status = 'failed'
                   OR (notification_deliveries.status = 'processing'
                       AND notification_deliveries.claimed_at < NOW() - INTERVAL '5 minutes')
                RETURNING slot_key
                """,
                (slot_key,),
            )
            return cursor.fetchone() is not None

    def complete_delivery(self, slot_key: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE notification_deliveries "
                "SET status = 'sent', sent_at = NOW(), last_error = '' "
                "WHERE slot_key = %s AND status = 'processing'",
                (slot_key,),
            )

    def fail_delivery(self, slot_key: str, error: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE notification_deliveries SET status = 'failed', last_error = %s "
                "WHERE slot_key = %s AND status = 'processing'",
                (error[:500], slot_key),
            )

    @staticmethod
    def _canonicalize_existing_urls(cursor) -> None:
        """Normalize legacy URLs, refusing ambiguous merges instead of deleting data."""
        cursor.execute("SELECT id, job_url FROM job_postings ORDER BY id")
        rows = cursor.fetchall()
        normalized_to_ids: dict[str, list[int]] = {}
        updates: list[tuple[str, int]] = []
        for row in rows:
            normalized = canonical_url(row["job_url"])
            if not normalized:
                continue
            normalized_to_ids.setdefault(normalized, []).append(int(row["id"]))
            if normalized != row["job_url"]:
                updates.append((normalized, int(row["id"])))
        conflicts = {url: ids for url, ids in normalized_to_ids.items() if len(ids) > 1}
        if conflicts:
            ids = sorted(job_id for group in conflicts.values() for job_id in group)
            raise RuntimeError(
                "PostgreSQL URL migration found duplicate canonical job rows; "
                f"manual merge required for IDs: {ids}"
            )
        if updates:
            cursor.executemany(
                "UPDATE job_postings SET job_url = %s WHERE id = %s", updates
            )

    def save(self, job: JobPosting) -> int:
        if job.discovered_at is None:
            raise ValueError("Job posting must have a discovery timestamp.")
        normalized_url = canonical_url(job.job_url)
        if not normalized_url:
            raise ValueError("Job posting must have a valid HTTP(S) URL.")
        job.job_url = normalized_url
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO job_postings (
                    title, company, location, job_url, description,
                    requirements, source, discovered_at, is_relevant, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(job_url) DO UPDATE SET
                    title = EXCLUDED.title,
                    company = EXCLUDED.company,
                    location = EXCLUDED.location,
                    description = EXCLUDED.description,
                    requirements = EXCLUDED.requirements,
                    source = EXCLUDED.source,
                    discovered_at = EXCLUDED.discovered_at,
                    is_relevant = EXCLUDED.is_relevant
                RETURNING id
                """,
                (
                    job.title, job.company, job.location, job.job_url,
                    job.description, job.requirements, job.source,
                    job.discovered_at, job.is_relevant, job.status,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Saved job could not be read back.")
        job.id = int(row["id"])
        return job.id

    def list_all(self) -> list[JobPosting]:
        return self._query_jobs(
            "SELECT * FROM job_postings ORDER BY discovered_at DESC, id DESC"
        )

    def list_by_status(self, status: str) -> list[JobPosting]:
        _validate_status(status)
        return self._query_jobs(
            "SELECT * FROM job_postings WHERE status = %s "
            "ORDER BY discovered_at DESC, id DESC",
            (status,),
        )

    def list_relevant_by_status(self, status: str) -> list[JobPosting]:
        _validate_status(status)
        return self._query_jobs(
            "SELECT * FROM job_postings WHERE is_relevant = TRUE AND status = %s "
            "ORDER BY discovered_at DESC, id DESC",
            (status,),
        )

    def list_relevant(self) -> list[JobPosting]:
        return self._query_jobs(
            "SELECT * FROM job_postings WHERE is_relevant = TRUE "
            "ORDER BY discovered_at DESC, id DESC"
        )

    def get_by_id(self, job_id: int) -> JobPosting | None:
        jobs = self._query_jobs("SELECT * FROM job_postings WHERE id = %s", (job_id,))
        return jobs[0] if jobs else None

    def get_by_url(self, job_url: str) -> JobPosting | None:
        normalized_url = canonical_url(job_url)
        if not normalized_url:
            return None
        jobs = self._query_jobs(
            "SELECT * FROM job_postings WHERE job_url = %s", (normalized_url,)
        )
        return jobs[0] if jobs else None

    def update_relevance_by_url(self, job_url: str, is_relevant: bool) -> None:
        normalized_url = canonical_url(job_url)
        if not normalized_url:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE job_postings SET is_relevant = %s WHERE job_url = %s",
                (is_relevant, normalized_url),
            )

    def update_relevance(self, job_id: int, is_relevant: bool) -> None:
        """Reclassify an exact stored row without normalizing legacy URLs."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE job_postings SET is_relevant = %s WHERE id = %s",
                (is_relevant, job_id),
            )

    def update_status(self, job_id: int, status: str) -> JobPosting | None:
        _validate_status(status)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE job_postings SET status = %s WHERE id = %s RETURNING *",
                (status, job_id),
            )
            row = cursor.fetchone()
        return _job_from_row(row) if row else None

    def _query_jobs(self, query: str, parameters: tuple = ()) -> list[JobPosting]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        return [_job_from_row(row) for row in rows]

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL support requires psycopg; install project requirements"
            ) from error
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=10,
        )


def _validate_status(status: str) -> None:
    if status not in JOB_STATUSES:
        raise ValueError(f"Invalid job status {status!r}")


def _job_from_row(row) -> JobPosting:
    discovered_at = row["discovered_at"]
    if isinstance(discovered_at, str):
        discovered_at = datetime.fromisoformat(discovered_at)
    return JobPosting(
        id=row["id"], title=row["title"], company=row["company"],
        location=row["location"], job_url=row["job_url"],
        description=row["description"], requirements=row["requirements"],
        source=row["source"], discovered_at=discovered_at,
        is_relevant=bool(row["is_relevant"]), status=row["status"],
    )
