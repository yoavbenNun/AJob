"""Playwright-based discovery of job links on configured career pages."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import re
import time

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import Company, JobPosting
from src.job_content import description_problem, clean_job_text, QUALIFICATION_HEADING
from src.urls import canonical_url
from src.scrapers.greenhouse import fetch_greenhouse_jobs, is_greenhouse_board
from src.scrapers.official_sources import fetch_jobs as fetch_official_jobs, supported as supports_official_feed
from src.scrapers.paused_sources import scan_public_feed, supported as supports_public_feed
from src.scrapers.workday_details import workday_urls, legacy_workday_url, fetch_workday_detail
from src.scrapers.workday_search import israel_facet, explicitly_israel, contradicts_israel_filter, search_payload

WORKDAY_SEARCH_TERMS = (
    "student software",
    "software intern",
    "junior software",
    "graduate software",
)


class JobWriter(Protocol):
    """Minimal persistence boundary needed by scan orchestration."""

    def save(self, job: JobPosting) -> int: ...
    def get_by_url(self, job_url: str) -> JobPosting | None: ...
    def update_relevance_by_url(self, job_url: str, is_relevant: bool) -> None: ...


@dataclass(slots=True)
class CompanyScanResult:
    """Outcome of scanning one company without hiding partial failures."""

    company: Company
    jobs: list[JobPosting] = field(default_factory=list)
    error: str | None = None
    collection_metrics: dict = field(default_factory=dict)


@dataclass(slots=True)
class ScanSummary:
    """Aggregate counters returned to the CLI."""

    companies_scanned: int = 0
    jobs_found: int = 0
    jobs_relevant: int = 0
    jobs_saved: int = 0
    failures: int = 0
    new_jobs: list[JobPosting] = field(default_factory=list)


def extract_jobs_from_links(
    company: Company, links: Iterable[Mapping[str, Any]]
) -> list[JobPosting]:
    """Normalize Playwright link data into unique job postings.

    URL patterns are company configuration, so markup changes usually require a
    configuration update rather than a new scraper implementation.
    """
    jobs_by_url: dict[str, JobPosting] = {}
    careers_url = canonical_url(company.careers_url)

    for link in links:
        url = canonical_url(str(link.get("href", "")).strip())
        raw_text = str(link.get("text", "")).strip()
        context = str(link.get("context", "")).strip()
        title = _first_nonempty_line(raw_text) or _title_from_url(url)
        if (
            not url
            or url == careers_url
            or not title
            or (urlsplit(url).hostname or "").casefold() not in company.allowed_job_hosts
            or not any(pattern.lower() in url.lower() for pattern in company.job_url_patterns)
        ):
            continue

        candidate = JobPosting(
                title=title,
                company=company.name,
                location=_infer_location(context, title),
                job_url=url,
                description=" ".join(context.split()),
                source=company.careers_url,
            )
        existing = jobs_by_url.get(url)
        has_descriptive_rendered_title = bool(raw_text) and not _is_generic_link_text(raw_text)
        if (
            existing is None
            or _is_generic_link_text(existing.title)
            or (
                has_descriptive_rendered_title
                and existing.title == _title_from_url(url)
            )
        ):
            jobs_by_url[url] = candidate

    return list(jobs_by_url.values())


class CareersScraper:
    """Scan career pages using one caller-owned Playwright browser."""

    def __init__(
        self, browser: Browser, timeout_ms: int = 30_000, max_pages: int = 2
    ) -> None:
        self.browser = browser
        self.timeout_ms = timeout_ms
        self.max_pages = max_pages

    def scan_company(self, company: Company) -> CompanyScanResult:
        if supports_official_feed(company):
            try:
                jobs, metrics = fetch_official_jobs(company, self.timeout_ms)
                for job in jobs:
                    job.requirements = extract_requirements(job.description)
                return CompanyScanResult(company=company, jobs=jobs, collection_metrics=metrics)
            except Exception as error:
                return CompanyScanResult(company=company, error=f"Official careers feed failed: {type(error).__name__}")
        if supports_public_feed(company):
            try:
                jobs = scan_public_feed(company, max(1, self.timeout_ms // 1000), self.max_pages)
                for job in jobs:
                    job.requirements = extract_requirements(job.description)
                return CompanyScanResult(company=company, jobs=jobs)
            except Exception as error:
                return CompanyScanResult(company=company, error=f"Public careers feed failed: {error}")
        if is_greenhouse_board(company.careers_url):
            try:
                metrics = {}
                jobs = fetch_greenhouse_jobs(company, self.timeout_ms, metrics)
                for job in jobs:
                    job.requirements = extract_requirements(job.description)
                return CompanyScanResult(company=company, jobs=jobs, collection_metrics=metrics)
            except Exception as error:
                return CompanyScanResult(company=company, error=f"Greenhouse API failed: {error}")
        if (urlsplit(company.careers_url).hostname or "").endswith("myworkdayjobs.com"):
            return self._scan_workday(company)
        page = self.browser.new_page()
        try:
            page.goto(
                company.careers_url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            page.wait_for_timeout(1_500)
            jobs_by_url: dict[str, JobPosting] = {}
            for page_number in range(self.max_pages):
                for job in extract_jobs_from_links(company, _read_links(page)):
                    jobs_by_url.setdefault(job.job_url, job)
                if page_number + 1 >= self.max_pages or not _go_to_next_page(page):
                    break
                page.wait_for_timeout(1_000)
            return CompanyScanResult(
                company=company,
                jobs=list(jobs_by_url.values()),
            )
        except PlaywrightTimeoutError:
            return CompanyScanResult(
                company=company,
                error=f"Timed out after {self.timeout_ms} ms",
            )
        except Exception as error:  # A single broken site must not stop the scan.
            return CompanyScanResult(company=company, error=str(error))
        finally:
            page.close()

    def _scan_workday(self, company: Company) -> CompanyScanResult:
        """Read Workday's public careers JSON endpoint for configured tenants."""
        try:
            endpoint = _workday_endpoint(company.careers_url)
        except Exception as error:
            return CompanyScanResult(company=company, error=f"Workday API failed: {error}")

        jobs_by_url: dict[str, JobPosting] = {}
        successful_requests = 0
        request_errors: list[str] = []
        metrics = dict(focused_discovered=0, global_discovered=0, unique_israel_jobs=0,
                       duplicates_avoided=0, page_limit_reached=False,
                       focused_page_limit_reached=False, global_page_limit_reached=False,
                       location_filter_available=False, location_filter_status='unavailable', search_errors=0,
                       focused_budget_exhausted=False, focused_unique=0, global_unique=0,
                       focused_israel_unique=0, global_israel_unique=0,
                       comparison_complete=False, focused_only_israel_unique=None)

        def fetch(facets, text, offset, limit=20, timeout=None):
            request = Request(endpoint, data=json.dumps(
                {'appliedFacets': facets, 'limit': limit, 'offset': offset, 'searchText': text}
            ).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}, method='POST')
            with urlopen(request, timeout=timeout if timeout is not None else max(1, self.timeout_ms // 1000)) as response:
                return search_payload(json.load(response))

        try:
            # Metadata request is bounded and is not counted as a discovery scan.
            facets = israel_facet(fetch({}, '', 0, limit=1, timeout=min(10, max(1, self.timeout_ms // 1000))))
            metrics['location_filter_available'] = facets is not None
            if facets:
                metrics['location_filter_status'] = 'available'
        except Exception as error:
            facets = None
            metrics['location_filter_status'] = 'metadata_failed'
            metrics['metadata_error'] = type(error).__name__
            if isinstance(error, HTTPError) and error.code in {403, 429}:
                metrics['coverage_limited'] = True
                return CompanyScanResult(company, error='Workday access denied', collection_metrics=metrics)

        blocked = False
        scope_urls = {'focused': set(), 'global': set()}
        israel_urls = {'focused': set(), 'global': set()}
        global_queries_completed = 0
        for scope, applied in ([('focused', facets)] if facets else []) + [('global', {})]:
            unreliable = False
            focused_deadline = time.monotonic() + min(30, max(1, self.timeout_ms // 1000)) if scope == 'focused' else None
            for search_text in WORKDAY_SEARCH_TERMS:
                if blocked or unreliable:
                    break
                if focused_deadline is not None and time.monotonic() >= focused_deadline:
                    metrics['focused_budget_exhausted'] = True
                    break
                query_completed = True
                for page_number in range(self.max_pages):
                    try:
                        remaining = focused_deadline - time.monotonic() if focused_deadline is not None else None
                        if remaining is not None and remaining <= 0:
                            metrics['focused_budget_exhausted'] = True
                            break
                        payload = fetch(applied, search_text, page_number * 20, timeout=remaining)
                        page_jobs = extract_workday_jobs(company, payload)
                        raw = payload['jobPostings']
                        successful_requests += 1
                        metrics[scope + '_discovered'] += len(page_jobs)
                        for job in page_jobs:
                            scope_urls[scope].add(job.job_url)
                            if explicitly_israel(job):
                                israel_urls[scope].add(job.job_url)
                            metrics['duplicates_avoided'] += int(job.job_url in jobs_by_url)
                            jobs_by_url.setdefault(job.job_url, job)
                        if scope == 'focused' and contradicts_israel_filter(page_jobs):
                            metrics['location_filter_status'] = 'unreliable'
                            unreliable = True
                            break  # Keep discoveries, and fall back to the unchanged global search.
                        total = payload.get('total')
                        valid_total = (isinstance(total, int) and not isinstance(total, bool)
                                       and total >= page_number * 20 + len(raw))
                        more = (page_number + 1) * 20 < total if valid_total else len(raw) >= 20
                        if page_number + 1 == self.max_pages and more:
                            metrics[scope + '_page_limit_reached'] = True
                            metrics['page_limit_reached'] = True
                        if not more or len(raw) < 20:
                            break
                    except Exception as error:
                        query_completed = False
                        request_errors.append(type(error).__name__)
                        blocked = isinstance(error, HTTPError) and error.code in {403, 429}
                        break
                if scope == 'global' and query_completed:
                    global_queries_completed += 1

        metrics['unique_israel_jobs'] = sum(explicitly_israel(job) for job in jobs_by_url.values())
        metrics['focused_unique'] = len(scope_urls['focused'])
        metrics['global_unique'] = len(scope_urls['global'])
        metrics['focused_israel_unique'] = len(israel_urls['focused'])
        metrics['global_israel_unique'] = len(israel_urls['global'])
        metrics['comparison_complete'] = global_queries_completed == len(WORKDAY_SEARCH_TERMS)
        # Compare against ALL global URLs, including ambiguous location labels.
        # A partial/failed global search cannot establish the counterfactual gap.
        metrics['focused_only_israel_unique'] = (
            len(israel_urls['focused'] - scope_urls['global']) if metrics['comparison_complete'] else None)
        metrics['search_errors'] = len(request_errors)
        metrics['coverage_limited'] = bool(request_errors or metrics['page_limit_reached']
                                           or metrics['focused_budget_exhausted']
                                           or metrics['location_filter_status'] != 'available')

        if successful_requests == 0:
            detail = "; ".join(request_errors[:2]) or "no query completed"
            return CompanyScanResult(company=company, error=f"Workday API failed: {detail}", collection_metrics=metrics)
        return CompanyScanResult(company=company, jobs=list(jobs_by_url.values()), collection_metrics=metrics)


def scan_companies(
    companies: Iterable[Company],
    repository: JobWriter,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    max_pages: int = 2,
) -> ScanSummary:
    """Browse enabled companies, persist discovered jobs, and print progress."""
    from playwright.sync_api import sync_playwright

    enabled_companies = [company for company in companies if company.enabled]
    summary = ScanSummary()
    if not enabled_companies:
        print("No enabled companies to scan.")
        return summary

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            scraper = CareersScraper(
                browser, timeout_ms=timeout_ms, max_pages=max_pages
            )
            for company in enabled_companies:
                summary.companies_scanned += 1
                print(f"Scanning {company.name}: {company.careers_url}")
                result = scraper.scan_company(company)
                if result.error:
                    summary.failures += 1
                    print(f"  Failed: {result.error}")
                    continue

                summary.jobs_found += len(result.jobs)
                relevant_for_company = 0
                detail_failure_reported = False
                detail_blocked = False
                for job in result.jobs:
                    prefilter = prefilter_job(job)
                    if not prefilter.relevant:
                        repository.update_relevance_by_url(job.job_url, False)
                        print(f"  Skipped: {job.title} - {prefilter.reason}")
                        continue
                    detail_error = 'WorkdayAccessDenied' if detail_blocked else _enrich_job(browser, job, timeout_ms)
                    detail_blocked = detail_error == 'WorkdayAccessDenied'
                    invalid_detail = detail_error or description_problem(job.description, job.requirements)
                    if invalid_detail and not detail_failure_reported:
                        summary.failures += 1
                        detail_failure_reported = True
                        print('  JobDetailUnavailable: retained for the next scheduled scan')
                    decision = classify_job(job)
                    if not decision.relevant and not invalid_detail:
                        repository.update_relevance_by_url(job.job_url, False)
                        print(f"  Skipped: {job.title} - {decision.reason}")
                        continue
                    summary.jobs_relevant += 1
                    relevant_for_company += 1
                    existing = repository.get_by_url(job.job_url)
                    alias = legacy_workday_url(job.source, job.job_url)
                    if existing is None and alias:
                        existing = repository.get_by_url(alias)
                        if existing is not None:
                            job.job_url = existing.job_url
                    repository.save(job)
                    if existing is None:
                        summary.new_jobs.append(job)
                    summary.jobs_saved += 1
                    print(f"  Saved: {job.title} - {decision.reason}")
                print(
                    f"  Reviewed {len(result.jobs)} candidate(s); "
                    f"saved {relevant_for_company} relevant role(s)."
                )
        finally:
            browser.close()
    return summary


def preview_companies(companies: Iterable[Company]) -> int:
    """Print enabled career pages without launching a browser."""
    enabled_companies = [company for company in companies if company.enabled]
    if not enabled_companies:
        print("No enabled companies to scan.")
        return 0
    print(f"Dry run: {len(enabled_companies)} company page(s) would be scanned:")
    for company in enabled_companies:
        print(f"- {company.name}: {company.careers_url}")
    return len(enabled_companies)


def _workday_endpoint(careers_url: str) -> str:
    parsed = urlsplit(careers_url)
    host_parts = (parsed.hostname or "").split(".")
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(host_parts) < 4 or len(path_parts) < 2 or path_parts[0] not in {"en-US", "en-us"}:
        raise ValueError("Unsupported Workday careers URL structure")
    tenant = host_parts[0]
    site = path_parts[1]
    return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}/jobs"


def extract_workday_jobs(company: Company, payload: Mapping[str, Any]) -> list[JobPosting]:
    """Normalize Workday job postings without depending on rendered markup."""
    postings = payload.get("jobPostings", [])
    if not isinstance(postings, list):
        raise ValueError("Workday response has no jobPostings list")
    jobs: list[JobPosting] = []
    for posting in postings:
        if not isinstance(posting, Mapping):
            continue
        title = str(posting.get("title", "")).strip()
        external_path = str(posting.get("externalPath", "")).strip()
        if not title or not external_path.startswith("/"):
            continue
        try:
            url, _ = workday_urls(company.careers_url, external_path)
        except ValueError:
            continue
        if not url or (urlsplit(url).hostname or "").casefold() not in company.allowed_job_hosts:
            continue
        jobs.append(
            JobPosting(
                title=title,
                company=company.name,
                location=str(posting.get("locationsText", "")).strip() or "Not specified",
                job_url=url,
                # Listing badges are not a verified description. Leave it empty
                # so a blocked detail fetch cannot overwrite prior valid content.
                description="",
                source=company.careers_url,
            )
        )
    return jobs


def _read_links(page: Page) -> list[dict[str, str]]:
    return page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(anchor => ({
            href: anchor.href,
            text: (anchor.innerText || anchor.textContent || '').trim(),
            context: (anchor.closest('article, li, section, div')?.innerText || '').trim()
        }))"""
    )


def _go_to_next_page(page: Page) -> bool:
    next_controls = page.locator(
        "a[aria-label*='next' i], button[aria-label*='next' i], "
        "a[title*='next' i], button[title*='next' i], "
        ".pagination a.next:not(.disabled):not([aria-disabled='true'])"
    )
    for index in range(next_controls.count()):
        control = next_controls.nth(index)
        if control.is_visible() and control.is_enabled():
            before_signature = _listing_signature(page)
            href = control.get_attribute("href")
            next_url = urljoin(page.url, href) if href else ""
            # Follow real same-origin pagination links directly. Site banners
            # can cover a perfectly usable Next anchor and intercept its click.
            if (next_url and urlsplit(next_url).scheme in {"http", "https"}
                    and urlsplit(next_url).netloc == urlsplit(page.url).netloc
                    and canonical_url(next_url) != canonical_url(page.url)):
                page.goto(next_url, wait_until="domcontentloaded", timeout=10_000)
            else:
                control.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(500)
            return _listing_signature(page) != before_signature
    return False


def _listing_signature(page: Page) -> tuple[str, ...]:
    hrefs = page.locator("a[href]").evaluate_all(
        "anchors => anchors.map(anchor => anchor.href)"
    )
    return tuple(sorted(set(hrefs)))


def _enrich_job(browser: Browser, job: JobPosting, timeout_ms: int) -> str | None:
    """Populate full page text and a focused requirements excerpt, best effort."""
    if '.myworkdayjobs.com/' in job.source:
        try:
            detail = fetch_workday_detail(job.source, job.job_url, job.title,
                                         timeout=max(1, timeout_ms // 1000))
            job.description = detail.description
            job.requirements = extract_requirements(detail.description)
            return None
        except HTTPError as error:
            return 'WorkdayAccessDenied' if error.code in {403, 429} else 'JobDetailUnavailable'
        except Exception:
            return 'JobDetailUnavailable'
    if (is_greenhouse_board(job.source) or supports_official_feed(
        Company(name=job.company, careers_url=job.source)
    ) or supports_public_feed(
        Company(name=job.company, careers_url=job.source)
    )) and job.description:
        # The official feed already supplies full content and precise location.
        # Custom careers sites may return an anti-bot page that would overwrite it.
        return
    page = browser.new_page()
    try:
        response = page.goto(job.job_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(750)
        main = page.locator("main")
        text = main.inner_text() if main.count() else page.locator("body").inner_text()
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        problem = description_problem(normalized, status_code=response.status if response is not None else None)
        if problem:
            return 'JobDetailUnavailable'
        if normalized:
            job.description = normalized[:20_000]
            job.requirements = extract_requirements(normalized)
            detail_location = extract_detail_location(normalized)
            if detail_location:
                job.location = detail_location
    except Exception:
        # Card data is still useful when an individual detail page is unavailable.
        return 'JobDetailUnavailable'
    finally:
        page.close()


def extract_requirements(description: str) -> str:
    """Extract a requirements/qualifications section from detail-page text."""
    description = clean_job_text(description)
    heading = re.search(r'(?im)^\s*' + QUALIFICATION_HEADING, description)
    if not heading:
        return ""
    start = heading.start()
    return description[start : start + 6_000]


def extract_detail_location(description: str) -> str:
    """Extract locations from common detail-page accessibility markers."""
    lines = [" ".join(line.split()) for line in description.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.casefold() != "place":
            continue
        locations: list[str] = []
        for candidate in lines[index + 1 : index + 8]:
            normalized = candidate.strip(" ;")
            if candidate.casefold() in {"bar_chart", "apply", "info_outline"}:
                break
            if normalized and normalized != "+3 more":
                locations.append(normalized)
        return "; ".join(locations)[:200]
    if lines and lines[0].casefold() == "back to search results" and len(lines) > 2:
        locations: list[str] = []
        stop_labels = {
            "hardware", "software and services", "machine learning and ai",
            "corporate functions", "retail", "sales and business development",
            "submit resume",
        }
        for candidate in lines[2:12]:
            if candidate.casefold() in stop_labels:
                break
            if candidate.casefold().startswith("work locations"):
                continue
            if candidate not in locations:
                locations.append(candidate)
        if locations:
            return "; ".join(locations)[:200]
    return ""


def _infer_location(context: str, title: str) -> str:
    lines = [" ".join(line.split()) for line in context.splitlines() if line.strip()]
    if lines and lines[0].casefold() == title.casefold():
        lines.pop(0)
    if not lines or lines[0].casefold() in {
        "apply",
        "apply now",
        "learn more",
        "view job",
        "view details",
    }:
        return "Not specified"
    return lines[0][:200]


def _first_nonempty_line(value: str) -> str:
    return next((" ".join(line.split()) for line in value.splitlines() if line.strip()), "")


def _is_generic_link_text(value: str) -> bool:
    return value.casefold().strip() in {
        "apply",
        "apply now",
        "learn more",
        "view details",
        "view job",
    }


def _title_from_url(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    parts = slug.split("-", 1)
    if len(parts) == 2 and parts[0].isdigit():
        slug = parts[1]
    return slug.replace("-", " ").strip().title()
