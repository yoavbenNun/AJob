"""Job-detail quality regressions; synthetic pages and disposable databases only."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.job_content import clean_job_text, description_problem
from src.matching.resume_matcher import match_resume_to_job
from src.models import Company, JobPosting
from src.resume.resume_parser import parse_resume_text
from src.scrapers.careers_scraper import _enrich_job, extract_requirements
from src.storage.job_repository import JobRepository
from src.storage.scan_store import ScanStore


RESUME = parse_resume_text('Example Candidate\nSKILLS\nC, Python, FastAPI\nEDUCATION\nBachelor Software Engineering (in progress)')


def job(**changes):
    return replace(JobPosting('Software Engineering Student', 'Example', 'Israel',
                              'https://example.test/job', description='Develop Python services.',
                              requirements='Python required.', status='applied'), **changes)


@pytest.mark.parametrize('error', [
    'English\nPage not found.\nThe URL you have provided is invalid.\nPlease contact the Workday Administrator at your company if you require assistance.',
    '404 Not Found', '403 Forbidden', 'Access Denied',
    'Access to this page has been denied.', 'Verify you are human',
    'Please complete the CAPTCHA', '<html><body>Just a moment...</body></html>',
])
@pytest.mark.parametrize('title', ['DevOps & Cloud Infrastructure Intern',
                                   'Research Software Engineering Student, AI and GPU Networking'])
def test_error_pages_do_not_establish_student_eligibility(error, title):
    posting = job(title=title, description=error, requirements='')
    result = match_resume_to_job(RESUME, posting)
    assert result.eligibility_status == 'potentially_eligible'
    assert 'cannot be verified' in ' '.join(result.eligibility_reasons)
    assert posting.status == 'applied'


def test_legitimate_role_discussing_error_pages_is_not_rejected():
    posting = job(description='Responsibilities\nDevelop Python handlers for 404 Not Found and CAPTCHA failures.')
    assert description_problem(posting.description) is None
    assert match_resume_to_job(RESUME, posting).eligibility_status == 'student_eligible'


def test_error_in_requirements_only_is_still_unknown():
    result = match_resume_to_job(RESUME, job(description='', requirements='Page not found.'))
    assert result.eligibility_status == 'potentially_eligible'


def test_navigation_job_alerts_does_not_hide_a_valid_posting():
    posting = job(description='Job Alerts\nResponsibilities\nDevelop Python services.', requirements='')
    result = match_resume_to_job(RESUME, posting)
    assert result.eligibility_status == 'student_eligible'
    assert result.job_skills == ('Python',)


def test_explicit_qualifications_outweigh_supporting_responsibility_mentions():
    posting = job(description='You will build services using FastAPI.', requirements='Python required.')
    python = parse_resume_text('Example\nSKILLS\nPython')
    fastapi = parse_resume_text('Example\nSKILLS\nFastAPI')
    assert match_resume_to_job(python, posting).breakdown.skills.points == 40
    assert match_resume_to_job(fastapi, posting).breakdown.skills.points == 20


@pytest.mark.parametrize('legacy_excerpt', [False, True])
def test_amazon_efa_does_not_score_company_prose_footer_or_related_jobs(legacy_excerpt):
    text = '''About us
Amazon Web Services helps customers worldwide.
Whatever qualifications and skills you bring, we welcome applicants.
Amazon Web Services supports many customers.
Basic Qualifications
BSc student with at least 2 semesters remaining.
Preferred Qualifications
Experience in C
Our inclusive culture empowers Amazonians.
https://example.test/support.html
Recommended jobs
HTML and AWS Engineer
Minimum Qualifications
PhD required
'''
    requirements = text[text.index('qualifications and skills'):] if legacy_excerpt else extract_requirements(text)
    posting = job(title='Software Development Student, EFA Annapurna Labs',
                  description=text, requirements=requirements)
    result = match_resume_to_job(RESUME, posting)
    assert result.job_skills == ('C',)
    assert result.score == 65
    if not legacy_excerpt:
        assert 'HTML' not in posting.requirements and 'PhD' not in posting.requirements
    assert result.eligibility_status != 'ineligible'


def test_real_html_aws_requirements_and_responsibility_skills_are_preserved():
    text = '''<nav>React Docker</nav><h2>Responsibilities</h2>
<p>You will build services using FastAPI.</p><h2>Requirements</h2>
<p>Python, HTML and AWS required.</p><footer>Kubernetes</footer>'''
    result = match_resume_to_job(RESUME, job(description=text, requirements=extract_requirements(text)))
    assert result.job_skills == ('Python', 'HTML', 'FastAPI', 'AWS')
    assert 'React' not in clean_job_text(text) and 'Kubernetes' not in clean_job_text(text)


@pytest.mark.parametrize('durable', [False, True])
def test_invalid_refresh_preserves_previous_content_and_application_status(tmp_path, durable):
    repository = JobRepository(tmp_path / 'jobs.db')
    repository.initialize()
    original = job()
    repository.save(original)
    invalid = job(description='English\nPage not found.', requirements='', status='new')
    if durable:
        store = ScanStore(repository)
        store.migrate()
        try:
            assert not store.save_discovery(invalid, 'test-slot')
        finally:
            store.close()
    else:
        repository.save(invalid)
    saved = repository.get_by_id(original.id)
    assert saved.description == original.description
    assert saved.requirements == original.requirements
    assert saved.status == 'applied'
    repository.save(job(description='', requirements='Python and C required.'))
    assert repository.get_by_id(original.id).description == original.description
    repository.save(job(description='Develop Python and C services.'))
    assert repository.get_by_id(original.id).description == 'Develop Python and C services.'


def fake_browser(text, status=200):
    class Page:
        def goto(self, *args, **kwargs): return SimpleNamespace(status=status)
        def wait_for_timeout(self, *_): pass
        def locator(self, *_): return SimpleNamespace(count=lambda: 1, inner_text=lambda: text)
        def close(self): pass
    return SimpleNamespace(new_page=lambda: Page())


@pytest.mark.parametrize('text,status', [('Page not found.', 200), ('Python job description', 404), ('Access Denied', 403)])
def test_detail_fetch_checks_http_and_body_before_overwriting(text, status):
    posting = job()
    assert _enrich_job(fake_browser(text, status), posting, 1000) == 'JobDetailUnavailable'
    assert posting.description == 'Develop Python services.' and posting.requirements == 'Python required.'


def test_worker_reports_detail_failure_once_and_keeps_other_discoveries(monkeypatch):
    from src.scrapers import budgeted_scan as scan
    monkeypatch.setattr(scan.os, 'setsid', lambda: None, raising=False)
    invalid = job(description='Page not found.', requirements='')
    valid = job(job_url='https://example.test/other')
    class Scraper:
        def __init__(self, *_): pass
        def scan_company(self, _): return SimpleNamespace(error=None, jobs=[invalid, valid])
    monkeypatch.setattr(scan, 'CareersScraper', Scraper)
    monkeypatch.setattr(scan, '_enrich_job', lambda *args: None)
    class Pipe:
        def __init__(self): self.events = []
        def send(self, event): self.events.append(event)
        def close(self): pass
    pipe = Pipe()
    scan.source_worker(Company('Example', 'https://example.test/jobs'), 1000, 1, True, pipe)
    assert len([event for event in pipe.events if event[0] == 'job']) == 2
    assert pipe.events[-1] == ('error', 'JobDetailUnavailable')
    assert sum(event[0] == 'error' for event in pipe.events) == 1
