"""SQLite repository for job postings."""

import sqlite3
from pathlib import Path

from src.models import JobPosting


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
                    discovered_at TEXT NOT NULL
                )
                """
            )

    def save(self, job: JobPosting) -> int:
        """Insert or update a posting by URL and return its database ID."""
        if job.discovered_at is None:
            raise ValueError("Job posting must have a discovery timestamp.")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_postings (
                    title, company, location, job_url, description,
                    requirements, source, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_url) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    description = excluded.description,
                    requirements = excluded.requirements,
                    source = excluded.source,
                    discovered_at = excluded.discovered_at
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
        return [
            JobPosting(
                id=row["id"],
                title=row["title"],
                company=row["company"],
                location=row["location"],
                job_url=row["job_url"],
                description=row["description"],
                requirements=row["requirements"],
                source=row["source"],
                discovered_at=_parse_timestamp(row["discovered_at"]),
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _parse_timestamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
