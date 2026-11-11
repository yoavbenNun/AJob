from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.web.app import _description_requirements, _role_list_duty, _role_list_preview, _source_role_preview, _summarize_role, create_app


NVIDIA_DESCRIPTION = Path(__file__).parent / "fixtures" / "nvidia_12706_description.txt"


@pytest.mark.parametrize('heading', ["What you'll be doing:", 'What you will do:', 'Day-to-day:'])
def test_workday_plain_text_lists_are_readable_and_source_based(heading):
    description = (f'{heading}\nDevelop Python automation for software testing\n'
                   'Analyze test results and investigate software defects\n'
                   'Maintain tools for validating software releases\n'
                   'What we need to see:\nCurrently pursuing a relevant degree\n'
                   'Experience with Python\nEqual opportunity:\nAll applicants welcome')
    before = description
    job = JobPosting('Software QA Student', 'Example', 'Israel', 'https://example.test/job', description=description)
    assert not _summarize_role(job)
    preview = _role_list_preview(description)
    assert 'Develop Python automation' in preview
    assert 'Analyze test results' in preview
    assert 'Equal opportunity' not in preview
    assert _description_requirements(description)['required'] == [
        'Currently pursuing a relevant degree', 'Experience with Python']
    assert job.description == before


def test_greenhouse_bullets_preserve_context():
    source = ('Responsibilities:\n- Build APIs with Python and SQL\n'
              '- Maintain automated integration tests\n\n'
              'About our company:\nOur mission is to change the world.')
    assert _role_list_preview(source) == 'Build APIs with Python and SQL · Maintain automated integration tests'


def test_never_drop_restrictive_continuation():
    source = ('Responsibilities:\nBuild tools for automated deployments\n'
              'Maintain the existing release pipelines\n'
              'These tools must never deploy to production.\nRequirements:\nPython')
    assert _role_list_preview(source) == ''


def test_review_requirements_documents_in_long_workday_description():
    source = ("About our company:\n" + "We build products for customers. " * 40 +
              "\nWhat you'll be doing:\nReview arch design and requirements documents\n"
              "Develop Python automation for software testing\n"
              "Requirements:\nCurrently pursuing a relevant degree\n"
              "Experience with Python\nEqual opportunity:\nAll applicants welcome")
    assert '\n\n' not in source and len(source) > 850
    assert _role_list_preview(source) == (
        'Review arch design and requirements documents · Develop Python automation for software testing')
    assert _description_requirements(source)['required'] == [
        'Currently pursuing a relevant degree', 'Experience with Python']


def test_review_duties_keep_restrictive_continuations_and_exclude_qualifications():
    source = ("What you'll be doing:\nReview arch design and requirements documents\n"
              "Develop Python automation for software testing\n"
              "Only use anonymized customer data.\nRequirements:\nPython")
    assert _role_list_preview(source) == ''
    assert not _role_list_preview('Requirements:\nReview job requirements documents\nDevelop Python skills')


def test_reported_bug_duties_without_a_following_heading():
    source = ("What you'll be doing:\nReview arch design and requirements documents\n"
              'Report bugs found during execution of software tests')
    assert _role_list_preview(source) == (
        'Review arch design and requirements documents · '
        'Report bugs found during execution of software tests')


@pytest.mark.parametrize('tail', [
    'Report bugs found during',
    'Report bugs found during execution...',
    'Report bugs found during execution of',
    'Only use anonymized customer data.',
    'Equal opportunity employer and welcoming workplace.',
    'Learn more about the benefits of working here.',
])
def test_report_list_fails_closed_for_truncated_or_unrelated_continuations(tail):
    source = ("What you'll be doing:\nReview arch design and requirements documents\n"
              'Develop Python automation for software testing\n' + tail)
    assert not _role_list_preview(source)


def test_report_duties_stop_before_qualifications():
    source = ("What you'll be doing:\nReview arch design and requirements documents\n"
              'Report bugs found during execution of software tests\n'
              'Requirements:\nCurrently pursuing a relevant degree\n'
              'Experience with Python\nEqual opportunity:\nAll applicants welcome')
    preview = _role_list_preview(source)
    assert len(preview) <= 500 and 'Report bugs found during execution' in preview
    assert all(text not in preview for text in ('degree', 'Python', 'Equal opportunity'))
    assert _description_requirements(source)['required'] == [
        'Currently pursuing a relevant degree', 'Experience with Python']


