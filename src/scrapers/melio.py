"""Melio's official public Ashby board, linked by its current careers app."""

import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from src.models import Company, JobPosting
from src.scrapers.greenhouse import html_text
from src.urls import canonical_url


ENDPOINT = 'https://api.ashbyhq.com/posting-api/job-board/melio-payments'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class MelioInvalidFeed(ValueError):
    """Public board schema or contents failed validation."""


class MelioNoUsableListings(MelioInvalidFeed):
    """Nonempty feed could not yield any validated job identity."""


class MelioUnexpectedResponse(ValueError):
    """HTTP response was not the expected public board response."""


def extract_jobs(company: Company, payload: dict, metrics: dict) -> list[JobPosting]:
    if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
        raise MelioInvalidFeed('Melio response has no jobs list')
    # This public posting endpoint is a complete board, not a paginated search.
    # Never infer closure from an ATS migration or this collection response.
    if payload.get('next') or payload.get('nextPage') or payload.get('next_page'):
        raise MelioInvalidFeed('Unexpected Melio pagination')
    metrics.update(raw_listings=len(payload['jobs']), invalid_listing=0,
                   unlisted_jobs=0, duplicates_avoided=0, page_limit_reached=False,
                   location_filter_status='not_applied')
    jobs = {}
    for item in payload['jobs']:
        if isinstance(item, dict) and item.get('isListed') is False:
            metrics['unlisted_jobs'] += 1
            continue
        if not isinstance(item, dict):
            metrics['invalid_listing'] += 1
            continue
        title = item.get('title')
        raw_url = canonical_url(item.get('jobUrl', '')) if isinstance(item.get('jobUrl'), str) else ''
        parts = urlsplit(raw_url)
        segments = parts.path.split('/')
        # Ashby currently returns the board as "Melio-payments" even for the
        # lowercase endpoint. Both official paths resolve, but persist the
        # lowercase board used previously to retain stable identity.
        valid_board = (parts.scheme == 'https' and parts.netloc == 'jobs.ashbyhq.com'
                       and not parts.query and len(segments) == 3
                       and segments[0] == '' and segments[1].isascii()
                       and segments[1].lower() == 'melio-payments')
        raw_identity = segments[2] if valid_board else ''
        identity = item['id'] if 'id' in item else raw_identity
        try:
            identity = str(UUID(identity)) if isinstance(identity, str) else ''
        except ValueError:
            identity = ''
        url = f'https://jobs.ashbyhq.com/melio-payments/{identity}'
        if (not valid_board or not identity or raw_identity != identity
                or not isinstance(title, str) or not title.strip()
                or 'jobs.ashbyhq.com' not in company.allowed_job_hosts
                or not any(pattern in url for pattern in company.job_url_patterns)):
            metrics['invalid_listing'] += 1
            continue
        if url in jobs:
            metrics['duplicates_avoided'] += 1
            continue
        location = item.get('location')
        description = item.get('descriptionPlain')
        if not isinstance(description, str) or not description.strip():
            description = html_text(item.get('descriptionHtml', '')) if isinstance(item.get('descriptionHtml'), str) else ''
        jobs[url] = JobPosting(title.strip(), company.name,
                               location.strip() if isinstance(location, str) and location.strip() else 'Not specified',
                               url, description=description[:20_000], source=company.careers_url)
    metrics['global_discovered'] = len(jobs)
    metrics['coverage_limited'] = bool(metrics['invalid_listing'])
    if not jobs and metrics['invalid_listing']:
        raise MelioNoUsableListings('Melio response contains no usable listings')
    return list(jobs.values())


def fetch_jobs(company: Company, timeout_ms: int):
    source = urlsplit(company.careers_url)
    if (source.scheme != 'https' or source.netloc != 'careers.meliopayments.com'
            or source.path not in {'', '/'} or source.query or source.fragment):
        raise ValueError('Unsupported Melio careers source')
    request = Request(ENDPOINT, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=min(20, max(1, timeout_ms / 1000))) as response:
        if response.geturl() != ENDPOINT:
            raise MelioUnexpectedResponse('Unexpected Melio feed redirect')
        if getattr(response, 'status', 200) != 200:
            raise MelioUnexpectedResponse('Unexpected Melio response status')
        headers = getattr(response, 'headers', {})
        media_type = headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if media_type and media_type != 'application/json':
            raise MelioUnexpectedResponse('Unexpected Melio response content type')
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise MelioUnexpectedResponse('Melio response exceeds size limit')
    metrics = {}
    jobs = extract_jobs(company, json.loads(body), metrics)
    return jobs, metrics
