"""Bounded public feeds linked by the employers' current career pages."""

import base64
import json
import re
from html import unescape
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.models import Company, JobPosting
from src.scrapers.greenhouse import extract_greenhouse_jobs, html_text
from src.scrapers.elbit import listings as elbit_listings
from src.urls import canonical_url


def supported(company: Company) -> bool:
    host = urlsplit(company.careers_url).hostname
    return host in {"www.taboola.com", "www.fiverr.com", "careers.lightricks.com", "elbitsystemscareer.com", "careers.qualcomm.com"}


def _read(url, timeout):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def extract_listings(company, items, metrics):
    """Validate employer URLs without guessing location or job eligibility."""
    if not isinstance(items, list):
        raise ValueError("Official careers response has no listings list")
    jobs = {}
    metrics["raw_listings"] = metrics.get("raw_listings", 0) + len(items)
    for item in items:
        if not isinstance(item, dict):
            metrics["invalid_listing"] = metrics.get("invalid_listing", 0) + 1
            continue
        url = canonical_url(str(item.get("url") or ""))
        title = unescape(str(item.get("title") or "")).strip()
        if (not title or urlsplit(url).scheme != "https"
                or urlsplit(url).hostname not in company.allowed_job_hosts
                or not any(pattern in url for pattern in company.job_url_patterns)):
            metrics["invalid_listing"] = metrics.get("invalid_listing", 0) + 1
            continue
        if url in jobs:
            metrics["duplicates_avoided"] = metrics.get("duplicates_avoided", 0) + 1
            continue
        jobs[url] = JobPosting(title, company.name, item.get("location") or "Not specified",
                               url, description=item.get("description", "")[:20_000],
                               source=company.careers_url)
    return list(jobs.values())


def extract_fiverr_jobs(company, html, metrics):
    match = re.search(r'"comeetPositions"\s*:', html)
    if not match:
        raise ValueError("Fiverr careers listings are missing")
    items, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
    if not isinstance(items, list):
        raise ValueError("Fiverr careers listings are invalid")
    listings = []
    for item in items:
        if not isinstance(item, dict):
            listings.append({})
            continue
        encoded = str(item.get("encodedId") or "")
        uid = str(item.get("uid") or "")
        # Stable official URLs encode the Comeet requisition ID, not a title.
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", encoded)
                or base64.urlsafe_b64encode(uid.encode()).decode().rstrip("=") != encoded):
            listings.append({})
            continue
        details = item.get("details") or []
        if not isinstance(details, list):
            raise ValueError("Fiverr job details are invalid")
        listings.append(dict(title=item.get("title"), location=item.get("location"),
                             url=f"https://www.fiverr.com/jobs/{encoded}",
                             description="\n".join(html_text(str(d.get("name") or "") + "\n" +
                                                             str(d.get("value") or ""))
                                                   for d in details if isinstance(d, dict))))
    return extract_listings(company, listings, metrics)


def fetch_jobs(company, timeout_ms):
    if not supported(company):
        raise ValueError("Unsupported official careers feed")
    if urlsplit(company.careers_url).hostname == 'careers.qualcomm.com':
        from src.scrapers.qualcomm import fetch_jobs as fetch_qualcomm_jobs
        return fetch_qualcomm_jobs(company, timeout_ms)
    timeout = max(1, timeout_ms // 1000)
    metrics = dict(raw_listings=0, invalid_listing=0, duplicates_avoided=0,
                   page_limit_reached=False, location_filter_status="not_applied")
    host = urlsplit(company.careers_url).hostname
    if host == "elbitsystemscareer.com":
        # One complete snapshot, advertised by the employer's own search page;
        # no 25-card browser pagination or per-posting detail requests.
        items = json.loads(_read("https://elbitsystemscareer.com/cron/jobs.json", timeout))
        jobs = extract_listings(company, elbit_listings(items), metrics)
        if not metrics['invalid_listing']:
            from src.scrapers.closure_evidence import snapshot
            metrics['_closure_snapshot'] = snapshot(company.careers_url, items, 'elbit')
    elif host == "www.taboola.com":
        items = json.loads(_read("https://www.taboola.com/wp-content/uploads/careers/jobs.json", timeout))
        if not isinstance(items, list):
            raise ValueError("Taboola careers listings are invalid")
        jobs = extract_listings(company, [dict(title=j.get("title"), url=j.get("link"),
            location=j.get("office_textual")) if isinstance(j, dict) else {} for j in items], metrics)
    elif host == "www.fiverr.com":
        jobs = extract_fiverr_jobs(company, _read("https://www.fiverr.com/jobs", timeout), metrics)
    else:
        # The former Lightricks board is empty. Both successor boards are linked
        # explicitly by careers.lightricks.com; scan each once, with no retries.
        jobs_by_url = {}
        for board in ("ltx", "facetune"):
            payload = json.loads(_read(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", timeout))
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise ValueError("Lightricks careers listings are invalid")
            metrics["raw_listings"] += len(payload["jobs"])
            board_metrics = {}
            parsed = extract_greenhouse_jobs(company, payload, board_metrics)
            metrics["duplicates_avoided"] += board_metrics["duplicates_avoided"]
            for job in parsed:
                metrics["duplicates_avoided"] += int(job.job_url in jobs_by_url)
                jobs_by_url.setdefault(job.job_url, job)
            metrics["invalid_listing"] += board_metrics["invalid_listing"]
        jobs = list(jobs_by_url.values())
    metrics["global_discovered"] = len(jobs)
    return jobs, metrics
