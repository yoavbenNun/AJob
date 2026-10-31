"""Synthetic HTTP responses only: never record server content or credentials."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from src.scrapers.workday_http import read_json_response, WorkdayResponseError, MAX_RESPONSE_BYTES


ENDPOINT = 'https://example.wd1.myworkdayjobs.com/wday/cxs/example/External/jobs'


class Response(BytesIO):
    def __init__(self, body, status=200, content_type='application/json', url=ENDPOINT):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Type': content_type, 'Set-Cookie': 'private-cookie'}
        self.url = url

    def geturl(self):
        return self.url


def test_valid_json_and_headerless_fixtures():
    assert read_json_response(Response(b'{"jobPostings":[]}'), ENDPOINT) == {'jobPostings': []}
    assert read_json_response(BytesIO(b'{}'), ENDPOINT) == {}


def test_invalid_schema_retains_http_metadata_without_schema_error_text():
    def validate(payload):
        raise ValueError('private-schema-value')
    with pytest.raises(WorkdayResponseError) as caught:
        read_json_response(Response(b'{"unexpected":[]}'), ENDPOINT, validate=validate)
    assert caught.value.response_diagnostics['body_kind'] == 'json'
    assert caught.value.response_diagnostics['http_status'] == 200
    assert caught.value.response_diagnostics['response_problem'] == 'invalid_schema'
    assert str(caught.value) == 'Invalid JSON schema'


@pytest.mark.parametrize('body,content_type,kind', [
    (b'<html>private-token</html>', 'text/html; charset=utf-8', 'html'),
    (b'<!DOCTYPE html><html>private-token</html>', 'application/json', 'html'),
    (b'{"private-token":', 'application/json', 'json'),
    (b'', 'application/json', 'empty'),
    (b'private-token', 'secret-header-value', 'other'),
])
def test_invalid_response_is_failure_with_private_metadata(body, content_type, kind):
    with pytest.raises(WorkdayResponseError) as caught:
        read_json_response(Response(body, content_type=content_type), ENDPOINT)
    details = caught.value.response_diagnostics
    assert details['http_status'] == 200 and details['body_kind'] == kind
    serialized = json.dumps(details) + str(caught.value)
    assert 'private-token' not in serialized
    assert 'private-cookie' not in serialized
    assert 'secret-header-value' not in serialized
    assert 'https://' not in serialized


@pytest.mark.parametrize('status', [403, 429])
def test_http_error_retains_status_without_sensitive_error_text(status):
    response = HTTPError(ENDPOINT + '?secret=value', status, 'private-error',
                         {'Content-Type': 'text/html'}, BytesIO(b'<html>private-token</html>'))
    with pytest.raises(HTTPError) as caught:
        read_json_response(response, ENDPOINT)
    assert caught.value is response
    details = response.response_diagnostics
    assert details['http_status'] == status and details['body_kind'] == 'html'
    assert 'private' not in json.dumps(details) and 'secret' not in json.dumps(details)


@pytest.mark.parametrize('url,same_host', [
    (ENDPOINT + '?private-token=1', True),
    ('https://other.example/private-token', False),
])
def test_redirect_is_failure_and_only_boolean_metadata_is_kept(url, same_host):
    with pytest.raises(WorkdayResponseError) as caught:
        read_json_response(Response(b'{}', url=url), ENDPOINT)
    details = caught.value.response_diagnostics
    assert details['redirected'] is True and details['redirect_same_host'] is same_host
    assert 'private-token' not in json.dumps(details)


def test_timeout_retains_type_and_safe_metadata():
    class TimeoutResponse(Response):
        def read(self, size):
            raise TimeoutError('private-timeout')
    with pytest.raises(TimeoutError) as caught:
        read_json_response(TimeoutResponse(b''), ENDPOINT)
    details = caught.value.response_diagnostics
    assert details['body_kind'] == 'unread' and details['http_status'] == 200
    assert 'private-timeout' not in json.dumps(details)


def test_oversize_response_is_bounded_and_not_successful():
    with pytest.raises(WorkdayResponseError) as caught:
        read_json_response(Response(b' ' * (MAX_RESPONSE_BYTES + 100)), ENDPOINT)
    details = caught.value.response_diagnostics
    assert details['response_truncated'] and details['response_bytes'] == MAX_RESPONSE_BYTES + 1
