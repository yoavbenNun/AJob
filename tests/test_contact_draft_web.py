import re

import pytest

from src.storage.outreach_repository import OutreachRepository
from test_contact_discovery import record, job, source  # reusable synthetic fixtures
from test_web_dashboard import _client_with_jobs, _csrf_token


def configured(tmp_path, record):
    client, job_id = _client_with_jobs(tmp_path)
    from src.storage.job_repository import JobRepository
    stored = JobRepository(tmp_path / "jobs.db").get_by_id(job_id)
    record.update(job_url=stored.job_url, company=stored.company)
    client.app.state.contact_source = source(tmp_path, [record])
    return client, job_id


def test_draft_ignores_forged_contact_facts_and_preserves_history(tmp_path, record):
    client, job_id = configured(tmp_path, record)
    token = _csrf_token(client, job_id)
    response = client.post(f"/jobs/{job_id}/contacts/draft", json={
        "csrf_token": token, "profile_url": "https://www.linkedin.com/in/synthetic-test",
        "style": "inquiry", "display_name": "Forged Person", "headline": "CEO",
    })
    assert response.status_code == 200
    assert "Hi Test," in response.json()["body"] and "Forged" not in response.json()["body"]
    assert OutreachRepository(tmp_path / "outreach.db").list_approved_drafts(job_id) == []
    page = client.get(f"/jobs/{job_id}")
    assert "Prepare application" not in page.text
    assert "data-profile-url" in page.text


@pytest.mark.parametrize("patch,code", [
    ({"csrf_token": "wrong"}, 403), ({"csrf_token": "יואב"}, 403),
    ({"profile_url": "https://linkedin.com/in/forged"}, 404),
    ({"profile_url": "https://example.test@linkedin.com:8443/jobs"}, 404),
    ({"profile_url": "http://linkedin.com/company/example"}, 404),
    ({"style": "send"}, 400), ({"style": []}, 400), ({"revision": -1}, 400),
])
def test_draft_boundary(tmp_path, record, patch, code):
    client, job_id = configured(tmp_path, record)
    data = {"csrf_token": _csrf_token(client, job_id),
            "profile_url": "https://www.linkedin.com/in/synthetic-test", "style": "short"}
    data.update(patch)
    assert client.post(f"/jobs/{job_id}/contacts/draft", json=data).status_code == code


def test_provider_failure_does_not_block_page_or_leak_details(tmp_path):
    client, job_id = _client_with_jobs(tmp_path)
    def broken(job):
        raise RuntimeError("Sensitive provider response")
    client.app.state.reviewed_contact_evidence = broken
    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200 and "Sensitive provider" not in page.text
    assert page.context["contact_state"] == "unavailable"
    response = client.post(f"/jobs/{job_id}/contacts/draft", json={
        "csrf_token": _csrf_token(client, job_id), "style": "short",
        "profile_url": "https://www.linkedin.com/in/synthetic-test"})
    assert response.status_code == 404


def test_bad_json_and_oversized_request(tmp_path, record):
    client, job_id = configured(tmp_path, record)
    path = f"/jobs/{job_id}/contacts/draft"
    assert client.post(path, content="[").status_code == 400
    assert client.post(path, content="x" * 4097).status_code == 413
    assert client.post(path, content="[" * 1500 + "0" + "]" * 1500).status_code == 400
    assert client.post(path, content=iter([b"x" * 3000, b"x" * 3000])).status_code == 413


def test_legacy_malicious_rows_cannot_become_cards_or_drafts(tmp_path):
    from src.models import OutreachContact
    from src.outreach.contact_cards import ReviewedContactEvidence, ContactRelevance
    client, job_id = _client_with_jobs(tmp_path)
    repository = OutreachRepository(tmp_path / "outreach.db")
    bad = "https://example.test@linkedin.com:8443/jobs"
    repository.save_contact(OutreachContact(job_id, "Legacy Person", "Recruiter", "Example", bad))
    client.app.state.reviewed_contact_evidence = lambda job: (
        ReviewedContactEvidence(job.job_url, bad, ContactRelevance.COMPANY_RECRUITER, "Legacy Person"),
    )
    page = client.get(f"/jobs/{job_id}")
    assert page.context["contact_cards"] == ()
    assert repository.list_contacts_for_job(job_id)  # No destructive cleanup.
    assert client.post(f"/jobs/{job_id}/contacts/draft", json={
        "csrf_token": _csrf_token(client, job_id), "style": "short", "profile_url": bad,
    }).status_code == 404
