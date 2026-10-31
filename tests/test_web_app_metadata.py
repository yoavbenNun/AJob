"""Home-screen metadata is static and never caches private dashboard data."""
import json
from struct import unpack
from pathlib import Path

from fastapi.testclient import TestClient
from test_balanced_workspace import workspace


def test_web_app_metadata_and_icons_are_local_and_data_free(workspace):
    app, _, ids = workspace
    client = TestClient(app, base_url='http://127.0.0.1')
    for url in ['/',f'/jobs/{ids[0]}']:
        response = client.get(url)
        assert 'manifest.webmanifest' in response.text
        assert 'name="apple-mobile-web-app-capable" content="yes"' in response.text
        assert 'icons/ajob-180.png' in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'same-origin'
    response = client.get('/static/manifest.webmanifest')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    manifest = response.json()
    assert manifest['start_url'] == manifest['scope'] == manifest['id'] == '/'
    assert manifest['display'] == 'standalone'
    assert not {'shortcuts','protocol_handlers','share_target'} & manifest.keys()
    for size in [180,192,512]:
        icon = client.get(f'/static/icons/ajob-{size}.png')
        assert icon.status_code == 200 and icon.content[:8] == b'\x89PNG\r\n\x1a\n'
        assert unpack('>II',icon.content[16:24]) == (size,size)
    static = Path(__file__).parents[1]/'src/web/static'
    assert all('serviceWorker.register' not in path.read_text(encoding='utf8') for path in static.glob('*.js'))
