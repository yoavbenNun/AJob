from urllib.parse import parse_qs, urlsplit

from src.models import JobPosting, OutreachContact
from src.outreach.service import (
    build_contact_searches, draft_outreach_message, rank_contact,
    validate_approved_body, validate_profile_url,
)


def test_contact_ranking_is_explainable() -> None:
    job = JobPosting("Python Software Engineer", "Example", "Tel Aviv", "https://example.com/job/1")
    contact = OutreachContact(1, "Ari", "Software Engineer", "Example",
                              "https://linkedin.com/in/ari", "hiring_team", "Tel Aviv")
    result = rank_contact(job, contact)
    assert result.score == 90
    assert result.reasons == ("Works at the hiring company", "Relevant relationship: hiring team",
                              "Role overlap: engineer, software", "Same listed location")


def test_draft_uses_only_contact_and_job_facts() -> None:
    job = JobPosting("Backend Intern", "Example", "Remote", "https://example.com/job/1")
    contact = OutreachContact(1, "Ari", "Backend Engineer", "Example", "https://linkedin.com/in/ari")
    body = draft_outreach_message(job, contact)
    assert "Backend Intern" in body and "Example" in body and "Ari" in body
    assert "referral" not in body.casefold()


def test_profile_url_removes_tracking_query() -> None:
    assert validate_profile_url("https://LinkedIn.com/in/ari/?trk=feed") == "https://linkedin.com/in/ari"


def test_draft_does_not_claim_contact_works_at_target_company() -> None:
    job = JobPosting("Backend Intern", "Target", "Remote", "https://example.com/job/1")
    contact = OutreachContact(1, "Ari", "Engineer", "Different Co", "https://linkedin.com/in/ari")
    body = draft_outreach_message(job, contact)
    assert "at Different Co" in body
    assert "work there" not in body


def test_searches_are_public_links_and_drafts_have_distinct_limits() -> None:
    job = JobPosting("Backend Intern", "Example", "Remote", "https://example.com/job/1")
    contact = OutreachContact(1, "Ari", "Engineer", "Example", "https://linkedin.com/in/ari")
    searches = build_contact_searches(job)
    assert len(searches) == 4
    assert all(item["url"].startswith("https://www.google.com/search?") for item in searches)
    connection = draft_outreach_message(job, contact, "connection_note")
    full = draft_outreach_message(job, contact, "full_message")
    assert len(connection) <= 300 and connection != full
    assert validate_approved_body(" Edited ", "full_message") == "Edited"


def test_contact_searches_use_broad_role_company_and_real_location() -> None:
    job = JobPosting(
        "Backend Software Engineer Student - Payments Platform",
        "Example Tech", "Tel Aviv, Israel", "https://example.com/job/2",
    )

    searches = build_contact_searches(job)
    queries = [parse_qs(urlsplit(item["url"]).query)["q"][0] for item in searches]

    assert all('"Example Tech"' in query for query in queries)
    assert all('"Tel Aviv"' in query for query in queries)
    assert any('"talent acquisition"' in query for query in queries)
    assert any('"R&D manager"' in query and '"team lead"' in query for query in queries)
    assert any('"backend engineer"' in query for query in queries)
    assert all(job.title not in query for query in queries)


def test_contact_searches_do_not_invent_location_for_remote_jobs() -> None:
    job = JobPosting("Frontend Intern", "Example", "Remote", "https://example.com/job/3")

    queries = [
        parse_qs(urlsplit(item["url"]).query)["q"][0]
        for item in build_contact_searches(job)
    ]

    assert all("Remote" not in query and "Israel" not in query for query in queries)


def test_contact_searches_simplify_multi_location_and_avoid_database_false_match() -> None:
    job = JobPosting(
        "Database Software Engineer Student", "Example",
        "Israel, Haifa; Tel Aviv, Israel", "https://example.com/job/4",
    )

    queries = [
        parse_qs(urlsplit(item["url"]).query)["q"][0]
        for item in build_contact_searches(job)
    ]

    assert all('"Haifa"' in query and "Tel Aviv" not in query for query in queries)
    assert all('"data engineer"' not in query for query in queries)
    assert any('"software engineer"' in query for query in queries)
