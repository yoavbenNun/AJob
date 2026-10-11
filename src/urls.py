"""Shared URL normalization for stable job identity."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    """Normalize equivalent HTTP(S) URLs without discarding query identity."""
    if not url.casefold().startswith(("http://", "https://")):
        return ""
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), query, "")
    )
