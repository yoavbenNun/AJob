from pathlib import Path

import html as html_lib
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
    assert _role_list_preview(source).endswith('These tools must never deploy to production.')


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
    assert _role_list_preview(source).endswith('Only use anonymized customer data.')
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
    assert len(preview) > 500
    assert selected == duties
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
    assert _role_list_preview(source) == ' · '.join(context + extra)
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


def _long_duty_list(context, final):
    duties = [
        "Develop validation services for internal engineering teams to coordinate application releases across multiple platforms and monitor delivery failures.",
        "Build automated integration checks for deployment tooling across supported platforms and record detailed diagnostics for engineers investigating failures.",
        context,
        "Improve regression coverage and publish detailed test results for platform engineering teams responsible for validating every supported software release.",
        "Monitor integration pipelines and investigate recurring failures with developers responsible for supported services and automated release environments.",
        final,
    ]
    assert len(" · ".join(duties)) > 500
    return "Responsibilities:\n\n" + "\n\n".join(duties)


def test_over_budget_list_does_not_drop_a_prohibition_in_a_second_sentence():
    restriction = "Operate release tooling in staging environments. Production deployment is forbidden."
    source = _long_duty_list(restriction, "Deploy release tooling for internal engineering teams.")
    assert restriction in _role_list_preview(source)
    job = JobPosting("Engineer", "Example", "Israel", "https://example.test/job", description=source)
    assert not _source_role_preview(job) and not _summarize_role(job)


@pytest.mark.parametrize(("referent", "dependent"), [
    ("Build a separate incident triage service for integration testing", "Maintain that service"),
    ("Design an internal incident management system", "Maintain this system during software releases"),
    ("Develop diagnostic tools for integration testing", "Maintain these tools for release engineering"),
    ("Build an automated test framework for internal services", "Maintain the framework for integration testing"),
])
def test_over_budget_list_does_not_orphan_a_referring_duty(referent, dependent):
    assert _role_list_preview("Responsibilities:\n" + referent + "\n" + dependent) == f"{referent} · {dependent}"
    preview = _role_list_preview(_long_duty_list(referent, dependent))
    assert referent in preview and dependent in preview


@pytest.mark.parametrize("technical_duty", [
    "Develop skills assessment tools for engineering teams",
    "Build experience sampling software for research teams",
    "Maintain knowledge management APIs for internal services",
])
def test_technical_duty_is_not_mistaken_for_career_marketing(technical_duty):
    assert _role_list_preview("Responsibilities:\n" + technical_duty + "\nMaintain integration tests") == (
        technical_duty + " · Maintain integration tests"
    )


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
    assert 'Report bugs found during execution' in html
    assert 'Perform tests in different scopes' in html
    assert 'Current B.Sc. student' in html
    assert 'Scripting skills in Python' in html
    summary = html.split('class="role-duties-preview">', 1)[1].split("</span>", 1)[0]
    assert "Current B.Sc. student" not in summary and "Scripting skills in Python" not in summary
    duties = description.split("What you'll be doing:\n\n", 1)[1].split("\nWhat we need to see:", 1)[0].splitlines()
    assert all(duty in html_lib.unescape(html) for duty in duties)
    assert 'View all responsibilities' in html
    assert '<details class="role-duties">' in html
    expanded = html_lib.unescape(html.split('<details class="role-duties">', 1)[1].split('</details>', 1)[0])
    full_list = expanded.split('<ul>', 1)[1].split('</ul>', 1)[0]
    assert [full_list.index(duty) for duty in duties] == sorted(full_list.index(duty) for duty in duties)
    assert 'Current B.Sc. student' not in full_list and 'Scripting skills in Python' not in full_list
    assert '<details class="content-section collapsible-section" id="resume-match">' in html
    assert 'All applicants welcome' not in html
    assert 'Find People on LinkedIn' in html
    assert repo.get_by_id(job_id).description == description
