"""Real auth SQL/concurrency tests, only in disposable loopback PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib

import psycopg
from psycopg import sql
import pytest

from test_cloud_private_postgres import cloud_database, scoped
from src.storage.postgres_job_repository import PostgresJobRepository
from src.web.cloud_auth import AuthSession, PostgresAuthStore


@pytest.fixture(scope="module")
def auth_database(cloud_database):
    target, role, denied, _ = cloud_database
    migration = (Path(__file__).resolve().parents[1] / "deployment/cloud-auth.sql").read_text()
    migration = migration.replace("ajob_dashboard", role)
    with psycopg.connect(target, autocommit=True) as connection:
        connection.execute(migration)
        connection.execute(migration)
    return target, role, denied


def store_for(target, role):
    return PostgresAuthStore(scoped(PostgresJobRepository, target, role)._connect)


@pytest.fixture
def auth_store(auth_database):
    target, role, _ = auth_database
    store = store_for(target, role)
    with store._connect() as connection:
        connection.execute("DELETE FROM ajob_private.dashboard_sessions")
        connection.execute("UPDATE ajob_private.dashboard_login_throttle SET attempts=0, window_start=0")
    return store


def test_real_persistent_atomic_global_throttle(auth_database, auth_store):
    target, role, _ = auth_database
    auth_store.verify_schema()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: store_for(target, role).reserve_login(10000), range(24)))
    assert sum(results) == 5
    assert not store_for(target, role).reserve_login(10899)
    assert store_for(target, role).reserve_login(10900)


def test_real_session_restart_expiry_and_revoke(auth_database, auth_store):
    target, role, _ = auth_database
    token = hashlib.sha256(b"synthetic-session").hexdigest()
    auth_store.create_session(token, "synthetic-csrf", 10000, 10200)
    restarted = store_for(target, role)
    assert restarted.get_session(token, 10010, 60) == AuthSession("synthetic-csrf")
    assert restarted.get_session(token, 10070, 60) is None
    auth_store.create_session(hashlib.sha256(b"second").hexdigest(), "other-csrf", 10000, 10200)
    second = hashlib.sha256(b"second").hexdigest()
    assert restarted.get_session(second, 10050, 60)
    assert restarted.get_session(second, 10100, 60)
    assert restarted.get_session(second, 10150, 60)
    assert restarted.get_session(second, 10200, 60) is None
    restarted.revoke_session(token)
    assert auth_store.get_session(token, 10020, 60) is None


def test_real_concurrent_sessions_are_bounded(auth_database, auth_store):
    target, role, _ = auth_database
    def create(index):
        token = hashlib.sha256(f"synthetic-{index}".encode()).hexdigest()
        store_for(target, role).create_session(token, "csrf", 10000 + index, 11000)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(create, range(20)))
    with auth_store._connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM ajob_private.dashboard_sessions").fetchone()["n"] == 5


def test_real_auth_tables_rls_and_least_privilege(auth_database, auth_store):
    target, role, denied = auth_database
    for table in ("dashboard_sessions", "dashboard_login_throttle"):
        with auth_store._connect() as connection:
            row = connection.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("ajob_private." + table,)).fetchone()
            assert row == {"relrowsecurity": True, "relforcerowsecurity": True}
        blocked = store_for(target, denied)
        with blocked._connect() as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(sql.SQL("SELECT * FROM ajob_private.{}").format(sql.Identifier(table)))
    with auth_store._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE ajob_private.forbidden_auth_table (id integer)")
    with auth_store._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM ajob_private.dashboard_login_throttle")


def test_real_failed_session_insert_rolls_back_eviction(auth_store):
    for index in range(5):
        auth_store.create_session(hashlib.sha256(str(index).encode()).hexdigest(), "csrf", 10000, 11000)
    with pytest.raises(psycopg.errors.CheckViolation):
        auth_store.create_session("invalid-hash", "csrf", 10001, 11000)
    with auth_store._connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM ajob_private.dashboard_sessions").fetchone()["n"] == 5
