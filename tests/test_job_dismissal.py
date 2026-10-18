"""Filtering and dismissal against disposable data; no real jobs or mail."""

from dataclasses import asdict
import json
import re

import pytest
from fastapi.testclient import TestClient

from src.models import JobPosting
from src.notifications.durable_scan import deliver_batch
from src.notifications.email_notifier import DeliveryError, SmtpSettings, compose_scan_summary, EmailJobRecommendation
from src.scrapers.budgeted_scan import DurableSummary
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.storage.scan_store import ScanStore
from src.web.app import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    ids = [repo.save(JobPosting(f'Student software {state}', 'Example', 'Israel',
                               f'https://example.test/{state}', is_relevant=True, status=state))
           for state in ('new', 'reviewed', 'applied', 'rejected', 'ignored')]
    def build():
        return TestClient(create_app(tmp_path / 'jobs.db', notification_recipient_updater=None,
                          outreach_database_path=tmp_path / 'outreach.db',
                          application_database_path=tmp_path / 'applications.db'),
                          base_url='http://127.0.0.1')
    return repo, ids, build, tmp_path


def token(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get('/').text)[1]


def change(client, job_id, action, **kwargs):
    return client.post(f'/jobs/{job_id}/dismissal',
                       data={'action': action, 'csrf_token': token(client)}, **kwargs)


def shown(response):
    return re.findall(r'<article class="job-card" data-job-id="(\d+)"', response.text)


def test_multiselect_or_and_defaults_and_legacy(workspace):
    repo, ids, build, _ = workspace
    client = build()
    assert set(shown(client.get('/?status=new&status=reviewed'))) == {str(ids[0]), str(ids[1])}
    assert shown(client.get('/?status=new&status=reviewed&query=reviewed&company=Example')) == [str(ids[1])]
    assert shown(client.get('/?status=new&status=reviewed&company=Other')) == []
    assert str(ids[-1]) not in shown(client.get('/'))
    assert shown(client.get('/?status=ignored')) == [str(ids[-1])]
    assert shown(client.get('/?status=dismissed')) == [str(ids[-1])]
    assert shown(client.get('/?status=new')) == [str(ids[0])]
    assert shown(client.get('/?status=all')) == shown(client.get('/'))
    assert client.get('/?status=new&status=bad').status_code == 400
    assert 'href="/">Reset filters' in client.get('/?status=new&status=reviewed').text
    assert 'New, Reviewed' in client.get('/?status=new&status=reviewed').text


@pytest.mark.parametrize('index', [0, 1, 2, 3])
def test_dismiss_restart_restore_preserves_job_notes_and_upsert(workspace, index):
    repo, ids, build, path = workspace
    client = build()
    job_id = ids[index]
    before = asdict(repo.get_by_id(job_id))
    notes = PersonalNotesRepository(path / 'personal_notes.db')
    notes.save(job_id, '<script>alert("synthetic")</script>')
    assert change(client, job_id, 'dismiss').json()['status'] == 'ignored'
    assert str(job_id) not in shown(client.get('/'))
    repo.save(JobPosting(**before))
    assert repo.get_by_id(job_id).status == 'ignored'
    assert change(client, job_id, 'dismiss').json()['changed'] is False
    restarted = build()
    assert str(job_id) in shown(restarted.get('/?status=ignored'))
    assert '&lt;script&gt;' in restarted.get('/?status=ignored').text
    assert change(restarted, job_id, 'restore').json()['status'] == before['status']
    assert asdict(repo.get_by_id(job_id)) == before
    assert notes.for_jobs([job_id]) == {job_id: '<script>alert("synthetic")</script>'}
    assert repo.get_by_id(ids[-1]).status == 'ignored'


