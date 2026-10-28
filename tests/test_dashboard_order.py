"""Dashboard ordering uses insertion identity independently of dates and filters."""

from datetime import datetime, timezone
import re

from fastapi.testclient import TestClient
import pytest

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.web.app import create_app


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESUME_DIRECTORY", raising=False)
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    client = TestClient(create_app(
        tmp_path / "jobs.db", resume_directory=tmp_path / "resumes",
        outreach_database_path=tmp_path / "outreach.db",
        application_database_path=tmp_path / "applications.db",
        personal_notes_database_path=tmp_path / "notes.db",
    ), base_url="http://127.0.0.1")
    return client, repository


def add(repository, status="new", **fields):
    count = len(repository.list_all())
    return repository.save(JobPosting(
        fields.pop("title", f"Python Software Intern {count}"),
        fields.pop("company", "Example"), fields.pop("location", "Israel"),
        f"https://example.test/jobs/{count}", status=status, **fields,
    ))


def ids(response):
    assert response.status_code == 200
    return [int(value) for value in re.findall(r'<article class="job-card" data-job-id="(\d+)"', response.text)]


def test_status_groups_and_descending_insertion_ids(dashboard):
    client, repository = dashboard
    statuses = ["new", "reviewed", "applied", "ignored", "rejected"] * 2
    inserted = [(add(repository, status), status) for status in statuses]
    assert all(isinstance(job_id, int) for job_id, _ in inserted)
    assert [job_id for job_id, _ in inserted] == sorted(job_id for job_id, _ in inserted)
    expected = [job_id for group in ({"applied"}, {"new"}, {"reviewed"}, {"ignored", "rejected"})
                for job_id, status in reversed(inserted) if status in group]
    params = [("status", status) for status in set(statuses)]
    assert ids(client.get("/", params=params)) == expected
    assert ids(client.get("/")) == [job_id for job_id in expected
                                     if repository.get_by_id(job_id).status != "ignored"]


def test_multiselect_preserves_priority_regardless_of_parameter_order(dashboard):
    client, repository = dashboard
    applied = add(repository, "applied")
    reviewed = add(repository, "reviewed")
    new = add(repository)
    assert ids(client.get("/?status=new&status=applied")) == [applied, new]
    assert ids(client.get("/?status=reviewed&status=new")) == [new, reviewed]


@pytest.mark.parametrize("params,expected_names", [
    ({"query": "Python"}, ["applied", "new"]),
    ({"company": "Example"}, ["applied", "new"]),
    ({"relevance": "relevant"}, ["applied", "new"]),
    ({"relevance": "all"}, ["applied", "new", "unrelated"]),
    ({"location": "all"}, ["applied", "foreign", "new"]),
    ({"query": "does-not-exist"}, []),
])
def test_existing_filters_preserve_membership_and_order(dashboard, params, expected_names):
    client, repository = dashboard
    jobs = {
        "unrelated": add(repository, "reviewed", title="Other role", company="Other", is_relevant=False),
        "applied": add(repository, "applied"),
        "new": add(repository),
        "foreign": add(repository, location="London, UK"),
    }
    assert ids(client.get("/", params=params)) == [jobs[name] for name in expected_names]


def test_dates_and_refreshes_do_not_change_insertion_order(dashboard, monkeypatch):
    client, repository = dashboard
    older_id = add(repository, discovered_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    newer_id = add(repository, discovered_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    refreshed = repository.get_by_id(older_id)
    refreshed.discovered_at = datetime(2040, 1, 1, tzinfo=timezone.utc)
    assert repository.save(refreshed) == older_id
    assert ids(client.get("/")) == [newer_id, older_id]
    # Missing dates in an in-memory adapter must not affect presentation ordering.
    jobs = repository.list_all()
    for job in jobs:
        job.discovered_at = None
    monkeypatch.setattr(JobRepository, "list_all", lambda self: jobs)
    assert ids(client.get("/")) == [newer_id, older_id]


def test_empty_dashboard(dashboard):
    client, _ = dashboard
    assert ids(client.get("/")) == []


def test_status_update_moves_job_on_next_request(dashboard):
    client, repository = dashboard
    older_id = add(repository)
    newer_id = add(repository)
    page = client.get("/")
    assert ids(page) == [newer_id, older_id]
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(f"/jobs/{older_id}/status", data={
        "status": "applied", "csrf_token": token,
    })
    assert response.status_code == 200
    assert ids(client.get("/")) == [older_id, newer_id]
