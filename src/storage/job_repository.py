"""SQLite repository for job postings."""

import sqlite3
from pathlib import Path

from src.models import JOB_STATUSES, JobPosting
from src.job_content import description_problem
from src.urls import canonical_url


class JobRepository:
    """Persist and query normalized job postings in a local SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_postings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    job_url TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    requirements TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    discovered_at TEXT NOT NULL,
                    is_relevant INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'reviewed', 'applied', 'ignored', 'rejected'))
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(job_postings)").fetchall()
            }
            if "is_relevant" not in columns:
                connection.execute(
                    "ALTER TABLE job_postings "
                    "ADD COLUMN is_relevant INTEGER NOT NULL DEFAULT 0"
                )
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE job_postings "
                    "ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"
                )
            self._merge_equivalent_urls(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    slot_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('processing', 'sent', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    claimed_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def begin_delivery(self, slot_key: str) -> bool:
        """Claim a delivery slot unless it was sent or has a live claim."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO notification_deliveries (
                    slot_key, status, attempts, claimed_at, sent_at, last_error
                ) VALUES (?, 'processing', 1, datetime('now'), NULL, '')
                ON CONFLICT(slot_key) DO UPDATE SET
                    status = 'processing',
                    attempts = notification_deliveries.attempts + 1,
                    claimed_at = datetime('now'),
                    last_error = ''
                WHERE notification_deliveries.status = 'failed'
                   OR (notification_deliveries.status = 'processing'
                       AND notification_deliveries.claimed_at < datetime('now', '-5 minutes'))
                """,
                (slot_key,),
            )
            return cursor.rowcount == 1

    def complete_delivery(self, slot_key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notification_deliveries "
                "SET status = 'sent', sent_at = datetime('now'), last_error = '' "
                "WHERE slot_key = ? AND status = 'processing'",
                (slot_key,),
            )

    def fail_delivery(self, slot_key: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE notification_deliveries SET status = 'failed', last_error = ? "
                "WHERE slot_key = ? AND status = 'processing'",
                (error[:500], slot_key),
            )

    def save(self, job: JobPosting) -> int:
        """Insert or update a posting by URL and return its database ID."""
        if job.discovered_at is None:
            raise ValueError("Job posting must have a discovery timestamp.")
        normalized_url = canonical_url(job.job_url)
        if not normalized_url:
            raise ValueError("Job posting must have a valid HTTP(S) URL.")
        job.job_url = normalized_url

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_postings (
                    title, company, location, job_url, description,
                    requirements, source, discovered_at, is_relevant, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_url) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    description = CASE WHEN ? THEN excluded.description ELSE job_postings.description END,
                    requirements = CASE WHEN ? THEN excluded.requirements ELSE job_postings.requirements END,
                    source = excluded.source,
                    discovered_at = excluded.discovered_at,
                    is_relevant = excluded.is_relevant
                """,
                (
                    job.title,
                    job.company,
                    job.location,
                    job.job_url,
                    job.description,
                    job.requirements,
                    job.source,
                    job.discovered_at.isoformat(),
                    int(job.is_relevant),
                    job.status,
                    description_problem(job.description) is None,
                    description_problem(job.description, job.requirements) is None,
                ),
            )
            row = connection.execute(
                "SELECT id FROM job_postings WHERE job_url = ?", (job.job_url,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Saved job could not be read back.")
        job.id = int(row["id"])
        return job.id

    def list_all(self) -> list[JobPosting]:
        """Return all postings ordered from newest to oldest."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_postings ORDER BY discovered_at DESC, id DESC"
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_by_status(self, status: str) -> list[JobPosting]:
        """Return postings with one lifecycle status, newest first."""
        _validate_status(status)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_postings WHERE status = ? "
                "ORDER BY discovered_at DESC, id DESC",
                (status,),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_relevant_by_status(self, status: str) -> list[JobPosting]:
        """Return relevant postings with one lifecycle status, newest first."""
        _validate_status(status)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_postings WHERE is_relevant = 1 AND status = ? "
                "ORDER BY discovered_at DESC, id DESC",
                (status,),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def get_by_id(self, job_id: int) -> JobPosting | None:
        """Return one posting by database ID, or None when it does not exist."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM job_postings WHERE id = ?", (job_id,)
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def get_by_url(self, job_url: str) -> JobPosting | None:
        """Return a posting by canonical URL, or None when it is new."""
        normalized_url = canonical_url(job_url)
        if not normalized_url:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM job_postings WHERE job_url = ?", (normalized_url,)
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def update_relevance_by_url(self, job_url: str, is_relevant: bool) -> None:
        """Update relevance without overwriting user lifecycle status or job details."""
        normalized_url = canonical_url(job_url)
        if not normalized_url:
            return
        with self._connect() as connection:
            connection.execute(
                "UPDATE job_postings SET is_relevant = ? WHERE job_url = ?",
                (int(is_relevant), normalized_url),
            )

    def update_relevance(self, job_id: int, is_relevant: bool) -> None:
        """Reclassify an exact stored row, including legacy URL identities."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE job_postings SET is_relevant = ? WHERE id = ?",
                (int(is_relevant), job_id),
            )

    def update_status(self, job_id: int, status: str) -> JobPosting | None:
        """Set a posting's lifecycle status and return it, if it exists."""
        _validate_status(status)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE job_postings SET status = ? WHERE id = ?", (status, job_id)
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM job_postings WHERE id = ?", (job_id,)
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def list_relevant(self) -> list[JobPosting]:
        """Return relevant postings, collapsing equivalent URL encodings."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_postings WHERE is_relevant = 1 "
                "ORDER BY discovered_at DESC, id DESC"
            ).fetchall()
        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for row in rows:
            job = _job_from_row(row)
            identity = canonical_url(job.job_url)
            if identity in seen:
                continue
            seen.add(identity)
            jobs.append(job)
        return jobs

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _merge_equivalent_urls(connection: sqlite3.Connection) -> None:
        """Keep the newest row for each canonical URL during migration."""
        rows = connection.execute(
            "SELECT id, job_url FROM job_postings ORDER BY discovered_at DESC, id DESC"
        ).fetchall()
        keepers: dict[str, int] = {}
        duplicates: list[int] = []
        for row in rows:
            normalized = canonical_url(row["job_url"])
            if not normalized:
                continue
            if normalized in keepers:
                duplicates.append(int(row["id"]))
                continue
            keepers[normalized] = int(row["id"])
        if duplicates:
            connection.executemany(
                "DELETE FROM job_postings WHERE id = ?",
                ((job_id,) for job_id in duplicates),
            )
        connection.executemany(
            "UPDATE job_postings SET job_url = ? WHERE id = ?",
            ((url, job_id) for url, job_id in keepers.items()),
        )


def _parse_timestamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def _validate_status(status: str) -> None:
    if status not in JOB_STATUSES:
        allowed = ", ".join(JOB_STATUSES)
        raise ValueError(f"Invalid job status {status!r}; expected one of: {allowed}")


def _job_from_row(row: sqlite3.Row) -> JobPosting:
    return JobPosting(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        job_url=row["job_url"],
        description=row["description"],
        requirements=row["requirements"],
        source=row["source"],
        discovered_at=_parse_timestamp(row["discovered_at"]),
        is_relevant=bool(row["is_relevant"]),
        status=row["status"],
    )
