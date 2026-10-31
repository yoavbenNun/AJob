"""Opt-in dashboard-only factory. Never initializes or migrates a database."""

from dataclasses import dataclass
import os
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

from src.web.app import create_app


@dataclass
class CloudServices:
    jobs: object
    notes: object
    dismissals: object


class CloudSurfaceMiddleware(BaseHTTPMiddleware):
    """Positive route allowlist: desktop-only capabilities stay unreachable."""

    async def dispatch(self, request, call_next):
        # The configured HTTPS edge terminates TLS. Never consume arbitrary
        # Forwarded/X-Forwarded-* headers; auth has already checked the exact Host.
        request.scope['scheme'] = 'https'
        path, method = request.url.path, request.method
        allowed = (
            (method in {'GET', 'HEAD'} and (
                path in {'/', '/healthz', '/login'} or path.startswith('/static/')
                or re.fullmatch(r'/jobs/[1-9][0-9]*', path)))
            or (method == 'POST' and (path in {'/login', '/logout'}
                or re.fullmatch(r'/jobs/[1-9][0-9]*/(status|personal-note|dismissal)', path)))
        )
        if not allowed:
            response = PlainTextResponse('Not available', status_code=404)
        elif method == 'POST' and (
            not request.headers.get('content-length', '').isdigit()
            or int(request.headers['content-length']) > 16384
            or request.headers.get('transfer-encoding')
        ):
            response = PlainTextResponse('Invalid request size', status_code=413)
        else:
            try:
                response = await call_next(request)
            except Exception:
                # Neither database URLs nor exception details reach responses/logs.
                response = PlainTextResponse('Workspace temporarily unavailable. Please retry.', status_code=503)
        response.headers.update({
            'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Strict-Transport-Security': 'max-age=31536000',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response


def build_cloud_app(config, services, auth_store):
    """Inject synthetic services in tests; production is configured below."""
    from src.web.cloud_auth import CloudAuthMiddleware

    app = create_app(cloud_services=services, notification_recipient_updater=None)

    @app.get('/healthz', include_in_schema=False)
    def healthz():
        return {'status': 'ok'}  # liveness, deliberately not DB readiness

    app.add_middleware(CloudSurfaceMiddleware)
    app.add_middleware(CloudAuthMiddleware, config=config, store=auth_store)
    return app


def verify_dashboard_role(connection):
    """Fail closed for scanner/owner credentials or unexpectedly broad grants."""
    with connection.cursor() as cursor:
        cursor.execute("""SELECT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole
            OR pg_has_role(current_user, 'pg_write_all_data', 'MEMBER')
            OR pg_has_role(current_user, 'pg_read_all_data', 'MEMBER')
            OR has_schema_privilege(current_user, 'public', 'CREATE')
            OR has_schema_privilege(current_user, 'ajob_private', 'CREATE')
            OR has_table_privilege(current_user, 'public.job_postings', 'UPDATE')
            OR has_table_privilege(current_user, 'public.job_postings', 'DELETE')
            OR has_table_privilege(current_user, 'public.job_postings', 'INSERT')
            OR has_table_privilege(current_user, 'public.job_postings', 'TRUNCATE')
            OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='net' AND has_schema_privilege(current_user,n.oid,'USAGE')
                AND CASE WHEN c.relkind='S' THEN
                    has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')
                WHEN c.relkind IN ('r','p','v','m','f') THEN
                    has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
                    OR has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,TRIGGER')
                ELSE false END)
            OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='net' AND has_schema_privilege(current_user,n.oid,'USAGE')
                AND has_function_privilege(current_user,p.oid,'EXECUTE'))
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.job_postings'::regclass
                AND attnum>0 AND NOT attisdropped AND attname<>'status'
                AND has_column_privilege(current_user, attrelid, attnum, 'UPDATE'))
            OR EXISTS (SELECT 1 FROM pg_class WHERE relnamespace='ajob_private'::regnamespace
                AND pg_has_role(current_user, relowner, 'MEMBER'))
            OR EXISTS (SELECT 1 FROM pg_roles privileged WHERE
                (privileged.rolsuper OR privileged.rolbypassrls OR privileged.rolcreaterole)
                AND pg_has_role(current_user, privileged.oid, 'MEMBER'))
            OR pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid='public.job_postings'::regclass), 'MEMBER')) AS unsafe
            FROM pg_roles WHERE rolname=current_user""")
        row = cursor.fetchone()
        values = list(row.values()) if isinstance(row, dict) else list(row)
        if not values or any(values):
            raise ValueError('Cloud dashboard requires its restricted database role')


def create_cloud_app():
    """Uvicorn --factory entry point; explicit settings only, no SQLite fallback."""
    from src.storage.postgres_job_repository import PostgresJobRepository
    from src.storage.postgres_personal_notes_repository import PostgresPersonalNotesRepository
    from src.storage.postgres_dismissal_repository import PostgresDismissalRepository
    from src.web.cloud_auth import CloudAuthConfig, PostgresAuthStore

    if os.getenv('AJOB_CLOUD_ENABLED') != '1':
        raise RuntimeError('Cloud dashboard is disabled')
    try:
        database_url = os.environ['AJOB_DASHBOARD_DATABASE_URL']
        parsed = urlsplit(database_url)
        if parse_qs(parsed.query).get('sslmode') != ['verify-full']:
            raise ValueError('Verified database TLS is required')
        config = CloudAuthConfig(
            public_origin=os.environ['AJOB_PUBLIC_ORIGIN'],
            password_hash=os.environ['AJOB_OWNER_PASSWORD_HASH'],
        )
        jobs = PostgresJobRepository(database_url)
        bound_connection_time(jobs)
        with jobs._connect() as connection:
            verify_dashboard_role(connection)
        notes = PostgresPersonalNotesRepository(database_url)
        dismissals = PostgresDismissalRepository(database_url)
        bound_connection_time(notes)
        bound_connection_time(dismissals)
        auth = PostgresAuthStore(jobs._connect)
        auth.verify_schema()
        return build_cloud_app(config, CloudServices(jobs, notes, dismissals), auth)
    except Exception:
        raise RuntimeError('Cloud startup validation failed; check approved settings and schema') from None


def bound_connection_time(repository):
    """Bound slow reads and row-lock waits without changing shared DB settings."""
    connect = repository._connect
    def bounded():
        connection = connect()
        try:
            connection.execute("SET statement_timeout = '10s'")
            connection.execute("SET lock_timeout = '3s'")
            connection.commit()
            return connection
        except Exception:
            connection.close()
            raise
    repository._connect = bounded
