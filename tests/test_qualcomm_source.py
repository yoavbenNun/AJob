"""Public portal fixtures only; no cloud, browser or SMTP access."""
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job
from src.models import Company
from src.scrapers import qualcomm
from src.scrapers.careers_scraper import CareersScraper
from src.scrapers.official_sources import fetch_jobs
from src.storage.job_repository import JobRepository

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/qualcomm_student.json').read_text())
COMPANY = Company('Qualcomm', 'https://careers.qualcomm.com/careers')


class Reply(BytesIO):
    def __init__(self, payload, url):
        super().__init__(json.dumps(payload).encode())
        self.url = url
    def geturl(self):
        return self.url


def install(monkeypatch, search=None, detail=None):
    calls = []
    def read(request, timeout):
        calls.append((request.full_url, timeout))
        params = parse_qs(urlsplit(request.full_url).query)
        payload = (search(params) if search else dict(count=1, positions=[deepcopy(FIXTURE)])) if '/search?' in request.full_url else (detail if detail is not None else deepcopy(FIXTURE))
        return Reply(dict(status=200, data=payload), request.full_url)
    monkeypatch.setattr(qualcomm, 'urlopen', read)
    return calls


def test_official_student_posting_is_collected_and_deduplicated(monkeypatch):
    calls = install(monkeypatch)
    browser = Mock()
    result = CareersScraper(browser).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_url == 'https://careers.qualcomm.com/careers/job/446721349370'
    assert job.location == FIXTURE['locations'][0] and classify_job(job).relevant
    assert '2 year remaining' in job.requirements
    assert result.collection_metrics['duplicates_avoided'] == 3
    assert len(calls) == 5 and all(0 < timeout <= 15 for _, timeout in calls)
    browser.new_page.assert_not_called()


def test_retired_workday_empty_feed_does_not_cover_official_posting(monkeypatch):
    from src.scrapers import careers_scraper
    old = Company('Qualcomm', 'https://qualcomm.wd12.myworkdayjobs.com/en-US/External')
    monkeypatch.setattr(careers_scraper, 'urlopen',
                        lambda *_args, **_kwargs:BytesIO(json.dumps(dict(total=0, jobPostings=[], facets=[])).encode()))
    assert CareersScraper(Mock()).scan_company(old).jobs == []
    install(monkeypatch)
    assert len(CareersScraper(Mock()).scan_company(COMPANY).jobs) == 1


def test_source_repair_does_not_relax_candidate_eligibility(monkeypatch):
    from src.matching import eligibility
    from src.resume.resume_parser import parse_resume_text
    install(monkeypatch)
    job = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 30, tzinfo=tz)
    monkeypatch.setattr(eligibility, 'datetime', Clock)
    profile = parse_resume_text('Third-year Software Engineering student\nEDUCATION\n'
                                'B.Sc. Software Engineering, in progress, expected 2027\nSKILLS\nPython')
    assert eligibility.assess_student_eligibility(profile, job)[0] == 'ineligible'


@pytest.mark.parametrize('title', ['QA Intern', 'Helpdesk Student', 'Sales Student'])
def test_collection_does_not_make_non_development_roles_relevant(monkeypatch, title):
    item = deepcopy(FIXTURE)
    item['name'] = title
    item['jobDescription'] = '<p>Execute manual test plans and log results. Assist customers and document issues.</p>' * 3
    install(monkeypatch, search=lambda _:dict(count=1, positions=[item]), detail=item)
    jobs, _ = fetch_jobs(COMPANY, 15000)
    assert len(jobs) == 1 and not classify_job(jobs[0]).relevant


@pytest.mark.parametrize('change', [dict(id=999), dict(atsJobId='different'),
                                   dict(positionUrl='/careers/job/999'),
                                   dict(name='Different role'), dict(jobDescription='Page not found')])
def test_wrong_identity_or_bad_description_fails_visibly(monkeypatch, change):
    detail = {**FIXTURE, **change}
    install(monkeypatch, detail=detail)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and result.jobs == [] and result.failure_details


def test_payload_url_is_never_followed(monkeypatch):
    item = {**FIXTURE, 'positionUrl':'https://evil.test/private'}
    calls = install(monkeypatch, search=lambda _:dict(count=1, positions=[item]))
    with pytest.raises(ValueError):
        fetch_jobs(COMPANY, 15000)
    assert len(calls) == 1


def test_rate_limit_is_not_retried_or_hidden(monkeypatch):
    read = Mock(side_effect=HTTPError('https://careers.qualcomm.com/',429,'blocked',{},None))
    monkeypatch.setattr(qualcomm, 'urlopen', read)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and result.failure_details['http_status'] == 429
    assert read.call_count == 1


@pytest.mark.parametrize('payload', [dict(count=1, positions=[]), dict(count='1', positions=[]),
                                     dict(count=0, positions=[FIXTURE]), dict(count=1, positions=[None])])
def test_malformed_or_incomplete_page_is_not_empty_success(monkeypatch, payload):
    install(monkeypatch, search=lambda _:payload)
    assert CareersScraper(Mock()).scan_company(COMPANY).error


def test_pagination_is_bounded_and_observable(monkeypatch):
    def page(params):
        number = int(params['start'][0]) + 1
        item = {**FIXTURE, 'id':number, 'positionUrl':f'/careers/job/{number}',
                'name':'Senior Software Engineer'}
        return dict(count=3, positions=[item])
    calls = install(monkeypatch, search=page)
    jobs, metrics = fetch_jobs(COMPANY, 15000)
    assert len(jobs) == 2 and metrics['page_limit_reached'] and metrics['coverage_limited']
    assert len(calls) == 8


def test_total_source_budget_stops_requests(monkeypatch):
    times = iter([0,61])
    monkeypatch.setattr(qualcomm.time, 'monotonic', lambda:next(times))
    read = Mock()
    monkeypatch.setattr(qualcomm, 'urlopen', read)
    with pytest.raises(TimeoutError):
        fetch_jobs(COMPANY, 15000)
    read.assert_not_called()


def test_repeated_page_is_not_mistaken_for_complete_coverage(monkeypatch):
    item = {**FIXTURE, 'name':'Senior Software Engineer'}
    install(monkeypatch, search=lambda _:dict(count=2, positions=[item]))
    with pytest.raises(ValueError, match='Repeated'):
        fetch_jobs(COMPANY, 15000)


def test_upsert_preserves_stable_identity_and_manual_status(monkeypatch, tmp_path):
    install(monkeypatch)
    job = fetch_jobs(COMPANY, 15000)[0][0]
    repo = JobRepository(tmp_path/'jobs.db');repo.initialize()
    identity = repo.save(job)
    repo.update_status(identity, 'ignored')
    assert repo.save(job) == identity
    assert len(repo.list_all()) == 1 and repo.get_by_id(identity).status == 'ignored'


def test_existing_company_configuration_only_switches_qualcomm():
    companies = load_companies('config/companies.yaml')
    company = next(c for c in companies if c.name == 'Qualcomm')
    assert len([c for c in companies if c.name == 'Qualcomm']) == 1
    assert company.careers_url == COMPANY.careers_url
    assert company.job_url_patterns == ('/careers/job/',)
