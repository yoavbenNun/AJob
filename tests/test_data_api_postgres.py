"""Opt-in real RPC/RLS tests; never read production DATABASE_URL.

AJOB_TEST_POSTGRES_DSN must target a disposable loopback administrator cluster.
Every run creates/destroys its own database and uniquely named temporary roles.
The auth.uid shim models Supabase's signed-claim lookup; gateway tests separately
exercise the actual JWT verification and HTTP schema boundary.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import uuid
from urllib.parse import quote, urlencode

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from src.models import JobPosting
from src.storage.postgres_job_repository import PostgresJobRepository


ROOT = Path(__file__).resolve().parents[1]


def test_database_uri(dsn, **overrides):
    """Keep the repository's normal URI validation; don't bypass it for fixtures."""
    settings = {**conninfo_to_dict(dsn), **overrides}
    host = settings.pop('host')
    if ':' in host:
        host = '[' + host + ']'
    user, password = settings.pop('user', ''), settings.pop('password', None)
    authority = quote(user, safe='')
    if password is not None:
        authority += ':' + quote(password, safe='')
    authority = (authority + '@' if authority else '') + host
    if settings.get('port'):
        authority += ':' + str(settings.pop('port'))
    database = settings.pop('dbname')
    return 'postgresql://' + authority + '/' + quote(database, safe='') + (
        '?' + urlencode(settings) if settings else '')


# Helper, not an independent pytest test.
test_database_uri.__test__ = False


@pytest.fixture(scope="module")
def data_api_database():
    dsn = os.environ.get("AJOB_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set AJOB_TEST_POSTGRES_DSN to a disposable loopback PostgreSQL cluster")
    settings = conninfo_to_dict(dsn)
    if settings.get("host") not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Refusing non-loopback PostgreSQL test host")
    if settings.get("hostaddr") not in {None, "127.0.0.1", "::1"} or settings.get("service"):
        pytest.fail("Refusing PostgreSQL test service or non-loopback hostaddr")
    suffix = uuid.uuid4().hex[:20]
    database = "ajob_data_api_test_" + suffix
    names = ("ajob_cloud_api", "ajob_api_owner", "anon", "authenticated", "service_role",
             "authenticator", "supabase_auth_admin", "ajob_dashboard")
    roles = {name: "ajob_test_" + name + "_" + suffix for name in names}
    target = test_database_uri(dsn, dbname=database)
    password = uuid.uuid4().hex
    owner_uuid, foreign_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        jobs = PostgresJobRepository(target)
        jobs.initialize()
        with psycopg.connect(target, autocommit=True) as connection:
            for name in names:
                if name in {"ajob_cloud_api", "ajob_api_owner", "ajob_dashboard"}:
                    continue  # Manual migrations create these three roles.
                if name == "authenticator":
                    connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                        sql.Identifier(roles[name]), sql.Literal(password)))
                else:
                    connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(sql.Identifier(roles[name])))
            connection.execute("CREATE SCHEMA auth")
            connection.execute("""CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
                SELECT (current_setting('request.jwt.claims',true)::jsonb->>'sub')::uuid $$""")
            connection.execute("ALTER TABLE public.job_postings ENABLE ROW LEVEL SECURITY")
            # A scanner policy must survive both first application and reapplication.
            connection.execute("CREATE POLICY synthetic_scanner ON public.job_postings TO PUBLIC USING(false)")
            for path in ("deployment/data-api.sql",):
                migration = (ROOT / path).read_text(encoding="utf-8")
                for original in sorted(roles, key=len, reverse=True):
                    migration = migration.replace(original, roles[original])
                connection.execute(migration)
                if path.endswith("data-api.sql"):
                    connection.execute(migration)
            connection.execute("INSERT INTO ajob_private.data_api_owner VALUES(true,%s)", (owner_uuid,))
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(roles["anon"]), sql.Identifier(roles["authenticator"])))
            # Synthetic sensitive surfaces exist so denial never means 'missing'.
            for schema in ("net", "vault", "cron"):
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                connection.execute(sql.SQL("CREATE TABLE {}.sensitive(secret text)").format(sql.Identifier(schema)))
            # Model the actual managed-extension hazard: PUBLIC grants remain
            # usable in SQL, making NOLOGIN + the HTTP schema boundary essential.
            connection.execute("CREATE TABLE net.http_request_queue(headers jsonb)")
            connection.execute("CREATE TABLE net._http_response(content text)")
            connection.execute("CREATE FUNCTION net.http_post(url text) RETURNS bigint LANGUAGE sql AS 'SELECT 1::bigint'")
            connection.execute("GRANT USAGE ON SCHEMA net TO PUBLIC")
            connection.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON net.http_request_queue,net._http_response TO PUBLIC")
            connection.execute("GRANT EXECUTE ON FUNCTION net.http_post(text) TO PUBLIC")
            connection.execute("CREATE TABLE public.synthetic_outbox(secret text)")
        job_id = jobs.save(JobPosting("Student engineer", "Synthetic", "Israel", "https://example.test/data-api"))
        yield {"target": target, "roles": roles, "owner_uuid": owner_uuid, "foreign_uuid": foreign_uuid,
               "job_id": job_id, "jobs": jobs, "migration": migration,
               "authenticator_dsn": test_database_uri(target, user=roles["authenticator"], password=password)}
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in roles.values():
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def api_connect(data, subject=None, *, role="ajob_cloud_api", claims=None):
    connection = psycopg.connect(data["target"])
    connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(data["roles"][role])))
    if claims is None:
        claims = {"sub": subject if subject is not None else data["owner_uuid"],
                  "role": data["roles"]["ajob_cloud_api"]}
    connection.execute("SELECT set_config('request.jwt.claims',%s,false)", (json.dumps(claims),))
    connection.commit()
    return connection


def rpc(data, name, *args, subject=None):
    with api_connect(data, subject) as connection:
        return call(connection, name, *args)


def call(connection, name, *args):
    query = sql.SQL("SELECT ajob_api.{}({})").format(sql.Identifier(name), sql.SQL(",").join(sql.Placeholder() for _ in args))
    return connection.execute(query, args).fetchone()[0]


def test_real_owner_rpc_contract_status_and_notes(data_api_database):
    data = data_api_database
    job_id = data["job_id"]
    assert rpc(data, "ajob_preflight") == {"ready": True, "version": 1}
    jobs = rpc(data, "ajob_jobs")
    assert job_id in {j["id"] for j in jobs}
    assert set(jobs[0]) == {"id", "title", "company", "location", "job_url", "description", "requirements", "source", "discovered_at", "is_relevant", "status"}
    assert rpc(data, "ajob_job", job_id)["id"] == job_id
    assert rpc(data, "ajob_job", -1) is None
    assert rpc(data, "ajob_status", job_id, "applied", "new")["status"] == "applied"
    assert rpc(data, "ajob_status", job_id, "reviewed", "new") is None
    assert rpc(data, "ajob_status", -1, "new") is None
    assert rpc(data, "ajob_note_save", job_id, " \t synthetic note\n ") == "synthetic note"
    assert rpc(data, "ajob_notes", [job_id]) == {str(job_id): "synthetic note"}
    assert rpc(data, "ajob_note_save", job_id, " \t\n ") == ""
    assert rpc(data, "ajob_notes", [job_id]) == {}
    assert rpc(data, "ajob_notes", []) == {}
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        rpc(data, "ajob_note_save", job_id, "x" * 2001)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        rpc(data, "ajob_status", job_id, "forged")


@pytest.mark.parametrize("claims", [{}, {"sub": "foreign"}, {"role": "authenticated"}, {"sub": "owner", "role": "authenticated"}])
def test_real_invalid_claims_fail_closed(data_api_database, claims):
    data = data_api_database
    claims = {k: data["owner_uuid"] if v == "owner" else data["foreign_uuid"] if v == "foreign" else v for k, v in claims.items()}
    with api_connect(data, claims=claims) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            call(connection, "ajob_preflight")


@pytest.mark.parametrize("name,args", [
    ("ajob_jobs", ()), ("ajob_job", (1,)), ("ajob_status", (1,"applied")),
    ("ajob_notes", ([1],)), ("ajob_note_save", (1,"forbidden")),
    ("ajob_dismiss", (1,"dismiss")), ("ajob_login_reserve", (10000.,)),
    ("ajob_session_create", ("0"*64,"csrf",10000.,11000.)),
    ("ajob_session_get", ("0"*64,10000.,60)), ("ajob_session_revoke", ("0"*64,)),
    ("ajob_preflight", ()),
])
def test_real_foreign_subject_denied_every_rpc(data_api_database, name, args):
    data = data_api_database
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rpc(data, name, *args, subject=data["foreign_uuid"])


def test_real_anon_and_direct_table_extension_access_denied(data_api_database):
    data = data_api_database
    with api_connect(data, role="anon") as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            call(connection, "ajob_preflight")
    for table in ("public.job_postings", "public.synthetic_outbox", "ajob_private.personal_notes",
                  "ajob_private.dashboard_sessions", "ajob_private.data_api_owner",
                  "net.sensitive", "vault.sensitive", "cron.sensitive"):
        with api_connect(data) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(sql.SQL("SELECT * FROM {}").format(sql.SQL(table)))
    with api_connect(data) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT ajob_private.custom_access_token_hook('{}')")
    with api_connect(data) as connection:
        assert connection.execute("SELECT net.http_post('https://example.invalid')").fetchone()[0] == 1
        assert connection.execute("SELECT * FROM net.http_request_queue").fetchall() == []
        # These inherited privileges are intentionally not 'fixed' by revoking
        # managed extension grants; real gateway tests deny the net profile.
        assert connection.execute("SELECT has_table_privilege(current_user,'net.http_request_queue','INSERT')").fetchone()[0]


def test_real_hook_preserves_claims_maps_only_approved_subject(data_api_database):
    data = data_api_database
    for subject in (data["owner_uuid"], data["foreign_uuid"]):
        claims = {"sub": subject, "role": "authenticated", "aud": "authenticated", "exp": 123456,
                  "app_metadata": {"synthetic": True}, "aal": "aal1"}
        event = {"user_id": subject, "claims": claims, "authentication_method": "password"}
        with api_connect(data, role="supabase_auth_admin") as connection:
            returned = connection.execute("SELECT ajob_private.custom_access_token_hook(%s::jsonb)", (json.dumps(event),)).fetchone()[0]
        expected_role = data["roles"]["ajob_cloud_api"] if subject == data["owner_uuid"] else "authenticated"
        assert returned == {**event, "claims": {**claims, "role": expected_role}}
    # A nonowner's pre-existing custom claims/role are preserved. The RPC subject
    # guard still denies that identity even if another hook assigned this role.
    event = {"user_id": data["foreign_uuid"], "claims": {"sub": data["owner_uuid"], "role": data["roles"]["ajob_cloud_api"]}}
    with api_connect(data, role="supabase_auth_admin") as connection:
        result = connection.execute("SELECT ajob_private.custom_access_token_hook(%s::jsonb)", (json.dumps(event),)).fetchone()[0]
    assert result == event


def test_real_rls_identity_and_function_hardening(data_api_database):
    data = data_api_database
    with psycopg.connect(data["target"]) as connection:
        functions = connection.execute("""SELECT p.prosecdef,p.proconfig,r.rolname FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
            WHERE n.nspname='ajob_api'""").fetchall()
        assert len(functions) == 11
        for definer, settings, owner in functions:
            assert definer and owner == data["roles"]["ajob_api_owner"]
            assert {"search_path=pg_catalog", "statement_timeout=10s", "lock_timeout=3s"} <= set(settings)
        assert connection.execute("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND policyname='synthetic_scanner'").fetchone()[0] == 1
        for table in ("personal_notes", "job_dismissal_history", "dashboard_sessions", "dashboard_login_throttle", "data_api_owner"):
            assert connection.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("ajob_private."+table,)).fetchone() == (True, True)
        for schema in ("ajob_api", "ajob_private"):
            assert not connection.execute("SELECT has_schema_privilege(%s,%s,'CREATE')", (data["roles"]["ajob_api_owner"],schema)).fetchone()[0]
    # Exercise actual policies independently of the function's explicit guard.
    with api_connect(data, subject=data["foreign_uuid"], role="ajob_api_owner") as connection:
        assert connection.execute("SELECT count(*) FROM public.job_postings").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM ajob_private.personal_notes").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("INSERT INTO ajob_private.personal_notes VALUES(%s,'foreign')", (data["job_id"],))


def test_real_concurrent_dismiss_restore_and_rollback(data_api_database):
    data = data_api_database
    job_id = data["job_id"]
    rpc(data, "ajob_status", job_id, "applied")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: rpc(data, "ajob_dismiss", job_id,"dismiss"), range(16)))
    assert sum(result["changed"] for result in results) == 1
    assert all(result["status"] == "ignored" for result in results)
    assert rpc(data, "ajob_dismiss", job_id,"restore") == {"status":"applied","changed":True}
    connection = api_connect(data)
    try:
        call(connection,"ajob_dismiss",job_id,"dismiss")
        connection.rollback()
    finally:
        connection.close()
    assert rpc(data,"ajob_job",job_id)["status"] == "applied"
    with psycopg.connect(data["target"]) as connection:
        assert connection.execute("SELECT count(*) FROM ajob_private.job_dismissal_history WHERE job_id=%s", (job_id,)).fetchone()[0] == 0
    assert rpc(data,"ajob_dismiss",-1,"dismiss") is None


def test_real_persistent_throttle_session_limits_expiry_and_rollback(data_api_database):
    data = data_api_database
    with psycopg.connect(data["target"]) as connection:
        connection.execute("DELETE FROM ajob_private.dashboard_sessions")
        connection.execute("UPDATE ajob_private.dashboard_login_throttle SET attempts=0,window_start=0")
    with ThreadPoolExecutor(max_workers=8) as pool:
        reserved = list(pool.map(lambda _: rpc(data,"ajob_login_reserve",10000.), range(16)))
    assert sum(reserved) == 5
    assert not rpc(data,"ajob_login_reserve",10899.)
    assert rpc(data,"ajob_login_reserve",10900.)
    def create(index):
        token = hashlib.sha256(str(index).encode()).hexdigest()
        rpc(data,"ajob_session_create",token,"csrf",10000.+index,11000.)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(create,range(12)))
    with psycopg.connect(data["target"]) as connection:
        tokens = connection.execute("SELECT token_hash FROM ajob_private.dashboard_sessions ORDER BY token_hash").fetchall()
    assert len(tokens) == 5
    with pytest.raises(psycopg.errors.CheckViolation):
        rpc(data,"ajob_session_create","invalid-hash","csrf",10020.,11000.)
    with psycopg.connect(data["target"]) as connection:
        assert connection.execute("SELECT token_hash FROM ajob_private.dashboard_sessions ORDER BY token_hash").fetchall() == tokens
    token = hashlib.sha256(b"expiry").hexdigest()
    rpc(data,"ajob_session_create",token,"special",10000.,10200.)
    assert rpc(data,"ajob_session_get",token,10010.,60) == {"csrf_token":"special"}
    assert rpc(data,"ajob_session_get",token,10070.,60) is None
    assert rpc(data,"ajob_session_get",token,10200.,9999) is None
    rpc(data,"ajob_session_revoke",token)
    assert rpc(data,"ajob_session_get",token,10020.,60) is None


def test_real_pagination_is_complete_and_bounded(data_api_database):
    data = data_api_database
    with psycopg.connect(data["target"]) as connection:
        connection.execute("""INSERT INTO public.job_postings(title,company,location,job_url,discovered_at)
            SELECT 'Synthetic','Test','Israel','https://example.test/page/'||n,now() FROM generate_series(1,405) n""")
    rows, after = [], 0
    while True:
        page = rpc(data,"ajob_jobs",after,200)
        rows.extend(page)
        if len(page)<200:
            break
        after = page[-1]["id"]
    assert len(rows) >= 406
    assert [row["id"] for row in rows] == sorted({row["id"] for row in rows})
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        rpc(data,"ajob_jobs",0,201)


def test_real_pg17_non_superuser_standalone_migration_and_reapplication(data_api_database):
    """The real migration administrator has CREATEROLE, never superuser/BYPASSRLS.

    A fresh database proves no dependency on the legacy dashboard migration.
    PG16+ CREATE ROLE supplies ADMIN TRUE / INHERIT FALSE / SET FALSE; both
    apply and reapply must restore those exact grantor-scoped memberships.
    """
    data = data_api_database
    admin_dsn = os.environ["AJOB_TEST_POSTGRES_DSN"]
    with psycopg.connect(admin_dsn) as admin:
        if admin.info.server_version < 160000:
            pytest.skip("Grantor-scoped SET/INHERIT role options require PostgreSQL 16+")
    suffix = uuid.uuid4().hex[:20]
    database, migrator = "ajob_migration_test_" + suffix, "ajob_test_migrator_" + suffix
    new_roles = {**data["roles"], "ajob_cloud_api": "ajob_test_api_" + suffix,
                 "ajob_api_owner": "ajob_test_owner_" + suffix}
    password = uuid.uuid4().hex
    migration = (ROOT / "deployment/data-api.sql").read_text(encoding="utf-8")
    for original in sorted(new_roles, key=len, reverse=True):
        migration = migration.replace(original,new_roles[original])
    target = test_database_uri(admin_dsn, dbname=database)
    migration_dsn = test_database_uri(target,user=migrator,password=password)
    managed_roles = [data["roles"][name] for name in ("anon","authenticated","service_role","authenticator","supabase_auth_admin")]
    role_query = "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolcanlogin,rolinherit,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname"
    membership_query = """SELECT roleid::regrole::text,member::regrole::text,grantor::regrole::text,
        admin_option,inherit_option,set_option FROM pg_auth_members
        WHERE roleid=%s::regrole AND member=%s::regrole ORDER BY grantor"""
    managed_membership_query = """SELECT granted.rolname,member_role.rolname,grantor_role.rolname,
        m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m
        JOIN pg_roles granted ON granted.oid=m.roleid JOIN pg_roles member_role ON member_role.oid=m.member
        JOIN pg_roles grantor_role ON grantor_role.oid=m.grantor
        WHERE (granted.rolname=ANY(%s) OR member_role.rolname=ANY(%s))
        AND granted.rolname<>%s ORDER BY granted.rolname,member_role.rolname,grantor_role.rolname"""
    with psycopg.connect(admin_dsn,autocommit=True) as admin:
        managed_before = admin.execute(role_query,(managed_roles,)).fetchall()
        managed_memberships_before = admin.execute(managed_membership_query,(managed_roles,managed_roles,new_roles["ajob_cloud_api"])).fetchall()
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT CREATEROLE NOSUPERUSER NOCREATEDB NOBYPASSRLS PASSWORD {}").format(sql.Identifier(migrator),sql.Literal(password)))
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database),sql.Identifier(migrator)))
    try:
        jobs = PostgresJobRepository(migration_dsn)
        jobs.initialize()
        with psycopg.connect(migration_dsn,autocommit=True) as connection:
            assert connection.execute("SELECT rolsuper,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False,True,False)
            connection.execute("CREATE SCHEMA auth")
            connection.execute("""CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
                SELECT (current_setting('request.jwt.claims',true)::jsonb->>'sub')::uuid $$""")
            connection.execute("ALTER TABLE public.job_postings ENABLE ROW LEVEL SECURITY")
            connection.execute("CREATE POLICY untouched_scanner ON public.job_postings TO PUBLIC USING(false)")
            connection.execute(migration)
            first_membership = connection.execute(membership_query,(new_roles["ajob_api_owner"],migrator)).fetchall()
            assert first_membership and any(row[3] for row in first_membership)
            assert all(not row[4] and not row[5] for row in first_membership)
            assert not connection.execute("SELECT pg_has_role(current_user,%s,'SET')",(new_roles["ajob_api_owner"],)).fetchone()[0]
            connection.execute(migration)
            assert connection.execute(membership_query,(new_roles["ajob_api_owner"],migrator)).fetchall() == first_membership
            owners = connection.execute("""SELECT r.rolname,count(*) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
                WHERE n.nspname='ajob_api' GROUP BY r.rolname""").fetchall()
            assert owners == [(new_roles["ajob_api_owner"],11)]
            for schema in ("ajob_api","ajob_private"):
                assert not connection.execute("SELECT has_schema_privilege(%s,%s,'CREATE')",(new_roles["ajob_api_owner"],schema)).fetchone()[0]
            assert connection.execute("SELECT count(*) FROM pg_policies WHERE policyname='untouched_scanner'").fetchone()[0] == 1
            # FORCE RLS deliberately prevents even this non-super table owner
            # from inserting until the explicit manual seed transaction.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO ajob_private.data_api_owner VALUES(true,%s)",(data["owner_uuid"],))
            with connection.transaction():
                connection.execute("ALTER TABLE ajob_private.data_api_owner NO FORCE ROW LEVEL SECURITY")
                connection.execute("INSERT INTO ajob_private.data_api_owner VALUES(true,%s)",(data["owner_uuid"],))
                connection.execute("ALTER TABLE ajob_private.data_api_owner FORCE ROW LEVEL SECURITY")
            # Seed is preserved by reapplication; private state is never reset.
            connection.execute(migration)
            assert connection.execute(membership_query,(new_roles["ajob_api_owner"],migrator)).fetchall() == first_membership
        scoped_data = {**data,"target":target,"roles":new_roles}
        assert rpc(scoped_data,"ajob_preflight") == {"ready":True,"version":1}
        with psycopg.connect(admin_dsn) as admin:
            assert admin.execute(role_query,(managed_roles,)).fetchall() == managed_before
            # Only the required new caller -> authenticator membership is added;
            # every pre-existing managed-role grant and option stays identical.
            assert admin.execute(managed_membership_query,(managed_roles,managed_roles,new_roles["ajob_cloud_api"])).fetchall() == managed_memberships_before
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in (new_roles["ajob_cloud_api"],new_roles["ajob_api_owner"],migrator):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
