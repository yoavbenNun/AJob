"""Verify a completed Actions failure before suppressing a duplicate incident.

This verifies workflow reporting, not delivery of a GitHub notification email.
No remote URLs, response text or authorization values are emitted on failure.
"""

import json
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 256 * 1024
WORKFLOW_PATH = '.github/workflows/scheduled-scan.yml'
FAILURE_STEPS = {
    'Reconcile windows and retry pending notifications',
    'Publish business status independently of workflow result',
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer token to a redirected endpoint.


def _get_json(path, token):
    request = Request('https://api.github.com/' + path, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'AJob-scan-alert-verifier',
    })
    try:
        with build_opener(_NoRedirect()).open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError('Unexpected response status')
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('Oversized response')
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError('Invalid response shape')
        return result
    except Exception:
        raise RuntimeError('GitHub failure receipt unavailable') from None


def _positive_integer(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise RuntimeError('Invalid GitHub failure receipt context')
    return int(value)


def verify_failure(repo, run_id, attempt, token):
    """Return True only for a verified completed scheduled reporting failure.

    Unfinished/cancelled/non-reporting failures return False. Invalid context,
    response identity or network/authorization errors raise a sanitized error.
    Caller must fail closed on either False or an error; neither is a receipt.
    """
    if (not isinstance(repo, str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repo)
            or not isinstance(token, str) or not token or len(token) > 8192
            or '\r' in token or '\n' in token):
        raise RuntimeError('Invalid GitHub failure receipt context')
    run_id, attempt = _positive_integer(run_id), _positive_integer(attempt)
    base = f'repos/{repo}/actions/runs/{run_id}'
    attempt_base = base + f'/attempts/{attempt}'
    run = _get_json(attempt_base, token)
    repository = run.get('repository')
    if (type(run.get('id')) is not int or run['id'] != run_id
            or not isinstance(repository, dict)
            or not isinstance(repository.get('full_name'), str)
            or repository['full_name'].casefold() != repo.casefold()
            or run.get('path') != WORKFLOW_PATH
            or run.get('event') not in {'schedule', 'workflow_dispatch'}
            or run.get('head_branch') != 'main'
            or type(run.get('run_attempt')) is not int or run['run_attempt'] != attempt):
        raise RuntimeError('GitHub failure receipt identity mismatch')
    if run.get('status') != 'completed' or run.get('conclusion') != 'failure':
        return False
    payload = _get_json(attempt_base + '/jobs?per_page=100', token)
    jobs, total = payload.get('jobs'), payload.get('total_count')
    if (not isinstance(jobs, list) or type(total) is not int or total != len(jobs)
            or total > 100 or any(not isinstance(job, dict) for job in jobs)):
        raise RuntimeError('GitHub failure receipt jobs unavailable')
    for job in jobs:
        if (job.get('name') != 'scan' or job.get('status') != 'completed'
                or job.get('conclusion') != 'failure' or job.get('run_id') != run_id):
            continue
        steps = job.get('steps')
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            raise RuntimeError('GitHub failure receipt steps unavailable')
        if any(step.get('name') in FAILURE_STEPS and step.get('conclusion') == 'failure'
               and step.get('status') == 'completed' for step in steps):
            return True
    return False
