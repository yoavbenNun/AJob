"""Bounded discovery via the public JSON endpoint used by Amazon's search UI."""

from http.client import HTTPException
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
import json
import re
import time

from src.models import JobPosting
from src.urls import canonical_url


HOST = "www.amazon.jobs"
JOB_PATH = re.compile(r"/en/jobs/([1-9][0-9]*)(?:/[a-zA-Z0-9_-]+)?/?")
PAGE_SIZE = 10
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def amazon_job_id(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    match = JOB_PATH.fullmatch(parts.path)
    if parts.scheme == "https" and parts.netloc.lower() == HOST and match:
        return match[1]
    return None


def amazon_url_index(urls):
    index = {}
    for url in sorted(urls):
        if (identity := amazon_job_id(url)) is not None:
            index.setdefault(identity, url)
    return index


def supported(company):
    parts = urlsplit(company.careers_url)
    return (parts.scheme == "https" and parts.netloc == HOST and parts.path == "/en/search"
            and parse_qs(parts.query) == {"base_query": ["software student"], "country": ["ISR"]})


def _fetch(endpoint, timeout):
    deadline = time.monotonic() + timeout
    with urlopen(endpoint, timeout=timeout) as response:
        if response.geturl() != endpoint:
            raise ValueError("Unexpected Amazon search redirect")
        body = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Oversized Amazon search response")
        return json.loads(body)


def discover(company, timeout_ms, max_pages):
    """Return usable jobs and a partial flag; do not use as closure evidence."""
    jobs = {}
    partial = False
    deadline = time.monotonic() + timeout_ms / 1000
    for query in ("software student", "software intern"):
        # Give each query its own bounded share so a slow first query cannot
        # ordinarily consume the second query's entire budget.
        query_deadline = min(deadline, time.monotonic() + timeout_ms / 2000)
        seen = set()
        for page in range(max_pages):
            offset = page * PAGE_SIZE
            endpoint = f"https://{HOST}/en/search.json?" + urlencode(
                dict(base_query=query, country="ISR", offset=offset, result_limit=PAGE_SIZE)
            )
            try:
                remaining = query_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                payload = _fetch(endpoint, remaining)
                if (not isinstance(payload, dict) or payload.get("error")
                        or type(payload.get("hits")) is not int or payload["hits"] < 0
                        or not isinstance(payload.get("jobs"), list)
                        or len(payload["jobs"]) > PAGE_SIZE):
                    raise ValueError("Invalid Amazon search response")
            except (OSError, ValueError, HTTPException):
                partial = True
                break
            rows = payload["jobs"]
            for row in rows:
                if not isinstance(row, dict):
                    partial = True
                    continue
                path = row.get("job_path")
                title, city = row.get("title"), row.get("city")
                url = f"https://{HOST}{path}" if isinstance(path, str) and path.startswith("/") else ""
                identity = amazon_job_id(url)
                if (identity is None or row.get("id_icims") != identity
                        or not isinstance(title, str) or not title.strip()
                        or not isinstance(city, str) or not city.strip()
                        or row.get("country_code") != "ISR"):
                    partial = True
                    continue
                if identity in seen:
                    partial = True
                seen.add(identity)
                jobs.setdefault(identity, JobPosting(
                    title.strip(), company.name, f"{city.strip()}, ISR", canonical_url(url),
                    source=company.careers_url,
                ))
            if offset + len(rows) >= payload["hits"]:
                break
            if len(rows) < PAGE_SIZE:
                partial = True
                break
        else:
            partial = True
    return list(jobs.values()), partial
