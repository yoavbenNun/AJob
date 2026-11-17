from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from src.models import JobPosting
from src.scrapers.careers_scraper import _enrich_job


URL = "https://jobs.apple.com/en-il/details/200676939-0865/software-engineering-student?team=HRDWR"
TITLE = "Software Engineering Student"


# Each adversarial fixture protects one boundary: identity, section ownership,
# completeness or transport. Invalid pages must leave both evidence fields intact.
@pytest.mark.parametrize("scenario", [
    "usable", "inline_header", "preferred", "wrong_title", "wrong_id", "duplicate_id",
    "mixed_postings", "inline_second_id", "embedded_role_marker", "bare_foreign_id",
    "duplicate_description", "duplicate_minimum", "missing_minimum", "missing_description",
    "multiple_main", "decoy_requirements", "empty_requirements", "loading_requirements", "please_wait",
    "trailing_placeholder", "empty_description", "http_error", "navigation_timeout",
])
def test_apple_detail_waits_for_verified_content_without_domcontentloaded(scenario):
    title = "Other Role" if scenario == "wrong_title" else TITLE
    role = "200676939-6109" if scenario == "wrong_id" else "200676939-0865"
    desc = "Loading..." if scenario == "empty_description" else "Develop low-level software in C++."
    qualifications = "" if scenario == "empty_requirements" else "<p>Currently pursuing a degree.</p>"
    if scenario == "loading_requirements":
        qualifications = "<p>Loading qualifications…</p>"
    if scenario == "please_wait":
        qualifications = "<p>Please wait...</p>"
    if scenario == "mixed_postings":
        qualifications = '<p>Role Number:</p><p>200676939-6109</p><p>Other posting requirements.</p>'
    extras = {
        "duplicate_id": "<p>Role Number: 200676939-0865</p>",
        "inline_second_id": "<p>Role Number: 200676939-6109</p><p>Other posting requirements.</p>",
        "embedded_role_marker": "<p>Python experience. Role <b>Number:</b> 200676939-6109. Other posting requirements.</p>",
        "bare_foreign_id": "<p>Other posting requirements (200676939-6109).</p>",
        "duplicate_description": "<h2>Description</h2><p>Other responsibilities.</p>",
        "duplicate_minimum": "<h2>Minimum Qualifications</h2><p>Other requirements.</p>",
        "trailing_placeholder": "<p>Please wait...</p>",
        "preferred": "<h2>Preferred Qualifications</h2><p>Embedded experience.</p>",
    }
    qualifications += extras.get(scenario, "")
    header = (f"<p>Role Number: {role}</p>" if scenario == "inline_header"
              else f"<p>Role Number:</p><p>{role}</p>")
    description_heading = "" if scenario == "missing_description" else "<h2>Description</h2>"
    minimum_heading = "" if scenario == "missing_minimum" else "<h2>Minimum Qualifications</h2>"
    html = f"""<head><script defer src="/stalled.js"></script></head><main>
        <h1>{title}</h1>{header}
        {description_heading}<p>{desc}</p>
        {minimum_heading}{qualifications}<h2>Benefits</h2>
        <p>Python experience.</p></main>"""
    if scenario == "multiple_main":
        html += "<main><h1>Another posting</h1></main>"
    if scenario == "decoy_requirements":
        html = html.replace("<h2>Description</h2>", "<h2>Qualifications</h2><p>Other team.</p><h2>Description</h2>")
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = p.chromium.launch()
        context = browser.new_context()

        def serve(route):
            if route.request.url == URL and scenario != "navigation_timeout":
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
        if scenario in {"usable", "inline_header", "preferred"}:
            assert result is None
            assert desc in job.description and "Currently pursuing a degree." in job.requirements
            assert "Python experience." not in job.requirements
            if scenario == "preferred":
                assert "Embedded experience." in job.requirements
        else:
            assert result == "JobDetailUnavailable"
            assert job.description == "Retained listing evidence" and job.requirements == "Prior requirements"
        assert context.pages == []
        browser.close()


@pytest.mark.parametrize("primary", [TimeoutError, KeyboardInterrupt])
def test_apple_cleanup_does_not_mask_primary_failure_or_cancellation(primary, caplog):
    class Page:
        def goto(self, *args, **kwargs):
            raise primary()

        def close(self):
            raise RuntimeError()

    class Browser:
        def new_page(self):
            return Page()

    job = JobPosting(TITLE, "Apple", "Herzliya", URL, description="Original")
    if primary is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            _enrich_job(Browser(), job, 1000)
    else:
        assert _enrich_job(Browser(), job, 1000) == "JobDetailUnavailable"
        assert "Apple detail unavailable: TimeoutError" in caplog.text
    assert "Apple detail cleanup failed: RuntimeError" in caplog.text
    assert job.description == "Original"
