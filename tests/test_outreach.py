from src.models import JobPosting, OutreachContact
from src.outreach.service import draft_outreach_message, rank_contact, validate_profile_url


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
