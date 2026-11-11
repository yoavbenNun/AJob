"""Offline checks for the manually dispatched, read-only DB diagnostic."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/database-connection-check.yml'


class FakeConnection:
    def __init__(self, tables=(), version=None, read_only='on', tls=True, failure=False, schema_usage=True):
        self.info = SimpleNamespace(host='example.pooler.supabase.com', port=5432)
        self.pgconn = SimpleNamespace(ssl_in_use=tls)
        self.tables, self.version, self.read_only, self.failure = tables, version, read_only, failure
        self.schema_usage = schema_usage
        self.active = False
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        compact = ' '.join(sql.split())
        self.commands.append(compact)
        if compact == 'BEGIN READ ONLY':
            assert not self.active
            self.active = True
        elif compact == 'COMMIT':
            assert self.active
            self.active = False
        else:
            assert self.active, 'All metadata reads must be in the read-only transaction'
            assert compact.startswith(('SELECT ', 'SET LOCAL statement_timeout'))
            if self.failure:
                raise RuntimeError('sensitive connection text must never be shown')
        if compact.startswith('SELECT current_user'):
            row = ('postgres', 'postgres', 'postgres', 'public', '17.11', self.read_only, self.schema_usage)
        elif 'FROM pg_catalog.pg_class' in compact:
            row = self.tables
        elif compact.startswith('SELECT version FROM public.scan_alert_schema'):
            row = (self.version,) if self.version is not None else None
        else:
            row = None
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: row)


def run_diagnostic(monkeypatch, connection):
    doc = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    job = doc['jobs']['connection']
    trigger = doc.get('on', doc.get(True))  # PyYAML 1.1 treats 'on' as a boolean.
    assert set(trigger) == {'workflow_dispatch'}
    assert doc['permissions'] == {'contents': 'read'}
    assert job['timeout-minutes'] == 5
    step = next(step for step in job['steps'] if step.get('name') == 'Read-only connection diagnostic')
    script = step['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    def connect(*args, **kwargs):
        assert kwargs['autocommit'] is True
        return connection
    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(connect=connect))
    monkeypatch.setenv('DATABASE_URL', 'synthetic-private-value')
    exec(compile(script, 'diagnostic', 'exec'), {})


def test_missing_tables_are_read_only_and_reported(monkeypatch, capsys):
    connection = FakeConnection(tables=[])
    run_diagnostic(monkeypatch, connection)
    output = capsys.readouterr().out
    result = json.loads(output.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['transaction_read_only'] == 'on'
    assert result['alert_tables']['scan_alert_schema'] == {'exists': False}
    assert result['alert_tables']['scan_alert_receipts'] == {'exists': False}
    assert result['schema_version'] is None and result['schema_version_status'] == 'table_absent'
    assert connection.commands[0] == 'BEGIN READ ONLY' and connection.commands[-1] == 'COMMIT'
    assert not any('SELECT version FROM public.scan_alert_schema' in x for x in connection.commands)
    assert 'synthetic-private-value' not in output


def test_existing_tables_permissions_rls_and_version(monkeypatch, capsys):
    rows = [('scan_alert_schema', True, False, True, False, False, 1),
            ('scan_alert_receipts', True, False, True, True, True, 3)]
    connection = FakeConnection(tables=rows, version=1)
    run_diagnostic(monkeypatch, connection)
    result = json.loads(capsys.readouterr().out.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['schema_version'] == 1 and result['schema_version_status'] == 'visible'
    assert result['alert_tables']['scan_alert_receipts'] == {
        'exists': True, 'rls': True, 'force_rls': False,
        'select': True, 'insert': True, 'update': True, 'policy_count': 3}
    assert result['endpoint_classification'] == 'supabase_session_pooler'


def test_inaccessible_version_is_unknown_without_attempting_select(monkeypatch, capsys):
    connection = FakeConnection(tables=[('scan_alert_schema', True, True, False, False, False, 0)])
    run_diagnostic(monkeypatch, connection)
    result = json.loads(capsys.readouterr().out.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['schema_version'] is None and result['schema_version_status'] == 'select_not_granted'
    assert not any(x.startswith('SELECT version FROM public.scan_alert_schema') for x in connection.commands)


def test_version_without_schema_usage_is_unknown(monkeypatch, capsys):
    connection = FakeConnection(tables=[('scan_alert_schema', True, False, True, False, False, 0)],
                                schema_usage=False)
    run_diagnostic(monkeypatch, connection)
    result = json.loads(capsys.readouterr().out.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['schema_version_status'] == 'schema_usage_not_granted'
    assert not any(x.startswith('SELECT version FROM public.scan_alert_schema') for x in connection.commands)


def test_version_row_hidden_or_missing_remains_unknown(monkeypatch, capsys):
    connection = FakeConnection(tables=[('scan_alert_schema', True, False, True, False, False, 1)])
    run_diagnostic(monkeypatch, connection)
    result = json.loads(capsys.readouterr().out.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['schema_version'] is None
    assert result['schema_version_status'] == 'row_absent_or_rls_hidden'


@pytest.mark.parametrize('connection', [FakeConnection(read_only='off'), FakeConnection(failure=True), FakeConnection(tls=False)])
def test_unverified_or_failed_diagnostic_fails_closed_without_secrets(monkeypatch, capsys, connection):
    with pytest.raises(SystemExit) as stopped:
        run_diagnostic(monkeypatch, connection)
    assert stopped.value.code == 1
    output = capsys.readouterr().out
    assert 'synthetic-private-value' not in output
    assert 'sensitive connection text' not in output

def test_real_postgres_metadata_queries_in_disposable_database(cluster, monkeypatch, capsys):
    """CI loopback database only; no application migration or production access."""
    import psycopg
    with psycopg.connect(cluster, autocommit=True) as setup:
        setup.execute('CREATE TABLE public.scan_alert_schema (id integer, version integer)')
        setup.execute('INSERT INTO public.scan_alert_schema VALUES (1, 1)')
        setup.execute('CREATE TABLE public.scan_alert_receipts (id integer)')
        setup.execute('ALTER TABLE public.scan_alert_receipts ENABLE ROW LEVEL SECURITY')
        setup.execute('CREATE POLICY synthetic_receipt_read ON public.scan_alert_receipts FOR SELECT USING (true)')

    class RealConnection:
        def __init__(self):
            self.connection = psycopg.connect(cluster, autocommit=True)
            self.info = self.connection.info
            # CI PostgreSQL is loopback-only; TLS is tested separately above.
            self.pgconn = SimpleNamespace(ssl_in_use=True)
            self.commands = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()
            return False

        def execute(self, sql):
            self.commands.append(' '.join(sql.split()))
            return self.connection.execute(sql)

    connection = RealConnection()
    run_diagnostic(monkeypatch, connection)
    result = json.loads(capsys.readouterr().out.split('DATABASE_DIAGNOSTIC ', 1)[1])
    assert result['transaction_read_only'] == 'on'
    assert result['schema_version'] == 1
    assert result['alert_tables']['scan_alert_receipts']['rls'] is True
    assert result['alert_tables']['scan_alert_receipts']['policy_count'] == 1
    assert connection.commands[0] == 'BEGIN READ ONLY'
    assert connection.commands[-1] == 'COMMIT'


# The fixture refuses non-loopback hosts and creates a disposable test database.
from test_postgres_scan_integration import cluster  # noqa: E402,F401

def test_diagnostic_and_scanner_use_the_same_repository_secret_reference():
    root = WORKFLOW.parents[2]
    diagnostic = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']['connection']
    scanner = yaml.safe_load((root / '.github/workflows/scheduled-scan.yml').read_text(encoding='utf-8'))['jobs']['scan']
    step = next(step for step in diagnostic['steps'] if step.get('name') == 'Read-only connection diagnostic')
    assert 'environment' not in diagnostic and 'environment' not in scanner
    assert step['env']['DATABASE_URL'] == scanner['env']['DATABASE_URL'] == '${{ secrets.DATABASE_URL }}'
