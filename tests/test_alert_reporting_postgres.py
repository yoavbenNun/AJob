"""Real disposable PostgreSQL concurrency; never reads DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

import psycopg
from psycopg import sql
import pytest

from test_postgres_scan_integration import cluster, repository, ScopedRepository
from src.storage.scan_store import ScanStore
from src.scheduling.alert_reporting import AlertContext, AlertLedger, prepare_alert_schema


def test_postgres_concurrent_unconfirmed_failures_and_restart(repository):
    store = ScanStore(repository)
    prepare_alert_schema(store)
    store.close()
    barrier = Barrier(2)
    event = dict(kind='window', slot='synthetic', outcome='FINAL_FAILURE', scan_attempts=2)
    def observe(run_id):
        connection = ScanStore(repository)
        try:
            barrier.wait(timeout=10)
            return AlertLedger(connection, AlertContext('owner/AJob', run_id, 1),
                               lambda *args: False).check([event])
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(observe, ['101', '102'])) == [1, 1]
    fresh = ScanStore(repository)
    try:
        assert fresh.execute('SELECT count(*) AS n FROM scan_alert_receipts').fetchone()['n'] == 2
        assert AlertLedger(fresh, AlertContext('owner/AJob', '103', 1),
                           lambda *args: True).check([event]) == 0
        assert fresh.execute("SELECT count(*) AS n FROM scan_alert_receipts WHERE state='confirmed'").fetchone()['n'] == 1
    finally:
        fresh.close()


def test_runtime_role_needs_rls_policies_not_just_table_grants(repository):
    """Only the fixture's disposable loopback DB/unique role is ever modified."""
    role = "aj008_" + uuid.uuid4().hex
    admin = ScanStore(repository)
    prepare_alert_schema(admin)
    identity, schema = sql.Identifier(role), sql.Identifier(repository.schema)
    tables = ("scan_alert_schema", "scan_alert_receipts")
    policies = (("scan_alert_schema", "aj008_version"),
                ("scan_alert_receipts", "aj008_read"),
                ("scan_alert_receipts", "aj008_insert"),
                ("scan_alert_receipts", "aj008_update"))
    admin.connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(identity))
    restricted = None
    try:
        admin.connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, identity))
        for table in tables:
            rights = sql.SQL("SELECT") if table == "scan_alert_schema" else sql.SQL("SELECT, INSERT, UPDATE")
            admin.connection.execute(sql.SQL("GRANT {} ON {}.{} TO {}").format(
                rights, schema, sql.Identifier(table), identity))
        restricted = ScanStore(ScopedRepository(repository.database_url, repository.schema, role))
        event = dict(kind="window", slot="synthetic", outcome="FINAL_FAILURE")
        with pytest.raises(RuntimeError, match="Alert schema not approved"):
            AlertLedger(restricted, AlertContext("owner/AJob", "101", 1),
                        lambda *args: True).check([event])
        admin.connection.execute(sql.SQL(
            "CREATE POLICY aj008_version ON {}.scan_alert_schema FOR SELECT TO {} USING (true)"
        ).format(schema, identity))
        for name, command, clause in (
            ("aj008_read", "SELECT", "USING (repository='owner/AJob')"),
            ("aj008_insert", "INSERT", "WITH CHECK (repository='owner/AJob')"),
            ("aj008_update", "UPDATE", "USING (repository='owner/AJob') WITH CHECK (repository='owner/AJob')"),
        ):
            admin.connection.execute(sql.SQL("CREATE POLICY {} ON {}.scan_alert_receipts FOR {} TO {} {}").format(
                sql.Identifier(name), schema, sql.SQL(command), identity, sql.SQL(clause)))
        assert AlertLedger(restricted, AlertContext("owner/AJob", "101", 1),
                           lambda *args: True).check([event]) == 1
        assert AlertLedger(restricted, AlertContext("owner/AJob", "102", 1),
                           lambda *args: True).check([event]) == 0
        def no_new_api_call(*args):
            raise AssertionError("Confirmed receipt must remain readable")
        assert AlertLedger(restricted, AlertContext("owner/AJob", "103", 1),
                           no_new_api_call).check([event]) == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            AlertLedger(restricted, AlertContext("other/AJob", "104", 1),
                        lambda *args: True).check([event])
    finally:
        if restricted:
            restricted.close()
        for table, policy in policies:
            admin.connection.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}.{}").format(
                sql.Identifier(policy), schema, sql.Identifier(table)))
            admin.connection.execute(sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
                schema, sql.Identifier(table), identity))
        admin.connection.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(schema, identity))
        admin.connection.execute(sql.SQL("DROP ROLE {}").format(identity))
        admin.close()
