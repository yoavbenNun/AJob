"""Independent baseline 95624a7 audit; synthetic stores and MockTransport only.

These tests exercise real cloud middleware/adapters, not a live PostgREST server.
The small remote-state model is not evidence of production SQL isolation.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import socket
import threading

import httpx
import pytest
from starlette.testclient import TestClient

from src.models import JobPosting
from src.storage.data_api_repository import DataApiAuthStore
from src.storage.dismissal_repository import DismissalRepository
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.web.cloud import CloudServices, build_cloud_app
from src.web.cloud_auth import CloudAuthConfig, LOGIN_COOKIE, SESSION_COOKIE, hash_password
from src.web.data_api_transport import DataApiClient

ORIGIN = 'https://audit.example.test'
API_ORIGIN = 'http://127.0.0.1'
SUBJECT = '00000000-0000-4000-8000-000000000019'
PASSWORD = 'independent-synthetic-owner-audit'  # pragma: allowlist secret -- synthetic fixture


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Independent audit forbids external network')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', denied)


@pytest.fixture(scope='module')
def credential():
    return hash_password(PASSWORD)


class RemoteState:
    def __init__(self, now):
        self.now, self.sessions, self.attempts = now, {}, 0
        self.calls, self.lock, self.fail = [], threading.Lock(), None

    def handle(self, request):
        with self.lock:
            path = request.url.path
            self.calls.append(path)
            if path == '/auth/v1/token':
                encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')
                claims = dict(sub=SUBJECT, role='ajob_cloud_api', iss=API_ORIGIN + '/auth/v1',
                              aud='authenticated', iat=self.now[0], exp=self.now[0] + 3600)
                token = encode({'alg': 'ES256'}) + '.' + encode(claims) + '.c3ludGhldGlj'
                return httpx.Response(200, json={'access_token': token})
            if path == '/auth/v1/user':
                return httpx.Response(200, json={'id': SUBJECT})
            name = path.rsplit('/', 1)[-1]
            if self.fail == name:
                raise httpx.ReadTimeout('synthetic-private-transport-detail', request=request)
            body = json.loads(request.content)
            if name == 'ajob_login_reserve':
                self.attempts += 1
                result = self.attempts <= 5
            elif name == 'ajob_session_create':
                self.sessions[body['p_session_hash']] = [body['p_csrf_token'], body['p_now'], body['p_expires_at']]
                result = None
            elif name == 'ajob_session_get':
                row = self.sessions.get(body['p_session_hash'])
                if row and row[1] > body['p_now'] - body['p_idle_seconds'] and row[2] > body['p_now']:
                    row[1] = max(row[1], body['p_now'])
                    result = {'csrf_token': row[0]}
                else:
                    result = None
            elif name == 'ajob_session_revoke':
                self.sessions.pop(body['p_session_hash'], None)
                result = None
            else:
                raise AssertionError('Unapproved synthetic operation: ' + name)
            return httpx.Response(200, json=result)


@pytest.fixture
def workspace(tmp_path, credential):
    now = [1800000000.0]
    remote = RemoteState(now)
    transport = DataApiClient(API_ORIGIN, 'sb_publishable_synthetic_audit', 'audit@example.test',
                              'synthetic-machine-password', SUBJECT, clock=lambda: now[0],
                              client=httpx.Client(transport=httpx.MockTransport(remote.handle)),
                              test_allow_loopback=True)
    store = DataApiAuthStore(transport)
    jobs = JobRepository(tmp_path / 'jobs.db')
    jobs.initialize()
    job_id = jobs.save(JobPosting('Software Student', 'Audit Synthetic', 'Israel',
                                 'https://example.test/job/19', is_relevant=True))
    services = CloudServices(jobs, PersonalNotesRepository(tmp_path / 'notes.db'),
                             DismissalRepository(tmp_path / 'notes.db'))
    config = CloudAuthConfig(ORIGIN, credential, idle_seconds=60, max_age_seconds=180)
    def client():
        app = build_cloud_app(config, services, store)
        app.user_middleware[0].kwargs['clock'] = lambda: now[0]
        return TestClient(app, base_url=ORIGIN, follow_redirects=False)
    return client, services, job_id, remote, now


def login(client):
    token = client.get('/login').cookies[LOGIN_COOKIE]
    return client.post('/login', data={'csrf_token': token, 'password': PASSWORD}, headers={'Origin': ORIGIN})


def csrf(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get('/').text)[1]


def unchanged(services, job_id):
    assert services.jobs.get_by_id(job_id).status == 'new'
    assert services.notes.for_jobs([job_id]) == {}


@pytest.mark.parametrize('route', ['status', 'personal-note', 'dismissal'])
@pytest.mark.parametrize('cookie', [None, 'forged', 'A' * 43])
def test_missing_or_forged_session_never_mutates(workspace, route, cookie):
    create, services, job_id, remote, _ = workspace
    client = create()
    if cookie is not None:
        client.cookies.set(SESSION_COOKIE, cookie)
    result = client.post(f'/jobs/{job_id}/{route}', headers={'Origin': ORIGIN},
                         data={'csrf_token': 'forged', 'status': 'applied', 'note': 'forged', 'action': 'dismiss'})
    assert result.status_code == 401
    unchanged(services, job_id)
    assert 'ajob_note_save' not in ''.join(remote.calls)


@pytest.mark.parametrize('origin', [None, 'null', 'https://evil.test', ORIGIN + '.evil.test'])
@pytest.mark.parametrize('route', ['status', 'personal-note', 'dismissal'])
def test_authenticated_foreign_origin_rejected(workspace, origin, route):
    create, services, job_id, _, _ = workspace
    client = create()
    assert login(client).status_code == 303
    token = csrf(client)
    headers = {'Origin': origin} if origin else {}
    result = client.post(f'/jobs/{job_id}/{route}', headers=headers,
                         data={'csrf_token': token, 'status': 'applied', 'note': 'forged', 'action': 'dismiss'})
    assert result.status_code == 403
    unchanged(services, job_id)


@pytest.mark.parametrize('route', ['status', 'personal-note', 'dismissal'])
@pytest.mark.parametrize('submitted', ['', 'forged', '\u05d0'])
def test_same_origin_forged_csrf_never_mutates(workspace, route, submitted):
    create, services, job_id, _, _ = workspace
    client = create()
    assert login(client).status_code == 303
    result = client.post(f'/jobs/{job_id}/{route}', headers={'Origin': ORIGIN},
                         data={'csrf_token': submitted, 'status': 'applied', 'note': 'forged', 'action': 'dismiss'})
    assert result.status_code == 403
    unchanged(services, job_id)


@pytest.mark.parametrize('age', [-1, 600])
def test_future_or_expired_login_token_does_not_reserve_login(workspace, age):
    create, _, _, remote, now = workspace
    client = create()
    token = client.get('/login').cookies[LOGIN_COOKIE]
    now[0] += age
    response = client.post('/login', data={'csrf_token': token, 'password': PASSWORD}, headers={'Origin': ORIGIN})
    assert response.status_code == 403
    assert remote.calls == []


def test_forged_logout_and_cross_site_metadata_do_not_mutate(workspace):
    create, services, job_id, remote, _ = workspace
    client = create()
    assert login(client).status_code == 303
    token = csrf(client)
    assert client.post('/logout', data={'csrf_token': 'forged'}, headers={'Origin': ORIGIN}).status_code == 403
    assert len(remote.sessions) == 1
    assert client.post(f'/jobs/{job_id}/status', data={'csrf_token': token, 'status': 'applied'},
                       headers={'Origin': ORIGIN, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    unchanged(services, job_id)


@pytest.mark.parametrize('headers', [
    {'Host': 'evil.test', 'X-Forwarded-Host': 'audit.example.test'},
    [('Host', 'audit.example.test'), ('Host', 'evil.test')],
    [('Origin', ORIGIN), ('Origin', 'https://evil.test')],
    [('Cookie', 'x=y'), ('Cookie', 'z=w')],
    [('X-CSRF-Token', 'x'), ('X-CSRF-Token', 'y')],
])
def test_host_and_duplicate_identity_headers(workspace, headers):
    create, _, _, remote, _ = workspace
    client = create()
    assert client.get('/', headers=headers).status_code == 400
    assert remote.calls == []


def test_session_bound_csrf_and_replay_after_rotation_logout(workspace):
    create, services, job_id, remote, _ = workspace
    first, second = create(), create()
    assert login(first).status_code == login(second).status_code == 303
    old_cookie, old_csrf = first.cookies[SESSION_COOKIE], csrf(first)
    data = {'csrf_token': old_csrf, 'note': 'unauthorized'}
    assert second.post(f'/jobs/{job_id}/personal-note', data=data, headers={'Origin': ORIGIN}).status_code == 403
    assert login(first).status_code == 303
    replay = create()
    replay.cookies.set(SESSION_COOKIE, old_cookie)
    assert replay.post(f'/jobs/{job_id}/personal-note', data=data, headers={'Origin': ORIGIN}).status_code == 401
    fresh_cookie, fresh_csrf = first.cookies[SESSION_COOKIE], csrf(first)
    assert hashlib.sha256(old_cookie.encode()).hexdigest() not in remote.sessions
    assert first.post('/logout', data={'csrf_token': fresh_csrf}, headers={'Origin': ORIGIN}).status_code == 303
    replay.cookies.set(SESSION_COOKIE, fresh_cookie)
    assert replay.post(f'/jobs/{job_id}/status', data={'csrf_token': fresh_csrf, 'status': 'applied'}, headers={'Origin': ORIGIN}).status_code == 401
    unchanged(services, job_id)


@pytest.mark.parametrize('expiry', ['idle', 'absolute'])
def test_restart_expiry_rejects_mutations(workspace, expiry):
    create, services, job_id, _, now = workspace
    original = create()
    assert login(original).status_code == 303
    token = csrf(original)
    restarted = create()
    restarted.cookies.update(original.cookies)
    assert restarted.get('/').status_code == 200
    if expiry == 'absolute':
        for _ in range(3):
            now[0] += 50
            assert restarted.get('/').status_code == 200
        now[0] += 30
    else:
        now[0] += 60
    assert restarted.post(f'/jobs/{job_id}/status', data={'csrf_token': token, 'status': 'applied'}, headers={'Origin': ORIGIN}).status_code == 401
    unchanged(services, job_id)


@pytest.mark.parametrize('failure', ['ajob_session_get', 'ajob_session_revoke', 'ajob_login_reserve', 'ajob_session_create'])
def test_http_errors_are_redacted_and_never_retried(workspace, failure):
    create, _, _, remote, _ = workspace
    client = create()
    if failure in {'ajob_session_get', 'ajob_session_revoke'}:
        assert login(client).status_code == 303
        token = csrf(client)
    remote.fail = failure
    before = remote.calls.count('/rest/v1/rpc/' + failure)
    if failure == 'ajob_session_get':
        result = client.get('/')
    elif failure == 'ajob_session_revoke':
        result = client.post('/logout', data={'csrf_token': token}, headers={'Origin': ORIGIN})
    else:
        result = login(client)
    assert result.status_code == 503
    assert 'synthetic-private' not in result.text
    assert remote.calls.count('/rest/v1/rpc/' + failure) == before + 1
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/healthz').status_code == 200


def test_parallel_login_throttle_and_parallel_logout_revocation(workspace):
    create, _, _, remote, _ = workspace
    clients = [create() for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(login, clients))
    assert sorted(r.status_code for r in responses) == [303] * 5 + [429]
    assert len(remote.sessions) == 5
    active = [c for c, r in zip(clients, responses) if r.status_code == 303]
    def logout(client):
        return client.post('/logout', data={'csrf_token': csrf(client)}, headers={'Origin': ORIGIN})
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert all(r.status_code == 303 for r in pool.map(logout, active))
    assert remote.sessions == {}


def test_valid_mutations_and_cookie_security(workspace):
    create, services, job_id, _, _ = workspace
    client = create()
    result = login(client)
    cookie = result.headers.get_list('set-cookie')[0]
    for expected in ['__Host-ajob-session=', 'HttpOnly', 'Secure', 'SameSite=strict', 'Path=/']:
        assert expected in cookie
    assert 'Domain=' not in cookie
    token = csrf(client)
    assert client.post(f'/jobs/{job_id}/personal-note', data={'csrf_token': token, 'note': 'Synthetic audit note'}, headers={'Origin': ORIGIN}).status_code == 200
    assert client.post(f'/jobs/{job_id}/status', data={'csrf_token': token, 'status': 'applied'}, headers={'Origin': ORIGIN}).status_code == 303
    assert services.jobs.get_by_id(job_id).status == 'applied'
    assert services.notes.for_jobs([job_id]) == {job_id: 'Synthetic audit note'}
