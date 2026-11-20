from pathlib import Path

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
    assert "count(*) = 9" in export
    assert "pg_dump --format=custom --no-owner --no-acl" in export
    assert "--schema=public --schema='ajob_*'" in export
    assert '2>/dev/null | age -r "$RECIPIENT"' in export
    assert "--enable-row-security" not in export.split("# pg_dump")[0]
    upload = steps[2]
    assert upload["with"]["path"] == "${{ runner.temp }}/ajob-backup/"
    assert upload["with"]["retention-days"] == 1
    assert "continue-on-error" not in raw
    assert "--file=" not in export and "pg_dump -f" not in export
