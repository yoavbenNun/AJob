from types import SimpleNamespace

import pytest

from src.models import Company, JobPosting
from src.scrapers import budgeted_scan as scan


@pytest.mark.parametrize("reason,category", [
    ("title describes a hardware or electrical role", "role_type"),
    ("no software-development responsibilities were found", "role_type"),
    ("title contains a senior-level marker", "seniority_marker"),
    ("no early-career evidence was found", "missing_early_career_evidence"),
    ("future unknown reason", "other_unknown"),
])
def test_categories_describe_only_actual_classifier_decisions(reason, category):
    assert scan.rejection_category(reason) == category


def test_worker_reports_each_filter_stage_without_reclassifying_missing_content(monkeypatch):
    jobs = [JobPosting(title, "Example", "Israel", f"https://example.test/jobs/{i}",
                       description=description) for i, (title, description) in enumerate([
        ("Senior Software Engineer", "Develop software."),
        ("Software Engineer", "Develop software. Five years experience."),
        ("Software Student", "Develop software while pursuing a BSc."),
        ("Software Intern", "Page not found."),
    ])]
    monkeypatch.setattr(scan.os, "setsid", lambda: None, raising=False)
    monkeypatch.setattr(scan, "CareersScraper", lambda *_: SimpleNamespace(
        scan_company=lambda _: SimpleNamespace(error=None, jobs=jobs)))
    monkeypatch.setattr(scan, "_enrich_job", lambda *_: None)
    events = []
    pipe = SimpleNamespace(send=events.append, close=lambda: None)
    scan.source_worker(Company("Example", "https://example.test/jobs"), 1000, 1, True, pipe)
    decisions = [e for e in events if e[0] == "filter_decision"]
    assert len([e for e in decisions if e[1] == "preliminary"]) == 4
    assert len([e for e in decisions if e[1] == "detail"]) == 2
    assert ("filter_decision", "preliminary", False, "seniority_marker") in decisions
    assert ("filter_decision", "detail", False, "missing_early_career_evidence") in decisions
    assert len([e for e in events if e[0] == "invalid_description"]) == 1
    assert [e[1] for e in events if e[0] == "job"] == [False, False, True, True]
    assert events[-1] == ("error", "JobDetailUnavailable")


def test_recommendation_metrics_separate_location_and_prerequisites_without_personal_data(monkeypatch, capsys):
    import json
    from unittest.mock import Mock
    from src import main
    monkeypatch.setenv("CANDIDATE_PROFILE_JSON", "synthetic")
    monkeypatch.setattr(main, "load_safe_profile_json", lambda _: SimpleNamespace(
        to_resume_profile=lambda: SimpleNamespace(location="private candidate address")))
    monkeypatch.setattr(main, "OpenStreetMapDrivingProvider", lambda: None)
    match = lambda status: SimpleNamespace(eligible=status != "ineligible", eligibility_status=status,
        strengths=(), eligibility_reasons=(), score=70)
    monkeypatch.setattr(main, "match_resume_to_job", Mock(side_effect=[
        match("ineligible"), match("potentially_eligible"), match("student_eligible")]))
    monkeypatch.setattr(main, "evaluate_commute", Mock(side_effect=[
        SimpleNamespace(eligible=True, reason="", status="suitable"),
        SimpleNamespace(eligible=False, reason="", status="unknown"),
        SimpleNamespace(eligible=True, reason="", status="suitable")]))
    jobs = [JobPosting("Private title", "Private employer", "Israel", f"https://example.test/{i}")
            for i in range(3)]
    recommendations = main._cloud_recommendations(SimpleNamespace(new_jobs=jobs))
    output = capsys.readouterr().out
    metrics = json.loads(output)
    assert len(recommendations) == 1
    assert metrics["considered"] == 3 and metrics["candidate_ineligible"] == 1
    assert metrics["location_blocked"] == metrics["location_unknown"] == 1
    assert metrics["recommended"] == 1
    assert metrics["eligibility_by_status"] == dict(student_eligible=1, potentially_eligible=1, ineligible=1)
    assert "Private" not in output and "private candidate address" not in output
