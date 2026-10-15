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
    assert result.eligible is True
    assert result.disqualifiers == ()
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


def test_phd_role_is_ineligible_for_bachelors_resume() -> None:
    resume = parse_resume_text(RESUME_TEXT)
    job = JobPosting(
        title="Software Engineering Intern PhD",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/phd",
        requirements="Python and FastAPI",
    )

    result = match_resume_to_job(resume, job)

    assert result.eligible is False
    assert result.score <= 25
    assert "PhD" in result.disqualifiers[0]


def test_bsms_role_accepts_bachelors_resume() -> None:
    resume = parse_resume_text(RESUME_TEXT)
    job = JobPosting(
        title="Software Engineering BSMS Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/bsms",
        requirements="Python",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_bsms_role_accepts_university_student_without_explicit_degree_label() -> None:
    resume = parse_resume_text(
        """Dana Developer
Third-year Software Engineering student.
SKILLS
Python
EDUCATION
Software Engineering | Example University
2024-2027
"""
    )
    job = JobPosting(
        title="Software Engineering BSMS Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/bsms-student",
        requirements="Python",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_required_degree_is_not_confused_with_preferred_degree() -> None:
    resume = parse_resume_text(RESUME_TEXT)
    job = JobPosting(
        title="Software Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/masters",
        requirements="Bachelor's degree preferred; Master's degree required; Python",
    )

    result = match_resume_to_job(resume, job)

    assert result.eligible is False
    assert "master's" in result.disqualifiers[0]


def test_preferred_phd_is_not_a_hard_requirement() -> None:
    resume = parse_resume_text(RESUME_TEXT)
    job = JobPosting(
        title="Software Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/preferred-phd",
        requirements="Python required. PhD preferred.",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_degree_mentions_outside_education_do_not_satisfy_eligibility() -> None:
    resume = parse_resume_text(
        """Dana Developer
SKILLS
Python
PROJECTS
Built a portal for university PhD students.
"""
    )
    job = JobPosting(
        title="Software Engineering BSMS Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/no-education",
        requirements="Python",
    )

    assert match_resume_to_job(resume, job).eligible is False


def test_masters_candidate_qualifies_for_masters_or_phd_title() -> None:
    resume = parse_resume_text(
        """Dana Developer
SKILLS
Python
EDUCATION
M.Sc. Computer Science, Example University
"""
    )
    job = JobPosting(
        title="Master's/PhD Research Intern",
        company="Example",
        location="Remote",
        job_url="https://example.com/jobs/ms-phd",
        requirements="Python required.",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_closed_required_degree_fields_reject_different_engineering_major() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nSoftware Engineering | Example University\n2024-2027"
    )
    job = JobPosting(
        title="Software Engineering Student",
        company="Example",
        location="Israel",
        job_url="https://example.com/jobs/closed-major",
        requirements=(
            "Minimum Qualifications Pursuing a B.Sc./M.Sc. in Electrical Engineering, "
            "Computer Engineering. Students must have completed three semesters."
        ),
    )

    result = match_resume_to_job(resume, job)

    assert result.eligible is False
    assert result.score <= 25
    assert "Education field mismatch" in result.disqualifiers[0]


def test_closed_required_degree_fields_accept_listed_major() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Computer Engineering, Example University"
    )
    job = JobPosting(
        title="Software Engineering Student",
        company="Example",
        location="Israel",
        job_url="https://example.com/jobs/accepted-major",
        requirements=(
            "Minimum Qualifications: Pursuing a B.Sc. in Electrical Engineering, "
            "Computer Engineering."
        ),
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_related_field_wording_does_not_disqualify_known_major() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern",
        company="Example",
        location="Israel",
        job_url="https://example.com/jobs/related-major",
        requirements="B.Sc. in Electrical Engineering, Computer Engineering, or related field required.",
    )

    assert match_resume_to_job(resume, job).eligible is True

    # Official Google internships allow a related technical field, including
    # software engineering; this must not become a closed CS-only requirement.
    job.requirements = (
        "Minimum qualifications: Currently enrolled in a Bachelor's or Master's "
        "degree in Computer Science or a related technical field."
    )
    assert match_resume_to_job(resume, job).eligible is True


def test_preferred_degree_field_does_not_disqualify() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern",
        company="Example",
        location="Israel",
        job_url="https://example.com/jobs/preferred-major",
        requirements="B.Sc. required. Electrical Engineering preferred.",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_negated_degree_field_requirement_does_not_disqualify() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern", company="Example", location="Israel",
        job_url="https://example.com/jobs/not-required-major",
        requirements="A B.Sc. in Electrical Engineering is not required.",
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_common_qualified_and_contracted_negations_do_not_disqualify() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    for index, requirement in enumerate((
        "A B.Sc. in Electrical Engineering isn't required.",
        "A B.Sc. in Electrical Engineering is not strictly required.",
    )):
        job = JobPosting(
            title="Software Intern", company="Example", location="Israel",
            job_url=f"https://example.com/jobs/negated-major-{index}",
            requirements=requirement,
        )
        assert match_resume_to_job(resume, job).eligible is True


def test_required_degree_field_can_follow_required_level_clause() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern", company="Example", location="Israel",
        job_url="https://example.com/jobs/split-major",
        requirements=(
            "Minimum Qualifications: B.Sc. required. "
            "Degree field: Electrical Engineering or Computer Engineering."
        ),
    )

    result = match_resume_to_job(resume, job)

    assert result.eligible is False
    assert "Education field mismatch" in result.disqualifiers[0]


def test_job_domain_field_wording_is_not_treated_as_degree_field() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern", company="Example", location="Israel",
        job_url="https://example.com/jobs/domain-field",
        requirements=(
            "Minimum Qualifications: B.Sc. required. "
            "You will build tools for the electrical engineering field."
        ),
    )

    assert match_resume_to_job(resume, job).eligible is True


def test_required_degree_after_negated_degree_in_same_sentence_is_enforced() -> None:
    resume = parse_resume_text(
        "Dana Developer\nEDUCATION\nB.Sc. Software Engineering, Example University"
    )
    job = JobPosting(
        title="Software Intern", company="Example", location="Israel",
        job_url="https://example.com/jobs/mixed-negation",
        requirements=(
            "A B.Sc. is not required, but an M.Sc. in Electrical Engineering is required."
        ),
    )

    result = match_resume_to_job(resume, job)

    assert result.eligible is False
    assert "master's" in result.disqualifiers[0]
