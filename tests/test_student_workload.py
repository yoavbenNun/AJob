"""Marvell regression: student enrollment is distinct from employment hours."""

from dataclasses import replace

import pytest

from src.filtering.job_filter import classify_job
from src.matching.eligibility import student_acceptance_evidence
from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text


STUDENT = parse_resume_text('''Synthetic Candidate
Third-year Software Engineering student
SKILLS
Python, SQL
EDUCATION
B.Sc. Software Engineering (in progress), expected 2027
PROJECTS
Built Python software and SQL data pipelines.
''')
MARVELL = '''We are looking for a motivated advanced BSc student to join our team as a Data Preprocessing Engineer.
Build and maintain scripts and Python-based tooling for data infrastructure.
Requirements
Advanced BSc student in Computer Science, Software Engineering, Electrical Engineering, Data Science, or a related field
Good Python skills with pandas and NumPy.
'''


def job(description=MARVELL, title='AI Infrastructure Engineer Intern'):
    return JobPosting(title, 'Synthetic', 'Petah-Tikva', 'https://example.test/student',
                      description=description, status='reviewed')


@pytest.mark.parametrize('wording', [
    'Advanced BSc student in Software Engineering.',
    'Advanced B.Sc. student in Computer Science.',
    'We are looking for a motivated advanced BSc student to join us.',
    'Requirements: Advanced BSc student in Computer Science.',
    'Currently enrolled as a BS 3rd or 4th-year student or Master student.',
])
def test_explicit_bachelor_enrollment_is_recognized(wording):
    assert student_acceptance_evidence(wording)


@pytest.mark.parametrize('title', ['AI Infrastructure Engineer Intern', 'Software Engineer'])
def test_marvell_student_evidence_survives_generic_titles(title):
    posting = job(title=title)
    before = replace(posting)
    assert classify_job(posting).relevant
    result = match_resume_to_job(STUDENT, posting)
    assert result.eligibility_status == 'student_eligible'
    assert 'confirm working hours' in ' '.join(result.eligibility_reasons)
    assert posting == before


def test_marvell_requires_real_software_details_not_a_workday_error_page():
    assert not classify_job(job('The page you are looking for does not exist.')).relevant
    assert classify_job(job()).relevant


@pytest.mark.parametrize('hours', ['Full time', 'Full-time'])
def test_full_time_label_is_not_a_completed_degree_requirement(hours):
    posting = job(MARVELL + '\nEmployment type: ' + hours)
    result = match_resume_to_job(STUDENT, posting)
    baseline = match_resume_to_job(STUDENT, job())
    assert result.eligibility_status == 'student_eligible'
    assert result.technical_score == baseline.technical_score
    assert 'does not imply part-time' in ' '.join(result.eligibility_reasons)


def test_full_time_completed_degree_is_still_ineligible():
    result = match_resume_to_job(STUDENT, job('Full-time. Requirements\nCompleted B.Sc. degree required. Python.', 'Software Engineer'))
    assert result.eligibility_status == 'ineligible'
    assert result.score <= 25


def test_preferred_advanced_degree_does_not_override_student_acceptance():
    result = match_resume_to_job(STUDENT, job(MARVELL + '\nPreferred Qualifications\nCompleted MSc degree.'))
    assert result.eligibility_status == 'student_eligible'


@pytest.mark.parametrize('prose', [
    'Our community supports advanced BSc students through outreach.',
    'Mentor advanced BSc students.',
    'We collaborate with BSc students at universities.',
])
def test_company_prose_does_not_establish_candidate_enrollment(prose):
    assert not student_acceptance_evidence(prose)


def test_explicit_part_time_keeps_student_eligibility_without_unknown_hours_warning():
    result = match_resume_to_job(STUDENT, job(MARVELL + '\nPart-time, 20 hours per week.'))
    assert result.eligibility_status == 'student_eligible'
    assert not any('confirm working hours' in reason for reason in result.eligibility_reasons)
