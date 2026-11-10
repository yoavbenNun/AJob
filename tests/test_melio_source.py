"""Verified ATS migration; fixtures and mocked transport only, no real scans."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job
from src.models import Company, JobPosting
from src.scrapers import melio
from src.scrapers.careers_scraper import CareersScraper
from src.storage.job_repository import JobRepository


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/melio_ashby.json').read_text())
COMPANY = Company('Melio', 'https://careers.meliopayments.com/',
                  job_url_patterns=('/melio-payments/',), allowed_job_hosts=('jobs.ashbyhq.com',))


class Reply(BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json; charset=utf-8'}

    def geturl(self):
        return melio.ENDPOINT


def install(monkeypatch, payload=None):
    calls = []
    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        return Reply(json.dumps(FIXTURE if payload is None else payload).encode())
    monkeypatch.setattr(melio, 'urlopen', fetch)
    return calls


def test_current_official_postings_restore_retired_greenhouse_coverage(monkeypatch):
    calls = install(monkeypatch)
    browser = Mock()
    result = CareersScraper(browser).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 2
    assert result.jobs[0].job_url == FIXTURE['jobs'][0]['jobUrl']
    assert result.jobs[0].location == FIXTURE['jobs'][0]['location']
    assert result.jobs[0].source == COMPANY.careers_url
    assert '3+ years' in result.jobs[0].description
    assert result.closure_snapshot is None
    assert calls == [(melio.ENDPOINT, 20)]
    browser.new_page.assert_not_called()
    # Restored collection is not a claim that these senior/experienced roles
    # qualify for the student's search.
    assert not any(classify_job(job).relevant for job in result.jobs)


def test_retired_feed_was_empty_but_same_employer_has_current_postings(monkeypatch):
    monkeypatch.setattr('src.scrapers.greenhouse.urlopen',
        lambda *args, **kwargs: BytesIO(b'{"jobs":[],"meta":{"total":0}}'))
    retired = Company('Melio', 'https://job-boards.greenhouse.io/melio',
                      job_url_patterns=('/melio/jobs/',), allowed_job_hosts=('job-boards.greenhouse.io',))
    old = CareersScraper(Mock()).scan_company(retired)
    assert old.jobs == [] and old.closure_snapshot is None
    install(monkeypatch)
    assert len(CareersScraper(Mock()).scan_company(COMPANY).jobs) == 2


def test_future_student_posting_uses_existing_filters_without_relaxation(monkeypatch):
    payload = deepcopy(FIXTURE)
    payload['jobs'][0].update(title='Software Engineering Student',
        descriptionPlain='Develop software and backend services in Python.\nRequirements:\n'
                         'B.Sc. Software Engineering student with at least two years remaining.')
    install(monkeypatch, payload)
    job = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    assert classify_job(job).relevant
    assert 'two years remaining' in job.requirements


def test_stable_uuid_identity_deduplicates_and_does_not_change_old_rows(monkeypatch, tmp_path):
    payload = deepcopy(FIXTURE)
    payload['jobs'].append(deepcopy(payload['jobs'][0]))
    install(monkeypatch, payload)
    repository = JobRepository(tmp_path / 'jobs.db')
    repository.initialize()
    result = CareersScraper(Mock()).scan_company(COMPANY)
    first = result.jobs[0]
    legacy = deepcopy(first)
    legacy.job_url = 'https://job-boards.greenhouse.io/melio/jobs/123'
    legacy.source = 'https://job-boards.greenhouse.io/melio'
    legacy.status = 'applied'
    old_id = repository.save(legacy)
    for _ in range(2):
        repository.save(first)
    assert len(result.jobs) == 2 and result.collection_metrics['duplicates_avoided'] == 1
    assert len(repository.list_all()) == 2
    assert repository.get_by_id(old_id).status == 'applied'
    assert result.closure_snapshot is None


@pytest.mark.parametrize('has_id', [True, False])
def test_observed_mixed_case_board_uses_stable_existing_identity(monkeypatch, has_id):
    payload = deepcopy(FIXTURE)
    for item in payload['jobs']:
        item['jobUrl'] = item['jobUrl'].replace('/melio-payments/', '/Melio-payments/')
        if not has_id:
            item.pop('id')
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 2
    assert result.jobs[0].job_url == FIXTURE['jobs'][0]['jobUrl']
    assert result.closure_snapshot is None


def test_board_case_variants_do_not_duplicate_postings(monkeypatch):
    payload = deepcopy(FIXTURE)
    other = deepcopy(payload['jobs'][0])
    other['jobUrl'] = other['jobUrl'].replace('/melio-payments/', '/Melio-payments/')
    payload['jobs'].append(other)
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 2
    assert result.collection_metrics['duplicates_avoided'] == 1


def test_observed_case_preserves_existing_applied_job(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / 'jobs.db')
    repository.initialize()
    install(monkeypatch)
    old = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    old.status = 'applied'
    original_id = repository.save(old)
    payload = deepcopy(FIXTURE)
    payload['jobs'][0]['jobUrl'] = payload['jobs'][0]['jobUrl'].replace('/melio-payments/', '/Melio-payments/')
    install(monkeypatch, payload)
    discovered = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    assert repository.save(discovered) == original_id
    assert len(repository.list_all()) == 1
    assert repository.get_by_id(original_id).status == 'applied'


def test_documented_public_posting_without_id_uses_verified_url(monkeypatch):
    payload = deepcopy(FIXTURE)
    for item in payload['jobs']:
        item.pop('id')
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 2
    assert result.jobs[0].job_url == payload['jobs'][0]['jobUrl']
    assert result.closure_snapshot is None


def test_unusable_feed_has_distinct_safe_failure_diagnostics(monkeypatch):
    payload = deepcopy(FIXTURE)
    payload['jobs'] = [dict(payload['jobs'][0], jobUrl='https://invalid.example/posting')]
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error
    assert result.failure_details['exception_type'] == 'MelioNoUsableListings'
    assert 'invalid.example' not in str(result.failure_details)


def test_missing_id_does_not_accept_forged_or_malformed_urls(monkeypatch):
    for bad_url in ('https://evil.example/melio-payments/78b22a8c-3409-4266-8f3c-f79d446b9ca6',
                    'https://jobs.ashbyhq.com/other/78b22a8c-3409-4266-8f3c-f79d446b9ca6',
                    'https://jobs.ashbyhq.com/melio-payments/not-a-uuid'):
        payload = deepcopy(FIXTURE)
        item = payload['jobs'][0]
        item.pop('id')
        item['jobUrl'] = bad_url
        payload['jobs'] = [item]
        install(monkeypatch, payload)
        assert CareersScraper(Mock()).scan_company(COMPANY).error


@pytest.mark.parametrize('change', [
    dict(id='not-a-uuid'), dict(id=None), dict(title=''), dict(jobUrl=''),
    dict(jobUrl='https://evil.example/melio-payments/78b22a8c-3409-4266-8f3c-f79d446b9ca6'),
    dict(jobUrl='https://jobs.ashbyhq.com/other/78b22a8c-3409-4266-8f3c-f79d446b9ca6'),
    dict(jobUrl='https://jobs.ashbyhq.com/melio-paymentſ/78b22a8c-3409-4266-8f3c-f79d446b9ca6'),
    dict(jobUrl='https://jobs.ashbyhq.com/melio-payments/1203e6eb-5467-445e-a766-efacc11543b8'),
])
def test_missing_or_forged_identity_is_not_collected(monkeypatch, change):
    payload = deepcopy(FIXTURE)
    payload['jobs'] = [dict(payload['jobs'][0], **change)]
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and result.jobs == []
    assert result.closure_snapshot is None


def test_mixed_valid_and_invalid_listings_retain_jobs_and_report_limited_coverage(monkeypatch):
    payload = deepcopy(FIXTURE)
    payload['jobs'].append(dict(payload['jobs'][0], id='malformed-identity'))
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 2
    assert result.collection_metrics['invalid_listing'] == 1
    assert result.collection_metrics['coverage_limited'] is True
    assert result.closure_snapshot is None


@pytest.mark.parametrize("source", ["https://careers.meliopayments.com/", "HTTPS://careers.meliopayments.com/"])
def test_incomplete_melio_feed_keeps_jobs_but_worker_does_not_report_full(monkeypatch, source):
    from src.scrapers import budgeted_scan
    payload = deepcopy(FIXTURE)
    payload['jobs'].append(dict(payload['jobs'][0], id='invalid-identity'))
    metrics = {}
    jobs = melio.extract_jobs(COMPANY, payload, metrics)
    assert len(jobs) == 2 and metrics['invalid_listing'] == 1
    result = SimpleNamespace(error='', jobs=jobs, collection_metrics=metrics, closure_snapshot=None)
    monkeypatch.setattr(budgeted_scan, 'CareersScraper', lambda *a, **k: SimpleNamespace(scan_company=lambda _: result))
    if hasattr(budgeted_scan.os, 'setsid'):
        monkeypatch.setattr(budgeted_scan.os, 'setsid', lambda: None)
    events = []
    pipe = Mock()
    pipe.send.side_effect = events.append
    company = Company('Melio', source, job_url_patterns=COMPANY.job_url_patterns,
                      allowed_job_hosts=COMPANY.allowed_job_hosts)
    budgeted_scan.source_worker(company, 1000, 1, True, pipe)
    assert len([event for event in events if event[0] == 'job']) == 2
    assert ('done',) not in events
    assert any(event[:2] == ('error', 'MelioIncompleteFeed') for event in events)


def test_incomplete_feed_policy_does_not_change_other_sources(monkeypatch):
    from src.scrapers import budgeted_scan
    result = SimpleNamespace(error='', jobs=[], collection_metrics={'invalid_listing': 1}, closure_snapshot=None)
    monkeypatch.setattr(budgeted_scan, 'CareersScraper', lambda *a, **k: SimpleNamespace(scan_company=lambda _: result))
    if hasattr(budgeted_scan.os, 'setsid'):
        monkeypatch.setattr(budgeted_scan.os, 'setsid', lambda: None)
    events = []
    pipe = Mock()
    pipe.send.side_effect = events.append
    budgeted_scan.source_worker(Company('Other', 'https://example.test/'), 1000, 1, True, pipe)
    assert ('done',) in events
    assert not any(event[0] == 'error' for event in events)


def test_missing_location_and_description_never_invent_evidence(monkeypatch):
    payload = deepcopy(FIXTURE)
    payload['jobs'] = [dict(payload['jobs'][0], location=None, descriptionPlain=None)]
    install(monkeypatch, payload)
    job = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    assert job.location == 'Not specified' and not job.description


def test_unlisted_postings_are_not_collected(monkeypatch):
    payload = deepcopy(FIXTURE)
    payload['jobs'][0]['isListed'] = False
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert len(result.jobs) == 1 and result.collection_metrics['unlisted_jobs'] == 1


def test_all_unlisted_is_a_legitimate_empty_response(monkeypatch):
    payload = deepcopy(FIXTURE)
    for item in payload['jobs']:
        item['isListed'] = False
    install(monkeypatch, payload)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and result.jobs == []
    assert result.collection_metrics['unlisted_jobs'] == 2
    assert result.collection_metrics['coverage_limited'] is False
    assert result.closure_snapshot is None


@pytest.mark.parametrize('payload', [None, [], {}, {'jobs': None}, {'jobs': [], 'nextPage': 'cursor'}])
def test_bad_or_paginated_response_fails_instead_of_claiming_empty_success(monkeypatch, payload):
    monkeypatch.setattr(melio, 'urlopen', lambda *args, **kwargs: Reply(json.dumps(payload).encode()))
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and result.closure_snapshot is None


@pytest.mark.parametrize('status', [403, 429])
def test_http_failure_is_visible_and_does_not_use_browser_bypass(monkeypatch, status):
    def fail(*args, **kwargs):
        raise HTTPError(melio.ENDPOINT, status, 'synthetic', {}, None)
    monkeypatch.setattr(melio, 'urlopen', fail)
    browser = Mock()
    result = CareersScraper(browser).scan_company(COMPANY)
    assert result.error and result.failure_details['http_status'] == status
    assert result.closure_snapshot is None
    browser.new_page.assert_not_called()


def test_empty_official_feed_is_not_closure_evidence(monkeypatch):
    install(monkeypatch, {'jobs': []})
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert not result.error and result.jobs == [] and result.closure_snapshot is None


def test_melio_failure_does_not_abort_next_company(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError('synthetic')
    monkeypatch.setattr(melio, 'urlopen', fail)
    other = Company('Synthetic', 'https://job-boards.greenhouse.io/synthetic')
    job = JobPosting('Software Student', other.name, 'Israel',
                     'https://job-boards.greenhouse.io/synthetic/jobs/1')
    monkeypatch.setattr('src.scrapers.careers_scraper.fetch_greenhouse_jobs',
                        lambda *args, **kwargs: [job])
    scraper = CareersScraper(Mock())
    results = [scraper.scan_company(company) for company in (COMPANY, other)]
    assert results[0].error and not results[1].error
    assert results[1].jobs == [job]


def test_html_redirect_timeout_and_oversize_fail_closed(monkeypatch):
    cases = [b'<html>not jobs</html>', b'x' * (melio.MAX_RESPONSE_BYTES + 1)]
    for body in cases:
        monkeypatch.setattr(melio, 'urlopen', lambda *args, **kwargs: Reply(body))
        assert CareersScraper(Mock()).scan_company(COMPANY).error
    class Redirect(Reply):
        def geturl(self):
            return 'https://example.test/login'
    monkeypatch.setattr(melio, 'urlopen', lambda *args, **kwargs: Redirect(b'{"jobs":[]}'))
    assert CareersScraper(Mock()).scan_company(COMPANY).error
    def timeout(*args, **kwargs):
        raise TimeoutError('synthetic')
    monkeypatch.setattr(melio, 'urlopen', timeout)
    assert CareersScraper(Mock()).scan_company(COMPANY).failure_details['timeout']


def test_config_repairs_existing_employer_without_adding_one():
    companies = load_companies('config/companies.yaml')
    configured = [company for company in companies if company.name == 'Melio']
    assert len(configured) == 1 and configured[0].careers_url == COMPANY.careers_url
    assert configured[0].allowed_job_hosts == COMPANY.allowed_job_hosts
    assert configured[0].job_url_patterns == COMPANY.job_url_patterns
