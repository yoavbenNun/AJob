"""Restricted cloud role gates on disposable PostgreSQL only."""
import pytest
from psycopg import sql
import psycopg

from test_cloud_private_postgres import cloud_database, scoped
from src.storage.postgres_job_repository import PostgresJobRepository
from src.web.cloud import verify_dashboard_role


@pytest.mark.parametrize('grant', ['UPDATE', 'DELETE', 'INSERT', 'UPDATE(title)'])
def test_rejects_excessive_job_permissions_individually(cloud_database, grant):
    target, role, _, _=cloud_database
    repo=scoped(PostgresJobRepository,target,role)
    with repo._connect() as connection:
        verify_dashboard_role(connection)
    with psycopg.connect(target) as admin:
        admin.execute(sql.SQL('GRANT {} ON public.job_postings TO {}').format(sql.SQL(grant),sql.Identifier(role)))
    try:
        with repo._connect() as connection, pytest.raises(ValueError,match='restricted'):
            verify_dashboard_role(connection)
    finally:
        with psycopg.connect(target) as admin:
            admin.execute(sql.SQL('REVOKE {} ON public.job_postings FROM {}').format(sql.SQL(grant),sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT UPDATE(status) ON public.job_postings TO {}').format(sql.Identifier(role)))
