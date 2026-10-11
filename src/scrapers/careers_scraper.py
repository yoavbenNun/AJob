"""Playwright-based discovery of job links on configured career pages."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError

from src.filtering.job_filter import classify_job, prefilter_job
from src.models import Company, JobPosting
from src.urls import canonical_url


class JobWriter(Protocol):
    """Minimal persistence boundary needed by scan orchestration."""

    def save(self, job: JobPosting) -> int: ...


@dataclass(slots=True)
class CompanyScanResult:
    """Outcome of scanning one company without hiding partial failures."""

    company: Company
    jobs: list[JobPosting] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class ScanSummary:
    """Aggregate counters returned to the CLI."""

    companies_scanned: int = 0
    jobs_found: int = 0
    jobs_relevant: int = 0
    jobs_saved: int = 0
    failures: int = 0


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

        jobs_by_url.setdefault(
            url,
            JobPosting(
                title=title,
                company=company.name,
                location=_infer_location(context, title),
                job_url=url,
                description=" ".join(context.split()),
                source=company.careers_url,
            ),
        )

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
                for job in result.jobs:
                    prefilter = prefilter_job(job)
                    if not prefilter.relevant:
                        print(f"  Skipped: {job.title} - {prefilter.reason}")
                        continue
                    _enrich_job(browser, job, timeout_ms)
                    decision = classify_job(job)
                    if not decision.relevant:
                        print(f"  Skipped: {job.title} - {decision.reason}")
                        continue
                    summary.jobs_relevant += 1
                    relevant_for_company += 1
                    repository.save(job)
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
        "a[title*='next' i], button[title*='next' i]"
    )
    for index in range(next_controls.count()):
        control = next_controls.nth(index)
        if control.is_visible() and control.is_enabled():
            before_signature = _listing_signature(page)
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


def _enrich_job(browser: Browser, job: JobPosting, timeout_ms: int) -> None:
    """Populate full page text and a focused requirements excerpt, best effort."""
    page = browser.new_page()
    try:
        page.goto(job.job_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(750)
        main = page.locator("main")
        text = main.inner_text() if main.count() else page.locator("body").inner_text()
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if normalized:
            job.description = normalized[:20_000]
            job.requirements = extract_requirements(normalized)
            detail_location = extract_detail_location(normalized)
            if detail_location:
                job.location = detail_location
    except Exception:
        # Card data is still useful when an individual detail page is unavailable.
        return
    finally:
        page.close()


def extract_requirements(description: str) -> str:
    """Extract a requirements/qualifications section from detail-page text."""
    headings = (
        "minimum qualifications",
        "basic qualifications",
        "requirements",
        "qualifications",
        "what you'll need",
        "what you will need",
    )
    lower = description.casefold()
    starts = [lower.find(heading) for heading in headings]
    starts = [start for start in starts if start >= 0]
    if not starts:
        return ""
    start = min(starts)
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


def _title_from_url(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    parts = slug.split("-", 1)
    if len(parts) == 2 and parts[0].isdigit():
        slug = parts[1]
    return slug.replace("-", " ").strip().title()
