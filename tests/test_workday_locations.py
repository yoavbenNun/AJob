"""Explicit Workday locations; public Intel JR0288513 is the regression case."""

from io import BytesIO
import json
from unittest.mock import Mock

import pytest

from src.location.commute import evaluate_commute, is_israel_or_remote_location
from src.models import Company
from src.matching.resume_matcher import match_resume_to_job
from src.resume.resume_parser import parse_resume_text
from src.scrapers.careers_scraper import extract_workday_jobs, _enrich_job
from src.scrapers.workday_details import fetch_workday_detail, workday_location, workday_urls


SOURCE = "https://intel.wd1.myworkdayjobs.com/en-US/External"
PATH = "/job/Israel-Petah-Tikva/DevOps-Engineering-Student_JR0288513"
TITLE = "DevOps Engineering Student"
DESCRIPTION = ("<p>Develop software infrastructure, Python tools and continuous integration services.</p>" * 3
               + "<p>Requirements</p><p>Currently pursuing a B.Sc. in Software Engineering.</p>")


def listing(location="2 Locations"):
    return extract_workday_jobs(Company("Intel", SOURCE), {"jobPostings": [
        {"title": TITLE, "externalPath": PATH, "locationsText": location}]})[0]


def install_detail(monkeypatch, **fields):
    info = {"title": TITLE, "jobReqId": "JR0288513", "externalUrl": SOURCE + PATH,
            "jobDescription": DESCRIPTION, **fields}

    class Response(BytesIO):
        def geturl(self):
            return workday_urls(SOURCE, SOURCE + PATH)[1]

    monkeypatch.setattr("src.scrapers.workday_details.urlopen",
                        lambda *_args, **_kwargs: Response(json.dumps({"jobPostingInfo": info}).encode()))


def test_intel_jr0288513_listing_to_verified_israeli_destinations(monkeypatch):
    install_detail(monkeypatch, location="Israel, Petah-Tikva", additionalLocations=["Israel, Haifa"])
    job = listing()
    assert job.location == "Not specified"
    assert not is_israel_or_remote_location(job.location)
    job.status = "applied"
    assert _enrich_job(None, job, 1000) is None
    assert job.location == "Israel, Petah-Tikva; Israel, Haifa"
    assert is_israel_or_remote_location(job.location)
    assert job.status == "applied"
    provider = Mock(name="routing")
    provider.driving_minutes.side_effect = [45, 110]
    result = evaluate_commute("Synthetic origin", job.location, provider)
    assert result.eligible and result.minutes == 45
    assert provider.driving_minutes.call_args_list[0].args[1] == "Israel, Petah-Tikva"
    assert provider.driving_minutes.call_args_list[1].args[1] == "Israel, Haifa"


@pytest.mark.parametrize("primary,additional,expected,israel", [
    ("Israel, Haifa", None, "Israel, Haifa", True),
    ("France, Paris", ["Germany, Munich"], "France, Paris; Germany, Munich", False),
    ("PRC, Shanghai", ["PRC, Shenzhen", "PRC, Beijing"],
     "PRC, Shanghai; PRC, Shenzhen; PRC, Beijing", False),
    ("France, Paris", ["Israel, Haifa"], "France, Paris; Israel, Haifa", True),
    (" Israel, Haifa ", ["israel, haifa", "2 Locations"], "Israel, Haifa", True),
    (None, ["Israel, Haifa"], "Israel, Haifa", True),
    ("Multiple Locations", None, "", False),
    ("2 Locations", ["3 Locations", "Unknown"], "", False),
    (None, None, "", False),
    ({"descriptor": "Israel"}, [{"descriptor": "Israel, Haifa"}], "", False),
    ("Not specified", "Israel", "", False),
])
def test_only_explicit_typed_labels_are_geographic_evidence(primary, additional, expected, israel):
    location = workday_location(primary, additional)
    assert location == expected
    assert is_israel_or_remote_location(location) is israel


def test_foreign_and_mixed_locations_keep_existing_commute_policy():
    provider = Mock(name="routing")
    provider.driving_minutes.return_value = 50
    foreign = workday_location("France, Paris", ["Germany, Munich"])
    assert evaluate_commute("Synthetic origin", foreign, provider).status == "outside_israel"
    provider.driving_minutes.assert_not_called()
    mixed = workday_location("France, Paris", ["Israel, Haifa"])
    assert evaluate_commute("Synthetic origin", mixed, provider).eligible
    provider.driving_minutes.assert_called_once_with("Synthetic origin", "Israel, Haifa")


def test_unknown_detail_does_not_infer_israel_from_url_or_description(monkeypatch):
    install_detail(monkeypatch, location="2 Locations", additionalLocations=[])
    job = listing()
    assert _enrich_job(None, job, 1000) is None
    assert "Israel-Petah-Tikva" in job.job_url
    assert job.location == "Not specified"
    assert not is_israel_or_remote_location(job.location)
    assert not evaluate_commute("Synthetic origin", job.location, None).eligible


def test_missing_detail_fields_preserve_explicit_listing_location(monkeypatch):
    install_detail(monkeypatch)
    job = listing("Israel, Haifa")
    assert _enrich_job(None, job, 1000) is None
    assert job.location == "Israel, Haifa"


def test_invalid_detail_cannot_change_known_location(monkeypatch):
    install_detail(monkeypatch, location="France, Paris", jobDescription="Access Denied")
    job = listing("Israel, Haifa")
    assert _enrich_job(None, job, 1000) == "JobDetailUnavailable"
    assert job.location == "Israel, Haifa"


def test_location_changes_leave_professional_evidence_identical(monkeypatch):
    install_detail(monkeypatch, location="Israel, Haifa")
    single = fetch_workday_detail(SOURCE, SOURCE + PATH, TITLE)
    install_detail(monkeypatch, location="Israel, Haifa", additionalLocations=["Israel, Petah-Tikva"])
    multiple = fetch_workday_detail(SOURCE, SOURCE + PATH, TITLE)
    assert single.description == multiple.description
    assert single.title == multiple.title and single.requisition == multiple.requisition
    profile = parse_resume_text("""Synthetic Candidate
Third-year Software Engineering student
EDUCATION
B.Sc. Software Engineering (in progress)
SKILLS
Python
""")
    job = listing()
    job.description, job.requirements = single.description, "Currently pursuing a B.Sc. in Software Engineering."
    job.location = single.location
    before = match_resume_to_job(profile, job)
    job.location = multiple.location
    after = match_resume_to_job(profile, job)
    assert (before.score, before.technical_score, before.eligibility_status) == (
        after.score, after.technical_score, after.eligibility_status)
