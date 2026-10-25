import base64
import json
from unittest.mock import Mock

import pytest

from src.config_loader import load_companies
from src.models import Company
from src.scrapers import official_sources as feeds
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from src.scrapers.greenhouse import extract_greenhouse_jobs


def companies():
    return {c.name: c for c in load_companies("config/companies.yaml")}


def test_taboola_uses_complete_json_instead_of_landing_page_anchors(monkeypatch):
    company = companies()["Taboola"]
    listing = dict(title="Software Student", link="https://www.taboola.com/careers/job/software-student",
                   office_textual="Tel Aviv, Israel")
    read = Mock(return_value=json.dumps([listing, listing, dict(listing, link="https://evil.test/careers/job/1")]))
    monkeypatch.setattr(feeds, "_read", read)
    browser = Mock()
    result = CareersScraper(browser).scan_company(company)
    assert result.error is None and len(result.jobs) == 1
    assert result.jobs[0].location == "Tel Aviv, Israel"
    assert result.collection_metrics["raw_listings"] == 3
    assert result.collection_metrics["duplicates_avoided"] == 1
    assert result.collection_metrics["invalid_listing"] == 1
    assert read.call_count == 1
    browser.new_page.assert_not_called()


def fiverr_item(uid="12.ABC", **updates):
    return dict(uid=uid, encodedId=base64.urlsafe_b64encode(uid.encode()).decode().rstrip("="),
                title="Software Intern", location="Tel Aviv-Yafo",
                details=[dict(name="Requirements", value="<p>Currently pursuing a BSc. Develop software.</p>")],
                **updates)


def test_fiverr_embedded_feed_needs_no_rendered_links_and_preserves_id(monkeypatch):
    item = fiverr_item()
    html = '<script>window.data=' + json.dumps(dict(comeetPositions=[item, item])) + '</script>'
    monkeypatch.setattr(feeds, "_read", lambda *_: html)
    result = CareersScraper(Mock()).scan_company(companies()["Fiverr"])
    assert result.error is None and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_url.endswith("/jobs/" + item["encodedId"])
    assert "Currently pursuing" in job.requirements
    browser = Mock()
    assert _enrich_job(browser, job, 1000) is None
    browser.new_page.assert_not_called()


def test_fiverr_rejects_mismatched_or_injected_ids():
    item = fiverr_item()
    for encoded in ("../../private", "YWJj"):
        item["encodedId"] = encoded
        assert feeds.extract_fiverr_jobs(companies()["Fiverr"],
            json.dumps(dict(comeetPositions=[item])), {}) == []


def test_plain_titles_with_ampersands_remain_valid_text():
    company = companies()["Taboola"]
    jobs = feeds.extract_listings(company, [dict(title="VP R&amp;D",
        url="https://www.taboola.com/careers/job/vp-rd")], {})
    assert jobs[0].title == "VP R&D"


def test_lightricks_reads_both_official_successor_boards(monkeypatch):
    read = Mock(side_effect=[json.dumps(dict(jobs=[dict(title="Software Intern", content="Develop software",
        absolute_url="https://ltx.io/careers/position?gh_jid=123", location=dict(name="Jerusalem"))])),
        json.dumps(dict(jobs=[dict(title="Software Student", content="Develop software",
        absolute_url="https://www.facetuneapp.com/career/position?gh_jid=456", location=dict(name="Haifa"))]))])
    monkeypatch.setattr(feeds, "_read", read)
    result = CareersScraper(Mock()).scan_company(companies()["Lightricks"])
    assert result.error is None
    assert len(result.jobs) == 2
    assert {j.company for j in result.jobs} == {"Lightricks"}
    assert result.collection_metrics["raw_listings"] == 2
    assert [c.args[0].split("/boards/")[1].split("/")[0] for c in read.call_args_list] == ["ltx", "facetune"]


@pytest.mark.parametrize("name,body", [("Taboola", "{}"), ("Fiverr", "Access Denied"),
                                        ("Lightricks", '{"error":"unavailable"}')])
def test_invalid_feed_is_reported_as_failure_without_retry(monkeypatch, name, body):
    read = Mock(return_value=body)
    monkeypatch.setattr(feeds, "_read", read)
    result = CareersScraper(Mock()).scan_company(companies()[name])
    assert result.error and not result.jobs
    assert read.call_count == 1


def test_cisco_routes_to_existing_workday_adapter(monkeypatch):
    company = companies()["Cisco"]
    sentinel = object()
    scan = Mock(return_value=sentinel)
    monkeypatch.setattr(CareersScraper, "_scan_workday", scan)
    assert CareersScraper(Mock()).scan_company(company) is sentinel
    assert company.careers_url == "https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers"
    assert company.allowed_job_hosts == ("cisco.wd5.myworkdayjobs.com",)


def test_other_sources_keep_existing_adapters():
    assert not feeds.supported(Company("Example", "https://job-boards.greenhouse.io/example"))
    assert not feeds.supported(companies()["Qualcomm"])


def test_greenhouse_counts_listing_rejections_and_duplicates_separately():
    company = Company("Example", "https://job-boards.greenhouse.io/example")
    item = dict(title="Software Intern", absolute_url="https://job-boards.greenhouse.io/example/jobs/1")
    metrics = {}
    assert len(extract_greenhouse_jobs(company, dict(jobs=[item, item, {}, None]), metrics)) == 1
    assert metrics["raw_listings"] == 4
    assert metrics["duplicates_avoided"] == 1
    assert metrics["invalid_listing"] == 2
