"""Bounded ordinary public searches used by Qualcomm's official careers portal."""
import json
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from src.models import JobPosting
from src.filtering.job_filter import prefilter_job
from src.job_content import description_problem
from src.scrapers.greenhouse import html_text


ORIGIN = 'https://careers.qualcomm.com'
SEARCHES = ('student', 'intern', 'junior software', 'graduate software')


def fetch_jobs(company, timeout_ms):
    if urlsplit(company.careers_url).hostname != 'careers.qualcomm.com':
        raise ValueError('Unsupported Qualcomm source')
    deadline = time.monotonic() + 60
    timeout = min(15, max(1, timeout_ms / 1000))
    metrics = dict(raw_listings=0, duplicates_avoided=0, page_limit_reached=False,
                   location_filter_status='official_israel_search')

    def read(path, values):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Qualcomm source budget exceeded')
        request = Request(ORIGIN + path + '?' + urlencode({'domain': 'qualcomm.com', **values}),
                          headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urlopen(request, timeout=min(timeout, remaining)) as response:
            if urlsplit(response.geturl()).hostname != 'careers.qualcomm.com':
                raise ValueError('Unexpected Qualcomm redirect')
            payload = json.load(response)
        if (not isinstance(payload, dict) or payload.get('status') != 200
                or not isinstance(payload.get('data'), dict)):
            raise ValueError('Invalid Qualcomm response')
        return payload['data']

    jobs = {}
    for query in SEARCHES:
        offset = 0
        seen_page_ids = set()
        for page in range(2):
            data = read('/api/pcsx/search', dict(query=query, location='Israel', start=offset))
            positions, count = data.get('positions'), data.get('count')
            if (not isinstance(positions, list) or type(count) is not int
                    or count < offset + len(positions) or any(not isinstance(p, dict) for p in positions)):
                raise ValueError('Invalid Qualcomm search results')
            metrics['raw_listings'] += len(positions)
            page_ids = set()
            for item in positions:
                identity, title = item.get('id'), item.get('name')
                if type(identity) is not int or identity <= 0 or not isinstance(title, str) or not title.strip():
                    raise ValueError('Invalid Qualcomm posting identity')
                page_ids.add(identity)
                path = f'/careers/job/{identity}'
                if item.get('positionUrl') != path:
                    raise ValueError('Qualcomm posting URL mismatch')
                url = ORIGIN + path
                if url in jobs:
                    metrics['duplicates_avoided'] += 1
                    continue
                labels = item.get('locations')
                if not isinstance(labels, list) or any(not isinstance(p, str) for p in labels):
                    raise ValueError('Invalid Qualcomm locations')
                job = JobPosting(title.strip(), company.name, '; '.join(labels) or 'Not specified',
                                 url, source=company.careers_url)
                if prefilter_job(job).relevant:
                    detail = read('/api/pcsx/position_details', dict(position_id=identity))
                    if (type(detail.get('id')) is not int or detail.get('id') != identity or detail.get('name') != title
                            or detail.get('positionUrl') != path
                            or detail.get('atsJobId') != item.get('atsJobId')
                            or not item.get('atsJobId')):
                        raise ValueError('Qualcomm detail identity mismatch')
                    job.description = html_text(str(detail.get('jobDescription') or ''))
                    if description_problem(job.description) or len(job.description) < 150:
                        raise ValueError('Qualcomm detail is unavailable')
                jobs[url] = job
            if positions and page > 0 and not page_ids - seen_page_ids:
                raise ValueError('Repeated Qualcomm search page')
            seen_page_ids.update(page_ids)
            offset += len(positions)
            if offset >= count:
                break
            if not positions:
                raise ValueError('Incomplete Qualcomm search page')
            if page == 1:
                metrics['page_limit_reached'] = True
    metrics['focused_discovered'] = metrics['raw_listings']
    metrics['focused_unique'] = len(jobs)
    metrics['global_discovered'] = 0  # These requests use the official Israel location search.
    metrics['coverage_limited'] = metrics['page_limit_reached']
    return list(jobs.values()), metrics
