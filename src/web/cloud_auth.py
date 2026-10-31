"""Single-owner authentication for the deliberately restricted cloud dashboard.

The HTTPS edge must preserve Host and disable access logs containing query strings.
Forwarded headers never establish identity, origin, or rate-limit buckets here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import html
import re
import secrets
import time
from typing import Callable, Protocol
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

SESSION_COOKIE = "__Host-ajob-session"
LOGIN_COOKIE = "__Host-ajob-login"
MAX_BODY = 16384
PUBLIC_FILES = frozenset({"/static/manifest.webmanifest", "/static/icons/ajob-180.png", "/static/icons/ajob-192.png", "/static/icons/ajob-512.png"})
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def hash_password(password: str) -> str:
    """Generate an externally stored owner credential; never pass it via CLI args."""
    if not 16 <= len(password) <= 1024:
        raise ValueError("Owner password must contain 16 to 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt$32768$8$3${salt.hex()}${digest.hex()}"


def _credential(value: str) -> tuple[bytes, bytes]:
    if not re.fullmatch(r"scrypt\$32768\$8\$3\$[0-9a-f]{32}\$[0-9a-f]{64}", value):
        raise ValueError("Owner password hash must use the supported scrypt format")
    *_, salt, digest = value.split("$")
    return bytes.fromhex(salt), bytes.fromhex(digest)


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = _credential(encoded)
    if not 16 <= len(password) <= 1024:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64 * 1024 * 1024)
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class CloudAuthConfig:
    public_origin: str
    password_hash: str = field(repr=False)
    idle_seconds: int = 1800
    max_age_seconds: int = 43200

    def __post_init__(self):
        origin = urlsplit(self.public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?", origin.netloc)
                or origin.port not in {None, 443}):
            raise ValueError("Cloud public origin must be an exact HTTPS origin")
        if not 60 <= self.idle_seconds <= self.max_age_seconds <= 86400:
            raise ValueError("Invalid session expiry configuration")
        _credential(self.password_hash)


@dataclass(frozen=True)
class AuthSession:
    csrf_token: str


class AuthStore(Protocol):
    def reserve_login(self, now: float) -> bool: ...
    def create_session(self, session_hash: str, csrf_token: str, now: float, expires_at: float) -> None: ...
    def get_session(self, session_hash: str, now: float, idle_seconds: int) -> AuthSession | None: ...
    def revoke_session(self, session_hash: str) -> None: ...


class PostgresAuthStore:
    """All operations commit atomically; no process-local authorization state."""
    def __init__(self, connection_factory: Callable):
        self._connect = connection_factory

    def verify_schema(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT token_hash, csrf_token, created_at, last_seen, expires_at FROM ajob_private.dashboard_sessions LIMIT 0")
            cursor.execute("SELECT singleton, window_start, attempts FROM ajob_private.dashboard_login_throttle WHERE singleton = TRUE")
            if cursor.fetchone() is None:
                raise RuntimeError("Cloud authentication schema is not provisioned")

    def reserve_login(self, now):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE ajob_private.dashboard_login_throttle
                SET attempts = CASE WHEN window_start <= %s - 900 THEN 1 ELSE attempts + 1 END,
                    window_start = CASE WHEN window_start <= %s - 900 THEN %s ELSE window_start END
                WHERE singleton = TRUE AND (window_start <= %s - 900 OR attempts < 5)
                RETURNING attempts""", (now, now, now, now))
            return cursor.fetchone() is not None

    def create_session(self, session_hash, csrf_token, now, expires_at):
        with self._connect() as connection, connection.cursor() as cursor:
            # Shared transaction lock bounds sessions even across concurrent instances.
            cursor.execute("SELECT singleton FROM ajob_private.dashboard_login_throttle WHERE singleton = TRUE FOR UPDATE")
            if cursor.fetchone() is None:
                raise RuntimeError("Cloud authentication schema is not provisioned")
            cursor.execute("DELETE FROM ajob_private.dashboard_sessions WHERE expires_at <= %s", (now,))
            cursor.execute("""DELETE FROM ajob_private.dashboard_sessions WHERE token_hash IN
                (SELECT token_hash FROM ajob_private.dashboard_sessions ORDER BY created_at DESC, token_hash OFFSET 4)""")
            cursor.execute("""INSERT INTO ajob_private.dashboard_sessions
                (token_hash, csrf_token, created_at, last_seen, expires_at) VALUES (%s,%s,%s,%s,%s)""",
                (session_hash, csrf_token, now, now, expires_at))

    def get_session(self, session_hash, now, idle_seconds):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE ajob_private.dashboard_sessions SET last_seen = GREATEST(last_seen, %s)
                WHERE token_hash = %s AND expires_at > %s AND last_seen > %s
                RETURNING csrf_token""", (now, session_hash, now, now - idle_seconds))
            row = cursor.fetchone()
            return AuthSession(row["csrf_token"] if isinstance(row, dict) else row[0]) if row else None

    def revoke_session(self, session_hash):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM ajob_private.dashboard_sessions WHERE token_hash = %s", (session_hash,))


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class CloudAuthMiddleware:
    def __init__(self, app, config: CloudAuthConfig, store: AuthStore, clock=time.time):
        self.app, self.config, self.store, self.clock = app, config, store, clock
        self.host = urlsplit(config.public_origin).netloc
        self.login_key = hashlib.sha256(("ajob-login:" + config.password_hash).encode()).digest()

    def _login_token(self):
        value = f"{int(self.clock())}.{secrets.token_urlsafe(32)}"
        return value + "." + hmac.new(self.login_key, value.encode(), "sha256").hexdigest()

    def _valid_login_token(self, token):
        try:
            stamp, random, signature = token.split(".")
            age = self.clock() - int(stamp)
            return (0 <= age < 600 and bool(_TOKEN.fullmatch(random)) and
                    hmac.compare_digest(signature, hmac.new(self.login_key, f"{stamp}.{random}".encode(), "sha256").hexdigest()))
        except (ValueError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return
        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in {b"cache-control", b"referrer-policy", b"content-security-policy"}]
                headers.extend([(b"cache-control", b"no-store"), (b"referrer-policy", b"same-origin"),
                    (b"strict-transport-security", b"max-age=31536000"), (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")])
                message = dict(message, headers=headers)
            await send(message)
        request = Request(scope, receive)
        # Duplicate identity/security headers are rejected, rather than coalesced.
        for name in (b"host", b"origin", b"cookie", b"x-csrf-token"):
            if sum(key.lower() == name for key, _ in scope["headers"]) > 1:
                await PlainTextResponse("Invalid request", 400)(scope, receive, secured_send)
                return
        if request.headers.get("host") != self.host:
            await PlainTextResponse("Invalid host", 400)(scope, receive, secured_send)
            return
        try:
            response, downstream_receive = await self._authorize(request, receive)
        except Exception:
            # Database and credential details must never reach a public response.
            response, downstream_receive = PlainTextResponse("Service temporarily unavailable", 503), receive
        if response is not None:
            await response(scope, receive, secured_send)
        else:
            await self.app(scope, downstream_receive, secured_send)

    async def _authorize(self, request, receive):
        path, method, now = request.url.path, request.method, self.clock()
        if method == "GET" and (path == "/healthz" or path in PUBLIC_FILES):
            return None, receive
        if path == "/login" and method == "GET":
            token = self._login_token()
            response = HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AJob sign in</title><style>body{margin:0;background:#f3f6fa;color:#173f59;font:16px system-ui}main{max-width:24rem;margin:12vh auto;padding:1.5rem}form,label{display:grid;gap:1rem}input,button{box-sizing:border-box;width:100%;min-height:48px;padding:.75rem;font:inherit;border:1px solid #64748b;border-radius:.6rem}button{background:#173f59;color:white;cursor:pointer}input:focus-visible,button:focus-visible{outline:3px solid #2563eb;outline-offset:3px}</style><main><h1>AJob sign in</h1><form method="post" action="/login"><input type="hidden" name="csrf_token" value="' + html.escape(token, quote=True) + '"><label>Password <input type="password" name="password" autocomplete="current-password" required maxlength="1024"></label><button type="submit">Sign in</button></form></main></html>')
            response.set_cookie(LOGIN_COOKIE, token, max_age=600, secure=True, httponly=True, samesite="strict", path="/")
            return response, receive
        raw_session = request.cookies.get(SESSION_COOKIE, "")
        session_hash = _digest(raw_session) if _TOKEN.fullmatch(raw_session) else None
        session = None
        if path != "/login" and session_hash:
            session = await run_in_threadpool(self.store.get_session, session_hash, now, self.config.idle_seconds)
        if path != "/login" and session is None:
            response = RedirectResponse("/login", status_code=303) if method == "GET" else PlainTextResponse("Authentication required", 401)
            response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
            return response, receive
        if method not in {"GET", "HEAD"}:
            if request.headers.get("origin") != self.config.public_origin or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                return PlainTextResponse("Invalid request", 403), receive
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > (8192 if path == "/login" else MAX_BODY):
                    return PlainTextResponse("Request too large", 413), receive
                chunks.append(chunk)
            body = b"".join(chunks)
            sent = False
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            parsed_request = Request(request.scope, replay)
            header_token = request.headers.get("x-csrf-token", "")
            if path == "/login" or not header_token:
                form = await parsed_request.form(max_files=2, max_fields=50)
                submitted = str(form.get("csrf_token", ""))
                password = str(form.get("password", "")) if path == "/login" else ""
                await form.close()
            else:
                form, submitted = {}, header_token
            sent = False
            if path == "/login":
                if method != "POST":
                    return PlainTextResponse("Method not allowed", 405), receive
                token = request.cookies.get(LOGIN_COOKIE, "")
                if not self._valid_login_token(token) or not hmac.compare_digest(submitted.encode(), token.encode()):
                    return PlainTextResponse("Invalid request", 403), receive
                if not await run_in_threadpool(self.store.reserve_login, now):
                    return PlainTextResponse("Sign in temporarily unavailable", 429, headers={"Retry-After": "900"}), receive
                if not await run_in_threadpool(verify_password, password, self.config.password_hash):
                    return PlainTextResponse("Unable to sign in", 401), receive
                if session_hash:
                    await run_in_threadpool(self.store.revoke_session, session_hash)
                new_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                await run_in_threadpool(self.store.create_session, _digest(new_token), csrf, now, now + self.config.max_age_seconds)
                response = RedirectResponse("/", 303)
                response.set_cookie(SESSION_COOKIE, new_token, max_age=self.config.max_age_seconds, secure=True, httponly=True, samesite="strict", path="/")
                response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="strict")
                return response, receive
            if not hmac.compare_digest(submitted.encode(), session.csrf_token.encode()):
                return PlainTextResponse("Invalid request", 403), receive
            if path == "/logout":
                if method != "POST":
                    return PlainTextResponse("Method not allowed", 405), receive
                await run_in_threadpool(self.store.revoke_session, session_hash)
                response = RedirectResponse("/login", 303)
                response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
                return response, receive
            receive = replay
        if path in {"/login", "/logout"}:
            return PlainTextResponse("Method not allowed", 405), receive
        request.scope.setdefault("state", {})["csrf_token"] = session.csrf_token
        return None, receive
