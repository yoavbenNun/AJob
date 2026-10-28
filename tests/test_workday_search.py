"""Bounded collection tests: tenant metadata, separate scopes, no production calls."""

from dataclasses import asdict
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from src.models import Company, JobPosting
from src.scrapers.careers_scraper import CareersScraper
from src.scrapers.workday_search import israel_facet
from src.storage.job_repository import JobRepository


COMPANY = Company('Example', 'https://example.wd1.myworkdayjobs.com/en-US/External')


def facets(value='tenant-israel', parameter='locationCountry'):
    return [{'facetParameter': 'locationMainGroup', 'values': [
        {'facetParameter': parameter, 'descriptor': 'Country', 'values': [
            {'descriptor': 'Israel', 'id': value}, {'descriptor': 'France', 'id': 'other'}]}]}]


def posting(number, location='Israel'):
    return {'title': 'Software Student', 'externalPath': f'/job/Office/Software_JR{number}',
            'locationsText': location}


def install_api(monkeypatch, reply):
    calls = []
    def fetch(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        result = reply(body)
        return BytesIO(json.dumps(result).encode())
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen', fetch)
    return calls


@pytest.mark.parametrize('value,parameter', [('one', 'locationCountry'), ('different-id', 'custom_country')])
def test_nested_tenant_facet_is_discovered_and_runs_before_global(monkeypatch, value, parameter):
    def reply(body):
        if body['limit'] == 1:
            return {'jobPostings': [], 'facets': facets(value, parameter)}
        return {'jobPostings': [posting(1)] if body['appliedFacets'] else [], 'total': 1}
    calls = install_api(monkeypatch, reply)
    result = CareersScraper(None, max_pages=2)._scan_workday(COMPANY)
    assert calls[1]['appliedFacets'] == {parameter: [value]}
    assert [bool(body['appliedFacets']) for body in calls[1:]] == [True]*4 + [False]*4
    assert len(result.jobs) == 1
    assert result.collection_metrics['focused_discovered'] == 4
    assert result.collection_metrics['unique_israel_jobs'] == 1
    assert result.collection_metrics['duplicates_avoided'] == 3
    assert result.collection_metrics['focused_israel_unique'] == 1
    assert result.collection_metrics['global_israel_unique'] == 0
    assert result.collection_metrics['focused_only_israel_unique'] == 1
    assert result.collection_metrics['location_filter_available'] is True


def test_israel_beyond_global_page_limit_and_remote_both_retained(monkeypatch):
    def reply(body):
        if body['limit'] == 1:
            return {'jobPostings': [], 'facets': facets()}
        if body['appliedFacets']:
            return {'jobPostings': [posting(999)], 'total': 1}
        rows = [posting(i + body['offset'], 'France') for i in range(20)]
        if body['offset'] == 20:
            rows[0] = posting(888, 'Remote worldwide')
            rows[1] = posting(777, '2 Locations')
        # The Israeli job appears only on global page 3, beyond the old cap.
        if body['offset'] >= 40:
            rows[0] = posting(999)
        return {'jobPostings': rows, 'total': 100}
    calls = install_api(monkeypatch, reply)
    result = CareersScraper(None, max_pages=2)._scan_workday(COMPANY)
    assert any(job.job_url.endswith('_JR999') for job in result.jobs)
    assert any(job.location == 'Remote worldwide' for job in result.jobs)
    assert any(job.job_url.endswith('_JR777') and job.location == 'Not specified' for job in result.jobs)
    assert all(body['offset'] < 40 for body in calls)  # No raised global page cap.
    assert len(calls) == 13  # 1 metadata, 4 focused, 8 global; bounded.
    assert result.collection_metrics['global_page_limit_reached'] is True
    assert result.collection_metrics['focused_page_limit_reached'] is False
    assert result.collection_metrics['focused_only_israel_unique'] == 1
    assert result.collection_metrics['comparison_complete'] is True


@pytest.mark.parametrize('metadata', [[], [{'facetParameter': 'locationCountry', 'values': [
    {'descriptor': 'France', 'id': 'fr'}]}]])
def test_no_israel_facet_falls_back_without_guessing_id(monkeypatch, metadata):
    calls = install_api(monkeypatch, lambda body: {'jobPostings': [posting(1, 'Remote worldwide')],
                                                  'facets': metadata, 'total': 1})
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert all(body['appliedFacets'] == {} for body in calls)
    assert len(result.jobs) == 1
    assert result.collection_metrics['focused_discovered'] == 0
    assert result.collection_metrics['location_filter_available'] is False
    assert result.collection_metrics['coverage_limited'] is True


def test_ambiguous_facet_ids_are_not_assumed_reliable():
    metadata = facets()
    metadata[0]['values'][0]['values'].append({'descriptor': 'Israel', 'id': 'second'})
    assert israel_facet({'facets': metadata}) is None


def test_city_only_intel_style_facets_include_all_explicit_israel_offices(monkeypatch):
    metadata = [{'facetParameter': 'locationMainGroup', 'values': [
        {'facetParameter': 'locations', 'values': [
            {'descriptor': 'Israel, Haifa', 'id': 'haifa-id'},
            {'descriptor': 'Israel, Petah-Tikva', 'id': 'petah-id'},
            {'descriptor': 'US, California', 'id': 'us-id'},
            {'descriptor': 'Haifa', 'id': 'unqualified-city-id'},
        ]}]}]
    def reply(body):
        return {'jobPostings': [], 'facets': metadata} if body['limit'] == 1 else {'jobPostings': [posting(1)]}
    calls = install_api(monkeypatch, reply)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert calls[1]['appliedFacets'] == {'locations': ['haifa-id', 'petah-id']}
    assert result.collection_metrics['location_filter_available'] is True
    assert result.collection_metrics['unique_israel_jobs'] == 1


def test_overlapping_scopes_deduplicate_and_do_not_change_stored_status(monkeypatch, tmp_path):
    calls = install_api(monkeypatch, lambda body: {'jobPostings': [posting(1)], 'facets': facets(), 'total': 1})
    repository = JobRepository(tmp_path / 'jobs.db')
    repository.initialize()
    job_id = repository.save(JobPosting('Software Student', COMPANY.name, 'Israel',
                              COMPANY.careers_url + '/job/Office/Software_JR1', status='applied'))
    before = asdict(repository.get_by_id(job_id))
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert len(result.jobs) == 1
    assert result.collection_metrics['duplicates_avoided'] == 7
    assert result.collection_metrics['focused_discovered'] == 4
    assert result.collection_metrics['global_discovered'] == 4
    assert result.collection_metrics['focused_unique'] == result.collection_metrics['global_unique'] == 1
    assert result.collection_metrics['focused_only_israel_unique'] == 0
    assert asdict(repository.get_by_id(job_id)) == before
    assert len(repository.list_all()) == 1
    assert len(calls) == 9


@pytest.mark.parametrize('bad', [None, [], {}, {'jobPostings': 'invalid'}, {'jobPostings': [None]}])
def test_bad_metadata_response_preserves_global_fallback(monkeypatch, bad):
    install_api(monkeypatch, lambda body: bad if body['limit'] == 1 else {'jobPostings': [posting(1)]})
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert len(result.jobs) == 1
    assert result.collection_metrics['location_filter_status'] == 'metadata_failed'


def test_failed_search_keeps_other_queries_and_reports_error(monkeypatch):
    def reply(body):
        if body['limit'] == 1:
            return {'jobPostings': [], 'facets': facets()}
        if body['searchText'] == 'software intern':
            raise TimeoutError('synthetic failure')
        return {'jobPostings': [posting(1)], 'total': 1}
    install_api(monkeypatch, reply)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.error is None and len(result.jobs) == 1
    assert result.collection_metrics['search_errors'] == 2
    assert result.collection_metrics['comparison_complete'] is False
    assert result.collection_metrics['focused_only_israel_unique'] is None
    assert result.collection_metrics['coverage_limited'] is True


def test_access_block_is_not_retried_or_bypassed(monkeypatch):
    def reply(body):
        raise HTTPError(COMPANY.careers_url, 403, 'Forbidden', {}, None)
    calls = install_api(monkeypatch, reply)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.error and len(calls) == 1 and result.jobs == []
    assert result.collection_metrics['focused_only_israel_unique'] is None
    assert result.collection_metrics['comparison_complete'] is False


def test_ignored_country_filter_is_reported_and_global_still_runs(monkeypatch):
    install_api(monkeypatch, lambda body: {'jobPostings': [], 'facets': facets()} if body['limit'] == 1
                else {'jobPostings': [posting(1, 'France')] if body['appliedFacets'] else [posting(2)], 'total': 1})
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert len(result.jobs) == 2  # Nothing discarded because of conflicting location evidence.
    assert result.collection_metrics['location_filter_status'] == 'unreliable'
    assert result.collection_metrics['unique_israel_jobs'] == 1


def test_slow_focused_search_reserves_the_unchanged_global_search(monkeypatch):
    clock = {'now': 0}
    monkeypatch.setattr('src.scrapers.careers_scraper.time.monotonic', lambda: clock['now'])
    def reply(body):
        if body['limit'] == 1:
            return {'jobPostings': [], 'facets': facets()}
        if body['appliedFacets']:
            clock['now'] = 31
            return {'jobPostings': [posting(1)], 'total': 1}
        return {'jobPostings': [posting(2, 'Remote worldwide')], 'total': 1}
    calls = install_api(monkeypatch, reply)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert sum(bool(body['appliedFacets']) for body in calls) == 1
    assert sum(not body['appliedFacets'] and body['limit'] == 20 for body in calls) == 4
    assert result.collection_metrics['focused_budget_exhausted'] is True
    assert len(result.jobs) == 2


def test_ambiguous_global_location_does_not_create_a_false_focused_gain(monkeypatch):
    install_api(monkeypatch, lambda body: {'jobPostings': [], 'facets': facets()} if body['limit'] == 1
                else {'jobPostings': [posting(1, 'Israel' if body['appliedFacets'] else '2 Locations')], 'total': 1})
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.collection_metrics['focused_israel_unique'] == 1
    assert result.collection_metrics['global_israel_unique'] == 0
    assert result.collection_metrics['focused_only_israel_unique'] == 0
