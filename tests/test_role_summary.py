"""Content regressions from the independent AJ-002 review."""
import pytest

from src.models import JobPosting
from src.web.app import _summarize_role


def summarize(description):
    return _summarize_role(JobPosting(
        "Software Intern", "Example", "Israel", "https://example.test/job",
        description=description,
    ))


NARRATIVE = (
    "In this role, you will develop backend services for our platform. "
    "You will collaborate with engineers to ship reliable APIs. "
    "You will investigate production issues and improve operational tooling."
)
LOWERCASE = "develop backend APIs. maintain CI pipelines. debug production issues."
LONG = (
    "Develop backend services with Python, typed APIs, automated integration tests, "
    "detailed operational documentation, robust monitoring, deployment automation, "
    "and close collaboration with product engineers to ship reliable customer features. "
    "Maintain production systems by investigating service errors, reviewing performance "
    "measurements, coordinating safe release plans, improving test coverage, and "
    "documenting operational procedures for the wider engineering team."
)


@pytest.mark.parametrize("description", [NARRATIVE, LOWERCASE, LONG])
def test_qa_informative_prose_is_preserved_without_inventing_duties(description):
    assert summarize(description) == description


@pytest.mark.parametrize("description", ["Build\nTest\nSupport", "Build / Test / Support"])
def test_qa_action_labels_are_not_a_role_summary(description):
    assert summarize(description) == ""


@pytest.mark.parametrize("description", [
    "", "Python. Git.", "Build APIs.", "Build the. Test and. Support our.",
    "You will build. You will test. You will support.",
    "Build your career with us. Develop your skills with our mentors.",
    "We require Python experience. A degree is preferred.",
    "You will not develop software. You must have testing experience.",
])
def test_insufficient_or_non_duty_text_uses_fallback(description):
    assert summarize(description) == ""


def test_preserves_restrictions_and_technical_abbreviations():
    description = (
        "Develop services in C++ for B.Sc. research projects, without access to customer data. "
        "Maintain API tests, but do not deploy changes to production."
    )
    assert summarize(description) == description


def test_numbered_duties_do_not_keep_list_markers():
    assert summarize("1. Build reliable APIs.\n2) Maintain service integration tests.") == (
        "Build reliable APIs. Maintain service integration tests."
    )


def test_disorganized_content_uses_fallback_instead_of_dropping_unknown_context():
    source = (
        "Apply now\nPython\nWelcome to our office.\nBuild reliable APIs.\n"
        "Requirements\nKnowledge of Linux.\nMaintain service integration tests."
    )
    assert summarize(source) == ""


@pytest.mark.parametrize("description", [
    "You will be responsible for developing backend services. "
    "You will be responsible for maintaining CI pipelines.",
    "You are responsible for writing API tests. You are responsible for deploying test services.",
    "The role is responsible for developing backend services. "
    "The role is responsible for maintaining CI pipelines.",
    "Develop backend APIs. This work is limited to test environments and excludes production access. "
    "Maintain CI pipelines.",
])
def test_narrative_responsibility_and_adjacent_scope_are_retained(description):
    assert summarize(description) == description


def test_falls_back_when_duties_and_scope_cannot_fit_without_omissions():
    assert summarize(
        "Develop backend APIs. This work is limited to test environments. "
        "Maintain CI pipelines. This responsibility excludes production changes."
    ) == ""


def test_application_package_hint_matches_its_separate_page(tmp_path):
    from test_web_dashboard import _client_with_jobs
    client, job_id = _client_with_jobs(tmp_path)
    page = client.get(f"/jobs/{job_id}/application")
    assert page.status_code == 200
    assert "Generate one above first" not in page.text
    assert "Resume match section on the job details page" in page.text


def test_line_wrapping_does_not_turn_a_partial_clause_into_a_sentence():
    assert summarize(
        "Develop backend services with\nPython and typed APIs.\nMaintain reliable CI pipelines."
    ) == "Develop backend services with Python and typed APIs. Maintain reliable CI pipelines."


def test_incomplete_clause_is_not_punctuated_as_a_complete_duty():
    assert summarize("Develop backend services with\nMaintain CI pipelines.") == ""


def test_role_page_renders_qa_prose_without_rewriting_stored_description(tmp_path):
    from test_web_dashboard import _client_with_jobs
    from src.storage.job_repository import JobRepository
    client, _ = _client_with_jobs(tmp_path)
    repository = JobRepository(tmp_path / "jobs.db")
    job_id = repository.save(JobPosting(
        "Software Intern", "Example", "Israel", "https://example.test/qa",
        description=NARRATIVE,
    ))
    response = client.get(f"/jobs/{job_id}")
    overview = response.text.split('id="overview"', 1)[1].split("</div>", 1)[0]
    assert overview.count("<p") == 1
    assert NARRATIVE in overview
    assert "Prepare application" not in response.text
    assert "View full captured description" not in response.text
    assert repository.get_by_id(job_id).description == NARRATIVE


