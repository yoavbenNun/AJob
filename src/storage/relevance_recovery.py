"""Explicit, bounded review of historical professional classifications.

This module never touches the notification outbox or sends messages.
"""
import hashlib
import json
from dataclasses import asdict
from urllib.parse import urlsplit

from src.filtering.job_filter import classify_job
from src.location.commute import classify_job_location, resolve_job_destination
from src.matching.resume_matcher import match_resume_to_job

MAX_JOBS = 100


def _valid_job_url(url):
    """Reject malformed stored URLs without changing the historical record."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
        return False
    try:
        parts = urlsplit(url)
        return bool(parts.scheme.lower() in {'http', 'https'} and parts.hostname
                    and parts.username is None and parts.password is None
                    and (parts.port is None or 1 <= parts.port <= 65535))
    except ValueError:
        return False


class RecoveryApplyError(RuntimeError):
    def __init__(self, results):
        self.results = results
        super().__init__('Recovery stopped after a database error; the interrupted update may have committed. Preview again before retrying.')


def preview(repository, job_ids, resume=None, *, target):
    ids = sorted(set(job_ids))
    if not ids or len(ids) > MAX_JOBS or any(i <= 0 for i in ids):
        raise ValueError(f"Specify between 1 and {MAX_JOBS} positive job IDs.")
    entries, snapshots = [], []
    for job_id in ids:
        job = repository.get_by_id(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} was not found; no changes made.")
        professional = classify_job(job)
        location = classify_job_location(resolve_job_destination(job.location, job.description))
        academic = match_resume_to_job(resume, job).eligibility_status if resume else 'not assessed'
        url_valid = _valid_job_url(job.job_url)
        candidate = (url_valid and not job.is_relevant and job.status in {'new', 'reviewed'}
                     and professional.relevant and location.status != 'outside_israel'
                     and academic != 'ineligible')
        entries.append(dict(id=job_id, title=job.title, status=job.status,
                            stored_relevant=job.is_relevant, professional=professional.reason,
                            location=location.status, eligibility=academic,
                            url_valid=url_valid, candidate=candidate))
        snapshots.append(asdict(job))
    # Bind approval to both the exact source fields and the evaluated outcome.
    payload = json.dumps({'target': target, 'entries': entries, 'snapshots': snapshots}, sort_keys=True,
                         default=str, ensure_ascii=False, separators=(',', ':'))
    return entries, hashlib.sha256(payload.encode('utf-8')).hexdigest(), snapshots


def apply(repository, entries, snapshots):
    """CAS ensures edits or new status changes after preview are not overwritten."""
    from src.models import JobPosting
    results = []
    for entry, snapshot in zip(entries, snapshots):
        if entry['candidate']:
            try:
                changed = repository.recover_relevance_if_unchanged(JobPosting(**snapshot))
            except Exception as error:
                raise RecoveryApplyError(results) from error
            results.append((entry['id'], changed))
    return results
