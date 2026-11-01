"""Independent real-boundary probes; only the opt-in disposable CI cluster.

No production DSN is read. Existing fixture guards create fresh databases/roles;
gateway fixture uses a pinned PostgREST container bound to loopback. PostgreSQL
claims and HMAC signing keys here model issuance, not live Supabase Auth.
"""
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from test_data_api_postgres import data_api_database, api_connect, call, rpc  # noqa: F401
from test_data_api_gateway import gateway, _jwt  # noqa: F401


def test_real_known_owner_job_id_does_not_authorize_foreign_notes(data_api_database):
    data = data_api_database
    job = data['job_id']
    note = 'Independent synthetic private note'
    assert rpc(data, 'ajob_note_save', job, note) == note
    for name, args in [('ajob_job', (job,)), ('ajob_notes', ([job],)),
                       ('ajob_note_save', (job, 'Foreign overwrite')),
                       ('ajob_status', (job, 'ignored'))]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rpc(data, name, *args, subject=data['foreign_uuid'])
    assert rpc(data, 'ajob_notes', [job]) == {str(job): note}
    assert rpc(data, 'ajob_job', job)['status'] == 'new'


def test_real_interrupted_dismiss_rolls_back_status_and_history(data_api_database):
    data = data_api_database
    job = data['job_id']
    with api_connect(data) as connection:
        with pytest.raises(RuntimeError, match='synthetic transaction interruption'):
            with connection.transaction():
                assert call(connection, 'ajob_dismiss', job, 'dismiss')['status'] == 'ignored'
                raise RuntimeError('synthetic transaction interruption')
    assert rpc(data, 'ajob_job', job)['status'] == 'new'
    restored = rpc(data, 'ajob_dismiss', job, 'restore')
    assert restored['status'] == 'new' and restored['changed'] is False


def test_real_duplicate_dismiss_is_atomic_and_preserves_note(data_api_database):
    data = data_api_database
    job = data['job_id']
    note = 'Independent note survives concurrent duplicate dismiss'
    rpc(data, 'ajob_note_save', job, note)
    rpc(data, 'ajob_status', job, 'applied')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rpc(data, 'ajob_dismiss', job, 'dismiss'), range(2)))
    assert sum(result['changed'] for result in results) == 1
    assert {result['status'] for result in results} == {'ignored'}
    assert rpc(data, 'ajob_dismiss', job, 'restore')['status'] == 'applied'
    assert rpc(data, 'ajob_notes', [job]) == {str(job): note}


def test_real_gateway_valid_signature_cannot_authorize_foreign_subject(gateway):
    client, data = gateway['client'], gateway['data']
    job = data['job_id']
    note = 'Independent signed gateway note'
    assert client.post('/rpc/ajob_note_save', json={'p_job_id': job, 'p_note': note},
                       headers=gateway['headers']).status_code == 200
    claims = dict(gateway['claims'], sub=data['foreign_uuid'])
    foreign = {'Authorization': 'Bearer ' + _jwt(gateway['secret'], claims)}
    for endpoint, payload in [('/rpc/ajob_notes', {'p_job_ids': [job]}),
                              ('/rpc/ajob_note_save', {'p_job_id': job, 'p_note': 'Foreign overwrite'})]:
        assert client.post(endpoint, json=payload, headers=foreign).status_code == 403
    response = client.post('/rpc/ajob_notes', json={'p_job_ids': [job]}, headers=gateway['headers'])
    assert response.status_code == 200 and response.json() == {str(job): note}


def test_real_public_net_privileges_require_the_http_schema_barrier(gateway):
    data = gateway['data']
    # These are intentionally PRESENT managed-PUBLIC-style grants, not missing
    # test objects. NOLOGIN and the complete exposed HTTP surface are essential.
    with api_connect(data) as connection:
        row = connection.execute("""SELECT rolcanlogin,rolsuper,rolbypassrls
            FROM pg_roles WHERE rolname=current_user""").fetchone()
        assert row == (False, False, False)
        assert connection.execute("SELECT has_schema_privilege(current_user,'net','USAGE')").fetchone()[0]
        assert connection.execute("SELECT has_table_privilege(current_user,'net.http_request_queue','SELECT')").fetchone()[0]
        assert connection.execute("SELECT has_function_privilege(current_user,'net.http_post(text)','EXECUTE')").fetchone()[0]
    response = gateway['client'].post('/rpc/http_post', json={'url': 'https://example.invalid'},
        headers=dict(gateway['headers'], **{'Content-Profile': 'net'}))
    assert response.status_code == 406 and response.json()['code'] == 'PGRST106'
    # This proves the configured synthetic gateway boundary only. It does not
    # inventory real public/GraphQL functions or assert all PUBLIC grants gone.


def test_real_mapping_revocation_blocks_previously_valid_gateway_token(gateway):
    data, client, headers = gateway['data'], gateway['client'], gateway['headers']
    assert client.post('/rpc/ajob_jobs', json={}, headers=headers).status_code == 200
    prior_notes = client.post('/rpc/ajob_notes', json={'p_job_ids': [data['job_id']]}, headers=headers)
    assert prior_notes.status_code == 200
    expected_notes = prior_notes.json()
    # Trusted administrator ONLY in this fresh disposable fixture database.
    # No real identity/session is deleted and no schema is dropped or migrated.
    with psycopg.connect(data['target'], autocommit=True) as admin:
        original = admin.execute('SELECT singleton,auth_user_id FROM ajob_private.data_api_owner').fetchone()
        assert original is not None
        try:
            admin.execute('DELETE FROM ajob_private.data_api_owner')
            assert client.post('/rpc/ajob_jobs', json={}, headers=headers).status_code == 403
            assert client.post('/rpc/ajob_note_save', json={
                'p_job_id': data['job_id'], 'p_note': 'Revoked write'}, headers=headers).status_code == 403
        finally:
            admin.execute('INSERT INTO ajob_private.data_api_owner(singleton,auth_user_id) VALUES(%s,%s)', original)
    assert client.post('/rpc/ajob_jobs', json={}, headers=headers).status_code == 200
    notes = client.post('/rpc/ajob_notes', json={'p_job_ids': [data['job_id']]}, headers=headers)
    assert notes.status_code == 200 and notes.json() == expected_notes
