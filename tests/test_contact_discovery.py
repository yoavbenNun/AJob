"""Synthetic reviewed records only; no network, real people or private stores."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.models import JobPosting
from src.outreach.discovery import ReviewedFileSource, safe_evidence_url
from src.outreach.personalized_drafts import personalized_draft


@pytest.fixture
def job():
    return JobPosting("Software Engineer Intern", "Synthetic Company", "Israel",
                      "https://careers.example.test/jobs/1",
                      description="You will develop backend services.", id=1)


@pytest.fixture
def record(job):
    return {
        "job_url": job.job_url, "company": job.company, "full_name": "Test Person",
        "headline": "Software Engineer", "profile_url": "https://linkedin.com/in/synthetic-test",
        "evidence_url": "https://careers.example.test/hiring-team",
        "role_category": "direct_team", "verification_status": "reviewed",
        "source_type": "official_company_page", "reuse_permitted": True,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "association_basis": "Fixture explicitly identifies this job's team.",
    }


def source(tmp_path, rows):
    path = tmp_path / "contact_people.json"
    path.write_text(json.dumps({"version": 1, "contacts": rows}), encoding="utf-8")
    return ReviewedFileSource(path)


@pytest.mark.parametrize("change", [
    {"company": "Other Company"}, {"job_url": "https://careers.example.test/other"},
    {"verification_status": "unverified"}, {"reuse_permitted": False},
    {"full_name": "Unknown"}, {"profile_url": "https://linkedin.com/company/example"},
    {"profile_url": "https://example.test@linkedin.com:8443/jobs"},
    {"profile_url": "http://linkedin.com/company/example"},
    {"evidence_url": "javascript:alert(1)"}, {"evidence_url": "https://linkedin.com/in/somebody"},
    {"association_basis": ""}, {"role_category": "guessed_recruiter"},
    {"source_type": "search_snippet"}, {"last_verified_at": "not-a-date"},
    {"last_verified_at": "2020-01-01T00:00:00+00:00"},
    {"last_verified_at": datetime.now().isoformat()},
    {"last_verified_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()},
    {"headline": ["Not a string"]}, {"profile_url": None},
])
def test_unverified_wrong_company_stale_or_malicious_not_shown(tmp_path, job, record, change):
    record.update(change)
    assert source(tmp_path, [record]).for_job(job).cards == ()


def test_dedup_rank_and_missing_photo_or_headline(tmp_path, job, record):
    weaker = {**record, "role_category": "company_employee",
              "profile_url": "https://www.linkedin.com/in/SYNTHETIC-TEST/?trk=fixture"}
    recruiter = {**record, "profile_url": "https://linkedin.com/in/synthetic-recruiter",
                 "full_name": "Test Recruiter", "role_category": "recruiter"}
    record.update(headline="", avatar_url="https://evil.test/tracking.jpg")
    results = source(tmp_path, [recruiter, weaker, record]).for_job(job)
    assert results.state == "ready"
    assert len(results.cards) == 2
    assert results.cards[0].reason == "Relevant team member"
    assert results.cards[0].role_title == "" and results.cards[0].avatar_url == ""
    assert results.cards[1].reason == "Company recruiter"
    assert results.cards[0].initials == "TP"


def test_cache_bounded_retention_deletion_and_reverification(tmp_path, job, record, monkeypatch):
    clock = [100.]
    monkeypatch.setattr("src.outreach.discovery.time.monotonic", lambda: clock[0])
    provider = source(tmp_path, [record])
    assert provider.for_job(job).cards
    provider.path.unlink()
    assert provider.for_job(job).cards  # Explicitly bounded 30 second cache.
    clock[0] += 31
    assert provider.for_job(job).state == "empty"


@pytest.mark.parametrize("contents", ["invalid JSON", "[]", '{"version":2,"contacts":[]}',
                                      '{"version":1,"contacts":"wrong"}', "x" * 512001],
                         ids=["malformed", "wrong-root", "version", "wrong-contacts", "oversize"])
def test_corrupt_unavailable_source(tmp_path, job, contents):
    path = tmp_path / "contact_people.json"
    path.write_text(contents)
    assert ReviewedFileSource(path).for_job(job).state == "unavailable"


@pytest.mark.parametrize("url", [
    "https://user:password@example.test/evidence", "https://example.test:8443/evidence",
    "https://example.test/evidence?secret=hidden", "https://127.0.0.1/evidence",
    "https://host.local/evidence", "//example.test/evidence", "javascript:bad",
])
def test_source_urls_are_safe_public_https_links(url):
    assert safe_evidence_url(url) is None


def test_authorized_local_avatar_only(tmp_path, job, record):
    record.update(avatar_url="/static/contact-avatars/test.webp", avatar_reuse_permitted=True)
    assert source(tmp_path, [record]).for_job(job).cards[0].avatar_url.endswith("test.webp")


@pytest.mark.parametrize("style", ["short", "inquiry", "referral"])
def test_drafts_use_only_supported_facts_and_no_delivery(tmp_path, job, record, style):
    card = source(tmp_path, [record]).for_job(job).cards[0]
    draft = personalized_draft(job, card, style)
    assert draft["body"].startswith("Hi Test,")
    assert len(draft["body"]) <= draft["limit"]
    assert "Synthetic Company" in draft["body"]
    assert "I have experience" not in draft["body"]
    assert "we met" not in draft["body"]
    assert personalized_draft(job, card, style, 1)["body"] != draft["body"]


def test_short_draft_long_fields_keeps_complete_sentence(tmp_path, job, record):
    job.title = "Extremely long title " * 20
    job.company = "Synthetic Company " * 30
    record["company"] = job.company
    # Independent card formatting test, not a validated oversized source record.
    from src.outreach.contact_cards import ContactCard
    card = ContactCard("https://linkedin.com/in/synthetic-test", "Test Person", "", "Company recruiter")
    draft = personalized_draft(job, card, "short")
    assert len(draft["body"]) <= 200 and draft["body"].endswith("Thank you!")


def test_short_unicode_draft_also_fits_browser_counter(job):
    from src.outreach.contact_cards import ContactCard
    job.title, job.company = "🎯" * 90, "🏢" * 90
    card = ContactCard("https://linkedin.com/in/synthetic-test", "😀" * 35 + " Person",
                       "", "Company recruiter")
    draft = personalized_draft(job, card, "short")
    assert len(draft["body"].encode("utf-16-le")) // 2 <= 200
