"""Public employer structure fixtures; no personal resume, network or storage."""

from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job, prefilter_job
from src.location.commute import classify_job_location
from src.matching.eligibility import assess_student_eligibility
from src.models import JobPosting, ResumeProfile
from src.scrapers import earnix, official_sources
from src.scrapers.careers_scraper import CareersScraper, _enrich_job


URL = "https://earnix.com/career/79.47a/automation-engineer-student-position"
TITLE = "Automation Engineer - Student Position"
DUTY = "Develop and extend automated tests within our existing Python integration test framework"
REQUIREMENT = ("Currently pursuing a B.Sc. in Computer Science or Software Engineering, "
               "with at least 3 semesters left to graduation and a GPA of 80 or above")


def company():
    return next(c for c in load_companies("config/companies.yaml") if c.name == "Earnix")


def card(url=URL, title=TITLE, location="Ramat Gan, Israel"):
    return (f'<li data-career-search-job data-location="{location}"><a href="{url}">'
            f'<span>{title}</span><span>{location}</span></a></li>')


def listing(*cards, count=None):
    return f'<p>{len(cards) if count is None else count} jobs in 1 locations</p>' + ''.join(cards)


def detail(url=URL, title=TITLE, uid="79.47A", duty=DUTY, requirement=REQUIREMENT):
    return (f'<link rel="canonical" href="{url}/"><h1>{title}</h1>'
            '<h3>Position Intro</h3><div data-career-content>Mentor junior developers.</div>'
            f'<h3>Description</h3><div data-career-content><p>{duty}</p></div>'
            f'<h3>Requirements</h3><div data-career-content><p>{requirement}</p></div>'
            f'<script type="comeet-applyform" data-position-uid="{uid}"></script>'
            '<footer>Senior software jobs in Boston</footer>')


def test_official_listing_to_verified_detail_and_existing_filters(monkeypatch):
    read = Mock(side_effect=[listing(card(), card(URL + '/?utm_source=search#apply'), count=1), detail()])
    monkeypatch.setattr(official_sources, "_read", read)
    browser = Mock()
    result = CareersScraper(browser).scan_company(company())
    assert result.error is None and len(result.jobs) == 1
    job = result.jobs[0]
    assert not job.description
    assert _enrich_job(browser, job, 1000) is None
    assert job.job_url == URL and job.title == TITLE
    assert job.location == "Ramat Gan, Israel"
    assert result.collection_metrics["raw_listings"] == 2
    assert result.collection_metrics["duplicates_avoided"] == 1
    assert not result.collection_metrics["page_limit_reached"]
    assert DUTY in job.description and REQUIREMENT in job.requirements
    assert "Mentor junior" not in job.description and "Boston" not in job.description
    assert classify_job(job).relevant and classify_job_location(job.location).status == "israel"
    student = ResumeProfile('', '', 'Israel', '', '', (),
        'Third-year B.Sc. Software Engineering student, in progress', '', '', '', '')
    status, reasons = assess_student_eligibility(student, job)
    assert status == "potentially_eligible" and any("3 semesters" in r for r in reasons)
    assert _enrich_job(browser, job, 1000) is None
    browser.new_page.assert_not_called()
    assert read.call_count == 2


def test_distinct_same_title_postings_are_not_merged():
    other = URL.replace('79.47a', '36.57c')
    jobs = earnix.extract_jobs(company(), listing(card(), card(other)), {})
    assert {j.job_url for j in jobs} == {URL, other}


def test_worker_discovery_persists_once_and_remains_reviewable(tmp_path, monkeypatch):
    from src.scrapers import budgeted_scan
    from src.scrapers.budgeted_scan import source_worker
    from src.matching.resume_matcher import match_resume_to_job
    from src.storage.job_repository import JobRepository
    from src.storage.scan_store import ScanStore
    from src.web.app import _filter_jobs

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(budgeted_scan.os, "setsid", lambda: None, raising=False)
    monkeypatch.setattr(official_sources, "_read", Mock(side_effect=[listing(card()), detail()]))
    events = []
    source_worker(company(), 1000, 1, True, SimpleNamespace(send=events.append, close=lambda: None))
    _, accepted, job, verified = next(event for event in events if event[0] == "job")
    assert accepted is True and verified is True and events[-1] == ("done",)
    job.is_relevant = accepted
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    store = ScanStore(repo)
    store.migrate()
    try:
        assert store.save_discovery(job, "synthetic-earnixtest-slot", detail_verified=verified)
        first_id = job.id
        assert not store.save_discovery(job, "synthetic-earnixtest-retry", detail_verified=verified)
        jobs = repo.list_all()
        assert len(jobs) == 1 and jobs[0].id == first_id and jobs[0].is_relevant
        assert store.execute("SELECT COUNT(*) AS n FROM notification_outbox").fetchone()["n"] == 1
        student = ResumeProfile('', '', 'Israel', '', '', (),
            'Third-year B.Sc. Software Engineering student, in progress', '', '', '', '')
        match = match_resume_to_job(student, jobs[0])
        assert match.eligibility_status == "potentially_eligible"
        candidate_ids = {jobs[0].id} if match.eligible else set()
        assert _filter_jobs(jobs, status_filter="all", company="", query="", relevance="relevant",
                            candidate_eligible_ids=candidate_ids) == jobs
        # Rediscovery retains the user's later dismissal and creates no new mail.
        repo.update_status(first_id, "ignored")
        assert not store.save_discovery(job, "synthetic-earnixtest-retry2", detail_verified=verified)
        assert repo.list_all()[0].status == "ignored"
        assert not _filter_jobs(repo.list_all(), status_filter="all", company="", query="",
                                relevance="relevant", candidate_eligible_ids=candidate_ids)
        assert store.execute("SELECT COUNT(*) AS n FROM notification_outbox").fetchone()["n"] == 1
    finally:
        store.close()


