from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job, prefilter_job
from src.scrapers import google
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from test_job_filter import google_early_ii


@pytest.mark.parametrize("fail", [False, True])
def test_google_israel_search_preserves_paginated_global_roles_and_runs_existing_filter(fail):
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
                if fail:
                    route.fulfill(status=503)
                    return
                body = f'<a href="{target.job_url}"></a><a href="{google.SEARCH}100-renamed?location=Israel"></a>'
            elif query.get("page") == ["2"]:
                body = f'<a href="{google.SEARCH}101-software-engineering-student"></a>'
            else:
                body = (f'<a href="{google.SEARCH}100-software-engineering-intern?q=software+intern"></a>'
                        '<a aria-label="Next page" href="?q=software+intern&amp;page=2">Next</a>')
            route.fulfill(content_type="text/html", body=body)

        context.route("**/*", serve)
        scraper = CareersScraper(context, timeout_ms=2000)
        result = scraper.scan_company(company)
        jobs = {google.job_id(job.job_url): job for job in result.jobs}
        assert len(calls) == 3
        assert list(jobs)[:2] == ["100", "101"]
        assert jobs["100"].job_url.endswith("100-software-engineering-intern?q=software+intern")
        assert len(result.jobs) == (2 if fail else 3)
        assert result.error == ("GoogleDiscoveryIncomplete" if fail else None)
        assert result.closure_snapshot is None
        if not fail:
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
