"""Explicit coding duties are distinct from tools mentioned in qualifications."""
import pytest

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import JobPosting


def test_elbit_20928_algorithm_student_develops_code():
    # Public official feed, 2026-10-30: job 20928. Only role evidence retained.
    job = JobPosting(
        'System Engineering Student – Algorithms Team', 'Elbit Systems', 'Haifa',
        'https://elbitsystemscareer.com/job/?jid=20928', description=(
            'In this role you will\n'
            'Assist in defining, analyzing and documenting system requirements '
            'and specifications for navigation and guidance systems\n'
            'Take part in the development, implementation and testing of algorithms '
            'in the fields of computer vision and image processing\n'
            'Develop code for performance research, data analysis and support '
            'integration activities, laboratory testing and field experiments\n'
            ':Requirements\n'
            'B.Sc. student in Electrical Engineering, Software Engineering or '
            'Aeronautical Engineering, from second year and above\n'
            'Experience working with MATLAB'))
    assert prefilter_job(job).relevant
    assert classify_job(job).relevant


@pytest.mark.parametrize('duties', [
    'Develop code for performance research and data analysis.',
    'You will write code for image processing.',
    'Developing production code for embedded systems.',
    'Writing code for algorithm implementation.',
])
def test_explicit_code_development_is_role_evidence(duties):
    job = JobPosting('Algorithms Student', 'Example', 'Israel', 'https://example.test/1',
                     description=duties + '\nRequirements\nBSc student.')
    assert classify_job(job).relevant


@pytest.mark.parametrize('description', [
    'Perform manual testing.\nRequirements\nAbility to develop code is preferred.',
    'Perform laboratory measurements.\nPreferred qualifications\nExperience writing code.',
    'You will not develop code for this role.',
    'You will never write code.',
    "You won't write code in this role.",
    "You don't develop code.",
    'You won’t write code in this role.',
    'Work without writing code.',
    'No need to develop code; execute manual test plans.',
    'Run existing code and report results.',
    'Use Python and MATLAB for laboratory measurements.',
    'Review code written by the engineering team.',
    'Document system requirements.\n:Requirements\nAbility to develop code.',
    'Document system requirements. Requirements: Experience writing code.',
    'Perform manual tests.\nJob requirements\nExperience writing code.',
    'Perform manual tests.\nMinimum requirements\nExperience writing code.',
    'Perform manual tests.\nBasic requirements\nExperience writing code.',
    'Perform manual tests.\nRequirements and qualifications\nExperience writing code.',
    'Perform manual tests.\nQualifications and requirements\nExperience writing code.',
])
def test_incidental_qualifications_and_negated_coding_are_not_duties(description):
    job = JobPosting('QA Student', 'Example', 'Israel', 'https://example.test/2',
                     description=description)
    assert not classify_job(job).relevant


@pytest.mark.parametrize('title', ['Algorithms Engineer', 'Senior Algorithms Student',
                                  'Product Manager Intern', 'Hardware Engineer Student'])
def test_code_duties_do_not_bypass_existing_role_or_seniority_gates(title):
    job = JobPosting(title, 'Example', 'Israel', 'https://example.test/3',
                     description='Develop code for performance research.')
    assert not classify_job(job).relevant
