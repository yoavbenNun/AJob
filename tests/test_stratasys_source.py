"""Synthetic public-site fixtures, disposable DBs, no network/SMTP/user files."""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job, prefilter_job
from src.job_content import description_problem
from src.location.commute import is_israel_or_remote_location
from src.matching.eligibility import assess_student_eligibility
from src.models import Company, JobPosting
from src.resume.resume_parser import parse_resume_text
from src.scrapers import stratasys
from src.scrapers.careers_scraper import CareersScraper
from src.scrapers.official_sources import fetch_jobs
from src.storage.job_repository import JobRepository


ROOT = Path(__file__).parent / "fixtures"
DETAIL = (ROOT / "stratasys_student.html").read_text(encoding="utf-8")
PAGE1 = (ROOT / "stratasys_search_1.html").read_text(encoding="utf-8")
PAGE2 = (ROOT / "stratasys_search_2.html").read_text(encoding="utf-8")
SITEMAP = (ROOT / "stratasys_sitemap.xml").read_text(encoding="utf-8")
ORIGIN = stratasys.ORIGIN
COMPANY = Company("Stratasys", ORIGIN + "/search/")
STUDENT_URL = ORIGIN + "/job/Rehovot-Software-Embedded-Student-IL/1444873900/"  # pragma: allowlist secret -- official public posting URL, no credentials


class Reply(BytesIO):
    def __init__(self, text, url):
        super().__init__(text.encode("utf-8"))
        self.url = url

    def geturl(self):
        return self.url


def install(monkeypatch, *, first=PAGE1, second=PAGE2, detail=DETAIL, sitemap=SITEMAP, redirect=None):
    calls = []

    def read(request, timeout):
        url = request.full_url
        calls.append((url, timeout))
        path = urlsplit(url).path
        if path == "/sitemap.xml":
            text = sitemap
        elif path == "/search/":
            text = second if parse_qs(urlsplit(url).query)["startrow"] != ["0"] else first
        elif stratasys.posting_id(url) == "1444873900":
            text = detail
        else:
            text = detail.replace("Software Embedded Student", "Software Developer Intern").replace("55642", "70001").replace("Rehovot", "Kiryat Gat")
        return Reply(text, redirect or url)

    monkeypatch.setattr(stratasys, "_open", read)
    return calls


def test_config_is_new_unique_and_enabled():
    companies = load_companies("config/companies.yaml")
    matches = [company for company in companies if "stratasys" in company.careers_url]
    assert len(matches) == 1 and matches[0].enabled
    assert matches[0].allowed_job_hosts == ("careers.stratasys.com",)


def test_real_field_shapes_paginate_and_support_future_roles(monkeypatch):
    calls = install(monkeypatch)
    browser = Mock()
    result = CareersScraper(browser).scan_company(COMPANY)
    assert not result.error and len(result.jobs) == 3
    job = next(job for job in result.jobs if job.job_url == STUDENT_URL)
    assert stratasys.posting_id(job.job_url) == "1444873900"
    assert job.title == "Software Embedded Student"
    assert job.location == "Rehovot, IL, Israel"
    assert "55642" in job.description
    assert "At least 2 years" in job.requirements
    assert "3 days per week" in job.requirements
    assert "Must have for this role:" in job.requirements
    assert "Preferred Qualifications:\nAdvantages:" in job.requirements
    assert "display: inline" not in job.location
    assert "Company navigation" not in job.description
    assert description_problem(job.description) is None
    assert prefilter_job(job).relevant and classify_job(job).relevant
    assert is_israel_or_remote_location(job.location)
    assert any(job.title == "Software Developer Intern" for job in result.jobs)
    assert result.closure_snapshot is None
    assert result.collection_metrics["pages_fetched"] == 2
    assert result.collection_metrics["details_fetched"] == 2
    assert result.collection_metrics["sitemap_only"] == result.collection_metrics["listing_only"] == 0
    assert len(calls) == 5 and all(0 < timeout <= 15 for _, timeout in calls)
    assert not any("/services/" in url for url, _ in calls)
    browser.new_page.assert_not_called()


