import pytest

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import JobPosting


def make_job(title: str) -> JobPosting:
    return JobPosting("" if title is None else title, "Example", "Remote", "https://example/jobs/1")


@pytest.mark.parametrize("title", [
    "Student Software Engineer", "Software Developer Intern", "Backend Student",
    "Embedded Software Student", "C/C++ Student", "Python Developer Student",
])
def test_target_student_roles_keep_positive_development_evidence(title: str) -> None:
    job = make_job(title)
    job.location = "Rehovot, Israel"
    job.description = "Develop software in C/C++ and Python for embedded and backend systems."
    assert prefilter_job(job).relevant and classify_job(job).relevant


@pytest.mark.parametrize("description", [
    "No prior experience required, develop software for embedded systems.",
    "You do not need prior experience to develop software.",
    "No supervision is needed to develop software for embedded systems.",
    "Not responsible for hardware design, develop software for embedded systems.",
])
def test_unrelated_negation_does_not_hide_positive_software_duty(description: str) -> None:
    job = make_job("Engineering Student")
    job.description = description
    assert classify_job(job).relevant


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


def test_currently_pursuing_a_degree_is_early_career_evidence() -> None:
    job = make_job("Software Engineer")
    job.requirements = "Currently pursuing or recently completed a Bachelor's degree in Computer Science."
    assert classify_job(job).relevant is True


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


def test_mentoring_new_graduates_does_not_make_role_early_career() -> None:
    job = make_job("Software Engineer")
    job.description = "Mentor new graduates in software development."
    job.requirements = "5+ years of professional experience required."
    assert classify_job(job).relevant is False


def test_classify_job_accepts_plural_internships_title() -> None:
    job = JobPosting(
        "2027 Internships: Software Engineering",
        "Example",
        "Remote",
        "https://example.com/jobs/internships",
    )

    assert classify_job(job).relevant is True


@pytest.mark.parametrize("title", [
    "Electrical Engineering Student", "Hardware Engineering Intern",
    "ASIC Design Verification Student", "RTL Developer Intern",
    "Physical Design Student", "FPGA Developer Intern",
    "ASIC Design Verification Software Intern",
])
def test_hardware_roles_are_excluded_despite_programming_tools(title: str) -> None:
    job = make_job(title)
    job.description = "Use Python and C++ software tools to validate circuits."
    job.requirements = "Student in Electrical Engineering or Computer Engineering."
    assert classify_job(job).relevant is False


def test_mixed_verification_software_title_uses_actual_responsibilities() -> None:
    job = make_job("Design Verification Software Engineer Intern")
    job.description = "Validate circuits and run simulation coverage."
    assert prefilter_job(job).relevant is True
    assert classify_job(job).relevant is False

    job.description = "Developing software tools for verification teams."
    assert classify_job(job).relevant is True


@pytest.mark.parametrize("title", [
    "Software Engineering Student", "Firmware Intern",
    "Embedded Software Student", "Junior Backend Developer",
    "ASIC Software Development Intern",
])
def test_software_in_hardware_companies_is_kept(title: str) -> None:
    job = make_job(title)
    job.company = "Hardware Manufacturer"
    job.description = "Develop low-level software in Python and C++."
    job.requirements = "B.Sc. in Computer Science, Electrical Engineering or Computer Engineering."
    assert classify_job(job).relevant is True


def test_generic_student_title_needs_software_responsibilities() -> None:
    job = make_job("Engineering Student")
    assert prefilter_job(job).relevant is True
    job.description = "Design analog circuits using Python tools."
    assert classify_job(job).relevant is False
    job.description = "Join our team developing software for network services."
    assert classify_job(job).relevant is True


def test_student_infrastructure_role_with_script_and_pipeline_work_is_relevant() -> None:
    job = make_job("AI Infrastructure Engineer Intern")
    job.description = (
        "Build and maintain scripts and pipelines using SQL and Python-based tooling."
    )
    assert classify_job(job).relevant is True


def test_mentioning_interns_does_not_make_experienced_role_early_career() -> None:
    job = make_job("Software Engineer")
    job.description = "Mentor interns and student colleagues in software development."
    assert classify_job(job).relevant is False


def test_degree_alternatives_do_not_establish_software_responsibilities() -> None:
    job = make_job("Engineering Student")
    job.description = "Design circuits. Minimum Qualifications: Software development coursework."
    job.requirements = "Student in Electrical Engineering or Software Engineering."
    assert classify_job(job).relevant is False


def test_pursuing_company_goals_is_not_candidate_education() -> None:
    job = make_job("Software Engineer")
    job.description = "We are pursuing excellence in software development."
    job.requirements = "5+ years of experience."
    assert classify_job(job).relevant is False


def test_industrial_automation_is_not_software_by_title_alone() -> None:
    job = make_job("Automation Engineer Intern")
    job.description = "Build PLC production fixtures and electrical panels."
    assert classify_job(job).relevant is False


@pytest.mark.parametrize("description", [
    "This role does not involve software development. Perform manual QA and customer support.",
    "You will not build software. Execute manual regression tests.",
    "No firmware development is involved. Configure customer workstations.",
    "The role doesn't involve developing software. Perform manual tests.",
    "Work without writing software. Provide technical support.",
    "Software development is not part of this role. Perform manual QA.",
])
def test_negated_software_duties_do_not_make_generic_student_role_relevant(description: str) -> None:
    job = make_job("Engineering Student")
    job.description = description
    assert prefilter_job(job).relevant is True
    assert classify_job(job).relevant is False


@pytest.mark.parametrize("description", [
    "Do not design hardware; develop software for embedded systems.",
    "Do not design hardware. Develop software for embedded systems.",
    "Do not design hardware but develop software for embedded systems.",
    "You will not only develop software but also test embedded systems.",
    "Develop software without direct supervision.",
])
def test_positive_software_duty_survives_negative_contrast(description: str) -> None:
    job = make_job("Engineering Student")
    job.description = description
    assert classify_job(job).relevant is True


def test_negated_duty_cannot_be_replaced_by_software_qualification() -> None:
    job = make_job("Engineering Student")
    job.description = (
        "This role does not involve software development. Perform manual QA.\n"
        "Requirements:\nSoftware development coursework."
    )
    assert classify_job(job).relevant is False
