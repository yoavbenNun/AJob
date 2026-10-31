"""Two fresh app instances against disposable PostgreSQL, never production."""
from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.postgres_job_repository import PostgresJobRepository
from src.storage.postgres_personal_notes_repository import PostgresPersonalNotesRepository
from src.storage.postgres_dismissal_repository import PostgresDismissalRepository
from src.web.cloud import CloudServices, build_cloud_app
from src.web.cloud_auth import CloudAuthConfig, PostgresAuthStore, hash_password
from test_cloud_auth import ORIGIN, PASSWORD, login
from test_cloud_dashboard import token
from test_cloud_private_postgres import cloud_database, scoped
from test_cloud_auth_postgres import auth_database


def test_cold_start_retains_session_notes_undo_and_recovers_from_db_failure(
        cloud_database, auth_database, monkeypatch):
    target, role, _, jobs = cloud_database
    job_id = jobs.save(JobPosting('Software Student', 'Synthetic', 'Israel',
                                 'https://example.test/cold-start', is_relevant=True))
    jobs.update_status(job_id, 'applied')
    config = CloudAuthConfig(ORIGIN, hash_password(PASSWORD))

    def new_instance():
        services = CloudServices(scoped(PostgresJobRepository, target, role),
            scoped(PostgresPersonalNotesRepository, target, role),
            scoped(PostgresDismissalRepository, target, role))
        auth = PostgresAuthStore(services.jobs._connect)
        app = build_cloud_app(config, services, auth)
        return TestClient(app, base_url=ORIGIN, follow_redirects=False), auth

    # Cloud mode must never consult SQLite or a local resume even after restart.
    import sqlite3
    import src.web.app as web
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('SQLite used')))
    monkeypatch.setattr(web, 'get_active_resume', lambda *a: (_ for _ in ()).throw(AssertionError('Resume read')))
    first, _ = new_instance()
    assert login(first).status_code == 303
    csrf = token(first.get('/'))
    headers = {'Origin': ORIGIN}
    assert first.post(f'/jobs/{job_id}/personal-note', data={
        'csrf_token': csrf, 'note': 'Synthetic durable note'}, headers=headers).status_code == 200
    assert first.post(f'/jobs/{job_id}/dismissal', data={
        'csrf_token': csrf, 'action': 'dismiss'}, headers=headers).json()['status'] == 'ignored'
    cookies = first.cookies
    first.close()

    second, auth = new_instance()
    second.cookies.update(cookies)
    page = second.get('/?status=ignored')
    assert page.status_code == 200 and 'Synthetic durable note' in page.text
    assert token(page) == csrf  # session-bound token survives a cold process start
    assert second.post(f'/jobs/{job_id}/dismissal', data={
        'csrf_token': csrf, 'action': 'restore'}, headers=headers).json()['status'] == 'applied'
    assert jobs.get_by_id(job_id).status == 'applied'
    original = auth.get_session
    def unavailable(*args):
        raise RuntimeError('synthetic-private-connection-detail')
    monkeypatch.setattr(auth, 'get_session', unavailable)
    response = second.get('/')
    assert response.status_code == 503 and 'synthetic-private' not in response.text
    monkeypatch.setattr(auth, 'get_session', original)
    assert 'Synthetic durable note' in second.get('/').text
    assert second.post('/logout', data={'csrf_token': csrf}, headers=headers).status_code == 303
    second.close()
    third, _ = new_instance()
    third.cookies.update(cookies)
    assert third.get('/').status_code == 303  # revocation also persists across restart
    third.close()
