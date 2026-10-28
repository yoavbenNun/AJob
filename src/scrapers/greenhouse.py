"""Official public Greenhouse board API, including custom careers-site links."""

import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.models import Company, JobPosting
from src.urls import canonical_url


BOARD_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}


def is_greenhouse_board(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in BOARD_HOSTS


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_text(content: str) -> str:
    parser = _Text()
    parser.feed(unescape(content))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def extract_greenhouse_jobs(company: Company, payload: dict, metrics: dict | None = None) -> list[JobPosting]:
    postings = payload.get("jobs")
    if not isinstance(postings, list):
        raise ValueError("Greenhouse response has no jobs list")
    if metrics is not None:
        metrics.update(raw_listings=len(postings), invalid_listing=0, duplicates_avoided=0,
                       page_limit_reached=False, location_filter_status="not_applied")
    jobs: dict[str, JobPosting] = {}
    for item in postings:
        if not isinstance(item, dict):
            if metrics is not None:
                metrics["invalid_listing"] += 1
            continue
        url = canonical_url(str(item.get("absolute_url") or ""))
        title = str(item.get("title") or "").strip()
        if (not title or not url or urlsplit(url).hostname not in company.allowed_job_hosts
                or not any(pattern.lower() in url.lower() for pattern in company.job_url_patterns)):
            if metrics is not None:
                metrics["invalid_listing"] += 1
            continue
        if metrics is not None:
            metrics["duplicates_avoided"] += int(url in jobs)
        location = item.get("location") or {}
        description = html_text(str(item.get("content") or ""))
        jobs.setdefault(url, JobPosting(
            title=title, company=company.name,
            location=str(location.get("name") or "Not specified").strip() if isinstance(location, dict) else "Not specified",
            job_url=url, description=description[:20_000], source=company.careers_url,
        ))
    return list(jobs.values())


def fetch_greenhouse_jobs(company: Company, timeout_ms: int, metrics: dict | None = None) -> list[JobPosting]:
    parts = urlsplit(company.careers_url)
    board = parts.path.strip("/")
    if not is_greenhouse_board(company.careers_url) or not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError("Invalid Greenhouse board URL")
    # This public endpoint returns the complete board; no browser pagination is needed.
    endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    request = Request(endpoint, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=max(1, timeout_ms // 1000)) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Invalid Greenhouse response")
    jobs = extract_greenhouse_jobs(company, payload, metrics)
    if metrics is not None and not metrics.get('invalid_listing'):
        from src.scrapers.closure_evidence import snapshot
        metrics['_closure_snapshot'] = snapshot(company.careers_url, payload, 'greenhouse')
    return jobs
