from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job, prefilter_job
from src.scrapers import google
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from test_job_filter import google_early_ii


@pytest.mark.parametrize("failure", [
    None, "navigation", "page_creation", "page_cleanup", "cross_search", "same_page",
])
def test_google_israel_search_preserves_paginated_global_roles_and_runs_existing_filter(failure):
    company = next(c for c in load_companies("config/companies.yaml") if c.name == "Google")
    target = google_early_ii()
    calls = []
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = p.chromium.launch()
        context = browser.new_context()

        def serve(route):
            url = route.request.url
            query = parse_qs(urlsplit(url).query)
            if google.job_id(url) == google.job_id(target.job_url):
                route.fulfill(content_type="text/html", body=f"<main><pre>{target.description}</pre></main>")
                return
            if not url.startswith(google.SEARCH):
                route.abort()
                return
            calls.append(query)
            if "location" in query:
                assert query["location"] == ["Israel"]
                assert set(query["target_level"]) == {"EARLY", "INTERN_AND_APPRENTICE"}
                if failure in {"navigation", "same_page"}:
                    route.fulfill(status=503)
                    return
                body = f'<a href="{target.job_url}"></a><a href="{google.SEARCH}100-renamed?location=Israel"></a>'
            elif query.get("page") == ["2"]:
                if failure == "cross_search":
                    route.fulfill(status=503)
                    return
                body = f'<a href="{google.SEARCH}101-software-engineering-student"></a>'
            else:
                body = (f'<a href="{google.SEARCH}100-software-engineering-intern?q=software+intern"></a>'
                        '<a aria-label="Next page" href="?q=software+intern&amp;page=2">Next</a>')
            route.fulfill(content_type="text/html", body=body)

        context.route("**/*", serve)
        created = []

        class SimulatedBrowser:
            def new_page(self):
                if failure == "page_creation" and created:
                    raise RuntimeError("simulated page creation failure")
                page = context.new_page()
                created.append(page)
                if failure in {"page_cleanup", "cross_search", "same_page"} and len(created) == 2:
                    close = page.close

                    def fail_close():
                        close()
                        raise RuntimeError("simulated page cleanup failure")

                    page.close = fail_close
                return page

        scraper = CareersScraper(SimulatedBrowser(), timeout_ms=2000)
        result = scraper.scan_company(company)
        jobs = {google.job_id(job.job_url): job for job in result.jobs}
        assert len(calls) == (2 if failure == "page_creation" else 3)
        assert all(page.is_closed() for page in created)
        expected = ["100"] if failure == "cross_search" else ["100", "101"]
        if failure in {None, "page_cleanup", "cross_search"}:
            expected.append(google.job_id(target.job_url))
        assert [google.job_id(job.job_url) for job in result.jobs] == expected
        assert jobs["100"].job_url.endswith("100-software-engineering-intern?q=software+intern")
        assert result.error == ("GoogleDiscoveryIncomplete" if failure else None)
        if failure in {"page_creation", "page_cleanup"}:
            assert result.failure_details["stage"] == failure
            assert result.failure_details["exception_type"] == "RuntimeError"
        elif failure:
            assert result.failure_details["stage"] == "fetch"
        assert result.closure_snapshot is None
        if failure is None:
            job = jobs[google.job_id(target.job_url)]
            assert prefilter_job(job).relevant
            assert _enrich_job(context, job, 2000) is None
            assert classify_job(job).relevant
        browser.close()


def test_google_stored_search_aliases_share_an_identity():
    old = google.SEARCH + "100-original?page=2&q=software+intern"
    new = google.SEARCH + "100-renamed?location=Israel"
    assert google.url_index([old])[google.job_id(new)] == old
    assert google.job_id(new.replace("www.google.com", "www.google.com.test")) is None
