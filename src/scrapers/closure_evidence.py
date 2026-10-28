"""Conservative evidence from complete employer snapshots, before role filtering.

No HTTP, no job text, no inference from missing paginated search results.
"""

import re
from urllib.parse import parse_qs, urlsplit

from src.urls import canonical_url


def snapshot(source, payload, kind):
    items = payload.get('jobs') if kind == 'greenhouse' and isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        return None  # A soft empty response is not sufficient evidence of closure.
    if kind == 'greenhouse':
        total = payload.get('meta', {}).get('total') if isinstance(payload.get('meta', {}), dict) else None
        if type(total) is not int or total != len(items):
            return None
        if payload.get('next') or payload.get('next_page'):
            return None
    active = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        identity = item.get('id' if kind == 'greenhouse' else 'jobId')
        if type(identity) is not int or identity <= 0:
            return None
        if str(identity) in active:
            return None  # Duplicated rows cannot establish complete coverage.
        if kind == 'greenhouse':
            if not item.get('title') or not item.get('absolute_url'):
                return None
            active.add(str(identity))
        elif kind == 'elbit':
            # Only the documented active state is interpreted. Unknown states
            # invalidate the snapshot, rather than guessing an inactive value.
            if type(item.get('status')) is not int or item['status'] != 1 or not item.get('jobTitle'):
                return None
            active.add(str(identity))
        else:
            return None
    return dict(source=canonical_url(source), kind=kind, active=sorted(active))


def identity(job, evidence):
    if canonical_url(job.source) != evidence['source']:
        return None
    parts = urlsplit(job.job_url)
    if evidence['kind'] == 'elbit':
        if parts.hostname != 'elbitsystemscareer.com' or parts.path.rstrip('/') != '/job':
            return None
        value = parse_qs(parts.query).get('jid', [''])[0]
    elif evidence['kind'] == 'greenhouse':
        # Custom employer domains are allowed only with the ATS's explicit ID.
        value = parse_qs(parts.query).get('gh_jid', [''])[0]
        if not value and parts.hostname in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
            if parts.path.strip('/').split('/')[0] != urlsplit(evidence['source']).path.strip('/'):
                return None
            match = re.search(r'/jobs/(\d+)$', parts.path.rstrip('/'))
            value = match[1] if match else ''
    else:
        return None
    return value if re.fullmatch(r'[1-9]\d*', value) else None


def classify(job, evidence):
    if not evidence:
        return 'UNKNOWN'
    value = identity(job, evidence)
    if value is None:
        return 'UNKNOWN'
    return 'OPEN' if value in evidence['active'] else 'CLOSED'
