from src.storage.job_repository import JobRepository
from src.storage.postgres_job_repository import PostgresJobRepository
from src.storage.repository_factory import create_job_repository


def test_factory_uses_sqlite_without_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    repository = create_job_repository(tmp_path / "jobs.db")

    assert isinstance(repository, JobRepository)


def test_factory_uses_postgres_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/jobs")

    repository = create_job_repository(tmp_path / "jobs.db")

    assert isinstance(repository, PostgresJobRepository)
