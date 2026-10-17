import sys
import types
from unittest.mock import MagicMock

import pytest

from src.storage.postgres_job_repository import PostgresJobRepository


def test_dashboard_schema_check_requires_only_read_access(monkeypatch) -> None:
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value

    def read_only_execute(query):
        assert query.startswith("SELECT ")
        assert "is_relevant, status FROM job_postings LIMIT 0" in query

    cursor.execute.side_effect = read_only_execute
    repository = PostgresJobRepository("postgresql://db.example/jobs")
    monkeypatch.setattr(repository, "_connect", lambda: connection)
    repository.verify_schema()
    cursor.execute.assert_called_once()


def test_dashboard_schema_check_does_not_hide_missing_schema(monkeypatch) -> None:
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("missing status column")
    repository = PostgresJobRepository("postgresql://db.example/jobs")
    monkeypatch.setattr(repository, "_connect", lambda: connection)
    with pytest.raises(RuntimeError, match="missing status column"):
        repository.verify_schema()


@pytest.mark.parametrize(
    "database_url",
    (
        "",
        "https://example.com/database",
        "postgresql:///jobs",
        "postgresql://database.example",
    ),
)
def test_postgres_repository_rejects_incomplete_connection_urls(database_url: str) -> None:
    with pytest.raises(ValueError, match="host and database name"):
        PostgresJobRepository(database_url)


def test_postgres_repository_uses_bounded_connection_timeout(monkeypatch) -> None:
    captured = {}
    connection = object()

    def connect(database_url, **kwargs):
        captured["database_url"] = database_url
        captured.update(kwargs)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    repository = PostgresJobRepository("postgresql://db.example/jobs")

    assert repository._connect() is connection
    assert captured["database_url"] == "postgresql://db.example/jobs"
    assert captured["connect_timeout"] == 10
    assert captured["row_factory"] is rows.dict_row


def test_postgres_initialize_never_deletes_existing_rows(monkeypatch) -> None:
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            queries.append(" ".join(query.split()))

        def fetchall(self):
            return []

        def executemany(self, query, parameters):
            queries.append(" ".join(query.split()))

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    repository = PostgresJobRepository("postgresql://db.example/jobs")
    monkeypatch.setattr(repository, "_connect", lambda: Connection())

    repository.initialize()

    assert any("CREATE INDEX IF NOT EXISTS" in query for query in queries)
    assert not any("DELETE FROM" in query for query in queries)


def test_postgres_url_migration_refuses_ambiguous_duplicate_rows() -> None:
    class Cursor:
        def execute(self, query):
            pass

        def fetchall(self):
            return [
                {"id": 1, "job_url": "https://example.com/job?a=1&b=2"},
                {"id": 2, "job_url": "https://example.com/job?b=2&a=1"},
            ]

    with pytest.raises(RuntimeError, match="manual merge required"):
        PostgresJobRepository._canonicalize_existing_urls(Cursor())


def test_postgres_url_migration_updates_safe_legacy_url() -> None:
    updates = []

    class Cursor:
        def execute(self, query):
            pass

        def fetchall(self):
            return [{"id": 7, "job_url": "https://example.com/job?b=2&a=1"}]

        def executemany(self, query, parameters):
            updates.extend(parameters)

    PostgresJobRepository._canonicalize_existing_urls(Cursor())

    assert updates == [("https://example.com/job?a=1&b=2", 7)]
