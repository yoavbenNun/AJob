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
    return dict(stage=stage, exception_type=type(error).__name__, http_status=status,
                timeout=timeout, transient=transient, recovery_planned=False)
