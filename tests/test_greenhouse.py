from unittest.mock import Mock

import pytest

from src.models import Company
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from src.scrapers.greenhouse import extract_greenhouse_jobs, fetch_greenhouse_jobs


def company():
    return Company("Example", "https://job-boards.greenhouse.io/example",
                   allowed_job_hosts=("careers.example.com",))


def posting(**updates):
    return {"title": "Software Intern", "absolute_url": "https://careers.example.com/jobs/1",
            "location": {"name": "Tel Aviv, Israel"},
            "content": "&lt;h2&gt;Requirements&lt;/h2&gt;&lt;p&gt;Currently pursuing a degree in Computer Science.&lt;/p&gt;",
            **updates}


def test_feed_keeps_explicit_location_full_content_and_deduplicates():
    jobs = extract_greenhouse_jobs(company(), {"jobs": [posting(), posting()]})
    assert len(jobs) == 1
    assert jobs[0].location == "Tel Aviv, Israel"
    assert jobs[0].description == "Requirements\nCurrently pursuing a degree in Computer Science."


def test_feed_rejects_untrusted_host_and_non_job_links():
    assert not extract_greenhouse_jobs(company(), {"jobs": [
        posting(absolute_url="https://evil.example/jobs/1"),
        posting(absolute_url="https://careers.example.com/about"),
        posting(absolute_url="javascript:alert(1)"),
    ]})


def test_feed_invalid_payload_is_visible_failure():
    with pytest.raises(ValueError, match="jobs list"):
        extract_greenhouse_jobs(company(), {})


def test_fetch_rejects_board_path_injection_before_network():
    with pytest.raises(ValueError, match="board URL"):
        fetch_greenhouse_jobs(Company("Bad", "https://job-boards.greenhouse.io/a/../b"), 1000)


def test_scanner_uses_feed_and_does_not_overwrite_with_browser_page(monkeypatch):
    jobs = extract_greenhouse_jobs(company(), {"jobs": [posting()]})
    monkeypatch.setattr("src.scrapers.careers_scraper.fetch_greenhouse_jobs", lambda *_: jobs)
    browser = Mock()
    result = CareersScraper(browser).scan_company(company())
    assert result.error is None
    assert "Currently pursuing" in result.jobs[0].requirements
    _enrich_job(browser, result.jobs[0], 1000)
    browser.new_page.assert_not_called()


def test_scanner_isolates_feed_failure(monkeypatch):
    def fail(*_):
        raise OSError("unavailable")
    monkeypatch.setattr("src.scrapers.careers_scraper.fetch_greenhouse_jobs", fail)
    result = CareersScraper(Mock()).scan_company(company())
    assert result.jobs == []
    assert "Greenhouse API failed" in result.error
