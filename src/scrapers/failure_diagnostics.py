"""Project exceptions to bounded diagnostics; never serialize error text or URLs."""

from urllib.error import HTTPError, URLError


def source_failure(error, stage='fetch'):
    cause = error.reason if isinstance(error, URLError) and isinstance(error.reason, Exception) else error
    timeout = isinstance(cause, TimeoutError) or 'timeout' in type(cause).__name__.lower()
    status = error.code if isinstance(error, HTTPError) else None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    transient = (timeout or status in {408, 425, 429} or bool(status and status >= 500)
                 or (isinstance(cause, OSError) and not isinstance(error, HTTPError)))
    details = dict(stage=stage, exception_type=type(error).__name__, http_status=status,
                   timeout=timeout, transient=transient, recovery_planned=False)
    # Only fixed metadata values may cross the worker/log boundary. Never copy
    # arbitrary headers, URLs, response text or exception messages.
    response = getattr(error, 'response_diagnostics', {})
    if isinstance(response, dict):
        for key, choices in {
            'content_type': {'application/json', 'text/json', 'text/html',
                             'application/xhtml+xml', 'other', 'missing'},
            'body_kind': {'unread', 'empty', 'html', 'json', 'other'},
            'response_problem': {'http_status', 'redirect', 'oversize', 'html',
                                 'invalid_json', 'invalid_schema', 'unknown'},
        }.items():
            if isinstance(response.get(key), str) and response[key] in choices:
                details[key] = response[key]
        for key in ('response_truncated', 'redirected', 'redirect_same_host'):
            if type(response.get(key)) is bool:
                details[key] = response[key]
        size = response.get('response_bytes')
        if type(size) is int and 0 <= size <= 4 * 1024 * 1024 + 1:
            details['response_bytes'] = size
        response_status = response.get('http_status')
        if type(response_status) is int and 100 <= response_status <= 599:
            details['http_status'] = response_status
    return details
