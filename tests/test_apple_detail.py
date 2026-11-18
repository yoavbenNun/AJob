from pathlib import Path
import json

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
    "multiple_main", "decoy_requirements", "ambiguous_boundary", "empty_requirements",
    "loading_requirements", "please_wait",
    "trailing_placeholder", "empty_description", "http_error", "navigation_timeout",
])
def test_apple_detail_waits_for_verified_content_without_domcontentloaded(scenario, caplog):
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
        "ambiguous_boundary": "<p>Benefits</p><p>Additional requirements.</p>",
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
            assert not [r for r in caplog.records if r.name == "src.scrapers.apple"]
            assert desc in job.description and "Currently pursuing a degree." in job.requirements
            assert "Python experience." not in job.requirements
            if scenario == "preferred":
                assert "Embedded experience." in job.requirements
        else:
            assert result == "JobDetailUnavailable"
            assert job.description == "Retained listing evidence" and job.requirements == "Prior requirements"
            records = [json.loads(record.message) for record in caplog.records
                       if record.name == "src.scrapers.apple"]
            assert len(records) == 1
            record = records[0]
            assert record["posting_id"] == "200676939-0865"
            assert record["content_verified"] is False
            assert record["timeout_ms"] == 1800 and record["elapsed_ms"] >= 0
            assert len(json.dumps(record)) < 512
            assert record["stage"] == ("navigation" if scenario == "navigation_timeout"
                                       else "response_validation" if scenario == "http_error"
                                       else "content_verification")
            assert record["category"] == ("http_error" if scenario == "http_error" else "timeout")
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
    records = [json.loads(record.message) for record in caplog.records
               if record.name == "src.scrapers.apple"]
    assert [record["category"] for record in records] == [
        "cancelled" if primary is KeyboardInterrupt else "timeout", "cleanup_error"]
    assert records[0]["stage"] == "navigation"


def test_apple_diagnostic_never_logs_external_text_or_changes_outcome(monkeypatch, caplog):
    from src.scrapers import apple
    class Browser:
        def new_page(self):
            raise RuntimeError("PRIVATE cookie token resume https://example.test")
    job = JobPosting(TITLE, "Apple", "Herzliya", URL + "&secret=PRIVATE", description="Original")
    assert _enrich_job(Browser(), job, 1000) == "JobDetailUnavailable"
    record = json.loads(next(r.message for r in caplog.records if r.name == apple.__name__))
    assert record["stage"] == "page_creation" and record["category"] == "unexpected_error"
    assert not record["navigation_completed"] and not record["content_verified"]
    assert "PRIVATE" not in caplog.text and "https:" not in caplog.text
    assert job.description == "Original"
    class BrokenLogger:
        def warning(self, *args):
            raise RuntimeError("handler failed")
    # Direct helper verifies the new instrumentation is best effort.
    with monkeypatch.context() as patch:
        patch.setattr(apple.logging, "getLogger", lambda *_: BrokenLogger())
        apple.report_failure(job.job_url, dict(stage="navigation", navigation_completed=False,
                                             content_verified=False), 0, 1000, "timeout")


def test_non_apple_failure_has_no_apple_diagnostic(caplog):
    class Page:
        def goto(self, *args, **kwargs):
            raise TimeoutError("unrelated employer")
        def close(self):
            pass
    class Browser:
        def new_page(self):
            return Page()
    job = JobPosting(TITLE, "Other", "Israel", "https://example.test/job", description="Original")
    assert _enrich_job(Browser(), job, 1000) == "JobDetailUnavailable"
    assert job.description == "Original"
    assert not [r for r in caplog.records if r.name == "src.scrapers.apple"]
