"""Notes use disposable local databases, never cloud jobs or real user notes."""

from dataclasses import asdict, replace
import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.web.app import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    database = tmp_path / 'jobs.db'
    repository = JobRepository(database)
    repository.initialize()
    ids = [repository.save(JobPosting(f'Software Student {i}', 'Example', 'Israel',
                                       f'https://example.test/jobs/{i}')) for i in (1, 2)]
    def client():
        return TestClient(create_app(database,
                          outreach_database_path=tmp_path / 'outreach.db',
                          application_database_path=tmp_path / 'applications.db',
                          notification_recipient_updater=None), base_url='http://127.0.0.1')
    return client, repository, ids, PersonalNotesRepository(tmp_path / 'personal_notes.db')


def token(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get('/').text)[1]


def save(client, job_id, note):
    return client.post(f'/jobs/{job_id}/personal-note', data={'csrf_token': token(client), 'note': note})


def test_create_edit_delete_preview_and_independent_jobs(workspace):
    build, jobs, ids, notes = workspace
    client = build()
    before = asdict(jobs.get_by_id(ids[0]))
    assert save(client, ids[0], 'Read the requirements. Ask about study flexibility.').status_code == 200
    assert save(client, ids[1], 'Different company questions').status_code == 200
    assert 'Read the requirements.' in client.get('/').text
    assert save(client, ids[0], 'Updated note').json() == {'note': 'Updated note'}
    assert notes.for_jobs(ids) == {ids[0]: 'Updated note', ids[1]: 'Different company questions'}
    assert asdict(jobs.get_by_id(ids[0])) == before
    assert save(client, ids[0], '  ').json() == {'note': ''}
    assert notes.for_jobs(ids) == {ids[1]: 'Different company questions'}
    assert jobs.get_by_id(ids[0]) is not None


def test_notes_survive_recreated_app_status_and_job_refresh(workspace):
    build, jobs, ids, notes = workspace
    client = build()
    save(client, ids[0], 'Keep this after restart')
    jobs.update_status(ids[0], 'reviewed')
    jobs.save(replace(jobs.get_by_id(ids[0]), description='Updated source description.'))
    restarted = build()
    assert 'Keep this after restart' in restarted.get('/').text
    assert jobs.get_by_id(ids[0]).status == 'reviewed'
    jobs.update_status(ids[0], 'applied')
    assert notes.for_jobs(ids)[ids[0]] == 'Keep this after restart'


def test_notes_are_escaped_text_and_have_limits(workspace):
    build, jobs, ids, notes = workspace
    client = build()
    raw = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
    assert save(client, ids[0], raw).status_code == 200
    html = client.get('/').text
    assert raw not in html and '&lt;script&gt;' in html
    assert notes.for_jobs(ids)[ids[0]] == raw
    assert save(client, ids[0], 'x' * 2001).status_code == 400
    assert save(client, ids[0], 'x' * 2000).status_code == 200
    assert save(client, ids[0], 'bad\x00note').status_code == 400


def test_unknown_job_csrf_origin_and_host_are_rejected(workspace):
    build, jobs, ids, notes = workspace
    client = build()
    assert save(client, 99999, 'Never stored').status_code == 404
    assert client.post(f'/jobs/{ids[0]}/personal-note', data={'note': 'No token'}).status_code == 403
    for headers in ({'Origin': 'https://evil.test'}, {'Origin': 'null'}, {'Host': 'evil.test'},
                    {'Referer': 'https://evil.test/'}):
        assert client.post(f'/jobs/{ids[0]}/personal-note',
                           data={'csrf_token': token(client), 'note': 'Blocked'}, headers=headers).status_code in (400, 403)
    assert notes.for_jobs([*ids, 99999]) == {}


def test_storage_failure_returns_safe_retry_error_without_changing_job(workspace, monkeypatch):
    build, jobs, ids, notes = workspace
    client = build()
    save(client, ids[0], 'Previous note')
    def failed(*args):
        raise sqlite3.OperationalError('private database details')
    monkeypatch.setattr(PersonalNotesRepository, 'save', failed)
    response = save(client, ids[0], 'Unsaved draft')
    assert response.status_code == 503
    assert 'try again' in response.json()['detail']
    assert 'private database' not in response.text
    assert notes.for_jobs(ids)[ids[0]] == 'Previous note'
