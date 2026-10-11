from src.matching.resume_matcher import match_resume, match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text


RESUME_TEXT = """Dana Developer

SKILLS
Python, FastAPI, PostgreSQL, Docker

EXPERIENCE
Software Engineering Intern
- Built Python REST APIs with FastAPI.

PROJECTS
Job Search Agent
- Built a Python service with PostgreSQL.

EDUCATION
B.Sc. Computer Science
"""


def test_matcher_returns_explainable_full_score_for_supported_job() -> None:
    job = JobPosting(
        title="Backend Engineering Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/1",
        requirements="Python, FastAPI, PostgreSQL, and Docker experience required.",
    )

    result = match_resume_to_job(parse_resume_text(RESUME_TEXT), job)

    assert result.score == 100
    assert result.minimum_score_applied is False
    assert result.breakdown.total == 100
    assert result.job_skills == ("Python", "FastAPI", "PostgreSQL", "Docker")
    assert result.matched_skills == ("Python", "FastAPI", "PostgreSQL", "Docker")
    assert result.missing_skills == ()
    assert result.breakdown.skills.points == 60
    assert "Matches job skills" in result.strengths[0]
    assert "Tailor the summary" in result.tailoring_suggestions[0]
    assert any("In Experience" in suggestion for suggestion in result.tailoring_suggestions)
    assert any("In Projects" in suggestion for suggestion in result.tailoring_suggestions)


def test_matcher_reports_missing_skills_without_claiming_them() -> None:
    job = JobPosting(
        title="Backend Engineering Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/2",
        requirements="Python, Kubernetes, and AWS experience required.",
    )

    result = match_resume(parse_resume_text(RESUME_TEXT), job)

    assert result.score == 60
    assert result.matched_skills == ("Python",)
    assert result.missing_skills == ("Kubernetes", "AWS")
    assert result.breakdown.skills.points == 20
    assert result.gaps[0] == "Job skills not listed on the resume: Kubernetes, AWS."
    assert "Do not add Kubernetes, AWS" in result.tailoring_suggestions[-1]


def test_matcher_considers_job_description_as_well_as_requirements() -> None:
    job = JobPosting(
        title="Backend Engineering Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/description-skill",
        description="You will build services using FastAPI.",
        requirements="Python experience required.",
    )

    result = match_resume_to_job(parse_resume_text(RESUME_TEXT), job)

    assert result.matched_skills == ("Python", "FastAPI")
    assert result.missing_skills == ()


def test_matcher_uses_minimum_score_and_flags_missing_resume_evidence() -> None:
    empty_resume = parse_resume_text("Dana Developer")
    job = JobPosting(
        title="Platform Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/3",
        requirements="Kubernetes and AWS required.",
    )

    result = match_resume_to_job(empty_resume, job)

    assert result.score == 1
    assert result.minimum_score_applied is True
    assert result.breakdown.total == 0
    assert result.matched_skills == ()
    assert result.missing_skills == ("Kubernetes", "AWS")
    assert "No experience section content" in result.gaps[1]


def test_matcher_does_not_award_skill_points_when_posting_has_no_known_skills() -> None:
    job = JobPosting(
        title="Software Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/4",
        requirements="Strong communication and curiosity required.",
    )

    result = match_resume_to_job(parse_resume_text(RESUME_TEXT), job)

    assert result.breakdown.skills.points == 0
    assert result.score == 5
    assert result.missing_skills == ()
    assert "no skill terms recognized" in result.gaps[0]


def test_unrelated_sections_do_not_receive_experience_or_project_points() -> None:
    resume = parse_resume_text(
        """Dana Developer
SKILLS
Python
EXPERIENCE
Worked in retail sales.
PROJECTS
Created watercolor paintings.
EDUCATION
B.Sc. Computer Science
"""
    )
    job = JobPosting(
        title="Python Software Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/5",
        requirements="Python required.",
    )

    result = match_resume_to_job(resume, job)

    assert result.breakdown.experience.points == 0
    assert result.breakdown.projects.points == 0
    assert result.score == 65
