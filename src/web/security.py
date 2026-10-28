"""Browser boundary protection for the loopback-only dashboard."""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # CSRF tokens remain required by each mutating route. These checks also
        # reject cross-origin requests before parsing a body or invoking a service.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
            or not _same_origin(request.headers.get("origin"), request.url)
            or not _same_origin(request.headers.get("referer"), request.url)
        ):
            response = PlainTextResponse("Cross-origin request rejected", status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            # Native forms under no-referrer send Origin: null. Keep local
            # provenance without forwarding Referer to external job sites.
            "Referrer-Policy": "same-origin",
            "Cache-Control": "no-store",
        })
        return response


def _same_origin(value, target) -> bool:
    if value is None:
        return True  # Non-browser clients still need the route's CSRF token.
    try:
        source = urlsplit(value)
        destination = urlsplit(str(target))
        def identity(url):
            return (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        return (
            source.scheme in {"http", "https"}
            and source.username is None and source.password is None
            and identity(source) == identity(destination)
        )
    except ValueError:
        return False