def test_security_and_missing_ids(workspace):
    repo, ids, build, _ = workspace
    client = build()
    assert client.post(f'/jobs/{ids[0]}/dismissal', data={'action': 'dismiss'}).status_code == 403
    assert change(client, ids[0], 'dismiss', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert change(client, ids[0], 'dismiss', headers={'Host': 'evil.test'}).status_code == 400
    assert change(client, 999999, 'dismiss').status_code == 404
    assert change(client, ids[0], 'delete').status_code == 400
    assert repo.get_by_id(ids[0]).status == 'new'


def test_conditional_status_does_not_overwrite_another_change(workspace):
    repo, ids, _, _ = workspace
    assert repo.update_status(ids[0], 'ignored', expected_status='applied') is None
    assert repo.get_by_id(ids[0]).status == 'new'


def test_dismissal_failure_does_not_hide_job_or_expose_errors(workspace, monkeypatch):
    repo, ids, build, _ = workspace
    client = build()
    def fail(*_, **__):
        raise RuntimeError('Synthetic confidential connection details')
    monkeypatch.setattr(JobRepository, 'update_status', fail)
    response = change(client, ids[0], 'dismiss')
    assert response.status_code == 503
    assert 'confidential' not in response.text
    assert repo.get_by_id(ids[0]).status == 'new'
    assert str(ids[0]) in shown(client.get('/'))


def test_changed_status_returns_conflict(workspace, monkeypatch):
    _, ids, build, _ = workspace
    client = build()
    monkeypatch.setattr(JobRepository, 'update_status', lambda *_, **__: None)
    assert change(client, ids[0], 'dismiss').status_code == 409


def test_missing_restore_history_uses_reviewed(workspace):
    _, ids, build, _ = workspace
    assert change(build(), ids[-1], 'restore').json()['status'] == 'reviewed'


@pytest.fixture
def delivery(tmp_path):
    repo = JobRepository(tmp_path / 'delivery.db')
    repo.initialize()
    store = ScanStore(repo)
    store.migrate()
    yield repo, store, SmtpSettings('localhost', 25, '', '', 'from@example.test', 'to@example.test', False)
    store.close()


@pytest.mark.parametrize('frozen', [False, True])
def test_dismissed_outbox_and_definitely_failed_frozen_mail_are_suppressed(delivery, frozen):
    repo, store, settings = delivery
    job = JobPosting('Synthetic Student', 'Example', 'Israel', 'https://example.test/dismiss')
    store.save_discovery(job, 'slot')
    before = store.execute('SELECT * FROM notification_outbox').fetchone()
    if frozen:
        def fail(*_):
            raise DeliveryError(RuntimeError('Synthetic failure'), uncertain=False)
        assert deliver_batch(store, 'slot', DurableSummary(), settings, sender=fail) == 'failed'
    stored = repo.get_by_url(job.job_url)
    repo.update_status(stored.id, 'ignored')
    assert not store.save_discovery(job, 'next-slot')
    assert store.prepare_batch('slot') == []
    messages = []
    assert deliver_batch(store, 'slot', DurableSummary(), settings,
                         sender=lambda _, message: messages.append(message)) == 'sent'
    assert job.job_url not in messages[0].get_content()
    assert repo.get_by_id(stored.id).status == 'ignored'
    after = store.execute('SELECT * FROM notification_outbox').fetchone()
    assert after['job_id'] == before['job_id'] and after['origin_slot'] == before['origin_slot']
    assert len(store.execute('SELECT * FROM job_postings').fetchall()) == 1


@pytest.mark.parametrize('terminal', ['sent', 'uncertain', 'sending'])
def test_terminal_or_inflight_messages_are_never_rewritten(delivery, terminal):
    repo, store, settings = delivery
    job = JobPosting('Synthetic Student', 'Example', 'Israel', 'https://example.test/terminal')
    store.save_discovery(job, 'slot')
    store.prepare_batch('slot')
    frozen = json.dumps({'subject': 'original', 'body': job.job_url})
    store.freeze_message('slot', frozen)
    store.execute('UPDATE notification_batches SET state=? WHERE slot_key=?', (terminal, 'slot'))
    repo.update_status(repo.get_by_url(job.job_url).id, 'ignored')
    assert deliver_batch(store, 'slot', DurableSummary(), settings,
                         sender=lambda *_: pytest.fail('Must not send')) == terminal
    assert store.execute('SELECT message FROM notification_batches').fetchone()['message'] == frozen


def test_frozen_replacement_requires_same_unsent_message(delivery):
    _, store, _ = delivery
    store.prepare_batch('slot')
    store.freeze_message('slot', 'original')
    assert store.freeze_message('slot', 'incorrect', previous_message='stale') == 'original'
    assert store.freeze_message('slot', 'updated', previous_message='original') == 'updated'
    store.execute("UPDATE notification_batches SET state='uncertain' WHERE slot_key='slot'")
    assert store.freeze_message('slot', 'incorrect', previous_message='updated') == 'updated'


def test_legacy_composer_excludes_dismissed_recommendations(delivery):
    _, _, settings = delivery
    job = JobPosting('Synthetic Student', 'Example', 'Israel', 'https://example.test/legacy', status='ignored')
    summary = DurableSummary(new_jobs=[job])
    recommendation = EmailJobRecommendation(job.title, job.company, job.location, job.job_url, 90, True, 'Example')
    for recommendations in (None, (recommendation,)):
        message = compose_scan_summary(settings, summary, recommendations)
        assert job.job_url not in message.get_content()
        assert '0 new matching' in message['Subject']
