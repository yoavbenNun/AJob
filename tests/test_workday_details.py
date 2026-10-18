"""Workday URL, identity and content regressions without network access."""

from io import BytesIO
import json
from urllib.parse import urljoin

import pytest

from src.models import Company, JobPosting
from src.scrapers.careers_scraper import extract_workday_jobs, _enrich_job
from src.scrapers.workday_details import (
    workday_urls, legacy_workday_url, fetch_workday_detail, WorkdayIdentityError, WorkdayTitleChanged,
)


@pytest.mark.parametrize('tenant,zone,site', [
    ('nvidia', 5, 'NVIDIAExternalCareerSite'), ('intel', 1, 'External'),
    ('ffive', 5, 'f5jobs'), ('motorolasolutions', 5, 'Careers'),
    ('redhat', 5, 'Jobs'), ('marvell', 1, 'MarvellCareers'),
])
def test_site_is_preserved_for_all_six_sources(tenant, zone, site):
    source = f'https://{tenant}.wd{zone}.myworkdayjobs.com/en-US/{site}'
    path = '/job/Israel/Software-Student_JR123'
    expected = source + path
    assert urljoin(source, path) != expected  # Reproduce the original defect.
    public, api = workday_urls(source, path)
    assert public == expected
    assert api == f'https://{tenant}.wd{zone}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}'
    posting = extract_workday_jobs(Company(tenant, source), {'jobPostings': [
        {'title': 'Software Student', 'externalPath': path}]})[0]
    assert posting.job_url == expected
    assert legacy_workday_url(source, public) == urljoin(source, path)
    assert workday_urls(source, public) == (public, api)


SOURCE = 'https://intel.wd1.myworkdayjobs.com/en-US/External'
URL = SOURCE + '/job/Haifa/Software-Student_JR123-1'
DESCRIPTION = '<p>Responsibilities</p><p>Develop Python software and automated tests.</p>' * 3 + '<p>Requirements</p><p>Currently pursuing a bachelor degree in software engineering.</p>'


def mock_detail(monkeypatch, **changes):
    info = dict(title='Software Student', jobReqId='JR123', externalUrl=URL,
                jobDescription=DESCRIPTION)
    info.update(changes)
    class Response(BytesIO):
        def geturl(self):
            return workday_urls(SOURCE, URL)[1]
    monkeypatch.setattr('src.scrapers.workday_details.urlopen', lambda *a, **k:
                        Response(json.dumps({'jobPostingInfo': info}).encode()))


def test_verified_detail_has_title_requirements_and_professional_content(monkeypatch):
    mock_detail(monkeypatch)
    detail = fetch_workday_detail(SOURCE, URL, 'Software Student')
    assert 'Software Student' in detail.description
    assert 'Requirements' in detail.description and 'Develop Python' in detail.description
    assert '<p>' not in detail.description


@pytest.mark.parametrize('change', [dict(jobReqId='JR999'),
                                    dict(externalUrl=SOURCE + '/job/Other/Role_JR999')])
def test_mismatched_identity_fails_closed(monkeypatch, change):
    mock_detail(monkeypatch, **change)
    with pytest.raises(WorkdayIdentityError):
        fetch_workday_detail(SOURCE, URL, 'Software Student')


def test_changed_title_is_held_for_review_even_with_verified_requisition(monkeypatch):
    mock_detail(monkeypatch, title='Different role')
    with pytest.raises(WorkdayTitleChanged):
        fetch_workday_detail(SOURCE, URL, 'Software Student')


def test_hyphenated_red_hat_requisition_is_not_truncated(monkeypatch):
    from src.scrapers.workday_details import requisition_id
    assert requisition_id('/job/Brno/Intern_R-059160') == 'r-059160'
    target = SOURCE + '/job/Brno/Intern_R-059160'
    mock_detail(monkeypatch, jobReqId='R-059160', externalUrl=target)
    assert fetch_workday_detail(SOURCE, target, 'Software Student').requisition == 'R-059160'


@pytest.mark.parametrize('body', ['Page not found.', 'Access Denied', 'Please complete the CAPTCHA', ''])
def test_invalid_detail_never_replaces_previous_valid_description(monkeypatch, body):
    mock_detail(monkeypatch, jobDescription=body)
    job = JobPosting('Software Student', 'Intel', 'Israel', URL,
                     source=SOURCE, description='Previous valid description', status='applied')
    assert _enrich_job(None, job, 1000) == 'JobDetailUnavailable'
    assert job.description == 'Previous valid description' and job.status == 'applied'


@pytest.mark.parametrize('url', ['https://evil.test/job/a_JR1', '//evil.test/job/a_JR1',
                                  '/en-US/Other/job/a_JR1', '/job/../a_JR1'])
def test_foreign_sites_and_ambiguous_paths_rejected(url):
    with pytest.raises(ValueError):
        workday_urls(SOURCE, url)


def test_recovered_content_changes_matching_without_formula_changes(monkeypatch):
    from src.resume.resume_parser import parse_resume_text
    from src.matching.resume_matcher import match_resume_to_job
    mock_detail(monkeypatch)
    resume = parse_resume_text('Example\nSKILLS\nPython\nEDUCATION\nBachelor Software Engineering (in progress)')
    job = JobPosting('Software Student', 'Intel', 'Israel', URL,
                     source=SOURCE, description='Page not found.')
    before = match_resume_to_job(resume, job)
    assert _enrich_job(None, job, 1000) is None
    after = match_resume_to_job(resume, job)
    assert before.eligibility_status == 'potentially_eligible'
    assert after.eligibility_status == 'student_eligible'
    assert after.score > before.score and 'Python' in after.job_skills


def test_blocked_source_is_not_retried_for_every_posting(monkeypatch):
    from types import SimpleNamespace
    from src.scrapers import budgeted_scan as scan
    monkeypatch.setattr(scan.os, 'setsid', lambda: None, raising=False)
    jobs = [JobPosting('Software Student', 'Intel', 'Israel', URL + str(i), source=SOURCE)
            for i in range(3)]
    monkeypatch.setattr(scan, 'CareersScraper', lambda *a:
                        SimpleNamespace(scan_company=lambda c: SimpleNamespace(error=None, jobs=jobs)))
    calls = []
    monkeypatch.setattr(scan, '_enrich_job', lambda *a: calls.append(a) or 'WorkdayAccessDenied')
    events = []
    pipe = SimpleNamespace(send=events.append, close=lambda: None)
    scan.source_worker(Company('Intel', SOURCE), 1000, 1, True, pipe)
    assert len(calls) == 1
    assert len([event for event in events if event[0] == 'job']) == 3
    assert events[-1] == ('error', 'JobDetailUnavailable')
