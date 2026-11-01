"""Independent opt-in/persistence audit; no network, credentials or migrations."""
import os
from pathlib import Path
import smtplib
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import psycopg
import pytest

from src.web.cloud import create_cloud_app
from src.web.cloud_auth import hash_password
from src.web.data_api_transport import DataApiClient
from test_cloud_auth import ORIGIN, PASSWORD


ROOT = Path(__file__).resolve().parents[1]


def deny(*args, **kwargs):
    raise AssertionError('Forbidden external effect in independent runtime audit')


@pytest.fixture
def isolated_settings(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('AJOB_', 'SUPABASE_', 'SMTP_', 'PG')) or key in {'DATABASE_URL', 'RESUME_DIRECTORY', 'JWT_SECRET'}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AJOB_CLOUD_ENABLED', '1')
    monkeypatch.setenv('AJOB_PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.setenv('AJOB_OWNER_PASSWORD_HASH', hash_password(PASSWORD))
    monkeypatch.setattr(psycopg, 'connect', deny)
    monkeypatch.setattr(smtplib, 'SMTP', deny)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', deny)


class ReadinessOnlyClient:
    def __init__(self, fail=False):
        self.calls = []
        self.closed = False
        self.fail = fail

    def preflight(self):
        self.calls.append('preflight')
        if self.fail:
            raise RuntimeError('SYNTHETIC_PRIVATE_DETAIL')
        return {'ready': True, 'version': 1}

    def rpc(self, *args, **kwargs):
        raise AssertionError('Startup must not mutate or enumerate private records')

    def close(self):
        self.closed = True


def test_cloud_import_creates_no_local_store_or_external_effect(tmp_path):
    code = '''import os,sys,socket,sqlite3,smtplib,psycopg
from pathlib import Path
sys.path.insert(0,sys.argv[1])
def deny(*a,**kw): raise AssertionError('Import attempted an external effect')
sqlite3.connect=psycopg.connect=smtplib.SMTP=smtplib.SMTP_SSL=deny
socket.create_connection=deny
from src.web.cloud import create_cloud_app
assert list(Path.cwd().iterdir()) == []
print('HERMETIC_IMPORT_OK')
'''
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('AJOB_', 'SUPABASE_', 'SMTP_', 'PG'))
           and key not in {'DATABASE_URL', 'RESUME_DIRECTORY', 'JWT_SECRET'}}
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'HERMETIC_IMPORT_OK'
    assert list(tmp_path.iterdir()) == []


def test_cloud_factory_never_uses_transient_sqlite_or_startup_mutation(isolated_settings, monkeypatch, tmp_path):
    client = ReadinessOnlyClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sqlite3, 'connect', deny)
    monkeypatch.setattr(DataApiClient, 'from_env', classmethod(lambda cls: client))
    app = create_cloud_app()
    assert app is not None
    assert len(client.calls) >= 4 and set(client.calls) == {'preflight'}
    assert not client.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('name', [
    'DATABASE_URL', 'AJOB_DASHBOARD_DATABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY',
    'SUPABASE_SECRET_KEY', 'SUPABASE_JWT_SECRET', 'JWT_SECRET', 'PGPASSWORD',
])
def test_data_api_factory_rejects_privileged_credential_before_client_creation(
        isolated_settings, monkeypatch, name):
    monkeypatch.setenv(name, 'synthetic-forbidden-value')
    monkeypatch.setattr(sqlite3, 'connect', deny)
    monkeypatch.setattr(DataApiClient, 'from_env', classmethod(deny))
    with pytest.raises(RuntimeError, match='^Cloud startup validation failed; check approved settings and schema$'):
        create_cloud_app()


def test_failed_preflight_is_redacted_closes_client_and_has_no_disk_fallback(isolated_settings, monkeypatch, tmp_path):
    client = ReadinessOnlyClient(fail=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sqlite3, 'connect', deny)
    monkeypatch.setattr(DataApiClient, 'from_env', classmethod(lambda cls: client))
    with pytest.raises(RuntimeError) as caught:
        create_cloud_app()
    assert 'SYNTHETIC_PRIVATE_DETAIL' not in str(caught.value)
    assert caught.value.__suppress_context__
    assert client.closed and client.calls == ['preflight']
    assert list(tmp_path.iterdir()) == []


def test_cloud_requires_explicit_opt_in_before_provider_or_sqlite(isolated_settings, monkeypatch):
    monkeypatch.delenv('AJOB_CLOUD_ENABLED')
    monkeypatch.setattr(sqlite3, 'connect', deny)
    monkeypatch.setattr(DataApiClient, 'from_env', classmethod(deny))
    with pytest.raises(RuntimeError, match='^Cloud dashboard is disabled$'):
        create_cloud_app()


def test_local_factory_remains_sqlite_without_cloud_auth_or_render(isolated_settings, monkeypatch, tmp_path):
    from src.web.app import create_app
    monkeypatch.setattr(DataApiClient, 'from_env', classmethod(deny))
    app = create_app(tmp_path / 'jobs.db', resume_directory=tmp_path / 'resumes',
                     outreach_database_path=tmp_path / 'outreach.db',
                     application_database_path=tmp_path / 'applications.db',
                     personal_notes_database_path=tmp_path / 'notes.db',
                     notification_recipient_updater=None)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/').status_code == 200
        health = client.get('/health')
        assert health.status_code == 200 and health.json()['storage'] == 'sqlite'
    assert (tmp_path / 'jobs.db').is_file()
