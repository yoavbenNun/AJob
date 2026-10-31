"""No server, socket or real settings are needed to verify process isolation."""
from pathlib import Path
from unittest.mock import Mock

import yaml

from src.web import cloud_server


def test_cloud_process_only_starts_factory_without_proxy_or_access_logs(monkeypatch):
    start=Mock()
    monkeypatch.setattr(cloud_server.uvicorn,'run',start)
    monkeypatch.setenv('PORT','12345')
    cloud_server.main()
    assert start.call_args.args == ('src.web.cloud:create_cloud_app',)
    assert start.call_args.kwargs == dict(factory=True,host='0.0.0.0',port=12345,
        workers=1,proxy_headers=False,access_log=False,server_header=False)


def test_example_is_explicitly_free_manual_and_contains_no_scanner_settings():
    root=Path(__file__).resolve().parents[1]
    spec=yaml.safe_load((root/'render.dashboard.yaml').read_text())['services'][0]
    assert spec['plan']=='free' and spec['autoDeployTrigger']=='off'
    assert spec['healthCheckPath']=='/healthz'
    assert {e['key'] for e in spec['envVars']} == {
        'AJOB_CLOUD_ENABLED','AJOB_PUBLIC_ORIGIN','AJOB_DASHBOARD_DATABASE_URL','AJOB_OWNER_PASSWORD_HASH'}
    assert all(e.get('sync') is False for e in spec['envVars'] if e['key']!='AJOB_CLOUD_ENABLED')
    docker=(root/'Dockerfile.dashboard').read_text()
    assert 'src.web.cloud_server' in docker
    assert 'playwright install' not in docker and 'src.main' not in docker
