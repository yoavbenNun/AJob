from pathlib import Path
from datetime import datetime, timedelta, timezone
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


def test_repository_preserves_lifecycle_status_when_refreshing_job(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting("Intern", "Example", "Remote", "https://example.com/jobs/1")
    job_id = repository.save(job)

    repository.update_status(job_id, "applied")
    job.title = "Updated Intern"
    repository.save(job)

    saved = repository.get_by_id(job_id)
    assert saved is not None
    assert saved.status == "applied"
    assert saved.title == "Updated Intern"


def test_repository_lists_and_updates_lifecycle_status(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    new_id = repository.save(JobPosting("New Intern", "Example", "Remote", "https://example/jobs/1"))
    applied_id = repository.save(JobPosting("Applied Intern", "Example", "Remote", "https://example/jobs/2"))

    updated = repository.update_status(applied_id, "applied")

    assert updated is not None
    assert updated.status == "applied"
    assert [job.id for job in repository.list_by_status("new")] == [new_id]
    assert [job.id for job in repository.list_by_status("applied")] == [applied_id]
    assert repository.update_status(999, "reviewed") is None


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


def test_repository_can_mark_existing_job_irrelevant_without_losing_status(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting(
        "Intern", "Example", "Remote", "https://example/jobs/1", status="reviewed"
    )
    repository.save(job)

    repository.update_relevance_by_url(job.job_url, False)

    saved = repository.get_by_id(job.id)
    assert saved is not None
    assert saved.is_relevant is False
    assert saved.status == "reviewed"


def test_repository_collapses_equivalent_job_url_encodings(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    repository.save(
        JobPosting(
            "Older Intern", "Example", "Remote",
            "https://example.com/jobs/1?q=software%20intern&team=platform",
        )
    )
    repository.save(
        JobPosting(
            "Newer Intern", "Example", "Remote",
            "https://example.com/jobs/1?team=platform&q=software+intern",
        )
    )

    jobs = repository.list_relevant()

    assert len(jobs) == 1
    assert jobs[0].title == "Newer Intern"
    assert len(repository.list_all()) == 1


def test_initialize_merges_existing_equivalent_url_rows(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    now = datetime.now(timezone.utc)
    with sqlite3.connect(database) as connection:
        for title, url, discovered_at in (
            (
                "Newest by timestamp",
                "https://EXAMPLE.com/jobs/1?q=software%20intern",
                (now + timedelta(hours=1)).isoformat(),
            ),
            (
                "Highest ID but stale",
                "https://example.com/jobs/1?q=software+intern",
                now.isoformat(),
            ),
        ):
            connection.execute(
                "INSERT INTO job_postings (title, company, location, job_url, "
                "discovered_at, is_relevant) VALUES (?, ?, ?, ?, ?, ?)",
                (title, "Example", "Remote", url, discovered_at, 1),
            )

    repository.initialize()

    jobs = repository.list_all()
    assert len(jobs) == 1
    assert jobs[0].title == "Newest by timestamp"
    assert jobs[0].job_url == "https://example.com/jobs/1?q=software+intern"


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


def test_initialize_migrates_existing_rows_to_new_status(tmp_path: Path) -> None:
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
                discovered_at TEXT NOT NULL,
                is_relevant INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO job_postings (title, company, location, job_url, discovered_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Legacy Role", "Example", "Remote", "https://example.com/legacy", datetime.now(timezone.utc).isoformat()),
        )

    repository = JobRepository(database)
    repository.initialize()

    assert repository.list_all()[0].status == "new"


def test_delivery_slot_is_claimed_once_and_sent_is_terminal(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    slot = "2026-10-14T08:17:00+03:00"

    assert repository.begin_delivery(slot) is True
    assert repository.begin_delivery(slot) is False
    repository.complete_delivery(slot)
    assert repository.begin_delivery(slot) is False


def test_failed_delivery_can_be_retried(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    slot = "2026-10-14T11:17:00+03:00"

    assert repository.begin_delivery(slot) is True
    repository.fail_delivery(slot, "temporary SMTP failure")
    assert repository.begin_delivery(slot) is True


def test_expired_delivery_claim_can_be_retried(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    slot = "2026-10-14T16:17:00+03:00"
    assert repository.begin_delivery(slot) is True
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE notification_deliveries SET claimed_at = datetime('now', '-6 minutes') "
            "WHERE slot_key = ?",
            (slot,),
        )

    assert repository.begin_delivery(slot) is True
