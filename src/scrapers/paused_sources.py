"""Public career feeds for AppsFlyer, Wix and SAP IT."""

import json
import re
import warnings
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.filtering.job_filter import prefilter_job
from src.models import Company, JobPosting
from src.urls import canonical_url


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _text(html):
    parser = _Text()
    parser.feed(unescape(html))
    return "\n".join(part.strip() for part in parser.parts if part.strip())


def supported(company: Company) -> bool:
    host = urlsplit(company.careers_url).hostname
    return host == "careers.appsflyer.com" or (
        host == "careers.smartrecruiters.com"
        and urlsplit(company.careers_url).path.rstrip("/") in {"/Wix2", "/SAPITBusinessSysteme"}
    )


def _read(url, timeout):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def extract_appsflyer_jobs(company: Company, html: str) -> list[JobPosting]:
    match = re.search(r"\bvar\s+af_gh_constants\s*=\s*", html)
    if not match:
        raise ValueError("AppsFlyer job feed is missing")
    payload, _ = json.JSONDecoder().raw_decode(html[match.end():])
    postings = payload["af_gh_jobs"]["jobs"]
    if not isinstance(postings, list):
        raise ValueError("AppsFlyer jobs must be a list")
    jobs = {}
    for posting in postings:
        url = canonical_url(posting.get("absolute_url", ""))
        title = posting.get("title", "").strip()
        if not title or urlsplit(url).hostname not in company.allowed_job_hosts:
            continue
        if not any(pattern in url for pattern in company.job_url_patterns):
            continue
        description = _text(posting.get("content", ""))
        jobs[url] = JobPosting(
            title=title, company=company.name, job_url=url,
            location=posting.get("location", {}).get("name") or "Not specified",
            description=description, source=company.careers_url,
        )
    return list(jobs.values())


def scan_public_feed(company: Company, timeout: int, max_pages: int) -> list[JobPosting]:
    if urlsplit(company.careers_url).hostname == "careers.appsflyer.com":
        return extract_appsflyer_jobs(company, _read(company.careers_url, timeout))

    tenant = urlsplit(company.careers_url).path.strip('/')
    if tenant not in {"Wix2", "SAPITBusinessSysteme"} or not supported(company):
        raise ValueError("Unsupported public careers feed")
    # These official careers sites link to these SmartRecruiters tenants. Construct
    # API/detail URLs ourselves rather than following untrusted payload links.
    jobs = {}
    for page in range(max_pages):
        endpoint = f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=100&offset={page * 100}&country=il"
        payload = json.loads(_read(endpoint, timeout))
        postings = payload.get("content")
        if not isinstance(postings, list):
            raise ValueError("SmartRecruiters response has no content list")
        for posting in postings:
            job_id = str(posting.get("id", ""))
            title = posting.get("name", "").strip()
            if not job_id.isdigit() or not title:
                continue
            url = f"https://jobs.smartrecruiters.com/{tenant}/{job_id}"
            if (urlsplit(url).hostname not in company.allowed_job_hosts
                    or not any(pattern in url for pattern in company.job_url_patterns)):
                continue
            location = posting.get("location", {})
            job = JobPosting(title=title, company=company.name, job_url=url,
                location=location.get("fullLocation") or "Not specified",
                source=company.careers_url)
            if prefilter_job(job).relevant:
                try:
                    detail = json.loads(_read(f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings/{job_id}", timeout))
                    sections = detail.get("jobAd", {}).get("sections", {})
                    job.description = "\n".join(
                        _text(section.get("title", "") + "\n" + section.get("text", ""))
                        for section in sections.values() if isinstance(section, dict)
                    )
                except (OSError, ValueError, TypeError, AttributeError):
                    # Keep listing evidence; the shared enrichment can retry this
                    # posting without discarding the company's other jobs.
                    warnings.warn(f"{company.name}: detail unavailable for posting {job_id}", RuntimeWarning)
            jobs[url] = job
        if len(postings) < 100:
            break
    return list(jobs.values())
