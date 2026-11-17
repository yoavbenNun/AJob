"""Mandatory academic enrollment, separate from technical skills and residence."""

from dataclasses import replace

import pytest

from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text


STUDENT = parse_resume_text('''Synthetic Candidate
Third-year Software Engineering student
SKILLS
Python, C++
EDUCATION
Bachelor's in Software Engineering (in progress), Example University, Israel; expected 2027
PROJECTS
Built Python and C++ software.
''')


def match(requirement, *, description='', resume=STUDENT, title='Software Engineering Intern', location='Israel'):
    return match_resume_to_job(resume, JobPosting(title, 'Synthetic', location,
                               'https://example.test/job', description=description,
                               requirements=requirement, status='reviewed'))


@pytest.mark.parametrize('requirement', [
    "Currently pursuing a master’s degree in Computer Science or a related field.",
    'Currently pursuing a MS or higher degree in CS/EE/CE or related field.',
    'Must be enrolled in an M.Sc. program in Computer Science.',
    "Master's degree required. Python and C++.",
    'Graduate student required. Python.',
    'Must be a graduate student in Computer Science.',
    'Currently a graduate student in Computer Science.',
    'Applicants must be graduate students in Computer Science.',
    'You must be enrolled in an accredited university program in the Czech Republic during the full duration of the internship.',
    'Must be a student at a Czech university.',
    'Currently studying at a university in Germany.',
    'Must be enrolled at a Canadian university.',
])
def test_explicit_conflicts_are_ineligible(requirement):
    result = match(requirement)
    assert result.eligibility_status == 'ineligible'
    assert not result.eligible
    assert result.score <= 25
    assert result.eligibility_reasons


@pytest.mark.parametrize('requirement', [
    "Bachelor's or master's students accepted. Python.",
    "Bachelor's and master's students accepted. Python.",
    "Currently pursuing a bachelor's or master's degree. Python.",
    'Currently pursuing B.Sc. / M.Sc. studies. Python.',
    "Preferred Qualifications\nMaster's degree in Computer Science. Python.",
    "Master's degree preferred, not required. Python.",
    'International students welcome. Python.',
    'Our engineers mentor graduate students. Python.',
    "Work with master's students on Python projects.",
    'Master Python and C++ tooling.',
    'Preferred Qualifications\nEnrollment at a Czech university.',
    'Currently enrolled at a university in Computer Science.',
    'Currently enrolled in a university program in Computer Science.',
    'Must be enrolled at an accredited university.',
    'Must be an international university student.',
    'Open to third-year students. Python.',
])
def test_counterexamples_preserve_student_eligibility(requirement):
    assert match(requirement).eligibility_status == 'student_eligible'


@pytest.mark.parametrize('separator', ['/', '\\', ' or '])
def test_apple_bachelor_or_master_alternative_accepts_listed_undergraduate(separator):
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    requirement = (f'Minimum Qualifications\nPursuing a B.Sc.{separator}M.Sc. in Electrical Engineering, '
                   'Computer Engineering.\nStudents who have completed at least 3 semesters.')
    job = JobPosting('Software Engineering Student', 'Apple', 'Jerusalem, Israel',
                     'https://example.test/apple', description=requirement,
                     requirements=requirement, status='reviewed')
    before = replace(job)
    result = match_resume_to_job(candidate, job)
    assert result.eligibility_status == 'student_eligible'
    assert job == before


def test_backslash_in_unrelated_text_does_not_override_masters_requirement():
    requirement = "Minimum Qualifications\nMaster's degree required in Computer Science. Path: tools\\scripts."
    assert match(requirement).eligibility_status == 'ineligible'


def test_required_backslash_degree_pair_is_not_masters_only():
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    requirement = 'A B.Sc.\\M.Sc. degree required in Computer Engineering.'
    assert match(requirement, resume=candidate).eligibility_status == 'potentially_eligible'


@pytest.mark.parametrize('requirement', [
    "Must be a master's student currently enrolled in B.Sc.\\M.Sc. Computer Engineering.",
    "Must have a master's degree and be enrolled in B.Sc.\\M.Sc. Computer Engineering.",
    "Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, only master's students accepted.",
    "Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, master's enrollment mandatory.",
    "Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, a master's degree is required.",
    "B.Sc.\\M.Sc. candidates must be enrolled in a master's program.",
    "B.Sc.\\M.Sc. in computer or electrical engineering, a master's degree is required.",
    "Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, must be a graduate student.",
    "B.Sc.\\M.Sc. in Computer Engineering, graduate students only.",
    "B.Sc.\\M.Sc. in Computer Engineering, only graduate students accepted.",
])
def test_explicit_masters_requirement_takes_precedence_over_backslash_alternative(requirement):
    from src.matching.eligibility import assess_student_eligibility
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    assert match(requirement, resume=candidate).eligibility_status == 'ineligible'
    job = JobPosting('Software Engineering Student', 'Apple', 'Israel', 'https://example.test/job', requirements=requirement)
    assert assess_student_eligibility(candidate, job)[0] == 'ineligible'


def test_additional_ambiguous_masters_mention_is_reviewable():
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    requirement = "Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, master's program status applies."
    assert match(requirement, resume=candidate).eligibility_status == 'potentially_eligible'


