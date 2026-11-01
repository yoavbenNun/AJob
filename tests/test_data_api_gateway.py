"""Real PostgREST JWT/RPC boundary against disposable PostgreSQL 17.

Opt in with AJOB_TEST_DATA_API_GATEWAY=1 on Linux with Docker and the isolated
AJOB_TEST_POSTGRES_DSN cluster. CI requires this suite; absent prerequisites fail
when opted in. Tokens and signing keys are synthetic test fixtures, never an
implementation of Supabase Auth issuance or its /user verification endpoint.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from test_data_api_postgres import data_api_database  # noqa: F401


# Fixed upstream release, not latest. Configuration/authentication references:
# https://docs.postgrest.org/en/v13/references/configuration.html
# https://docs.postgrest.org/en/v13/references/auth.html
POSTGREST_IMAGE = ('postgrest/postgrest:v13.0.7@sha256:'
                   '0a46780309a604cdc8b56c776c6e5e15788ce58174d709e40459ab5a2d44d228')  # pragma: allowlist secret -- public upstream image digest
RPC_NAMES = {
    'ajob_jobs', 'ajob_job', 'ajob_status', 'ajob_notes', 'ajob_note_save',
    'ajob_dismiss', 'ajob_preflight', 'ajob_login_reserve',
    'ajob_session_create', 'ajob_session_get', 'ajob_session_revoke',
}


def _jwt(secret, claims):
    def encoded(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=')
    message = encoded({'alg': 'HS256', 'typ': 'JWT'}) + b'.' + encoded(claims)
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).rstrip(b'=')
    return (message + b'.' + signature).decode()


@pytest.fixture(scope='module')
def gateway(request):
    if os.environ.get('AJOB_TEST_DATA_API_GATEWAY') != '1':
        pytest.skip('Real disposable PostgREST gateway not requested')
    if sys.platform != 'linux' or not shutil.which('docker'):
        pytest.fail('Required gateway suite needs Linux Docker host networking')
    if not os.environ.get('AJOB_TEST_POSTGRES_DSN'):
        pytest.fail('Required gateway suite needs disposable loopback PostgreSQL')
    probe = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                           capture_output=True, timeout=15)
    if probe.returncode:
        pytest.fail('Required disposable Docker engine unavailable')
    data = request.getfixturevalue('data_api_database')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    name = 'ajob-gateway-test-' + uuid.uuid4().hex
    secret = secrets.token_hex(32)
    claims = {'role': data['roles']['ajob_cloud_api'], 'sub': data['owner_uuid'],
              'exp': int(time.time()) + 600}
    settings = dict(
        PGRST_DB_URI=data['authenticator_dsn'], PGRST_DB_SCHEMAS='ajob_api',
        PGRST_DB_ANON_ROLE=data['roles']['anon'], PGRST_DB_CONFIG='false',
        PGRST_DB_EXTRA_SEARCH_PATH='', PGRST_DB_POOL='2',
        PGRST_SERVER_HOST='127.0.0.1', PGRST_SERVER_PORT=str(port),
        PGRST_JWT_SECRET=secret, PGRST_LOG_LEVEL='crit')
    environment = dict(os.environ, **settings)
    command = ['docker', 'run', '--detach', '--pull=never', '--name', name,
               '--network', 'host', '--cap-drop=ALL', '--security-opt',
               'no-new-privileges', '--read-only']
    for key in settings:
        command.extend(['--env', key])
    command.append(POSTGREST_IMAGE)
    try:
        started = subprocess.run(command, env=environment, capture_output=True, timeout=20)
        if started.returncode:
            pytest.fail('Disposable gateway failed to start; pull the pinned test image first')
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2,
                          trust_env=False) as client:
            headers = {'Authorization': 'Bearer ' + _jwt(secret, claims)}
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    response = client.post('/rpc/ajob_preflight', json={}, headers=headers)
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            else:
                pytest.fail('Disposable gateway did not pass authenticated readiness within 30 seconds')
            yield dict(client=client, secret=secret, claims=claims, headers=headers, data=data)
    finally:
        # Only our collision-resistant container name; never stop other services.
        removed = subprocess.run(['docker', 'rm', '--force', name],
                                 capture_output=True, timeout=15)
        if removed.returncode and 'No such container' not in removed.stderr.decode(errors='replace'):
            pytest.fail('Could not remove owned disposable gateway container')


def test_signed_owner_can_use_only_typed_rpc_surface(gateway):
    client, headers, data = gateway['client'], gateway['headers'], gateway['data']
    response = client.post('/rpc/ajob_jobs', json={'p_after_id': 0, 'p_limit': 200}, headers=headers)
    assert response.status_code == 200
    assert data['job_id'] in {row['id'] for row in response.json()}
    response = client.post('/rpc/ajob_note_save', json={
        'p_job_id': data['job_id'], 'p_note': 'Synthetic gateway note'}, headers=headers)
    assert response.status_code == 200
    response = client.post('/rpc/ajob_notes', json={'p_job_ids': [data['job_id']]}, headers=headers)
    assert response.status_code == 200
    assert 'Synthetic gateway note' in response.text
    description = client.get('/', headers=headers)
    assert description.status_code == 200
    assert set(description.json()['paths']) == {'/'} | {'/rpc/' + name for name in RPC_NAMES}


@pytest.mark.parametrize('identity', ['anonymous', 'wrong-subject', 'wrong-role',
                                      'expired', 'forged', 'missing-subject',
                                      'metadata-escalation'])
def test_gateway_rejects_unauthorized_reads_and_writes(gateway, identity):
    claims = dict(gateway['claims'])
    secret = gateway['secret']
    if identity == 'wrong-subject':
        claims['sub'] = gateway['data']['foreign_uuid']
    elif identity == 'wrong-role':
        claims['role'] = gateway['data']['roles']['authenticated']
    elif identity == 'expired':
        claims['exp'] = int(time.time()) - 120
    elif identity == 'forged':
        secret = secrets.token_hex(32)
    elif identity == 'missing-subject':
        del claims['sub']
    elif identity == 'metadata-escalation':
        claims['role'] = gateway['data']['roles']['anon']
        claims['user_metadata'] = {'role': gateway['data']['roles']['ajob_cloud_api'],
                                   'sub': gateway['data']['owner_uuid']}
    headers = {} if identity == 'anonymous' else {'Authorization': 'Bearer ' + _jwt(secret, claims)}
    for name, params in [('ajob_jobs', {}), ('ajob_note_save', {
            'p_job_id': gateway['data']['job_id'], 'p_note': 'Unauthorized write'})]:
        response = gateway['client'].post('/rpc/' + name, json=params, headers=headers)
        assert response.status_code in {401, 403}
    notes = gateway['client'].post('/rpc/ajob_notes', json={
        'p_job_ids': [gateway['data']['job_id']]}, headers=gateway['headers'])
    assert notes.status_code == 200
    assert 'Unauthorized write' not in notes.text


@pytest.mark.parametrize('schema', ['net', 'vault', 'cron', 'public', 'ajob_private'])
def test_client_profile_headers_cannot_expand_exposed_schemas(gateway, schema):
    for method, profile, path, params in [
        ('GET', 'Accept-Profile', '/job_postings', None),
        ('POST', 'Content-Profile', '/rpc/ajob_jobs', {}),
    ]:
        response = gateway['client'].request(method, path, json=params,
            headers=dict(gateway['headers'], **{profile: schema}))
        assert response.status_code == 406
        assert response.json()['code'] == 'PGRST106'


@pytest.mark.parametrize('path', [
    '/job_postings', '/personal_notes', '/data_api_owner', '/dashboard_sessions',
    '/rpc/custom_access_token_hook', '/rpc/http_post', '/rpc/exec_sql',
    '/rpc/ajob_identity', '/rpc/worker_restart',
])
def test_private_relations_helpers_and_generic_rpc_are_unreachable(gateway, path):
    response = gateway['client'].post(path, json={}, headers=gateway['headers'])
    assert response.status_code == 404
    assert response.json()['code'] in {'PGRST202', 'PGRST205'}
