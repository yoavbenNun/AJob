"""Bounded Workday JSON decoding with metadata-only failure diagnostics."""

import json
from urllib.error import HTTPError
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class WorkdayResponseError(ValueError):
    """A response is not usable JSON; never include response text in the error."""

    def __init__(self, reason, diagnostics):
        super().__init__(reason)
        self.response_diagnostics = dict(diagnostics, response_problem={
            'Unexpected HTTP status': 'http_status',
            'Unexpected JSON redirect': 'redirect',
            'JSON response exceeds size limit': 'oversize',
            'HTML instead of JSON': 'html',
            'Invalid JSON response': 'invalid_json',
            'Invalid JSON schema': 'invalid_schema',
        }.get(reason, 'unknown'))


def read_json_response(response, endpoint, validate=None):
    """Decode only bounded JSON; URLs, headers and body text never become diagnostics.

    urllib exposes the final URL, not the redirect chain. Report that limitation
    through final-destination comparisons rather than claiming a chain was seen.
    Header-less file-like fixtures remain supported.
    """
    status = getattr(response, 'status', getattr(response, 'code', None))
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    headers = getattr(response, 'headers', None)
    raw_type = headers.get('Content-Type', '') if headers is not None else ''
    media_type = raw_type.split(';', 1)[0].strip().lower()
    content_type = media_type if media_type in {
        'application/json', 'text/json', 'text/html', 'application/xhtml+xml',
    } else ('other' if media_type else 'missing')
    final_url = response.geturl() if hasattr(response, 'geturl') else endpoint
    redirected = final_url != endpoint
    same_host = urlsplit(final_url).netloc.lower() == urlsplit(endpoint).netloc.lower()
    diagnostics = dict(http_status=status, content_type=content_type,
                       body_kind='unread', response_bytes=0, response_truncated=False,
                       redirected=redirected, redirect_same_host=same_host)
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as error:
        if isinstance(response, HTTPError):
            # Optional diagnostics must never hide a known denial/rate limit
            # and accidentally cause the caller to keep querying that source.
            response.response_diagnostics = diagnostics
            raise response from None
        error.response_diagnostics = diagnostics
        raise
    diagnostics['response_bytes'] = len(body)
    diagnostics['response_truncated'] = len(body) > MAX_RESPONSE_BYTES
    prefix = body.lstrip()[:32].lower()
    diagnostics['body_kind'] = (
        'empty' if not body.strip() else 'html' if prefix.startswith((b'<!doctype html', b'<html'))
        else 'json' if prefix.startswith((b'{', b'[')) else 'other')
    if isinstance(response, HTTPError):
        response.response_diagnostics = diagnostics
        raise response
    if status is not None and not 200 <= status < 300:
        raise WorkdayResponseError('Unexpected HTTP status', diagnostics)
    if redirected or not same_host:
        raise WorkdayResponseError('Unexpected JSON redirect', diagnostics)
    if diagnostics['response_truncated']:
        raise WorkdayResponseError('JSON response exceeds size limit', diagnostics)
    if content_type in {'text/html', 'application/xhtml+xml'} or diagnostics['body_kind'] == 'html':
        raise WorkdayResponseError('HTML instead of JSON', diagnostics)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        raise WorkdayResponseError('Invalid JSON response', diagnostics) from None
    if validate is not None:
        try:
            return validate(payload)
        except ValueError:
            raise WorkdayResponseError('Invalid JSON schema', diagnostics) from None
    return payload
