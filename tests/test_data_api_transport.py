"""Hermetic machine identity and RPC boundary regression tests."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from src.web.data_api_transport import DataApiClient, DataApiError, RPC_NAMES


SUBJECT = "00000000-0000-4000-8000-000000000001"
ORIGIN = "https://synthetic.supabase.co"
NOW = 1_800_000_000


def jwt(**changes):
    claims = dict(sub=SUBJECT, role="ajob_cloud_api", iss=ORIGIN + "/auth/v1",
                  aud="authenticated", iat=NOW, exp=NOW + 3600)
    claims.update(changes)
    encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return encode({"alg": "ES256"}) + "." + encode(claims) + ".c3ludGhldGlj"


def make_client(handler=None, *, token=None, **kwargs):
    requests = []

    def route(request):
        requests.append(request)
        if handler:
            result = handler(request)
            if result is not None:
                return result
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": token or jwt()})
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": SUBJECT})
        if request.url.path.endswith("/ajob_preflight"):
            return httpx.Response(200, json={"ready": True, "version": 1})
        return httpx.Response(200, json={"ok": True})

    client = DataApiClient(ORIGIN, "sb_publishable_synthetic", "machine@example.test",
                           "synthetic-password", SUBJECT,
                           client=httpx.Client(transport=httpx.MockTransport(route)),
                           clock=kwargs.pop("clock", lambda: NOW), **kwargs)
    return client, requests


def test_verified_identity_precedes_rpc_and_fixed_headers():
    client, requests = make_client()
    assert client.preflight() == {"ready": True, "version": 1}
    assert [r.url.path for r in requests] == ["/auth/v1/token", "/auth/v1/user", "/rest/v1/rpc/ajob_preflight"]
    assert requests[0].url.query == b"grant_type=password"
    assert "authorization" not in requests[0].headers
    assert json.loads(requests[0].content) == {"email": "machine@example.test", "password": "synthetic-password"}  # pragma: allowlist secret -- synthetic MockTransport fixture only
    for request in requests[1:]:
        assert request.headers["Authorization"] == "Bearer " + jwt()
    assert requests[-1].headers["Content-Profile"] == "ajob_api"
    assert requests[-1].headers["Accept-Profile"] == "ajob_api"
    assert requests[-1].extensions["timeout"] == dict(connect=5.0, read=10.0, write=10.0, pool=5.0)
    client.rpc("ajob_notes", {"p_job_id": 1})
    assert len(requests) == 4


@pytest.mark.parametrize("claims", [
    {"sub": "00000000-0000-4000-8000-000000000002"},
    {"role": "service_role"}, {"role": "authenticated"},
    {"iss": "https://other.supabase.co/auth/v1"}, {"aud": "anon"},
    {"exp": NOW}, {"exp": True}, {"exp": float("nan")},
    {"iat": NOW + 120}, {"iat": -1}, {"iat": "0"},
])
def test_reject_claims_after_authoritative_verification(claims):
    client, requests = make_client(token=jwt(**claims))
    with pytest.raises(DataApiError, match="Cloud data API unavailable"):
        client.preflight()
    assert len(requests) == 2


@pytest.mark.parametrize("token", ["bad", "a.b.c", "e30.e30.c2ln", "a.b.c.d", "☃"])
def test_malformed_token_cannot_authorize_rpc(token):
    client, requests = make_client(token=token)
    with pytest.raises(DataApiError):
        client.preflight()
    assert not any("/rest/" in r.url.path for r in requests)


@pytest.mark.parametrize("response", [httpx.Response(401, json={"secret": "private-body"}),  # pragma: allowlist secret -- synthetic error-redaction fixture
                                      httpx.Response(200, json={"id": "wrong"})])
def test_server_identity_failure(response):
    client, requests = make_client(lambda r: response if r.url.path.endswith("/user") else None)
    with pytest.raises(DataApiError) as caught:
        client.preflight()
    assert str(caught.value) == "Cloud data API unavailable."
    assert len(requests) == 2


@pytest.mark.parametrize("url", ["http://synthetic.supabase.co", "https://evil.test",
    "https://synthetic.supabase.co.evil.test", "https://u:p@synthetic.supabase.co",
    ORIGIN + "/rest", ORIGIN + "?secret=x", ORIGIN + ":444", "http://127.0.0.1:1"])
def test_invalid_origins(url):
    with pytest.raises(DataApiError, match="configuration"):
        DataApiClient(url, "sb_publishable_synthetic", "a", "b", SUBJECT)


@pytest.mark.parametrize("key", ["sb_secret_synthetic", jwt(role="service_role"), "", "postgres://private"])
def test_reject_privileged_or_missing_key(key):
    with pytest.raises(DataApiError, match="configuration"):
        DataApiClient(ORIGIN, key, "a", "b", SUBJECT)


def test_missing_machine_password_is_not_anon_auth(monkeypatch):
    for name, value in {
        "AJOB_SUPABASE_URL": ORIGIN, "AJOB_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_synthetic",
        "AJOB_SUPABASE_AUTH_EMAIL": "machine@example.test", "AJOB_SUPABASE_AUTH_PASSWORD": "",
        "AJOB_SUPABASE_AUTH_SUBJECT": SUBJECT,
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(DataApiError):
        DataApiClient.from_env()


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 503])
def test_rpc_errors_never_redirect_or_retry(status):
    client, requests = make_client(lambda r: httpx.Response(status, headers={"Location": "https://evil.test/"},
        text="private-body") if "/rest/" in r.url.path else None)
    with pytest.raises(DataApiError) as caught:
        client.rpc("ajob_note_save", {"p_note": "private-note"})
    assert len(requests) == 3
    assert str(caught.value) == "Cloud data API unavailable."
    assert all(r.url.host == "synthetic.supabase.co" for r in requests)


def test_timeout_never_replays_or_leaks():
    def fail(request):
        if "/rest/" in request.url.path:
            raise httpx.ReadTimeout("private-token-private-password", request=request)
    client, requests = make_client(fail)
    with pytest.raises(DataApiError) as caught:
        client.rpc("ajob_dismiss")
    assert len(requests) == 3
    assert str(caught.value) == "Cloud data API unavailable."
    assert caught.value.__suppress_context__


def test_threaded_renewal_signs_in_once():
    now = [NOW]
    client, requests = make_client(clock=lambda: now[0],
                                  handler=lambda r: httpx.Response(200, json={"access_token": jwt(iat=now[0], exp=now[0]+3600)})
                                  if r.url.path.endswith("/token") else None)
    client.preflight()
    now[0] += 3550
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda _: client.preflight(), range(20)))
    assert sum(r.url.path.endswith("/token") for r in requests) == 2
    assert sum(r.url.path.endswith("/user") for r in requests) == 2


@pytest.mark.parametrize("name", ["jobs", "../jobs", "ajob_jobs?x=y", "ajob_jobs/other"])
def test_rpc_allowlist_rejects_before_auth(name):
    client, requests = make_client()
    with pytest.raises(DataApiError):
        client.rpc(name)
    assert not requests


def test_all_contract_methods_are_callable():
    client, requests = make_client()
    for name in RPC_NAMES:
        client.rpc(name)
    assert len(requests) == len(RPC_NAMES) + 2


def test_production_client_disables_env_proxy_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    client = DataApiClient(ORIGIN, "sb_publishable_synthetic", "a", "b", SUBJECT)
    try:
        assert client._client._trust_env is False
        assert client._client.follow_redirects is False
        assert not client._client._mounts
    finally:
        client.close()


@pytest.mark.parametrize("response", [httpx.Response(204), httpx.Response(200)])
def test_empty_void_rpc_succeeds_without_retry(response):
    client, requests = make_client(lambda r: response if "/rest/" in r.url.path else None)
    assert client.rpc("ajob_note_save") is None
    assert len(requests) == 3


@pytest.mark.parametrize("result", [None, {}, {"ready": False, "version": 1},
    {"ready": True, "version": 2}, {"ready": 1, "version": True},
    {"ready": True, "version": 1, "extra": 0}])
def test_preflight_requires_ready_schema_version(result):
    client, _ = make_client(lambda r: httpx.Response(200, json=result)
                           if "/rest/" in r.url.path else None)
    with pytest.raises(DataApiError):
        client.preflight()


@pytest.mark.parametrize("declared", [False, True])
def test_response_size_bound(declared):
    class LargeBody(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(129):
                yield b"x" * 65536

    client, requests = make_client(lambda r: httpx.Response(200,
        headers={"Content-Length": str(9 * 1024 * 1024)} if declared else {}, stream=LargeBody())
        if "/rest/" in r.url.path else None)
    with pytest.raises(DataApiError):
        client.rpc("ajob_jobs")
    assert len(requests) == 3
