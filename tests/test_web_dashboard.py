from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.web.app import create_app


def _client_with_jobs(tmp_path):
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    job_id = repository.save(
        JobPosting(
            "Python Software Intern", "Example", "Remote", "https://example.com/jobs/1",
            description="Build FastAPI services.", requirements="Python and FastAPI",
        )
    )
    repository.save(
        JobPosting(
            "Unreviewed role", "Other", "Tel Aviv", "https://example.com/jobs/2",
            is_relevant=False,
        )
    )
    return TestClient(create_app(database, resume_directory=tmp_path)), job_id


def _csrf_token(client: TestClient, job_id: int) -> str:
    response = client.get(f"/jobs/{job_id}")
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def test_dashboard_filters_to_relevant_jobs_by_default(tmp_path) -> None:
    client, _ = _client_with_jobs(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Python Software Intern" in response.text
    assert "Unreviewed role" not in response.text


def test_dashboard_can_filter_and_update_a_job_status(tmp_path) -> None:
    client, job_id = _client_with_jobs(tmp_path)

    filtered = client.get("/", params={"company": "Example", "query": "python"})
    updated = client.post(
        f"/jobs/{job_id}/status",
        data={"status": "applied", "csrf_token": _csrf_token(client, job_id)},
    )

    assert filtered.status_code == 200
    assert "Python Software Intern" in filtered.text
    assert updated.status_code == 200
    assert "Job status updated" in updated.text
    assert 'status--applied' in updated.text


def test_job_detail_calculates_a_local_match_without_exposing_resume_text(tmp_path) -> None:
    client, job_id = _client_with_jobs(tmp_path)
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Dana Developer\nSKILLS\nPython, FastAPI\nEDUCATION\nB.Sc. Computer Science",
        encoding="utf-8",
    )

    response = client.post(
        f"/jobs/{job_id}/match",
        data={"resume_path": str(resume), "csrf_token": _csrf_token(client, job_id)},
    )

    assert response.status_code == 200
    assert "/100 fit score" in response.text
    assert "Dana Developer" not in response.text


def test_dashboard_rejects_status_change_without_csrf_token(tmp_path) -> None:
    client, job_id = _client_with_jobs(tmp_path)

    response = client.post(f"/jobs/{job_id}/status", data={"status": "applied"})

    assert response.status_code == 403
import re
