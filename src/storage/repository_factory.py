"""Select local SQLite or cloud PostgreSQL storage from configuration."""

import os
from pathlib import Path

from src.storage.job_repository import JobRepository
from src.storage.postgres_job_repository import PostgresJobRepository


def create_job_repository(sqlite_path: str | Path):
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresJobRepository(database_url)
    return JobRepository(sqlite_path)
