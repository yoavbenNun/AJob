"""Student eligibility regressions: synthetic candidate, no network or cloud DB."""

from dataclasses import replace

import pytest

from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text


STUDENT = parse_resume_text('''Example Candidate
Third-year Software Engineering student
SKILLS
Python, C++, Java
EDUCATION
B.Sc. Software Engineering, Example University (in progress)
PROJECTS
Built Python services and Java applications.
''')
PALO_TITLE = 'Software Engineer – Graduate Program (Bootcamp)'
PALO_REQUIREMENTS = '''Qualifications
B.Sc. in Computer Science, Software Engineering, or a related technical degree with a GPA of 85 or above.
Preferred Qualifications
Python, C++, Java
'''


def posting(title, requirements='', description=''):
    return JobPosting(title, 'Example', 'Israel', 'https://example.test/jobs/1',
                      requirements=requirements, description=description, status='applied')


def test_palo_alto_degree_completion_regression():
    job = posting(PALO_TITLE, PALO_REQUIREMENTS)
    result = match_resume_to_job(STUDENT, job)
    assert result.eligibility_status == 'ineligible'
    assert not result.eligible
    assert result.score <= 25 < result.technical_score
    assert 'still studying' in ' '.join(result.disqualifiers)
    assert job.status == 'applied'  # Matching never changes lifecycle state.
    # Requirements captured only inside the full description must also count.
    assert not match_resume_to_job(STUDENT, replace(job, description=PALO_REQUIREMENTS, requirements='')).eligible


@pytest.mark.parametrize('title,requirements,description,expected', [
    ('Student Software Engineer', 'Currently pursuing a B.Sc. in Software Engineering. Python.', '', 'student_eligible'),
    ('Software Engineering Internship', 'Python, Java.', '', 'student_eligible'),
    ('Junior Software Engineer', 'Python and Java.', '', 'potentially_eligible'),
    ('Software Engineer', 'Minimum 2 years of industry experience. Python.', '', 'ineligible'),
    ('Software Engineer', 'Open to third-year students. Python.', '', 'student_eligible'),
    ('Software Engineer', 'Python.', 'Full-time position with flexibility for students.', 'student_eligible'),
    ('Junior Engineer', '', '', 'potentially_eligible'),
    ('Software Engineering Student', '', '', 'potentially_eligible'),
    ('Graduate Software Engineer', 'Python.', '', 'potentially_eligible'),
    ('Junior Software Engineer', 'Bachelor degree preferred. Python.', '', 'potentially_eligible'),
    ('Software Engineer', 'No degree required. Python.', '', 'potentially_eligible'),
    ('Student Software Engineer', 'B.Sc. degree required. Python.', '', 'potentially_eligible'),
    ('Intern', 'Completed bachelor degree required. Python.', '', 'ineligible'),
    ('Software Engineer', 'B.Sc. or currently pursuing a degree. Python.', '', 'student_eligible'),
    ('Software Engineer', 'B.Sc. or equivalent experience. Python.', '', 'potentially_eligible'),
    ('Software Engineer Intern', 'Preferred Qualifications\n2 years of industry experience.', '', 'student_eligible'),
    ('Software Engineer', 'Python.', 'Our company supports students through outreach.', 'potentially_eligible'),
    ('Junior Software Engineer', 'Python.', 'Full-time role.', 'potentially_eligible'),
    ('Software Engineer', 'Must be available 40 hours per week.', '', 'ineligible'),
    ('Software Engineer', 'Must be available 40 hours per week.', 'Flexible for students.', 'student_eligible'),
    ('Graduate Software Engineer', 'B.Sc in Software Engineering required.', '', 'ineligible'),
    ('Software Engineer', '2 years of Python experience required.', '', 'potentially_eligible'),
    ('Graduate Software Engineer', 'Preferred Qualifications\nPython.\nRequired Qualifications\nB.Sc. in Software Engineering.', '', 'ineligible'),
    ('Software Engineer', 'Not open to students. Python.', '', 'ineligible'),
    ('Software Engineering Intern', 'Graduates only. Python.', '', 'ineligible'),
    ('Software Engineer', 'Graduates only. Open to third-year students.', '', 'potentially_eligible'),
])
def test_student_eligibility_boundaries(title, requirements, description, expected):
    result = match_resume_to_job(STUDENT, posting(title, requirements, description))
    assert result.eligibility_status == expected
    assert result.eligibility_reasons
    assert result.eligible == (expected != 'ineligible')


