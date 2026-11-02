"""Independent offline PR25 audit probes; no provider or database access."""
import base64
import json
from pathlib import Path
import re

import httpx
import pytest

from src.web.data_api_transport import DataApiClient, DataApiError, RPC_NAMES


OWNER = "11111111-1111-4111-8111-111111111111"
FOREIGN = "22222222-2222-4222-8222-222222222222"
NOW = 1800000000
ORIGIN = "https://independent-audit.supabase.co"


def token(**override):
    claims = dict(sub=OWNER, role="ajob_cloud_api", aud="authenticated",
                  iss=ORIGIN + "/auth/v1", iat=NOW, exp=NOW + 120)
    claims.update(override)
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return part({"alg": "ES256"}) + "." + part(claims) + ".c3ludGhldGlj"


def client_for(route, clock=lambda: NOW):
    return DataApiClient(ORIGIN, "sb_publishable_independent_audit", "fixture@example.test",
                         "synthetic-audit-password", OWNER,
                         client=httpx.Client(transport=httpx.MockTransport(route)), clock=clock)


@pytest.mark.parametrize("name", sorted(RPC_NAMES))
@pytest.mark.parametrize("identity", ["foreign-user", "server-rejected", "service-role"])
def test_no_rpc_after_rejected_machine_identity(name, identity):
    paths = []
    def route(request):
        paths.append(request.url.path)
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": token(
                role="service_role" if identity == "service-role" else "ajob_cloud_api")})
        if request.url.path == "/auth/v1/user":
            return httpx.Response(401 if identity == "server-rejected" else 200,
                                  json={"id": FOREIGN if identity == "foreign-user" else OWNER})
        pytest.fail("Denied identity reached an RPC")
    client = client_for(route)
    with pytest.raises(DataApiError, match="^Cloud data API unavailable[.]$"):
        client.rpc(name)
    assert paths == ["/auth/v1/token", "/auth/v1/user"]
    assert client._token is None and client._expires == 0


def test_boundary_expiry_forces_authoritative_renewal_and_failed_user_cannot_use_cached_token():
    now, reject, paths = [NOW], [False], []
    def route(request):
        paths.append(request.url.path)
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": token(iat=now[0], exp=now[0] + 120)})
        if request.url.path == "/auth/v1/user":
            return httpx.Response(401 if reject[0] else 200, json={"id": OWNER})
        return httpx.Response(200, json=[])
    client = client_for(route, clock=lambda: now[0])
    assert client.rpc("ajob_jobs") == []
    now[0] += 59
    assert client.rpc("ajob_jobs") == []
    now[0] += 1
    reject[0] = True
    with pytest.raises(DataApiError):
        client.rpc("ajob_jobs")
    assert client._token is None
    assert paths == ["/auth/v1/token", "/auth/v1/user", "/rest/v1/rpc/ajob_jobs",
                     "/rest/v1/rpc/ajob_jobs", "/auth/v1/token", "/auth/v1/user"]


@pytest.mark.parametrize("name", ["ajob_jobs%2f..%2fexec_sql", "ajob_jobs#x", "ajob_jobs\x00", "AJOB_JOBS"])
def test_encoded_and_noncanonical_rpc_names_fail_before_network(name):
    def route(request):
        pytest.fail("Invalid RPC name attempted transport")
    with pytest.raises(DataApiError):
        client_for(route).rpc(name)


def test_write_transport_error_is_redacted_and_not_replayed():
    counts = {"rpc": 0}
    def route(request):
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": token()})
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": OWNER})
        counts["rpc"] += 1
        # Models a commit followed by lost response; retry would duplicate mutation.
        raise httpx.ReadError("synthetic-secret-note-and-credential", request=request)
    with pytest.raises(DataApiError) as failure:
        client_for(route).rpc("ajob_note_save", {"p_job_id": 1, "p_note": "synthetic-note"})
    assert str(failure.value) == "Cloud data API unavailable."
    assert counts["rpc"] == 1
    assert failure.value.__suppress_context__


def test_sql_and_client_expose_exactly_the_same_guarded_typed_rpc_set():
    source = (Path(__file__).resolve().parents[1] / "deployment/data-api.sql").read_text(encoding="utf-8")
    functions = re.findall(
        r"CREATE OR REPLACE FUNCTION ajob_api\.(\w+)\((.*?)\) RETURNS .*?\n(.*?)END \$\$;",
        source, re.S)
    assert {name for name, _, _ in functions} == RPC_NAMES
    assert len(functions) == 11
    for _, _, body in functions:
        assert "SECURITY DEFINER SET search_path=pg_catalog" in body
        assert "SET statement_timeout='10s' SET lock_timeout='3s'" in body
        assert "PERFORM ajob_private.data_api_require_owner();" in body
        assert not re.search(r"\bEXECUTE\b", body)
    assert "auth_user_id=auth.uid()" in source
    assert "->> 'role') = 'ajob_cloud_api'" in source
    assert "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS" in source
