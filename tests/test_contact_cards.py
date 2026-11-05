from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
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


def test_render_is_read_only_and_escapes_contact(workspace):
    client, job_id, outreach, contact_id, draft_id = workspace
    path = f"/jobs/{job_id}"
    response = client.get(path)
    assert response.status_code == 200
    section = response.text.split('id="outreach"', 1)[1].split("</details>", 1)[0]
    assert "&lt;Test Contact&gt;" in section and "Engineer &amp; mentor" in section
    assert 'target="_blank" rel="noopener noreferrer"' in section
    assert 'href="https://www.linkedin.com/in/synthetic-test-contact"' in section
    assert all(tag not in section for tag in ("<form", "<input", "<button", "<textarea", "<select"))
    assert "Private draft" not in section
    client.app.state.reviewed_contact_evidence = None
    assert "No verified LinkedIn contacts found for this role." in client.get(path).text
    assert outreach.get_contact(contact_id, job_id) is not None
    assert [(draft["id"], draft["body"]) for draft in outreach.list_approved_drafts(job_id)] == [
        (draft_id, "Private draft"),
    ]


@pytest.mark.parametrize("width", [360, 1280])
def test_contact_card_browser_layout(workspace, width):
    client, job_id, *_ = workspace
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed")
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 800})

            def serve_local(route):
                url = urlsplit(route.request.url)
                if url.netloc != "127.0.0.1":
                    route.abort()
                    return
                response = client.get(url.path)
                route.fulfill(status=response.status_code, body=response.content,
                              content_type=response.headers.get("content-type"))

            page.route("**/*", serve_local)
            page.goto(f"http://127.0.0.1/jobs/{job_id}")
            page.locator("#outreach summary").click()
            card = page.locator("#outreach a.contact-profile")
            assert card.is_visible()
            assert card.evaluate("(node) => getComputedStyle(node).display") == "flex"
            box = card.bounding_box()
            assert 0 <= box["x"] < box["x"] + box["width"] <= width
            assert card.evaluate("(node) => node.scrollWidth <= node.clientWidth")
        finally:
            browser.close()