def test_storage_refresh_retains_id_manual_status_and_no_duplicates(monkeypatch, tmp_path):
    install(monkeypatch)
    jobs, _ = fetch_jobs(COMPANY, 15000)
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    first = repository.save(jobs[0])
    repository.update_status(first, "applied")
    for job in jobs:
        repository.save(job)
    assert len(repository.list_all()) == 3
    assert repository.save(jobs[0]) == first
    assert repository.get_by_id(first).status == "applied"


@pytest.mark.parametrize("graduation,expected", [
    ("2027", "ineligible"), ("2028", "potentially_eligible"),
    ("2028-10-31", "student_eligible"), ("2029", "student_eligible"),
])
def test_eligibility_remains_evidence_based(monkeypatch, graduation, expected):
    from src.matching import eligibility

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 31, tzinfo=tz)

    monkeypatch.setattr(eligibility, "datetime", Clock)
    install(monkeypatch)
    job = CareersScraper(Mock()).scan_company(COMPANY).jobs[0]
    profile = parse_resume_text("Synthetic Candidate\nThird-year Software Engineering student\n"
                                "EDUCATION\nB.Sc. Software Engineering (in progress). Expected graduation: " + graduation +
                                "\nSKILLS\nC++, C, Python\nPROJECTS\nDeveloped embedded C++ software.")
    assert assess_student_eligibility(profile, job)[0] == expected


@pytest.mark.parametrize("bad", ["<html>CAPTCHA</html>", "<h1>Page not found</h1>", "", DETAIL.replace("55642", ""),
                                DETAIL.replace("Software Embedded Student", "Different role")])
def test_missing_bad_or_wrong_detail_is_visible_failure(monkeypatch, bad):
    install(monkeypatch, detail=bad)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and not result.jobs and result.failure_details


def test_missing_locations_are_not_inferred_from_url(monkeypatch):
    first = PAGE1.replace('<span class="jobLocation">Rehovot, IL, IL</span>', "")
    detail = DETAIL.replace('data-careersite-propertyid="location"', 'data-unrelated="location"')
    install(monkeypatch, first=first, detail=detail)
    jobs, _ = fetch_jobs(COMPANY, 15000)
    assert jobs[0].location == "Not specified"
    assert not is_israel_or_remote_location(jobs[0].location)


def test_illinois_is_not_israel(monkeypatch):
    install(monkeypatch)
    jobs, _ = fetch_jobs(COMPANY, 15000)
    assert jobs[1].location == "Chicago, IL, US"
    assert not is_israel_or_remote_location(jobs[1].location)


@pytest.mark.parametrize("first,second", [
    (PAGE1, PAGE1),
    (PAGE1, PAGE2.replace("of <b>3", "of <b>4")),
    (PAGE1.replace("data-row", "missing-row"), PAGE2),
    (PAGE1.replace('class="jobTitle-link"', 'class="missing-title"'), PAGE2),
    ("<html>Access denied</html>", PAGE2),
])
def test_incomplete_changed_or_repeated_pagination_fails(monkeypatch, first, second):
    install(monkeypatch, first=first, second=second)
    with pytest.raises(ValueError):
        fetch_jobs(COMPANY, 15000)


def test_page_budget_is_not_silent_success(monkeypatch):
    calls = install(monkeypatch)
    monkeypatch.setattr(stratasys, "MAX_PAGES", 1)
    with pytest.raises(ValueError, match="page budget"):
        fetch_jobs(COMPANY, 15000)
    assert len([url for url, _ in calls if "/search/" in url]) == 1


