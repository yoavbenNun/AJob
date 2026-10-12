import pytest

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import JobPosting


def make_job(title: str) -> JobPosting:
    return JobPosting("" if title is None else title, "Example", "Remote", "https://example/jobs/1")


@pytest.mark.parametrize(
    ("title", "relevant"),
    [
        ("Software Engineering Intern", True),
        ("Junior Backend Developer", True),
        ("Software Engineer", False),
        ("Senior Software Engineer", False),
        ("Software Engineer II", False),
        ("Product Manager Intern", False),
        ("Software Engineering Internship Coordinator", False),
        ("Software Engineer, Leadership Program", False),
    ],
)
def test_classify_job(title: str, relevant: bool) -> None:
    assert classify_job(make_job(title)).relevant is relevant


def test_classify_job_uses_detail_page_early_career_evidence() -> None:
    job = make_job("Software Engineer")
    job.requirements = "Suitable for new graduates with 0-2 years of experience."

    decision = classify_job(job)

    assert decision.relevant is True
    assert "early-career" in decision.reason


def test_prefilter_allows_software_role_before_detail_enrichment() -> None:
    assert prefilter_job(make_job("Software Engineer")).relevant is True


def test_detail_substrings_do_not_create_early_career_evidence() -> None:
    job = make_job("Software Engineer")
    job.description = "This team collaborates internally across the company."

    assert classify_job(job).relevant is False


def test_mentoring_junior_staff_does_not_make_role_early_career() -> None:
    job = make_job("CAD Software Developer Engineer")
    job.description = "Experienced engineer who will mentor junior team members."

    assert classify_job(job).relevant is False


def test_classify_job_accepts_plural_internships_title() -> None:
    job = JobPosting(
        "2027 Internships: Software Engineering",
        "Example",
        "Remote",
        "https://example.com/jobs/internships",
    )

    assert classify_job(job).relevant is True
