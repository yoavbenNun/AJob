"""Synthetic dashboard contract checks; all user records are disposable."""
from dataclasses import asdict
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.data_api_repository import (
    DataApiAuthStore, DataApiDismissalRepository, DataApiJobRepository,
    DataApiPersonalNotesRepository,
)
from src.web.cloud import CloudServices, build_cloud_app, create_cloud_app
from test_cloud_auth import MemoryStore, ORIGIN, PASSWORD, login
from test_cloud_dashboard import token
from src.web.cloud_auth import CloudAuthConfig, hash_password


def row(identifier=1, status='new'):
    result = asdict(JobPosting('Software Student', 'Synthetic', 'Israel',
                             'https://example.test/jobs/1', id=identifier, status=status))
    result['discovered_at'] = result['discovered_at'].isoformat()
    return result


def test_paginated_jobs_do_not_truncate_or_repeat():
    client = Mock()
    client.rpc.side_effect = [[row(i) for i in range(1, 201)], [row(201)]]
    jobs = DataApiJobRepository(client).list_all()
    assert len(jobs) == 201 and jobs[-1].id == 201
    assert client.rpc.call_args.args == ('ajob_jobs', {'p_after_id': 200, 'p_limit': 200})
    client.rpc.side_effect = [[row(1), row(1)]]
    with pytest.raises(RuntimeError, match='ordering'):
        DataApiJobRepository(client).list_all()


def test_notes_are_batched_and_do_not_write_other_job_fields():
    client = Mock()
    client.rpc.return_value = {'1': '<script>text only</script>'}
    notes = DataApiPersonalNotesRepository(client)
    assert notes.for_jobs([1, 1]) == {1: '<script>text only</script>'}
    client.rpc.return_value = 'Draft'
    assert notes.save(1, ' Draft ') == 'Draft'
    client.rpc.assert_called_with('ajob_note_save', {'p_job_id': 1, 'p_note': 'Draft'})
    for invalid in ['x' * 2001, '\x00', None]:
        with pytest.raises(ValueError):
            notes.save(1, invalid)
    client.rpc.return_value = {'999': 'foreign'}
    with pytest.raises(RuntimeError):
        notes.for_jobs([1])


def test_status_compare_and_swap_and_atomic_undo_contract():
    client = Mock()
    client.rpc.return_value = row(status='reviewed')
    jobs = DataApiJobRepository(client)
    assert jobs.update_status(1, 'reviewed', expected_status='new').status == 'reviewed'
    client.rpc.assert_called_once_with('ajob_status', {
        'p_job_id': 1, 'p_status': 'reviewed', 'p_expected_status': 'new'})
    client.reset_mock()
    client.rpc.return_value = {'status': 'ignored', 'changed': True}
    assert DataApiDismissalRepository(client).change_status(1, 'dismiss')['changed']
    client.rpc.assert_called_once_with('ajob_dismiss', {'p_job_id': 1, 'p_action': 'dismiss'})
    with pytest.raises(ValueError):
        jobs.update_status(1, 'full')


def test_persistent_auth_contract_and_no_silent_false_success():
    client = Mock()
    store = DataApiAuthStore(client)
    client.rpc.return_value = True
    assert store.reserve_login(100)
    client.rpc.return_value = {'csrf_token': 'synthetic'}
    assert store.get_session('hash', 100, 60).csrf_token == 'synthetic'
    client.rpc.return_value = None
    assert store.get_session('hash', 100, 60) is None
    with pytest.raises(RuntimeError):
        store.reserve_login(100)


