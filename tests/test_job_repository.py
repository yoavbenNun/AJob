from pathlib import Path
from datetime import datetime, timezone

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
