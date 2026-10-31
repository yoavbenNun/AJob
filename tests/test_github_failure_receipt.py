"""Synthetic API responses only; no live GitHub calls or notification delivery."""

from copy import deepcopy
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from src.scheduling import github_failure_receipt as receipt


REPO = 'owner/AJob'
TOKEN = 'private-test-value'


def run_payload(**changes):
    value = dict(id=123, repository={'full_name': REPO}, path=receipt.WORKFLOW_PATH,
                 event='schedule', head_branch='main', run_attempt=1,
                 status='completed', conclusion='failure')
    value.update(changes)
    return value


def jobs_payload():
    return dict(total_count=1, jobs=[dict(name='scan', run_id=123,
        status='completed', conclusion='failure', steps=[dict(
            name='Reconcile windows and retry pending notifications',
            status='completed', conclusion='failure')])])


def install_json(monkeypatch, run=None, jobs=None):
    calls = []
    values = [run_payload() if run is None else run,
              jobs_payload() if jobs is None else jobs]
    def get(path, token):
        calls.append(path)
        assert token == TOKEN
        return deepcopy(values[len(calls) - 1])
    monkeypatch.setattr(receipt, '_get_json', get)
    return calls


def test_completed_reporting_failure_is_receipt(monkeypatch):
    calls = install_json(monkeypatch)
    assert receipt.verify_failure(REPO, '123', '1', TOKEN)
    assert calls == ['repos/owner/AJob/actions/runs/123/attempts/1',
                     'repos/owner/AJob/actions/runs/123/attempts/1/jobs?per_page=100']


@pytest.mark.parametrize('changes', [
    dict(id=124), dict(repository={'full_name': 'other/AJob'}),
    dict(path='.github/workflows/ci.yml'), dict(event='pull_request'),
    dict(head_branch='feature'), dict(run_attempt=2), dict(run_attempt=True),
    dict(repository=None),
])
def test_identity_mismatch_fails_closed(monkeypatch, changes):
    install_json(monkeypatch, run_payload(**changes))
    with pytest.raises(RuntimeError, match='identity mismatch'):
        receipt.verify_failure(REPO, 123, 1, TOKEN)


@pytest.mark.parametrize('changes', [dict(status='in_progress', conclusion=None),
    dict(conclusion='cancelled'), dict(conclusion='success'), dict(conclusion='timed_out')])
def test_not_completed_failure_has_no_receipt(monkeypatch, changes):
    calls = install_json(monkeypatch, run_payload(**changes))
    assert not receipt.verify_failure(REPO, 123, 1, TOKEN)
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [('name', 'test'), ('conclusion', 'success'),
    ('status', 'in_progress'), ('run_id', 124)])
def test_unrelated_job_failure_does_not_acknowledge_report(monkeypatch, field, value):
    jobs = jobs_payload()
    jobs['jobs'][0][field] = value
    install_json(monkeypatch, jobs=jobs)
    assert not receipt.verify_failure(REPO, 123, 1, TOKEN)


def test_infrastructure_step_failure_is_not_reporting_receipt(monkeypatch):
    jobs = jobs_payload()
    jobs['jobs'][0]['steps'][0]['name'] = 'Build scanner image'
    install_json(monkeypatch, jobs=jobs)
    assert not receipt.verify_failure(REPO, 123, 1, TOKEN)


@pytest.mark.parametrize('jobs', [dict(total_count=101, jobs=[]), dict(jobs=[]),
    dict(total_count=1, jobs=[None])])
def test_incomplete_jobs_response_fails_closed(monkeypatch, jobs):
    install_json(monkeypatch, jobs=jobs)
    with pytest.raises(RuntimeError, match='jobs unavailable'):
        receipt.verify_failure(REPO, 123, 1, TOKEN)


@pytest.mark.parametrize('repo,run,attempt,token', [
    ('../owner/AJob', 123, 1, TOKEN), (REPO, '../123', 1, TOKEN),
    (REPO, 123, True, TOKEN), (REPO, 123, 1, ''),
    (REPO, 123, 1, TOKEN + '\nprivate-header'),
])
def test_invalid_context_never_requests_network(monkeypatch, repo, run, attempt, token):
    monkeypatch.setattr(receipt, '_get_json', lambda *a: pytest.fail('network called'))
    with pytest.raises(RuntimeError) as caught:
        receipt.verify_failure(repo, run, attempt, token)
    assert TOKEN not in str(caught.value)


class Response(BytesIO):
    status = 200


def install_transport(monkeypatch, response=None, error=None):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            if error:
                raise error
            return response
    monkeypatch.setattr(receipt, 'build_opener', lambda *args: Opener())
    return calls


def test_http_request_is_bounded_and_authenticated(monkeypatch):
    calls = install_transport(monkeypatch, Response(b'{"safe":true}'))
    assert receipt._get_json('repos/owner/AJob/actions/runs/123', TOKEN) == {'safe': True}
    request, timeout = calls[0]
    assert timeout == 5 and request.full_url.startswith('https://api.github.com/')
    assert request.get_header('Authorization') == 'Bearer ' + TOKEN


@pytest.mark.parametrize('body', [b'<html>private-response</html>', b'[1]',
    b'x' * (receipt.MAX_RESPONSE_BYTES + 1)], ids=['html', 'wrong-shape', 'oversize'])
def test_invalid_http_body_is_sanitized(monkeypatch, body):
    install_transport(monkeypatch, Response(body))
    with pytest.raises(RuntimeError) as caught:
        receipt._get_json('repos/owner/AJob/actions/runs/123', TOKEN)
    assert str(caught.value) == 'GitHub failure receipt unavailable'
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize('error', [TimeoutError('private-response'),
    HTTPError('https://api.github.com/private', 403, 'private-response', {}, None)])
def test_http_failure_is_sanitized(monkeypatch, error):
    install_transport(monkeypatch, error=error)
    with pytest.raises(RuntimeError) as caught:
        receipt._get_json('repos/owner/AJob/actions/runs/123', TOKEN)
    assert str(caught.value) == 'GitHub failure receipt unavailable'
    assert 'private' not in str(caught.value)


def test_bearer_token_is_never_forwarded_on_redirect():
    assert receipt._NoRedirect().redirect_request(None, None, 302, '', {},
                                                 'https://other.test/') is None