def test_cloud_factory_uses_only_api_and_preserves_browser_security(monkeypatch):
    memory = MemoryStore()
    rows, notes = {1: row()}, {}
    class Gateway:
        def preflight(self):
            return {'ready': True, 'version': 1}

        def rpc(self, name, args=None):
            args = args or {}
            if name == 'ajob_jobs':
                return list(rows.values())
            if name == 'ajob_job':
                return rows.get(args['p_job_id'])
            if name == 'ajob_notes':
                return {str(i): notes[i] for i in args['p_job_ids'] if i in notes}
            if name == 'ajob_note_save':
                notes[args['p_job_id']] = args['p_note']
                return args['p_note']
            if name == 'ajob_status':
                rows[args['p_job_id']]['status'] = args['p_status']
                return rows[args['p_job_id']]
            if name == 'ajob_login_reserve':
                return memory.reserve_login(args['p_now'])
            if name == 'ajob_session_create':
                return memory.create_session(args['p_session_hash'], args['p_csrf_token'],
                                             args['p_now'], args['p_expires_at'])
            if name == 'ajob_session_get':
                session = memory.get_session(args['p_session_hash'], args['p_now'], args['p_idle_seconds'])
                return {'csrf_token': session.csrf_token} if session else None
            if name == 'ajob_session_revoke':
                return memory.revoke_session(args['p_session_hash'])
            pytest.fail('Unexpected API capability')

    gateway = Gateway()
    monkeypatch.setenv('AJOB_CLOUD_ENABLED', '1')
    monkeypatch.setenv('AJOB_CLOUD_BACKEND', 'data_api')
    monkeypatch.setenv('AJOB_PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.setenv('AJOB_OWNER_PASSWORD_HASH', hash_password(PASSWORD))
    for key in ('DATABASE_URL', 'AJOB_DASHBOARD_DATABASE_URL', 'SUPABASE_JWT_SECRET'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('src.web.data_api_transport.DataApiClient.from_env', lambda: gateway)
    monkeypatch.setattr('src.storage.postgres_job_repository.PostgresJobRepository._connect',
                        lambda *a: pytest.fail('API mode attempted SQL'))
    app = create_cloud_app()
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get('/').status_code == 303
    assert login(client).status_code == 303
    csrf = token(client.get('/'))
    data = {'csrf_token': csrf, 'note': '<script>synthetic</script>'}
    assert client.post('/jobs/1/personal-note', data=data, headers={'Origin': ORIGIN}).status_code == 200
    assert '&lt;script&gt;' in client.get('/').text
    assert client.post('/jobs/1/status', data={'csrf_token': csrf, 'status': 'applied'},
                       headers={'Origin': ORIGIN}).status_code == 303
    assert notes[1] == data['note'] and rows[1]['status'] == 'applied'
    assert client.post('/jobs/1/status', data={'csrf_token': csrf, 'status': 'new'},
                       headers={'Origin': 'https://foreign.test'}).status_code == 403
    assert client.post('/jobs/1/status', data={'status': 'new'},
                       headers={'Origin': ORIGIN}).status_code == 403
    restarted = TestClient(build_cloud_app(CloudAuthConfig(ORIGIN, hash_password(PASSWORD)),
        CloudServices(DataApiJobRepository(gateway), DataApiPersonalNotesRepository(gateway),
                      DataApiDismissalRepository(gateway)), DataApiAuthStore(gateway)), base_url=ORIGIN)
    restarted.cookies.update(client.cookies)
    assert '&lt;script&gt;' in restarted.get('/').text


@pytest.mark.parametrize('key', ['DATABASE_URL', 'AJOB_DASHBOARD_DATABASE_URL',
    'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_SECRET_KEY', 'SUPABASE_JWT_SECRET', 'JWT_SECRET', 'PGPASSWORD'])
def test_api_mode_refuses_sql_or_admin_configuration(monkeypatch, key):
    monkeypatch.setenv('AJOB_CLOUD_ENABLED', '1')
    monkeypatch.setenv('AJOB_CLOUD_BACKEND', 'data_api')
    monkeypatch.setenv('AJOB_PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.setenv('AJOB_OWNER_PASSWORD_HASH', hash_password(PASSWORD))
    monkeypatch.setenv(key, 'synthetic-forbidden')
    with pytest.raises(RuntimeError, match='startup validation failed'):
        create_cloud_app()
