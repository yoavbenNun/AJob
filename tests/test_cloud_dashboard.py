"""Cloud surface integration using disposable stores; never production."""
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.storage.dismissal_repository import DismissalRepository
from src.web.cloud import build_cloud_app, CloudServices, create_cloud_app
from src.web.cloud_auth import CloudAuthConfig, hash_password
from test_cloud_auth import MemoryStore, PASSWORD, ORIGIN, login


class SyntheticDismissals(DismissalRepository):
    def __init__(self, path, jobs):
        super().__init__(path)
        self.jobs = jobs

    def change_status(self, job_id, action):
        job = self.jobs.get_by_id(job_id)
        if job is None:
            return None
        target = 'ignored' if action == 'dismiss' else self.previous(job_id) or 'reviewed'
        if action == 'dismiss':
            self.remember(job_id, job.status)
        self.jobs.update_status(job_id, target)
        return {'status': target, 'changed': True}


@pytest.fixture
def cloud_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    jobs = JobRepository(tmp_path / 'jobs.db')
    jobs.initialize()
    ids = [jobs.save(JobPosting('Software Student', 'Synthetic Company', 'Israel',
        f'https://example.test/jobs/{i}', description='Develop Python services.',
        status=status, is_relevant=True)) for i, status in enumerate(['new','reviewed','applied'])]
    services = CloudServices(jobs, PersonalNotesRepository(tmp_path / 'notes.db'),
                             SyntheticDismissals(tmp_path / 'notes.db', jobs))
    config = CloudAuthConfig(ORIGIN, hash_password(PASSWORD))
    store = MemoryStore()
    app = build_cloud_app(config, services, store)
    return app, services, ids, config, store


def token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def test_cloud_factory_disabled_and_no_sqlite_fallback(monkeypatch):
    monkeypatch.delenv('AJOB_CLOUD_ENABLED', raising=False)
    with pytest.raises(RuntimeError, match='disabled'):
        create_cloud_app()


def test_cloud_surface_and_session_bound_forms(cloud_workspace, monkeypatch):
    app, services, ids, config, store = cloud_workspace
    import src.web.app as web
    monkeypatch.setattr(web, 'get_active_resume', lambda *a: pytest.fail('Cloud read a local resume'))
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get('/healthz').json() == {'status':'ok'}
    assert client.get('/').status_code == 303
    assert client.post(f'/jobs/{ids[0]}/status').status_code == 401
    assert login(client).status_code == 303
    response = client.get('/')
    assert response.status_code == 200
    assert 'http://' not in response.text
    positions = [response.text.index(f'data-job-id="{job_id}"') for job_id in [ids[2], ids[0], ids[1]]]
    assert positions == sorted(positions)
    assert 'Update email' not in response.text and 'Update resume' not in response.text
    csrf = token(response)
    for path in ['/health','/openapi.json','/resume','/settings/notification-email',
                 f'/jobs/{ids[0]}/outreach/contacts',f'/jobs/{ids[0]}/application/preview']:
        assert client.get(path).status_code == 404
    data = {'csrf_token':csrf, 'note':'Synthetic <script>alert(1)</script>'}
    assert client.post(f'/jobs/{ids[0]}/personal-note', data=data, headers={'Origin':ORIGIN}).status_code == 200
    assert services.notes.for_jobs(ids) == {ids[0]:data['note']}
    assert client.post(f'/jobs/{ids[0]}/status', data={'csrf_token':csrf,'status':'applied'},headers={'Origin':ORIGIN}).status_code == 303
    assert services.jobs.get_by_id(ids[0]).status == 'applied'
    assert services.notes.for_jobs(ids)[ids[0]] == data['note']
    assert '&lt;script&gt;' in client.get('/').text
    detail = client.get(f'/jobs/{ids[0]}')
    assert detail.status_code == 200 and 'Contact people' not in detail.text
    assert 'https://example.test/jobs/0' in detail.text
    assert 'Session' not in client.get('/healthz').text
    # Rebuild the application (cold start), keeping only the persistent stores.
    restarted = TestClient(build_cloud_app(config,services,store),base_url=ORIGIN)
    restarted.cookies.update(client.cookies)
    assert '&lt;script&gt;' in restarted.get('/').text


def test_cloud_failures_are_redacted_and_do_not_lose_notes(cloud_workspace, monkeypatch):
    app, services, ids, _, _ = cloud_workspace
    client = TestClient(app,base_url=ORIGIN,follow_redirects=False)
    login(client)
    csrf = token(client.get('/'))
    def fail(*a, **k):
        raise RuntimeError('synthetic-private-database-secret')
    monkeypatch.setattr(services.notes,'save',fail)
    response=client.post(f'/jobs/{ids[0]}/personal-note',data={'csrf_token':csrf,'note':'Draft'},headers={'Origin':ORIGIN})
    assert response.status_code == 503 and 'synthetic-private' not in response.text
    assert services.notes.for_jobs(ids) == {}
    monkeypatch.setattr(services.jobs,'list_all',fail)
    response=client.get('/')
    assert response.status_code == 503 and 'synthetic-private' not in response.text


def test_cloud_csrf_cannot_be_reused_by_another_session(cloud_workspace):
    app, _, ids, _, _ = cloud_workspace
    first=TestClient(app,base_url=ORIGIN,follow_redirects=False)
    second=TestClient(app,base_url=ORIGIN,follow_redirects=False)
    login(first); login(second)
    csrf=token(first.get('/'))
    response=second.post(f'/jobs/{ids[0]}/status',data={'csrf_token':csrf,'status':'applied'},headers={'Origin':ORIGIN})
    assert response.status_code == 403
