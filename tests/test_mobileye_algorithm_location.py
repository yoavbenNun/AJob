"""Captured Mobileye #15212/#15371 headers; synthetic non-personal job bodies."""
from types import SimpleNamespace

import pytest

from src.location.commute import classify_job_location, resolve_job_destination
from src.models import JobPosting


def captured_header(mode):
    return ("Cookie notice\nAlgorithm Developer Student\n"
            f"Jerusalem, IsraelR&D – Algorithms /Student /{mode}\n"
            "APPLY FOR THIS JOB\nDevelop software in C++ and Python.")


@pytest.mark.parametrize("mode", ["Hybrid", "On-Site"])
def test_verified_algorithm_headers_resolve_without_mutating_source(mode):
    description = captured_header(mode)
    job = JobPosting("Algorithm Developer Student", "Mobileye", "Not specified",
                     "https://example.test/job", description=description)
    assert resolve_job_destination(job.location, description) == "Jerusalem, Israel"
    assert classify_job_location(resolve_job_destination(job.location, description)).eligible
    assert job.location == "Not specified" and job.description == description


@pytest.mark.parametrize("description", [
    captured_header("Hybrid").replace("Jerusalem, Israel", "London, UK"),
    captured_header("Hybrid").replace("APPLY FOR THIS JOB", "About our team"),
    captured_header("Hybrid").replace("Student", "Full time"),
    captured_header("Remote"),
    "Our team works with Jerusalem, Israel on algorithms.",
    "\n".join(["Boilerplate"] * 12) + "\n" + captured_header("Hybrid"),
])
def test_unverified_or_foreign_headers_stay_unknown(description):
    assert resolve_job_destination("Not specified", description) == "Not specified"
    assert not classify_job_location(resolve_job_destination("Not specified", description)).eligible


def test_explicit_foreign_location_takes_precedence():
    assert resolve_job_destination("London, UK", captured_header("Hybrid")) == "London, UK"


def test_recommendation_location_gate_accepts_both_headers_preserving_eligibility(monkeypatch):
    from src import main
    monkeypatch.setenv("CANDIDATE_PROFILE_JSON", "synthetic")
    monkeypatch.setattr(main, "load_safe_profile_json", lambda _: SimpleNamespace(
        to_resume_profile=lambda: object()))
    monkeypatch.setattr(main, "match_resume_to_job", lambda profile, job: SimpleNamespace(
        eligible=job.id != 3, eligibility_status="ineligible" if job.id == 3 else "potentially_eligible",
        strengths=(), eligibility_reasons=("Confirm remaining semesters.",), score=20))
    jobs = [JobPosting("Algorithm Developer Student", "Mobileye", "Not specified",
                       f"https://example.test/{i}", description=captured_header(mode), id=i)
            for i, mode in [(1, "Hybrid"), (2, "On-Site"), (3, "Hybrid"), (4, "Remote")]]
    result = main._cloud_recommendations(SimpleNamespace(new_jobs=jobs))
    assert [r.job_url for r in result] == ["https://example.test/1", "https://example.test/2"]
    assert all(r.location == "Jerusalem, Israel" and r.eligibility_status == "potentially_eligible"
               for r in result)
