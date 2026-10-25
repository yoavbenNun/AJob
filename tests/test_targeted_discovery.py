"""Regressions for FIL requisition 6148 and Amazon requisition 10503596."""

from copy import deepcopy
from unittest.mock import MagicMock, Mock

import pytest

from src.config_loader import load_companies
from src.filtering.job_filter import classify_job, prefilter_job
from src.models import JobPosting
from src.matching.resume_matcher import match_resume_to_job
from src.resume.resume_parser import parse_resume_text
from src.scrapers.careers_scraper import CareersScraper, _enrich_job
from src.scrapers.fives import extract_jobs, scan_jobs
from src.storage.job_repository import JobRepository


def company():
    return next(c for c in load_companies("config/companies.yaml") if c.name == "FIL Robotics / Fives")


def payload():
    # Public requisition identity and representative professional text only.
    return {"code": 200, "data": {"total": 1, "list": [{
        "id": "10774-NjE0OA", "reference": "6148", "company": "FIL Robotics Ltd",
        "title": "Junior Software Engineer - Student Position",
        "localities": [{"city_label": "Binyamina", "country": "Israel"}],
        "mission_description_formatted": "<p>Develop backend components using Java, Python and Kotlin.</p>"
            "<h3>Requirements</h3><p>3rd-year student pursuing a Bachelor's degree in Computer Science "
            "or Software Engineering. Strong OOP and basic SQL. Excellent academic record.</p>",
        "skills": [{"label": "Unrelated inferred technology"}],
    }]}}


def test_fil_6148_discovery_identity_location_and_relevance():
    job, = extract_jobs(company(), payload())
    assert job.job_url == "https://jobs.fivesgroup.com/en/offer/10774-NjE0OA"
    assert job.location == "Binyamina, Israel"
    assert prefilter_job(job).relevant and classify_job(job).relevant
    assert "Unrelated inferred technology" not in job.description
    assert "<p>" not in job.description


def test_fil_duplicate_and_other_company_do_not_create_extra_jobs():
    data = payload()
    data["data"]["list"] += [deepcopy(data["data"]["list"][0]),
        dict(data["data"]["list"][0], company="Other Fives company")]
    data["data"]["total"] = 3
    assert len(extract_jobs(company(), data)) == 1


@pytest.mark.parametrize("bad", [None, {}, {"code": 403},
    {"code": 200, "data": {"list": [], "total": 11}}])
def test_fil_invalid_or_truncated_results_are_visible_failures(bad):
    with pytest.raises(ValueError):
        extract_jobs(company(), bad)


def test_fil_mismatched_requisition_is_not_saved():
    data = payload()
    data["data"]["list"][0]["reference"] = "9999"
    with pytest.raises(ValueError, match="identity"):
        extract_jobs(company(), data)


def test_fil_normal_page_response_is_scoped_and_page_is_closed():
    browser, page = Mock(), MagicMock()
    browser.new_page.return_value = page
    page.expect_response.return_value.__enter__.return_value.value.json.return_value = payload()
    page.expect_response.return_value.__enter__.return_value.value.status = 200
    assert len(scan_jobs(company(), browser, 1000)) == 1
    predicate = page.expect_response.call_args.args[0]
    response = Mock(url="https://api.magnet.work/v2/job-offers")
    response.request.method = "POST"
    response.request.post_data_json = {"countries": [{"id": "Israel"}], "q": "FIL Robotics"}
    assert predicate(response)
    response.request.post_data_json = {"countries": [], "q": "FIL Robotics"}
    assert not predicate(response)
    page.close.assert_called_once()


def test_fil_adapter_uses_verified_content_without_detail_browser(monkeypatch):
    job, = extract_jobs(company(), payload())
    monkeypatch.setattr("src.scrapers.careers_scraper.scan_fives_jobs", lambda *_: [job])
    browser = Mock()
    result = CareersScraper(browser).scan_company(company())
    assert result.error is None and "3rd-year" in result.jobs[0].requirements
    assert _enrich_job(browser, job, 1000) is None
    browser.new_page.assert_not_called()


def test_fil_unavailable_search_is_reported_without_retries(monkeypatch):
    scan = Mock(side_effect=ValueError("unavailable"))
    monkeypatch.setattr("src.scrapers.careers_scraper.scan_fives_jobs", scan)
    result = CareersScraper(Mock()).scan_company(company())
    assert result.error == "FIL Robotics search failed: ValueError"
    assert not result.jobs
    scan.assert_called_once()


@pytest.mark.parametrize("responsibility", [
    "You will take part in designing, developing, and maintaining our automation frameworks.",
    "Develop test automation infrastructure in Python.",
    "Build automation tools and integrate them into CI.",
    "Implement automation frameworks for our cloud software.",
])
def test_amazon_automation_development_is_software_work(responsibility):
    job = JobPosting("SW Automation Student, Annapurna Labs", "Amazon", "Tel Aviv, ISR",
        "https://www.amazon.jobs/en/jobs/10503596/sw-automation-student-annapurna-labs",
        description=responsibility, requirements="Experience building test tools required.")
    assert prefilter_job(job).relevant and classify_job(job).relevant


@pytest.mark.parametrize("description", [
    "Execute manual test cases and report bugs. Use existing automation tools.",
    "Maintain automation frameworks by configuring existing test suites.",
    "Execute tests. Requirements: Experience developing automation frameworks preferred.",
    "You will not develop automation frameworks. Run existing tests only.",
])
def test_automation_student_without_development_responsibilities_stays_out(description):
    job = JobPosting("Automation Student", "Example", "Israel", "https://example.com/jobs/1",
                     description=description)
    assert not classify_job(job).relevant


def test_automation_development_does_not_override_seniority_or_early_career():
    for title in ("Senior Automation Engineer", "Automation Engineer"):
        job = JobPosting(title, "Example", "Israel", "https://example.com/jobs/1",
                         description="Develop automation frameworks using Python.")
        assert not classify_job(job).relevant


def test_software_relevance_does_not_override_candidate_prerequisites():
    candidate = parse_resume_text("""Example Candidate
Third-year Software Engineering student
EDUCATION
B.Sc. Software Engineering, Example University (in progress), expected 2027
SKILLS
Python, Java
""")
    job = JobPosting("SW Automation Student", "Example", "Israel", "https://example.com/jobs/1",
        description="Develop automation frameworks using Python.",
        requirements="Minimum 2 years of industry experience required.")
    assert classify_job(job).relevant
    assert match_resume_to_job(candidate, job).eligibility_status == "ineligible"


def test_relevance_refresh_preserves_efa_user_rejection(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    job = JobPosting("Software Development Student", "Amazon", "Haifa, Israel",
        "https://www.amazon.jobs/en/jobs/10558894/software-development-student")
    job_id = repository.save(job)
    repository.update_status(job_id, "rejected")
    repository.update_relevance_by_url(job.job_url, False)
    job.is_relevant = True
    repository.save(job)
    assert repository.get_by_id(job_id).status == "rejected"
    assert repository.get_by_id(job_id).is_relevant
