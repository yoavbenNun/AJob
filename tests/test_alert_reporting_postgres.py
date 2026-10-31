"""Real disposable PostgreSQL concurrency; never reads DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_postgres_scan_integration import cluster, repository
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
