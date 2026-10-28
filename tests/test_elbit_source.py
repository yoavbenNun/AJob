"""Official Elbit feed regressions; synthetic metadata and no network/database."""

import json
from unittest.mock import Mock

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job
from src.location.commute import is_israel_or_remote_location
from src.scrapers import official_sources as feeds
from src.scrapers.careers_scraper import CareersScraper, _enrich_job


def company():
    return next(c for c in load_companies("config/companies.yaml") if c.name == "Elbit Systems")


def posting(**changes):
    # Public requisition 6886 (jobId 20711), reduced to relevant job fields.
    return dict(jobId=20711, jobCode="6886", jobTitle="Software Engineer Student", status=1,
                extendedProperties=[dict(PropertyName="Cities", Value="416")],
                description="&lt;p&gt;Develop software in C++ with a focus on real-time systems.&lt;/p&gt;"
                            "&lt;p&gt;Requirements&lt;/p&gt;&lt;p&gt;B.Sc. student in Computer Science, "
                            "Software Engineering or another relevant field. Minimum of one year "
                            "remaining until graduation. At least two working days per week.&lt;/p&gt;",
                **changes)


def test_complete_snapshot_without_pagination_preserves_identity_and_content(monkeypatch):
    item = posting()
    # The real browser shows only 25 cards; a late student posting must survive.
    payload = [dict(item, jobId=1000 + i, jobTitle="Senior Software Engineer") for i in range(60)]
    payload.extend([item, item])
    read = Mock(return_value=json.dumps(payload))
    monkeypatch.setattr(feeds, "_read", read)
    browser = Mock()
    result = CareersScraper(browser).scan_company(company())
    assert result.error is None and len(result.jobs) == 61
    job = next(j for j in result.jobs if j.title == item["jobTitle"])
    assert job.job_url == "https://elbitsystemscareer.com/job?jid=20711"
    assert job.location == "חיפה" and is_israel_or_remote_location(job.location)
    assert "Minimum of one year" in job.requirements
    assert classify_job(job).relevant
    assert not classify_job(result.jobs[0]).relevant
    assert result.collection_metrics["raw_listings"] == 62
    assert result.collection_metrics["duplicates_avoided"] == 1
    assert result.collection_metrics["page_limit_reached"] is False
    read.assert_called_once_with("https://elbitsystemscareer.com/cron/jobs.json", 30)
    assert _enrich_job(browser, job, 1000) is None
    browser.new_page.assert_not_called()


@pytest.mark.parametrize("value", [None, "new-city-id", "7", "131", "Countrywide", "North"])
def test_unknown_or_non_city_codes_never_establish_israel(monkeypatch, value):
    item = posting()
    item["extendedProperties"] = [dict(PropertyName="Cities", Value=value)]
    monkeypatch.setattr(feeds, "_read", lambda *_: json.dumps([item]))
    jobs, _ = feeds.fetch_jobs(company(), 1000)
    assert jobs[0].location == "Not specified"
    assert not is_israel_or_remote_location(jobs[0].location)


def test_closed_invalid_and_injected_id_records_do_not_create_jobs(monkeypatch):
    item = posting()
    payload = [dict(item, status=0), dict(item, status=True), dict(item, jobId=True),
               dict(item, jobId="20711/../../other"), dict(item, jobId=-1), None, item]
    monkeypatch.setattr(feeds, "_read", lambda *_: json.dumps(payload))
    jobs, metrics = feeds.fetch_jobs(company(), 1000)
    assert len(jobs) == 1 and metrics["invalid_listing"] == 6


def test_extended_staff_metadata_is_never_carried_into_job(monkeypatch):
    item = posting()
    item["extendedProperties"].append(dict(PropertyName="staff", Value="PRIVATE_FIXTURE"))
    item["employerName"] = "PRIVATE_FIXTURE"
    item["requirements"] = "<p>Python</p>"
    monkeypatch.setattr(feeds, "_read", lambda *_: json.dumps([item]))
    job = CareersScraper(Mock()).scan_company(company()).jobs[0]
    assert "PRIVATE_FIXTURE" not in repr(job)
    assert "Requirements\nPython" in job.requirements


@pytest.mark.parametrize("payload", ["{}", '"Access Denied"', "not json"])
def test_failed_feed_is_explicit_and_has_no_retry(monkeypatch, payload):
    read = Mock(return_value=payload)
    monkeypatch.setattr(feeds, "_read", read)
    result = CareersScraper(Mock()).scan_company(company())
    assert result.error and not result.jobs
    assert read.call_count == 1