@pytest.mark.parametrize("restriction", [
    "These tools must only target test environments and must never deploy to production.",
    "You may only use anonymized data and must not access live customer records.",
    "Execution is allowed only in test environments.",
    "Anonymized data only is permitted for this work.",
    "Access to production databases is prohibited.",
    "Release to live systems requires a separate approval from the operations team.",
    "Anything outside the isolated lab remains beyond the remit of this assignment.",
    "Deploy these tools exclusively to isolated test systems.",
])
def test_every_followup_sentence_is_preserved_without_a_scope_keyword_list(restriction):
    description = f"Build automated deployment tools. {restriction} Maintain CI pipelines."
    assert summarize(description) == description


def test_standalone_anonymized_data_restriction_is_not_treated_as_a_duty():
    assert summarize(
        "You may only use anonymized data and must not access live customer records."
    ) == ""


@pytest.mark.parametrize("description", [
    "You may only use anonymized data and must not access live customer records. "
    "Build analytics tools. Maintain data pipelines.",
    "Build analytics tools. Maintain data pipelines. "
    "You may only use anonymized data and must not access live customer records.",
])
def test_scope_before_or_after_duties_is_preserved(description):
    assert summarize(description) == description


def test_late_global_scope_is_not_lost_after_three_duties():
    assert summarize(
        "Build deployment tools. Maintain CI pipelines. Debug integration failures. "
        "All of these activities are confined to isolated test environments."
    ) == ""


def test_overlong_scope_requires_fallback_without_selecting_unqualified_duties():
    restriction = "These tools must only target " + "isolated test environments and " * 18
    description = (
        f"Build deployment tools. {restriction}must never deploy to production. "
        "Maintain CI pipelines."
    )
    assert summarize(description) == ""


def test_many_short_qualifiers_cannot_be_dropped_to_meet_the_sentence_limit():
    assert summarize(
        "Build deployment tools. These tools are confined to the lab. "
        "Customer records are outside the scope of the assignment. Maintain CI pipelines."
    ) == ""


def test_invalid_action_fragment_is_not_retained_between_valid_duties():
    assert summarize("Build reliable APIs. Test and. Maintain CI pipelines.") == ""


@pytest.mark.parametrize("restriction", [
    "These tools must only target test environments and must never deploy to production.",
    "You may only use anonymized data and must not access live customer records.",
])
def test_role_page_retains_scope_and_source_without_mutation(tmp_path, restriction):
    from test_web_dashboard import _client_with_jobs
    from src.storage.job_repository import JobRepository
    client, _ = _client_with_jobs(tmp_path)
    repository = JobRepository(tmp_path / "jobs.db")
    source = f"Build automated deployment tools. {restriction} Maintain CI pipelines."
    job_id = repository.save(JobPosting(
        "Software Intern", "Example", "Israel", "https://example.test/scope",
        description=source, status="reviewed",
    ))
    before = repository.get_by_id(job_id)
    response = client.get(f"/jobs/{job_id}")
    overview = response.text.split('id="overview"', 1)[1].split("</div>", 1)[0]
    assert overview.count("<p") == 1
    assert source in overview
    assert "Prepare application" not in response.text
    assert repository.get_by_id(job_id) == before


@pytest.mark.parametrize("ending", [
    "You will", "Subject to", "Provided that", "You may", "Unless approved by",
    "You will.", "Subject to.", "Maintain services with", "Maintain services when",
])
def test_incomplete_continuation_invalidates_the_whole_passage(ending):
    assert summarize(f"Build APIs. Maintain CI pipelines. {ending}") == ""


@pytest.mark.parametrize("source, expected", [
    ("Build APIs. Maintain CI pipelines", "Build APIs. Maintain CI pipelines."),
    ("Build reliable APIs\nMaintain CI pipelines", "Build reliable APIs. Maintain CI pipelines."),
    ("You will develop backend services. You will maintain CI pipelines",
     "You will develop backend services. You will maintain CI pipelines."),
])
def test_clear_duties_without_final_punctuation_remain_supported(source, expected):
    assert summarize(source) == expected


def test_unrecognized_unpunctuated_scope_requires_fallback():
    assert summarize(
        "Build APIs. Maintain CI pipelines. Access remains restricted to the test lab"
    ) == ""
