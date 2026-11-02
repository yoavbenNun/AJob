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


@pytest.mark.parametrize('exposure', ['read-queue','write-queue','column-read','column-write',
                                     'sequence','routine','role-direct'])
def test_rejects_effective_pg_net_capabilities_without_executing_them(cloud_database, exposure):
    target, role, _, _ = cloud_database
    repo = scoped(PostgresJobRepository, target, role)
    with repo._connect() as connection:
        verify_dashboard_role(connection)
    with psycopg.connect(target) as admin:
        # Harmless fixtures, not the pg_net extension: no worker or HTTP request.
        admin.execute('CREATE SCHEMA net')
        admin.execute('CREATE TABLE net.http_request_queue(id bigint, payload text)')
        admin.execute('CREATE SEQUENCE net.http_request_queue_id_seq')
        admin.execute("CREATE FUNCTION net.worker_restart() RETURNS boolean LANGUAGE sql AS 'SELECT true'")
        admin.execute('REVOKE ALL ON ALL TABLES IN SCHEMA net FROM PUBLIC')
        admin.execute('REVOKE ALL ON ALL SEQUENCES IN SCHEMA net FROM PUBLIC')
        admin.execute('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA net FROM PUBLIC')
        admin.execute('GRANT USAGE ON SCHEMA net TO PUBLIC')
    try:
        # Schema visibility without object capabilities is not enough to reject.
        with repo._connect() as connection:
            verify_dashboard_role(connection)
        commands = {
            'read-queue':'GRANT SELECT ON net.http_request_queue TO PUBLIC',
            'write-queue':'GRANT INSERT ON net.http_request_queue TO PUBLIC',
            'column-read':'GRANT SELECT(payload) ON net.http_request_queue TO PUBLIC',
            'column-write':'GRANT UPDATE(payload) ON net.http_request_queue TO PUBLIC',
            'sequence':'GRANT USAGE ON net.http_request_queue_id_seq TO PUBLIC',
            'routine':'GRANT EXECUTE ON FUNCTION net.worker_restart() TO PUBLIC',
        }
        with psycopg.connect(target) as admin:
            if exposure == 'role-direct':
                admin.execute(sql.SQL('GRANT DELETE ON net.http_request_queue TO {}').format(sql.Identifier(role)))
            else:
                admin.execute(commands[exposure])
        with repo._connect() as connection, pytest.raises(ValueError,match='restricted'):
            verify_dashboard_role(connection)
    finally:
        with psycopg.connect(target) as admin:
            admin.execute('DROP SCHEMA net CASCADE')
    with repo._connect() as connection:
        verify_dashboard_role(connection)
