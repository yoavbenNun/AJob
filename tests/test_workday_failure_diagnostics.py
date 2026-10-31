"""Failures stay failures; public response diagnostics never include raw content."""

from io import BytesIO
import json
from urllib.error import HTTPError, URLError

import pytest

from src.models import Company
from src.scrapers.careers_scraper import CareersScraper
from src.scrapers.failure_diagnostics import source_failure


COMPANY = Company('Synthetic', 'https://example.wd1.myworkdayjobs.com/en-US/External')
PRIVATE = 'private-cookie-token-query-body'


class Response(BytesIO):
    status = 200

    def __init__(self, body, content_type='application/json', redirected=False):
        super().__init__(body)
        self.headers = {'Content-Type': content_type, 'Set-Cookie': PRIVATE}
        self.redirected = redirected

    def geturl(self):
        suffix = '/login?secret=' + PRIVATE if self.redirected else '/wday/cxs/example/External/jobs'
        return 'https://example.wd1.myworkdayjobs.com' + suffix


@pytest.mark.parametrize('body,content_type,kind', [
    (b'<html>private-cookie-token-query-body</html>', 'text/html', 'html'),
    (b'{"broken":', 'application/json', 'json'),
    (b'', 'application/json', 'empty'),
    (b'{"unexpected":"private-cookie-token-query-body"}', 'application/json', 'json'),
])
def test_bad_workday_responses_fail_with_safe_metadata(monkeypatch, body, content_type, kind):
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen',
                        lambda *args, **kwargs: Response(body, content_type))
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.error and result.jobs == [] and result.closure_snapshot is None
    assert result.failure_details['http_status'] == 200
    assert result.failure_details['body_kind'] == kind
    assert result.failure_details['content_type'] == content_type
    assert result.failure_details['stage'] == 'search'
    assert result.collection_metrics['metadata_failure']['http_status'] == 200
    assert PRIVATE not in json.dumps(result.failure_details)
    assert PRIVATE not in json.dumps(result.collection_metrics)


@pytest.mark.parametrize('status,transient', [(403, False), (429, True)])
def test_denial_keeps_status_and_stops_without_bypass(monkeypatch, status, transient):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError('https://example.test/?token=' + PRIVATE, status, PRIVATE,
                        {'Content-Type': 'text/html', 'Set-Cookie': PRIVATE},
                        BytesIO(b'<html>private-cookie-token-query-body</html>'))
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen', fail)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert len(calls) == 1 and result.error and not result.jobs
    assert result.closure_snapshot is None
    assert result.failure_details['http_status'] == status
    assert result.failure_details['body_kind'] == 'html'
    assert result.failure_details['transient'] is transient
    assert PRIVATE not in json.dumps(result.failure_details)


def test_redirected_json_is_not_success_or_closure_evidence(monkeypatch):
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen',
                        lambda *a, **kw: Response(b'{"jobPostings":[]}', redirected=True))
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.error and not result.jobs and result.closure_snapshot is None
    assert result.failure_details['redirected'] is True
    assert result.failure_details['redirect_same_host'] is True
    assert PRIVATE not in json.dumps(result.failure_details)


def test_connection_timeout_still_plans_no_implicit_retry(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError(TimeoutError(PRIVATE))
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen', fail)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert result.error and result.closure_snapshot is None
    assert result.failure_details['timeout'] and result.failure_details['transient']
    assert result.failure_details['recovery_planned'] is False
    assert PRIVATE not in json.dumps(result.failure_details)


def test_diagnostic_allowlist_rejects_untrusted_values():
    error = ValueError(PRIVATE)
    error.response_diagnostics = dict(http_status=PRIVATE, content_type=PRIVATE,
        body_kind=PRIVATE, response_bytes=-1, redirected=PRIVATE, headers=PRIVATE)
    assert PRIVATE not in json.dumps(source_failure(error))


@pytest.mark.parametrize('status', [403, 429])
def test_error_body_timeout_cannot_hide_denial_or_trigger_more_queries(monkeypatch, status):
    class SlowBody(BytesIO):
        def read(self, size):
            raise TimeoutError(PRIVATE)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError(COMPANY.careers_url, status, PRIVATE,
                        {'Content-Type': 'text/html'}, SlowBody())
    monkeypatch.setattr('src.scrapers.careers_scraper.urlopen', fail)
    result = CareersScraper(None)._scan_workday(COMPANY)
    assert len(calls) == 1 and result.error and result.closure_snapshot is None
    assert result.failure_details['http_status'] == status
    assert result.failure_details['exception_type'] == 'HTTPError'
    assert result.failure_details['body_kind'] == 'unread'
