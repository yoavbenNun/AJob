"""Security boundary tests use only temporary SQLite data and fake services."""

import re

import pytest
from fastapi.testclient import TestClient

from src.web.app import create_app


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    updates = []
    app = create_app(
        tmp_path / "jobs.db",
        outreach_database_path=tmp_path / "outreach.db",
        application_database_path=tmp_path / "applications.db",
        notification_recipient_updater=updates.append,
    )
    return TestClient(app, base_url="http://127.0.0.1"), updates


@pytest.mark.parametrize("host", ["attacker.example", "127.0.0.1.attacker.example", "localhost.attacker.example"])
def test_untrusted_hosts_cannot_read_dashboard_or_csrf_token(dashboard, host):
    client, _ = dashboard
    response = client.get("/", headers={"host": host})
    assert response.status_code == 400
    assert 'name="csrf_token"' not in response.text
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("headers", [
    {"origin": "https://attacker.example"},
    {"origin": "null"},
    {"origin": "http://127.0.0.1:9000"},
    {"origin": "https://127.0.0.1"},
    {"origin": "http://127.0.0.1:invalid"},
    {"referer": "https://attacker.example/form"},
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
])
def test_cross_origin_request_cannot_change_recipient_even_with_valid_csrf(dashboard, headers):
    client, updates = dashboard
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text)[1]
    response = client.post("/settings/notification-email", headers=headers, data={
        "csrf_token": token, "notification_email": "candidate@example.test",
    }, follow_redirects=False)
    assert response.status_code == 403
    assert updates == []


def test_same_origin_forms_work_and_csrf_is_still_required(dashboard):
    client, updates = dashboard
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text)[1]
    headers = {"origin": "http://127.0.0.1", "referer": "http://127.0.0.1/", "sec-fetch-site": "same-origin"}
    for csrf, expected in [("invalid", 403), (token, 303)]:
        response = client.post("/settings/notification-email", headers=headers, data={
            "csrf_token": csrf, "notification_email": "candidate@example.test",
        }, follow_redirects=False)
        assert response.status_code == expected
    assert updates == ["candidate@example.test"]


@pytest.mark.parametrize("path,status", [("/", 200), ("/missing", 404)])
def test_browser_privacy_and_framing_headers(dashboard, path, status):
    response = dashboard[0].get(path)
    assert response.status_code == status
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
