"""Synthetic authentication tests: no configured database, owner or network."""
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from src.web.cloud_auth import (
    AuthSession, CloudAuthConfig, CloudAuthMiddleware, LOGIN_COOKIE, SESSION_COOKIE,
    hash_password, verify_password,
)

ORIGIN = "https://dashboard.example.test"
PASSWORD = "synthetic-owner-password-for-tests"  # pragma: allowlist secret -- synthetic fixture, never a live credential


class MemoryStore:
    def __init__(self):
        self.sessions, self.attempts = {}, []
        self.lock = threading.Lock()
        self.unavailable = False

    def _check(self):
        if self.unavailable:
            raise RuntimeError("synthetic secret database connection string")

    def reserve_login(self, now):
        self._check()
        with self.lock:
            self.attempts = [stamp for stamp in self.attempts if stamp > now - 900]
            if len(self.attempts) >= 5:
                return False
            self.attempts.append(now)
            return True

    def create_session(self, token, csrf, now, expires):
        self._check()
        with self.lock:
            while len(self.sessions) >= 5:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[token] = [csrf, now, expires]

    def get_session(self, token, now, idle):
        self._check()
        with self.lock:
            row = self.sessions.get(token)
            if not row or row[1] <= now - idle or row[2] <= now:
                return None
            row[1] = now
            return AuthSession(row[0])

    def revoke_session(self, token):
        self._check()
        self.sessions.pop(token, None)


@pytest.fixture(scope="module")
def credential():
    return hash_password(PASSWORD)


@pytest.fixture
def setup(credential):
    app = FastAPI()
    @app.get("/")
    def index(request: Request):
        return {"csrf": request.state.csrf_token}
    @app.get("/healthz")
    def health():
        return {"ok": True}
    @app.get("/private/resume.pdf")
    def private():
        return {"secret": True}
    @app.post("/write")
    async def write(request: Request):
        return {"value": (await request.form()).get("value"), "csrf": request.state.csrf_token}
    @app.post("/json")
    async def json_write(request: Request):
        return await request.json()
    store, now = MemoryStore(), [10000.0]
    config = CloudAuthConfig(ORIGIN, credential, idle_seconds=60, max_age_seconds=180)
    middleware = CloudAuthMiddleware(app, config, store, clock=lambda: now[0])
    client = TestClient(middleware, base_url=ORIGIN, follow_redirects=False)
    return client, store, now, middleware


def login(client, password=PASSWORD):
    response = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]
    return client.post("/login", data={"csrf_token": token, "password": password}, headers={"Origin": ORIGIN})


def test_credential_hash_is_salted_and_strict(credential):
    assert credential != hash_password(PASSWORD)
    assert verify_password(PASSWORD, credential)
    assert not verify_password(PASSWORD + "!", credential)
    assert not verify_password("x" * 1025, credential)
    with pytest.raises(ValueError):
        hash_password("short")
    with pytest.raises(ValueError):
        CloudAuthConfig(ORIGIN, credential.replace("32768", "1048576"))


@pytest.mark.parametrize("origin", ["http://dashboard.example.test", "https://*.example.test", "https://user@dashboard.example.test", "https://dashboard.example.test/", "https://dashboard.example.test?a=b", "https://dashboard.example.test:444", "https://dashboard.example.test#x", "https://DASHBOARD.example.test"])
def test_config_fails_closed(credential, origin):
    with pytest.raises(ValueError):
        CloudAuthConfig(origin, credential)


def test_anonymous_routes_and_no_return_query(setup):
    client, *_ = setup
    for path in ["/", "/private/resume.pdf", "/static/unknown.js", "/?return=https://evil.test"]:
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    assert client.get("/healthz").status_code == 200
    assert client.post("/write").status_code == 401
    assert client.get("/login").status_code == 200


def test_cookie_flags_and_headers(setup):
    client, *_ = setup
    response = client.get("/login")
    cookie = response.headers["set-cookie"]
    assert LOGIN_COOKIE in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert "SameSite=strict" in cookie and "Path=/" in cookie and "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "http://" not in response.text and "https://" not in response.text
    response = login(client)
    assert response.status_code == 303
    assert SESSION_COOKIE in response.headers.get_list("set-cookie")[0]
    assert "HttpOnly" in response.headers.get_list("set-cookie")[0]


@pytest.mark.parametrize("origin", [None, "null", "https://evil.test", "https://dashboard.example.test.evil.test"])
def test_login_requires_exact_origin(setup, origin):
    client, *_ = setup
    token = client.get("/login").cookies[LOGIN_COOKIE]
    headers = {"Origin": origin} if origin else {}
    assert client.post("/login", data={"csrf_token": token, "password": PASSWORD}, headers=headers).status_code == 403