def test_additional_postgraduate_eligibility_wording_is_reviewable():
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    requirement = 'Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, postgraduate eligibility is required.'
    assert match(requirement, resume=candidate).eligibility_status == 'potentially_eligible'


def test_optional_postgraduate_eligibility_does_not_disqualify():
    candidate = replace(STUDENT, education='B.Sc. Computer Engineering student; completed 4 semesters')
    requirement = 'Currently pursuing B.Sc.\\M.Sc. in Computer Engineering, postgraduate eligibility preferred.'
    assert match(requirement, resume=candidate).eligibility_status == 'student_eligible'


def test_apple_alternative_does_not_override_closed_major_requirement():
    requirement = ('Minimum Qualifications\nPursuing a B.Sc.\\M.Sc. in Electrical Engineering, '
                   'Computer Engineering.\nStudents who have completed at least 3 semesters.')
    assert match(requirement).eligibility_status == 'ineligible'


def test_preferred_heading_does_not_leak_into_required_enrollment():
    result = match("Preferred Qualifications\nPython.\nRequired Qualifications\nCurrently pursuing a master's degree.")
    assert result.eligibility_status == 'ineligible'


def test_description_enrollment_is_mandatory_without_a_heading():
    assert match('', description="Currently pursuing a master's degree in Computer Science.").eligibility_status == 'ineligible'


@pytest.mark.parametrize('requirement', [
    "Master's degree or equivalent experience.",
    "Master's degree in Computer Science",
    'Must be enrolled at a local university.',
])
def test_ambiguous_requirements_are_reviewable(requirement):
    result = match('', description=requirement, location='Not specified')
    assert result.eligibility_status == 'potentially_eligible'
    assert result.eligible


def test_unknown_study_country_in_cloud_profile_is_reviewable_not_inferred_from_residence():
    resume = replace(STUDENT, education="Bachelor's Software Engineering, in progress", location='Israel')
    result = match('Must be enrolled at a Czech university.', resume=resume)
    assert result.eligibility_status == 'potentially_eligible'
    assert 'not documented' in ' '.join(result.eligibility_reasons)


def test_unrecognized_regional_adjective_is_reviewable():
    assert match('Must be enrolled at an Austrian university.').eligibility_status == 'potentially_eligible'


def test_enrollment_country_alternatives_and_local_requirement():
    assert match('Must be enrolled at a university in Israel or Germany.').eligibility_status == 'student_eligible'
    assert match('Must be enrolled at a local university.', location='Germany').eligibility_status == 'ineligible'
    czech = replace(STUDENT, education="Bachelor's in progress, university in Czech Republic")
    assert match('Must be enrolled at a Czech university.', resume=czech).eligibility_status == 'student_eligible'


def test_postgraduate_candidate_and_technical_score_are_not_rejected():
    master = replace(STUDENT, education="Master's in Software Engineering, currently pursuing, Israel")
    requirement = "Currently pursuing a master's degree. Python and C++."
    accepted, rejected = match(requirement, resume=master), match(requirement)
    assert accepted.eligibility_status == 'student_eligible'
    assert rejected.eligibility_status == 'ineligible'
    assert accepted.breakdown.skills == rejected.breakdown.skills
    assert accepted.breakdown.projects == rejected.breakdown.projects
    assert rejected.score < rejected.technical_score


def test_existing_job_is_recomputed_without_persistence_or_status_changes():
    job = JobPosting('Software Intern', 'Synthetic', 'Israel', 'https://example.test/saved',
                     requirements="Currently pursuing a master's degree. Python.", status='applied')
    before = replace(job)
    assert match_resume_to_job(STUDENT, job).eligibility_status == 'ineligible'
    assert job == before


def test_equivalent_experience_in_requirements_remains_reviewable():
    assert match("Master's degree or equivalent experience. Python.").eligibility_status == 'potentially_eligible'


def test_cloud_reduced_profile_and_email_share_the_corrected_eligibility(monkeypatch):
    from types import SimpleNamespace
    from src.main import _cloud_recommendations
    from src.notifications.email_notifier import compose_scan_summary, SmtpSettings
    from src.resume.safe_profile import SafeCandidateProfile
    from src.scrapers.budgeted_scan import DurableSummary
    profile = SafeCandidateProfile('Israel', ('Python',), (), ('Python',),
                                   "Bachelor's Software Engineering, in progress, third-year, expected 2027")
    monkeypatch.setenv('CANDIDATE_PROFILE_JSON', profile.to_json())
    jobs = [JobPosting('Software Intern', 'Synthetic', 'Israel', f'https://example.test/{i}',
                        description=text) for i, text in enumerate([
                            "Currently pursuing a master's degree. Python.",
                            'Must be enrolled at a Czech university. Python.',
                            "Currently pursuing a bachelor's or master's degree. Python."])]
    summary = DurableSummary(new_jobs=jobs)
    ranked = _cloud_recommendations(summary)
    assert {item.job_url for item in ranked} == {jobs[1].job_url, jobs[2].job_url}
    assert next(item for item in ranked if item.job_url == jobs[1].job_url).eligibility_status == 'potentially_eligible'
    settings = SmtpSettings('localhost',25,'','','from@example.test','to@example.test',False)
    body = compose_scan_summary(settings, summary, ranked).get_content()
    assert jobs[0].job_url not in body
    assert 'Possible Matches' in body