def test_experience_duration_unknown_is_not_an_automatic_rejection():
    resume = replace(STUDENT, experience='Developed Python services during a software internship.')
    result = match_resume_to_job(resume, posting('Software Engineer', '2 years of professional experience required.'))
    assert result.eligibility_status == 'potentially_eligible'
    assert 'unverified' in ' '.join(result.eligibility_reasons)


def test_completed_degree_does_not_trigger_student_completion_conflict():
    graduate = replace(STUDENT, education='B.Sc. Software Engineering, graduated 2024.', summary='Software developer.')
    result = match_resume_to_job(graduate, posting(PALO_TITLE, PALO_REQUIREMENTS))
    assert result.eligible


def test_generic_software_title_accepts_third_year_student_in_details():
    from src.filtering.job_filter import classify_job
    assert classify_job(posting('Software Engineer', 'Open to third-year students. Python.')).relevant


def test_email_separates_possible_matches_and_hides_ineligible():
    from types import SimpleNamespace
    from src.notifications.email_notifier import EmailJobRecommendation, SmtpSettings, compose_scan_summary

    settings = SmtpSettings('localhost', 25, '', '', 'sender@example.test', 'recipient@example.test', False)
    summary = SimpleNamespace(new_jobs=(), companies_scanned=1, jobs_found=3, jobs_saved=3, failures=0)
    recommendations = tuple(EmailJobRecommendation(
        title=title, company='Example', location='Israel', job_url='https://example.test/job',
        score=90, eligible=status != 'ineligible', reason='Python overlap.',
        eligibility_status=status, eligibility_reason='Eligibility evidence.')
        for title, status in [('Student role', 'student_eligible'), ('Junior role', 'potentially_eligible'),
                              ('Blocked role', 'ineligible')])
    message = compose_scan_summary(settings, summary, recommendations)
    body = message.get_content()
    assert '1 new matching' in message['Subject']
    assert '1 possible match' in message['Subject']
    assert body.index('Student role') < body.index('Possible Matches') < body.index('Junior role')
    assert 'Blocked role' not in body
    assert 'Eligibility evidence.' in body


def test_cloud_recommendations_keep_uncertain_roles_but_exclude_palo(monkeypatch):
    from types import SimpleNamespace
    from src import main

    from src.resume.safe_profile import build_safe_profile
    monkeypatch.setenv('CANDIDATE_PROFILE_JSON', build_safe_profile(STUDENT).to_json())
    monkeypatch.setattr(main, 'OpenStreetMapDrivingProvider', lambda: None)
    monkeypatch.setattr(main, 'evaluate_commute', lambda *_: SimpleNamespace(eligible=True, reason='Synthetic commute.'))
    summary = SimpleNamespace(new_jobs=(posting(PALO_TITLE, PALO_REQUIREMENTS),
                                        posting('Junior Software Engineer', 'Python.'),
                                        posting('Student Software Engineer', 'Python.')))
    recommendations = main._cloud_recommendations(summary)
    assert len(recommendations) == 2
    assert {item.eligibility_status for item in recommendations} == {'student_eligible', 'potentially_eligible'}
    assert all(item.title != PALO_TITLE for item in recommendations)


def test_safe_cloud_profile_roundtrip_preserves_ongoing_studies():
    from src.resume.safe_profile import build_safe_profile, load_safe_profile_json
    encoded = build_safe_profile(STUDENT).to_json()
    assert '(in progress)' in encoded
    assert 'Example Candidate' not in encoded
    assert 'Example University' not in encoded
    cloud_profile = load_safe_profile_json(encoded).to_resume_profile()
    assert not match_resume_to_job(cloud_profile, posting(PALO_TITLE, PALO_REQUIREMENTS)).eligible