def test_detail_budget_is_bounded(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(stratasys, "MAX_DETAILS", 0)
    with pytest.raises(ValueError, match="detail budget"):
        fetch_jobs(COMPANY, 15000)


def test_source_deadline_prevents_network(monkeypatch):
    read = Mock()
    monkeypatch.setattr(stratasys, "_open", read)
    monkeypatch.setattr(stratasys, "SOURCE_SECONDS", 0)
    with pytest.raises(TimeoutError):
        fetch_jobs(COMPANY, 15000)
    read.assert_not_called()


def test_deadline_during_final_detail_stream_cannot_report_success(monkeypatch):
    install(monkeypatch)
    original = stratasys._open
    clock = [0]
    monkeypatch.setattr(stratasys.time, "monotonic", lambda: clock[0])

    def slow(request, timeout):
        response = original(request, timeout)
        if "9000000002" in request.full_url:
            read1 = response.read1

            def drip(size):
                clock[0] = stratasys.SOURCE_SECONDS + 1
                return read1(size)

            response.read1 = drip
        return response

    monkeypatch.setattr(stratasys, "_open", slow)
    with pytest.raises(TimeoutError):
        fetch_jobs(COMPANY, 15000)


def test_oversized_response_fails_with_bounded_read(monkeypatch):
    calls = install(monkeypatch)
    monkeypatch.setattr(stratasys, "MAX_BYTES", 32)
    with pytest.raises(ValueError, match="Oversized"):
        fetch_jobs(COMPANY, 15000)
    assert len(calls) == 1


@pytest.mark.parametrize("sitemap", ["", "<html>CAPTCHA</html>", "<urlset/>",
    '<!DOCTYPE urlset [<!ENTITY x "bad">]><urlset/>',
    SITEMAP.replace("careers.stratasys.com", "evil.test"),
])
def test_invalid_sitemap_is_not_a_clean_empty_source(monkeypatch, sitemap):
    install(monkeypatch, sitemap=sitemap)
    result = CareersScraper(Mock()).scan_company(COMPANY)
    assert result.error and not result.jobs


def test_untrusted_job_link_is_rejected_before_detail_request(monkeypatch):
    calls = install(monkeypatch, first=PAGE1.replace('/job/Rehovot-', 'https://evil.test/job/Rehovot-'))
    with pytest.raises(ValueError):
        fetch_jobs(COMPANY, 15000)
    assert len(calls) == 2


@pytest.mark.parametrize("url", ["http://careers.stratasys.com/search/", "https://evil.test/search/", ORIGIN + "/services/rss/"])
def test_redirects_cannot_leave_public_source_boundary(url):
    with pytest.raises(ValueError):
        stratasys._PublicRedirect().redirect_request(None, None, 302, "Found", {}, url)


def test_detail_redirect_to_another_identity_fails(monkeypatch):
    calls = install(monkeypatch)
    original = stratasys._open

    def wrong(request, timeout):
        response = original(request, timeout)
        if "/job/" in request.full_url:
            response.url = ORIGIN + "/job/Other/999/"
        return response

    monkeypatch.setattr(stratasys, "_open", wrong)
    with pytest.raises(ValueError, match="different posting"):
        fetch_jobs(COMPANY, 15000)


def test_rate_limit_is_not_retried_and_another_source_still_works(monkeypatch):
    read = Mock(side_effect=HTTPError(ORIGIN, 429, "blocked", {}, None))
    monkeypatch.setattr(stratasys, "_open", read)
    scanner = CareersScraper(Mock())
    failed = scanner.scan_company(COMPANY)
    assert failed.error and failed.failure_details["http_status"] == 429
    assert read.call_count == 1
    other = Company("Synthetic", "https://job-boards.greenhouse.io/synthetic")
    monkeypatch.setattr("src.scrapers.careers_scraper.fetch_greenhouse_jobs", lambda *_: [
        JobPosting("Software Intern", "Synthetic", "Israel", "https://example.test/jobs/1", description="Develop C++ software.")])
    assert len(scanner.scan_company(other).jobs) == 1
