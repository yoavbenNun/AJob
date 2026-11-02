"""Restricted HTTPS transport for the cloud dashboard's Supabase identity.

JWT decoding here is policy checking, not signature verification. Auth /user
must first validate the exact token with Supabase before any claim is trusted.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class DataApiError(RuntimeError):
    """Safe, deliberately detail-free failure at the private API boundary."""


RPC_NAMES = frozenset({
    "ajob_jobs", "ajob_job", "ajob_status", "ajob_notes", "ajob_note_save",
    "ajob_dismiss", "ajob_login_reserve", "ajob_session_create",
    "ajob_session_get", "ajob_session_revoke", "ajob_preflight",
})
_TIMEOUT = httpx.Timeout(10.0, connect=5.0, pool=5.0)
_ERROR = "Cloud data API unavailable."
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _claims(token: str) -> dict:
    """Parse bounded JWT syntax without treating it as authenticated."""
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError
    parts = token.split(".")
    if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
        raise ValueError
    header, payload = [json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
                       for p in parts[:2]]
    if not isinstance(header, dict) or header.get("alg") not in {"HS256", "RS256", "ES256"}:
        raise ValueError
    if not isinstance(payload, dict):
        raise ValueError
    return payload


class DataApiClient:
    def __init__(self, project_url: str, publishable_key: str, auth_email: str,
                 auth_password: str, expected_subject: str, *,
                 client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time,
                 test_allow_loopback: bool = False):
        try:
            url = urlsplit(project_url)
            synthetic = (test_allow_loopback and client is not None
                         and isinstance(client._transport, httpx.MockTransport)
                         and url.scheme == "http"
                         and url.hostname in {"127.0.0.1", "localhost", "::1"})
            real = (url.scheme == "https" and url.port in {None, 443}
                    and re.fullmatch(r"[a-z0-9-]+\.supabase\.co", url.hostname or ""))
            if (not (real or synthetic) or url.username is not None
                    or url.password is not None or url.path not in {"", "/"}
                    or url.query or url.fragment):
                raise ValueError
            if not all(isinstance(v, str) and v and not any(c in v for c in "\r\n")
                       for v in [publishable_key, auth_email, auth_password, expected_subject]):
                raise ValueError
            if str(UUID(expected_subject)) != expected_subject:
                raise ValueError
            if publishable_key.startswith("sb_publishable_"):
                if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", publishable_key):
                    raise ValueError
            elif _claims(publishable_key).get("role") != "anon":
                raise ValueError
            # Injection is exclusively for hermetic tests. Production always owns
            # its client so callers cannot enable redirects, proxies or retries.
            if client is not None and not isinstance(client._transport, httpx.MockTransport):
                raise ValueError
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise DataApiError("Invalid cloud data API configuration.") from None
        self._origin = project_url.rstrip("/")
        self._key = publishable_key
        self._email, self._password = auth_email, auth_password
        self._subject = expected_subject
        self._clock = clock
        self._client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0

    @classmethod
    def from_env(cls) -> DataApiClient:
        return cls(*(os.environ.get(name, "") for name in (
            "AJOB_SUPABASE_URL", "AJOB_SUPABASE_PUBLISHABLE_KEY",
            "AJOB_SUPABASE_AUTH_EMAIL", "AJOB_SUPABASE_AUTH_PASSWORD",
            "AJOB_SUPABASE_AUTH_SUBJECT",
        )))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, *, token: str | None = None,
                 body: dict | None = None, profile: bool = False):
        headers = {"apikey": self._key, "Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if profile:
            headers.update({"Accept-Profile": "ajob_api", "Content-Profile": "ajob_api"})
        try:
            with self._client.stream(method, self._origin + path,
                                     headers=headers, json=body,
                                     timeout=_TIMEOUT, follow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise DataApiError(_ERROR)
                declared_size = response.headers.get("Content-Length")
                if declared_size is not None and int(declared_size) > _MAX_RESPONSE_BYTES:
                    raise DataApiError(_ERROR)
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise DataApiError(_ERROR)
                    content.extend(chunk)
                if not content:
                    return None
                return json.loads(content)
        except (httpx.HTTPError, ValueError, TypeError, RecursionError):
            raise DataApiError(_ERROR) from None

    def _access_token(self) -> str:
        with self._lock:
            if self._token is not None and self._expires > self._clock() + 60:
                return self._token
            self._token, self._expires = None, 0.0
            result = self._request("POST", "/auth/v1/token?grant_type=password", body={
                "email": self._email, "password": self._password,
            })
            try:
                token = result["access_token"]
                # Basic bounded syntax before transmitting; no authorization
                # decision based on claims until the server verifies this token.
                if not isinstance(token, str) or not token or len(token) > 16384 or re.search(r"\s", token):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise DataApiError(_ERROR) from None
            user = self._request("GET", "/auth/v1/user", token=token)
            try:
                claims = _claims(token)
                now = self._clock()
                exp, iat = claims["exp"], claims["iat"]
                if (not isinstance(user, dict) or user.get("id") != self._subject
                        or claims.get("sub") != self._subject
                        or claims.get("role") != "ajob_cloud_api"
                        or claims.get("iss") != self._origin + "/auth/v1"
                        or claims.get("aud") != "authenticated"
                        or any(type(v) not in {int, float} or not math.isfinite(v) for v in (exp, iat))
                        or exp <= now + 60 or iat > now + 30 or iat < 0 or iat >= exp):
                    raise ValueError
                self._token, self._expires = token, exp
                return token
            except (KeyError, ValueError, TypeError, RecursionError):
                raise DataApiError(_ERROR) from None

    def rpc(self, name: str, params: Mapping | None = None):
        if not isinstance(name, str) or name not in RPC_NAMES or (params is not None and not isinstance(params, Mapping)):
            raise DataApiError("Invalid cloud data API operation.")
        token = self._access_token()
        # Never retry: a timed-out write may already have committed remotely.
        return self._request("POST", "/rest/v1/rpc/" + name, token=token,
                             body=dict(params or {}), profile=True)

    def preflight(self):
        result = self.rpc("ajob_preflight")
        if (not isinstance(result, dict) or result != {"ready": True, "version": 1}
                or result.get("ready") is not True or type(result.get("version")) is not int):
            raise DataApiError(_ERROR)
        return result
