from pathlib import Path
from datetime import datetime, timezone
import sqlite3

from src.models import JobPosting
from src.storage.job_repository import JobRepository


def test_repository_saves_and_updates_job_by_url(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting(
        title="Software Engineering Intern",
        company="Example",
        location="Tel Aviv",
        job_url="https://example.com/jobs/1",
        source="Example careers",
    )

    first_id = repository.save(job)
    job.title = "Updated Software Engineering Intern"
    second_id = repository.save(job)
    saved_jobs = repository.list_all()

    assert first_id == second_id
    assert len(saved_jobs) == 1
    assert saved_jobs[0].title == "Updated Software Engineering Intern"


def test_repository_refreshes_discovery_timestamp_on_update(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting("Intern", "Example", "Remote", "https://example.com/jobs/1")
    repository.save(job)
    refreshed = datetime(2030, 1, 1, tzinfo=timezone.utc)
    job.discovered_at = refreshed

    repository.save(job)

    assert repository.list_all()[0].discovered_at == refreshed


def test_repository_gets_job_by_id(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting("Intern", "Example", "Remote", "https://example.com/jobs/1")
    job_id = repository.save(job)

    saved = repository.get_by_id(job_id)

    assert saved is not None
    assert saved.job_url == job.job_url
    assert repository.get_by_id(job_id + 1) is None


def test_repository_lists_only_relevant_jobs(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    repository.save(
        JobPosting("Relevant Intern", "Example", "Remote", "https://example/jobs/1")
    )
    repository.save(
        JobPosting(
            "Legacy Role",
            "Example",
            "Remote",
            "https://example/jobs/2",
            is_relevant=False,
        )
    )

    jobs = repository.list_relevant()

    assert [job.title for job in jobs] == ["Relevant Intern"]


def test_initialize_migrates_legacy_rows_as_unapproved(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE job_postings (
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
        connection.execute(
            """
            INSERT INTO job_postings
                (title, company, location, job_url, discovered_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Legacy Role",
                "Example",
                "Remote",
                "https://example.com/legacy",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    repository = JobRepository(database)
    repository.initialize()

    assert len(repository.list_all()) == 1
    assert repository.list_all()[0].is_relevant is False
    assert repository.list_relevant() == []
    repository.save(
        JobPosting("New Intern", "Example", "Remote", "https://example.com/new")
    )
    assert [job.title for job in repository.list_relevant()] == ["New Intern"]
