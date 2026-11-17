from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from src.models import JobPosting
from src.scrapers.careers_scraper import _enrich_job


URL = "https://jobs.apple.com/en-il/details/200676939-0865/software-engineering-student?team=HRDWR"
TITLE = "Software Engineering Student"


@pytest.mark.parametrize("scenario", ["usable", "wrong_title", "wrong_id", "empty_requirements",
                                      "loading_requirements", "empty_description", "http_error"])
def test_apple_detail_waits_for_verified_content_without_domcontentloaded(scenario):
    title = "Other Role" if scenario == "wrong_title" else TITLE
    role = "200676939-6109" if scenario == "wrong_id" else "200676939-0865"
    desc = "Loading..." if scenario == "empty_description" else "Develop low-level software in C++."
    qualifications = "" if scenario == "empty_requirements" else "<p>Currently pursuing a degree.</p>"
    if scenario == "loading_requirements":
        qualifications = "<p>Loading qualifications…</p>"
    html = f"""<head><script defer src="/stalled.js"></script></head><main>
        <h1>{title}</h1><p>Role Number:</p><p>{role}</p>
        <h2>Description</h2><p>{desc}</p>
        <h2>Minimum Qualifications</h2>{qualifications}<h2>Benefits</h2>
        <p>Python experience.</p></main>"""
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = p.chromium.launch()
        context = browser.new_context()

        def serve(route):
            if route.request.url == URL:
                route.fulfill(status=503 if scenario == "http_error" else 200,
                              content_type="text/html", body=html)
            # Leave the deferred script pending until the page closes.

        context.route("**/*", serve)
        if scenario == "usable":
            page = context.new_page()
            with pytest.raises(PlaywrightTimeoutError):
                page.goto(URL, wait_until="domcontentloaded", timeout=500)
            assert page.get_by_role("heading", name=TITLE, exact=True).is_visible()
            page.close()

        job = JobPosting(TITLE, "Apple", "Herzliya", URL,
                         description="Retained listing evidence", requirements="Prior requirements",
                         status="ignored")
        result = _enrich_job(context, job, 1800)
        assert job.job_url == URL and job.status == "ignored"
        if scenario == "usable":
            assert result is None
            assert desc in job.description and "Currently pursuing a degree." in job.requirements
        else:
            assert result == "JobDetailUnavailable"
            assert job.description == "Retained listing evidence" and job.requirements == "Prior requirements"
        assert context.pages == []
        browser.close()
