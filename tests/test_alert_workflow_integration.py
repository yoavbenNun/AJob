"""Synthetic tick/CLI integration: no real scanner, SMTP or GitHub calls."""

from pathlib import Path
import shlex

import yaml

from test_window_recovery import environment, COMPANIES, SETTINGS
from test_recovery_outcomes import partial_scan
from src.scheduling import alert_reporting
from src.scheduling.window_runner import scheduled_tick
from src.storage.window_store import WindowStore


def activate(monkeypatch, repository):
    for key, value in dict(AJOB_SCAN_ALERT_DEDUP='1', GITHUB_ACTIONS='true',
        GITHUB_REPOSITORY='owner/AJob', GITHUB_REF='refs/heads/main',
        GITHUB_EVENT_NAME='schedule', GITHUB_RUN_ID='101', GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_REF='owner/AJob/.github/workflows/scheduled-scan.yml@refs/heads/main',
        GITHUB_TOKEN='synthetic-test-only').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(alert_reporting, 'verify_failure', lambda *args: True)
    store = WindowStore(repository)
    try:
        alert_reporting.prepare_alert_schema(store)
    finally:
        store.close()


def test_tick_restart_dedup_and_new_failed_recovery_never_resend_mail(environment, monkeypatch):
    repository, clock = environment
    activate(monkeypatch, repository)
    sent = []
    def tick():
        return scheduled_tick(COMPANIES, repository, SETTINGS, clock=clock,
            scanner=partial_scan, sender=lambda _, message: sent.append(message))
    assert tick() == 1  # New partial failure, pending receipt, mail accepted.
    assert tick() == 1  # Same Actions run: cannot acknowledge its own pending row.
    monkeypatch.setenv('GITHUB_RUN_ID', '102')
    assert tick() == 0  # Prior completed failure verified; no new work.
    clock.advance(minutes=6)
    assert tick() == 1  # A new failed Recovery attempt must alert again.
    monkeypatch.setenv('GITHUB_RUN_ID', '103')
    assert tick() == 0
    assert len(sent) == 1
    store = WindowStore(repository, clock)
    try:
        row = store.report(1)[0]
        assert row['outcome'] == 'FINAL_FAILURE' and row['scan_attempts'] == 2
        assert row['notification'] == 'sent' and row['notification_attempts'] == 1
    finally:
        store.close()


def test_cli_strict_health_stays_read_only_even_when_dedup_is_enabled(environment, monkeypatch):
    from src import main
    from src.storage import window_store
    repository, clock = environment
    activate(monkeypatch, repository)
    assert scheduled_tick(COMPANIES, repository, SETTINGS, clock=clock, scanner=partial_scan,
                          sender=lambda *args: None) == 1
    monkeypatch.setenv('GITHUB_RUN_ID', '102')
    monkeypatch.setattr(main, 'create_job_repository', lambda *args: repository)
    original = window_store.WindowStore
    monkeypatch.setattr(window_store, 'WindowStore', lambda repo: original(repo, clock))
    assert main.main(['scan-report', '--check']) == 1
    store = original(repository, clock)
    try:
        assert store.execute("SELECT count(*) AS n FROM scan_alert_receipts WHERE state='confirmed'").fetchone()['n'] == 0
    finally:
        store.close()
    # CLI deliberately checks seven days; this fresh fixture has no earlier
    # windows, so those UNKNOWN records must first produce a new failure.
    assert main.main(['scan-report', '--check', '--alert-check']) == 1
    monkeypatch.setenv('GITHUB_RUN_ID', '103')
    assert main.main(['scan-report', '--check', '--alert-check']) == 0
    assert main.main(['scan-report', '--check']) == 1
    assert main.main(['scan-report', '--alert-check']) == 1


def test_workflow_gates_both_exit_paths_and_preserves_named_failure_proof():
    from src.scheduling.github_failure_receipt import FAILURE_STEPS
    workflow = yaml.safe_load(Path('.github/workflows/scheduled-scan.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    job = workflow['jobs']['scan']
    assert job['env']['AJOB_SCAN_ALERT_DEDUP'] == '${{ vars.AJOB_SCAN_ALERT_DEDUP }}'
    commands = {step['name']: step['run'] for step in job['steps'] if step.get('name') in FAILURE_STEPS}
    assert set(commands) == FAILURE_STEPS
    for command in commands.values():
        argv = shlex.split(command)
        assert 'GITHUB_TOKEN' in argv and 'AJOB_SCAN_ALERT_DEDUP' in argv
        assert 'GITHUB_RUN_ID' in argv and 'GITHUB_RUN_ATTEMPT' in argv
    assert '--alert-check' in commands['Publish business status independently of workflow result']
    assert all('continue-on-error' not in step for step in job['steps'])