def test_login_precsrf_expiry_and_tamper(setup):
    client, store, now, _ = setup
    token = client.get("/login").cookies[LOGIN_COOKIE]
    for submitted in ["", "forged", token + "x", "\u05d0"]:
        assert client.post("/login", data={"csrf_token": submitted, "password": PASSWORD}, headers={"Origin": ORIGIN}).status_code == 403
    now[0] += 600
    assert client.post("/login", data={"csrf_token": token, "password": PASSWORD}, headers={"Origin": ORIGIN}).status_code == 403
    assert not store.attempts


def test_login_wrong_password_throttle_persists_across_instances(setup, credential):
    client, store, now, middleware = setup
    for _ in range(5):
        assert login(client, "incorrect-but-long-password").status_code == 401
    second = TestClient(CloudAuthMiddleware(middleware.app, middleware.config, store, clock=lambda: now[0]), base_url=ORIGIN, follow_redirects=False)
    response = login(second)
    assert response.status_code == 429 and response.headers["Retry-After"] == "900"
    now[0] += 900
    assert login(second).status_code == 303


def test_store_throttle_concurrency_and_session_bound(setup):
    _, store, now, _ = setup
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(store.reserve_login, [now[0]] * 24)) == 5
    for i in range(10):
        store.create_session(str(i), "csrf", now[0], now[0] + 180)
    assert len(store.sessions) == 5


def test_session_token_hashed_and_mutation_session_csrf(setup):
    client, store, *_ = setup
    login(client)
    token = client.cookies[SESSION_COOKIE]
    assert token not in store.sessions
    assert hashlib.sha256(token.encode()).hexdigest() in store.sessions
    csrf = client.get("/").json()["csrf"]
    for origin, submitted in [(ORIGIN, "wrong"), (ORIGIN, "\u05d0"), ("null", csrf), (None, csrf)]:
        headers = {"Origin": origin} if origin else {}
        assert client.post("/write", data={"csrf_token": submitted}, headers=headers).status_code == 403
    response = client.post("/write", data={"csrf_token": csrf, "value": "preserved"}, headers={"Origin": ORIGIN})
    assert response.json() == {"value": "preserved", "csrf": csrf}
    assert client.post("/json", json={"preserved": True}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).json() == {"preserved": True}


def test_login_rotates_session_and_csrf_and_logout_revokes(setup):
    client, store, *_ = setup
    login(client)
    old_token, old_csrf = client.cookies[SESSION_COOKIE], client.get("/").json()["csrf"]
    login(client)
    csrf = client.get("/").json()["csrf"]
    assert csrf != old_csrf and client.cookies[SESSION_COOKIE] != old_token
    assert hashlib.sha256(old_token.encode()).hexdigest() not in store.sessions
    assert client.get("/logout").status_code == 405
    assert client.post("/logout", data={"csrf_token": "wrong"}, headers={"Origin": ORIGIN}).status_code == 403
    response = client.post("/logout", data={"csrf_token": csrf}, headers={"Origin": ORIGIN})
    assert response.status_code == 303 and not store.sessions
    assert client.get("/").status_code == 303


def test_csrf_does_not_transfer_between_sessions(setup):
    client, _, _, middleware = setup
    login(client)
    csrf = client.get("/").json()["csrf"]
    second = TestClient(middleware, base_url=ORIGIN, follow_redirects=False)
    login(second)
    assert second.post("/write", data={"csrf_token": csrf}, headers={"Origin": ORIGIN}).status_code == 403


def test_idle_and_absolute_expiry(setup):
    client, _, now, _ = setup
    login(client)
    now[0] += 60
    assert client.get("/").status_code == 303
    login(client)
    for _ in range(3):
        now[0] += 50
        assert client.get("/").status_code == 200
    now[0] += 30
    assert client.get("/").status_code == 303


def test_host_and_forwarded_identity_never_bypass_auth(setup):
    client, *_ = setup
    assert client.get("/", headers={"Host": "evil.test", "X-Forwarded-Host": "dashboard.example.test"}).status_code == 400
    assert client.get("/", headers={"X-Forwarded-User": "owner", "X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1;proto=https"}).status_code == 303
    assert client.get("/", headers=[("Host", "dashboard.example.test"), ("Host", "evil.test")]).status_code == 400


def test_database_failure_redacted_and_public_liveness_survives(setup):
    client, store, *_ = setup
    login(client)
    store.unavailable = True
    response = client.get("/")
    assert response.status_code == 503
    assert "secret" not in response.text and "database" not in response.text
    assert client.get("/healthz").status_code == 200


def test_login_body_size_and_get_logout_does_not_revoke(setup):
    client, store, *_ = setup
    assert client.post("/login", content="a" * 8193, headers={"Origin": ORIGIN}).status_code == 413
    login(client)
    assert client.get("/logout").status_code == 405
    assert store.sessions


def test_websocket_denied(setup):
    client, *_ = setup
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
