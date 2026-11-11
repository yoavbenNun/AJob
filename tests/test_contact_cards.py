from fastapi.testclient import TestClient
import pytest

from src.models import JobPosting, OutreachContact
from src.outreach.contact_cards import (
    ContactRelevance as Relevance, ReviewedContactEvidence, canonical_profile_url, contact_cards,
)
from src.storage.job_repository import JobRepository
from src.storage.outreach_repository import OutreachRepository
from src.web.app import create_app


def _contact(job_id, url):
    return OutreachContact(job_id, "Unreviewed name", "Unreviewed title", "Example", url, "hiring_team")


@pytest.mark.parametrize("url", [
    "", "javascript:bad", "http://linkedin.com/in/person", "https://linkedin.com.evil.test/in/person",
    "https://attacker@linkedin.com/in/person", "https://linkedin.com:443/in/person",
    "https://www.linkedin.com/company/example", "https://linkedin.com/search/results/people/",
    "https://linkedin.com/in/person/details/", "https://linkedin.com/in/person%2Fother",
    "https://linkedin.com/in/per\nson", "https://linkedin.com/in/person\\other",
])
def test_invalid_profile(url):
    assert canonical_profile_url(url) is None


def test_cards_require_saved_profile_and_reviewed_job_evidence():
    job = JobPosting("Engineer", "Example", "Israel", "https://example.test/job", id=1)
    team = "https://www.linkedin.com/in/team-member"
    recruiter = "https://www.linkedin.com/in/recruiter"
    contacts = [_contact(1, recruiter), _contact(1, team),
                _contact(1, "https://il.linkedin.com/in/TEAM-MEMBER/?trk=test#about"),
                _contact(1, ""), _contact(1, "https://linkedin.com/in/unreviewed"),
                _contact(2, "https://linkedin.com/in/different-job")]
    evidence = [
        ReviewedContactEvidence(job.job_url, recruiter, Relevance.COMPANY_RECRUITER, "Recruiter"),
        ReviewedContactEvidence(job.job_url, "https://linkedin.com/in/team-member?trk=sample",
                                Relevance.ROLE_RELEVANT_TEAM, "Weaker source", "Engineer"),
        ReviewedContactEvidence(job.job_url, team, Relevance.DIRECT_TEAM, "Direct source"),
        ReviewedContactEvidence(job.job_url, "", Relevance.DIRECT_TEAM, "Missing profile"),
        ReviewedContactEvidence(job.job_url, "https://linkedin.com/in/different-job",
                                Relevance.DIRECT_TEAM, "Not saved for this job"),
        ReviewedContactEvidence("https://example.test/other", "https://linkedin.com/in/unreviewed",
                                Relevance.DIRECT_TEAM, "Wrong posting"),
    ]
    assert [(card.display_name, card.reason, card.role_title, card.profile_url)
            for card in contact_cards(job, contacts, evidence)] == [
        ("Direct source", "Relevant team member", "", team),
        ("Recruiter", "Company recruiter", "", recruiter),
    ]
    assert contact_cards(job, contacts, ()) == ()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    jobs = JobRepository(tmp_path / "jobs.db")
    jobs.initialize()
    job_url = "https://example.test/job"
    job_id = jobs.save(JobPosting("Student software engineer", "Example", "Israel", job_url))
    outreach = OutreachRepository(tmp_path / "outreach.db")
    outreach.initialize()
    profile = "https://linkedin.com/in/synthetic-test-contact"
    contact_id = outreach.save_contact(_contact(job_id, profile))
    draft_id = outreach.save_approved_draft(job_id, contact_id, "Private draft", "fixture")
    evidence = ReviewedContactEvidence(job_url, profile, Relevance.DIRECT_TEAM,
                                       "<Test Contact>", "Engineer & mentor")
    app = create_app(
        tmp_path / "jobs.db", outreach_database_path=outreach.database_path,
        application_database_path=tmp_path / "applications.db",
        reviewed_contact_evidence=lambda job: (evidence,),
    )
    return TestClient(app, base_url="http://127.0.0.1"), job_id, outreach, contact_id, draft_id


def test_job_detail_hides_contacts_and_leaves_saved_data_unchanged(workspace):
    client, job_id, outreach, contact_id, draft_id = workspace
    path = f"/jobs/{job_id}"
    contact_before = outreach.get_contact(contact_id, job_id)
    drafts_before = outreach.list_approved_drafts(job_id)
    response = client.get(path)
    assert response.status_code == 200
    for text in ('id="outreach"', "Test Contact", "synthetic-test-contact", "Private draft", "Draft message"):
        assert text not in response.text
    assert response.text.count("Find People on LinkedIn") == 1
    client.app.state.reviewed_contact_evidence = None
    assert "No verified LinkedIn contacts found for this role." not in client.get(path).text
    assert contact_before is not None and outreach.get_contact(contact_id, job_id) == contact_before
    assert drafts_before[0]["id"] == draft_id
    assert outreach.list_approved_drafts(job_id) == drafts_before