def test_failed_detail_preserves_other_verified_discoveries_and_reports_partial(monkeypatch):
    from src.scrapers import budgeted_scan

    monkeypatch.setattr(budgeted_scan.os, "setsid", lambda: None, raising=False)
    other = URL.replace("79.47a", "36.57c")
    monkeypatch.setattr(official_sources, "_read", Mock(side_effect=[
        listing(card(other), card()), OSError("public detail unavailable"), detail()]))
    browser = Mock()
    browser.new_page.side_effect = AssertionError("Earnix details must not fall back to browser")
    monkeypatch.setattr(budgeted_scan, "LazyBrowser", lambda _: browser)
    events = []
    budgeted_scan.source_worker(company(), 1000, 1, True,
                               SimpleNamespace(send=events.append, close=lambda: None))
    found = {event[2].job_url: event for event in events if event[0] == "job"}
    assert len(found) == 2
    assert found[URL][1] is True and found[URL][3] is True
    assert DUTY in found[URL][2].description
    assert found[other][3] is False and not found[other][2].description
    assert events[-1] == ("error", "JobDetailUnavailable")
    browser.new_page.assert_not_called()


@pytest.mark.parametrize("body", [
    listing(card(), count=2),  # A new paginated layout cannot claim complete coverage.
    '<p>No search results</p>',
    listing(card('https://evil.test/career/79.47a/fake')),
    listing(card('https://earnix.com@evil.test/career/79.47a/fake')),
    listing(card('https://earnix.com:8443/career/79.47a/fake')),
    listing(card('https://earnix.com/career/not-a-requisition/fake')),
])
def test_partial_or_untrusted_listings_fail_closed(body):
    with pytest.raises(ValueError):
        earnix.extract_jobs(company(), body, {})


@pytest.mark.parametrize("body", [detail(uid="12.ABC"), detail(url=URL.replace('79.47a', '36.57c')),
    detail(title="Another Role"), detail(requirement=""), '<h1>Access Denied</h1>'])
def test_details_require_matching_identity_and_complete_scoped_content(body):
    with pytest.raises(ValueError):
        earnix.extract_detail(JobPosting(TITLE, 'Earnix', 'Ramat Gan, Israel', URL), body)


def test_no_detail_requests_for_prefiltered_senior_or_nonengineering_jobs(monkeypatch):
    read = Mock(return_value=listing(card(title="Senior Software Engineer"),
        card(URL.replace('79.47a', '36.57c'), title="Finance Manager")))
    monkeypatch.setattr(official_sources, "_read", read)
    jobs, _ = official_sources.fetch_jobs(company(), 1000)
    assert len(jobs) == 2 and not any(prefilter_job(j).relevant for j in jobs)
    assert read.call_count == 1


@pytest.mark.parametrize("title,duty,relevant", [
    (TITLE, DUTY, True),
    (TITLE, "Write automated tests using a Java integration test framework", True),
    (TITLE, "Do not develop automated tests within our Python test framework", False),
    (TITLE, "Never extend automated tests within our Python test framework", False),
    (TITLE, "Review automated tests within our Python test framework", False),
    (TITLE, "Develop automated tests within our test framework", False),
    (TITLE, "Run manual tests. Requirements\nPython test framework experience.", False),
    (TITLE, "Run manual tests.\nRequirements\nDevelop automated tests in Python test framework.", False),
    ("Automation Engineer", DUTY, False),
    ("Senior Automation Engineer - Student", DUTY, False),
    ("QA Automation Team Lead", DUTY, False),
    ("Marketing Automation Student", DUTY, False),
])
def test_coded_test_duties_keep_software_early_career_and_seniority_boundaries(title, duty, relevant):
    job = JobPosting(title, "Earnix", "Ramat Gan, Israel", URL, description=duty)
    assert classify_job(job).relevant is relevant
