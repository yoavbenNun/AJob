"""Real process-compliance false positive; no global profile/threshold changes."""

import json
from pathlib import Path

import pytest

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import JobPosting


def posting(description=None):
    payload = json.loads(Path('tests/fixtures/elbit_software_quality_student.json').read_text())
    if description is not None:
        payload['description'] = description
    return JobPosting(**payload)


def test_official_elbit_process_quality_role_is_not_software_development():
    job = posting()
    assert prefilter_job(job).relevant  # Still inspect actual detail evidence.
    assert not classify_job(job).relevant


@pytest.mark.parametrize('description', [
    'Develop software tools for testing embedded products.',
    'Write production code in Python to automate product tests.',
    'Develop test automation frameworks in Python.',
    'Build automation infrastructure for testing backend services.',
    'No prior experience is required. Develop software test tools.',
    'We do not perform manual audits; develop software tools instead.',
    'You will not only develop software but also test embedded products.',
    'Write production code in Python without direct supervision.',
    'No experience is required to write production code in Python.',
    'Develop automated tests in Python for embedded products.',
    'Implement automated tests in C++ for backend services.',
    'Write Python automated tests for embedded products.',
    'You will not require developing automation frameworks. Build automation infrastructure in Python.',
    'We will never be developing automation frameworks. Build automation infrastructure in Python.',
])
def test_quality_title_with_real_programming_duties_remains_eligible(description):
    assert classify_job(posting(description)).relevant


@pytest.mark.parametrize('description', [
    'Audit software development processes and review compliance documents.',
    'Perform internal audits.\nRequirements\nExperience developing software tools.',
    'No software development is involved. Perform manual testing.',
    'You will not develop test automation frameworks. Perform audits.',
    'You will never write code. Verify process documentation.',
    'You will not require developing automation frameworks. Perform process audits.',
    'You will not require developing automated tests in Python. Perform process audits.',
])
def test_process_only_qualification_only_and_negated_duties_do_not_qualify(description):
    assert not classify_job(posting(description)).relevant