def test_saved_nvidia_duties_fit_a_source_faithful_preview():
    source = NVIDIA_DESCRIPTION.read_text(encoding="utf-8")
    duties = source.split("What you'll be doing:\n\n", 1)[1].split("\nWhat we need to see:", 1)[0].splitlines()
    assert len(duties) == 5
    assert all(_role_list_duty(duty) for duty in duties)
    preview = _role_list_preview(source)
    selected = preview.split(" · ")
    assert 3 <= len(selected) <= 5 and len(preview) <= 500
    assert selected == [duty for duty in duties if duty in selected]
    assert duties[3] in selected and duties[4] in selected
    assert "Scripting skills in Python" in _description_requirements(source)["required"]
    assert "All applicants welcome" not in preview


def test_preview_keeps_all_duties_when_they_fit():
    duties = ["Review design documents", "Design integration tests", "Automate release checks",
              "Report defects found during testing", "Perform regression tests"]
    assert _role_list_preview("Responsibilities:\n" + "\n".join(duties)) == " · ".join(duties)


@pytest.mark.parametrize("tail", [
    "Perform regression tests with",
    "Perform tests in different scopes…",
    "Perform tests in different scopes:",
    "Only use anonymized customer data.",
    "Perform at your best and enjoy our benefits.",
    "You must have experience performing regression tests.",
    "Equal opportunity employer and welcoming workplace.",
])
def test_blank_line_does_not_hide_invalid_list_tail(tail):
    source = ("Responsibilities:\n\nDevelop automated test tools\n"
              "Perform automated regression testing on new releases\n\n")
    assert "Perform automated regression testing" in _role_list_preview(source)
    assert not _role_list_preview(source + tail + "\nRequirements:\nPython")


def test_sparse_marketing_and_legal_text_are_not_duties():
    assert not _role_list_preview('Software Student\nPython')
    assert not _role_list_preview('About us:\nBuild a better world\nEqual opportunity employer')


@pytest.mark.parametrize("separated", [False, True])
def test_scoped_duties_are_never_selected_without_their_context(separated):
    deploy = "Deploy automated release tooling for supported service versions."
    restriction = "Operate only in isolated staging environments and never use production credentials."
    unrelated = "Maintain automated integration checks for distributed services across all supported versions."
    context = [deploy, unrelated, restriction] if separated else [deploy, restriction, unrelated]
    assert _role_list_preview("Responsibilities:\n" + "\n\n".join(context)) == " · ".join(context)
    extra = [
        "Develop backend services for internal engineering teams to coordinate and validate application releases across supported platforms.",
        "Monitor scheduled jobs and investigate integration failures with developers responsible for the affected services and their automated pipelines.",
        "Improve deployment diagnostics for software engineers who maintain distributed services and validate changes across supported versions.",
    ]
    source = "Responsibilities:\n\n" + "\n\n".join(context + extra)
    assert len(" · ".join(context + extra)) > 500
    assert not _role_list_preview(source)
    job = JobPosting("Engineer", "Example", "Israel", "https://example.test/job", description=source)
    assert not _source_role_preview(job)
    assert not _summarize_role(job)


@pytest.mark.parametrize("ending", ["unless", "except", "provided that", "only if", "as long as", "subject to"])
def test_testing_duty_rejects_unfinished_conditions(ending):
    source = f"Responsibilities:\nDevelop automated test tools\nPerform regression testing {ending}."
    assert not _role_list_preview(source)


def test_blank_separator_does_not_admit_career_marketing():
    source = ("Responsibilities:\n\nBuild your career with our global engineering team.\n"
              "Maintain software test tools.")
    assert not _role_list_preview(source)


def test_detail_page_displays_workday_list_and_requirements(tmp_path):
    description = NVIDIA_DESCRIPTION.read_text(encoding="utf-8")
    db = tmp_path / 'jobs.db'
    repo = JobRepository(db)
    repo.initialize()
    job_id = repo.save(JobPosting('QA Student', 'Example', 'Israel',
                                  'https://example.test/job', description=description))
    app = create_app(db, outreach_database_path=tmp_path / 'outreach.db',
                     application_database_path=tmp_path / 'applications.db')
    html = TestClient(app, base_url='http://127.0.0.1').get(f'/jobs/{job_id}').text
    assert 'Review arch design and requirements documents' in html
    assert 'Report bugs found during execution' in html
    assert 'Perform tests in different scopes' in html
    assert 'Current B.Sc. student' in html
    summary = html.split('class="role-summary">', 1)[1].split("</p>", 1)[0]
    assert "Current B.Sc. student" not in summary and "Scripting skills in Python" not in summary
    assert 'All applicants welcome' not in html
    assert 'Find People on LinkedIn' in html
    assert repo.get_by_id(job_id).description == description
