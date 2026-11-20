from pathlib import Path
import re
import sqlite3

import pytest

import yaml


def test_manual_backup_is_encrypted_scoped_and_fail_closed():
    raw = Path(".github/workflows/manual-backup.yml").read_text()
    workflow = yaml.safe_load(raw)
    # PyYAML's YAML1.1 represents the Actions 'on' key as True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["backup"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    steps = job["steps"]
    export = steps[1]["run"]
    assert "set -euo pipefail" in export
    assert "default_transaction_read_only=on" in export
    assert '[[ "$inventory" != "8" ]]' in export
    assert "pg_dump --format=custom --no-owner --no-acl" in export
    assert "--schema=public --schema='ajob_*'" in export
    assert '2>/dev/null | age -r "$RECIPIENT"' in export
    assert "--enable-row-security" not in export.split("# pg_dump")[0]
    upload = steps[2]
    assert upload["with"]["path"] == "${{ runner.temp }}/ajob-backup/"
    assert upload["with"]["retention-days"] == 1
    assert "continue-on-error" not in raw
    assert "--file=" not in export and "pg_dump -f" not in export


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_backup_inventory_requires_only_mandatory_tables(optional, missing):
    raw = Path(".github/workflows/manual-backup.yml").read_text()
    query = re.search(r'"(SELECT count\(\*\) FROM information_schema.tables[^"]+)"', raw)[1]
    mandatory = ["job_postings", "scan_runs", "scan_source_metrics", "scan_windows",
                 "delivery_leases", "dispatcher_state", "notification_batches", "notification_outbox"]
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS information_schema")
        db.execute("CREATE TABLE information_schema.tables(table_schema,table_name)")
        names = mandatory[:-1] if missing else mandatory
        names += ["job_closure_dismissals", "scan_alert_receipts"] if optional else []
        db.executemany("INSERT INTO information_schema.tables VALUES ('public',?)", [(name,) for name in names])
        assert (str(db.execute(query).fetchone()[0]) == "8") is (not missing)
