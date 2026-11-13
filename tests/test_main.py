import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from src.main import main
from src.models import JobPosting
from src.storage.job_repository import JobRepository


RESUME = """Dana Developer
dana@example.com
SKILLS
Python, FastAPI, PostgreSQL, Docker, Git
EXPERIENCE
Software Engineering Intern building Python REST APIs.
EDUCATION
B.Sc. Computer Science
"""


def test_reclassify_previews_then_changes_only_relevance(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    for title, relevant, status in (
        ("Software Engineering Intern", False, "applied"),
        ("Senior Software Engineer", True, "ignored"),
    ):
        repository.save(JobPosting(
            title, "Example", "Haifa, Israel", f"https://example.com/jobs/{status}",
            description="Build Python backend services.", requirements="Python and Git",
            source="official careers", is_relevant=relevant, status=status,
        ))
    before = [asdict(job) for job in repository.list_all()]

    assert main(["reclassify", "--database", str(database)]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert [asdict(job) for job in repository.list_all()] == before

    assert main(["reclassify", "--database", str(database), "--apply"]) == 0
    assert "2 relevance changes" in capsys.readouterr().out
    after = [asdict(job) for job in repository.list_all()]
    for original, updated in zip(before, after):
        assert updated.pop("is_relevant") is not original.pop("is_relevant")
        assert updated == original
    assert main(["reclassify", "--database", str(database), "--apply"]) == 0
    assert "0 relevance changes" in capsys.readouterr().out


def test_reclassify_missing_database_does_not_create_it(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = tmp_path / "missing.db"
    assert main(["reclassify", "--database", str(database)]) == 1
    assert "Job database not found" in capsys.readouterr().err
    assert not database.exists()


def test_reclassify_postgres_requires_approval_before_connecting(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "unused-test-value")
    assert main(["reclassify", "--apply"]) == 1
    assert "--approve-postgres-apply" in capsys.readouterr().err


def test_relevance_apply_skips_stale_classification(tmp_path):
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()
    job = JobPosting("Software Student", "Example", "Israel", "https://example.com/job",
                     description="Develop Python services.", is_relevant=False)
    job_id = repo.save(job)
    snapshot = repo.get_by_id(job_id)
    job.description = "Perform manual tests."
    repo.save(job)
    assert not repo.apply_relevance_if_unchanged(snapshot, True)
    assert not repo.get_by_id(job_id).is_relevant


def test_reclassify_preview_does_not_normalize_legacy_urls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    job_id = repository.save(JobPosting(
        "Software Intern", "Example", "Israel", "https://example.com/jobs/legacy",
        description="Build and maintain Python backend services.",
        is_relevant=False, status="applied",
    ))
    legacy_url = "https://example.com/jobs/legacy?utm_source=old"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE job_postings SET job_url = ? WHERE id = ?", (legacy_url, job_id))
    original_bytes = database.read_bytes()

    assert main(["reclassify", "--database", str(database)]) == 0

    assert database.read_bytes() == original_bytes
    assert repository.get_by_id(job_id).job_url == legacy_url
    assert main(["reclassify", "--database", str(database), "--apply"]) == 0
    updated = repository.get_by_id(job_id)
    assert updated.is_relevant is True
    assert updated.job_url == legacy_url
    assert updated.status == "applied"


def test_reclassify_apple_recovery_is_bounded_and_never_replays(tmp_path, capsys, monkeypatch):
    from src.resume.safe_profile import SafeCandidateProfile
    from src.storage.scan_store import ScanStore

    monkeypatch.delenv("DATABASE_URL", raising=False)
    profile = SafeCandidateProfile("Rishon LeZion", ("Python",), (), (), "Bachelor's (in progress)")
    monkeypatch.setenv("CANDIDATE_PROFILE_JSON", profile.to_json())
    database = tmp_path / "jobs.db"
    repo = JobRepository(database)
    repo.initialize()
    store = ScanStore(repo)
    store.migrate()
    fixtures = [
        ("Software Engineering Student", "Haifa, Israel", "new", False),
        ("Software Engineering Student", "London, UK", "new", False),
        ("Software Engineering Student", "Israel", "ignored", False),
        ("Software Engineering Student", "Israel", "applied", False),
        ("Senior Software Engineer", "Israel", "new", False),
        ("Software Engineering Intern", "Israel", "new", True),
    ]
    try:
        for n, (title, location, status, relevant) in enumerate(fixtures, 21):
            job = JobPosting(title, "Apple", location, f"https://jobs.apple.com/en-il/details/{n}/student",
                             description="Develop software tools and write automated tests.\n"
                                         "Minimum Qualifications\nCurrently pursuing a degree in Software Engineering.\n"
                                         "Must have completed at least 3 semesters.",
                             is_relevant=relevant, status=status)
            if n == 21:
                store.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('job_postings',20)")
            assert store.save_discovery(job, "historical")
        before = [tuple(row) for row in store.execute("SELECT * FROM notification_outbox ORDER BY job_id")]
        args = ["reclassify", "--database", str(database)]
        assert main(args + ["--recovery-preview"]) == 0
        output = capsys.readouterr().out
        shortlist = output.split("Manual recovery shortlist", 1)[1]
        assert "details/21/student" in shortlist and "potentially_eligible" in shortlist
        assert all(f"details/{i}/student" not in shortlist for i in range(22, 27))
        assert "1 -> 5 relevant" in output and not repo.get_by_id(21).is_relevant
        assert main(args + ["--limit", "1", "--apply"]) == 0
        assert "Next batch: --after-id 21" in capsys.readouterr().out
        assert repo.get_by_id(21).is_relevant and not repo.get_by_id(22).is_relevant
        assert main(args + ["--after-id", "21", "--apply"]) == 0
        assert [repo.get_by_id(i).status for i in range(21, 27)] == [row[2] for row in fixtures]
        assert sum(job.is_relevant for job in repo.list_all()) == 5
        assert main(args + ["--apply"]) == 0
        assert "0 relevance changes" in capsys.readouterr().out
        assert not store.save_discovery(repo.get_by_id(21), "next")
        assert [tuple(row) for row in store.execute("SELECT * FROM notification_outbox ORDER BY job_id")] == before
    finally:
        store.close()


def _database_with_jobs(path: Path) -> tuple[JobRepository, int]:
    repository = JobRepository(path)
    repository.initialize()
    relevant_id = repository.save(
        JobPosting(
            "Python Software Intern",
            "Example",
            "Worldwide remote",
            "https://example.com/jobs/1",
            description="Build Python services with FastAPI and PostgreSQL.",
            requirements="Python, FastAPI, PostgreSQL, Git",
        )
    )
    repository.save(
        JobPosting(
            "Frontend Intern",
            "Other",
            "Worldwide remote",
            "https://example.com/jobs/2",
            description="Build React applications with TypeScript.",
            requirements="React, TypeScript, CSS",
        )
    )
    return repository, relevant_id


def test_match_command_returns_ranked_json(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    _database_with_jobs(database)

    exit_code = main(["match", str(resume), "--database", str(database), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema_version"] == 2
    assert "resume_path" not in payload
    assert len(payload["matches"]) == 2
    assert payload["matches"][0]["job"]["company"] == "Example"
    assert payload["matches"][0]["match"]["score"] > payload["matches"][1]["match"]["score"]
    assert "raw_text" not in payload
    assert "dana@example.com" not in json.dumps(payload)
    assert "origin" not in json.dumps(payload)
    assert payload["matches"][0]["location_check"]["status"] == "remote"


def test_match_command_selects_one_job(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    _, job_id = _database_with_jobs(database)

    exit_code = main(
        ["match", str(resume), "--database", str(database), "--job-id", str(job_id)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Python Software Intern" in output
    assert "/100" in output


def test_match_command_reports_missing_job(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    _database_with_jobs(database)

    exit_code = main(
        ["match", str(resume), "--database", str(database), "--job-id", "999"]
    )

    assert exit_code == 1
    assert "was not found" in capsys.readouterr().err


def test_match_command_rejects_unapproved_legacy_job(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    job_id = repository.save(
        JobPosting(
            "Legacy Role",
            "Example",
            "Worldwide remote",
            "https://example.com/jobs/legacy",
            is_relevant=False,
        )
    )

    exit_code = main(
        ["match", str(resume), "--database", str(database), "--job-id", str(job_id)]
    )

    assert exit_code == 1
    assert "has not passed" in capsys.readouterr().err


def test_match_command_handles_empty_database(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    JobRepository(database).initialize()

    exit_code = main(["match", str(resume), "--database", str(database)])

    assert exit_code == 0
    assert "No stored jobs" in capsys.readouterr().out


def test_match_command_excludes_foreign_job_from_recommendations(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Dana Developer\nRishon Lezion, Israel\nSKILLS\nPython\nEDUCATION\nB.Sc.",
        encoding="utf-8",
    )
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    repository.save(
        JobPosting(
            "Software Intern", "Example", "Warsaw, Poland",
            "https://example.com/jobs/poland", requirements="Python",
        )
    )

    exit_code = main(["match", str(resume), "--database", str(database), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["matches"] == []


def test_match_command_keeps_unverified_location_for_manual_review(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Dana Developer\nRishon Lezion, Israel\nSKILLS\nPython\nEDUCATION\nB.Sc.",
        encoding="utf-8",
    )
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    repository.save(
        JobPosting(
            "Software Intern", "Example", "Tel Aviv, Israel",
            "https://example.com/jobs/israel", requirements="Python",
        )
    )

    repository.save(JobPosting(
        "Software Intern", "Example", "Not specified",
        "https://example.com/jobs/unknown", requirements="Python",
    ))

    exit_code = main([
        "match", str(resume), "--database", str(database), "--json",
        "--skip-commute-check",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["matches"][0]["job"]["location"] == "Tel Aviv, Israel"
    assert payload["manual_review"][0]["job"]["location"] == "Not specified"


def test_match_command_does_not_recommend_applied_job(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    repository, job_id = _database_with_jobs(database)
    repository.update_status(job_id, "applied")

    exit_code = main(["match", str(resume), "--database", str(database), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert all(match["job"]["id"] != job_id for match in payload["matches"])


def test_list_and_status_commands_manage_job_lifecycle(tmp_path: Path, capsys) -> None:
    database = tmp_path / "jobs.db"
    _, job_id = _database_with_jobs(database)

    exit_code = main(["status", str(job_id), "applied", "--database", str(database)])
    status_output = capsys.readouterr().out
    list_exit_code = main(["list", "--database", str(database), "--status", "applied", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert f"Job {job_id} status updated to applied" in status_output
    assert list_exit_code == 0
    assert payload["jobs"] == [
        {
            "id": job_id,
            "title": "Python Software Intern",
            "company": "Example",
            "location": "Worldwide remote",
            "job_url": "https://example.com/jobs/1",
            "status": "applied",
        }
    ]


def test_status_command_reports_missing_job(tmp_path: Path, capsys) -> None:
    database = tmp_path / "jobs.db"
    JobRepository(database).initialize()

    exit_code = main(["status", "999", "reviewed", "--database", str(database)])

    assert exit_code == 1
    assert "was not found" in capsys.readouterr().err


def test_scan_dry_run_can_select_one_company(capsys) -> None:
    exit_code = main(["scan", "--dry-run", "--company", "Amazon"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Amazon" in output
    assert "Microsoft" not in output


def test_list_defaults_to_new_relevant_jobs(tmp_path: Path, capsys) -> None:
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.initialize()
    repository.save(
        JobPosting("New Intern", "Example", "Remote", "https://example/jobs/new")
    )
    repository.save(
        JobPosting(
            "Applied Intern", "Example", "Remote", "https://example/jobs/applied",
            status="applied",
        )
    )
    repository.save(
        JobPosting(
            "Legacy Senior", "Example", "Remote", "https://example/jobs/legacy",
            is_relevant=False,
        )
    )

    exit_code = main(["list", "--database", str(database), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [job["title"] for job in payload["jobs"]] == ["New Intern"]


def test_export_safe_profile_omits_contact_details(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")

    exit_code = main(["export-safe-profile", str(resume)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert "dana@example.com" not in output
    assert "name" not in payload
    assert "Python" in payload["skills"]


def test_tailor_resume_command_requires_approval(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    _, job_id = _database_with_jobs(database)

    exit_code = main([
        "tailor-resume", str(resume), str(job_id), str(tmp_path / "tailored.docx"),
        "--database", str(database),
    ])

    assert exit_code == 1
    assert "--approve" in capsys.readouterr().err


def test_tailor_resume_command_creates_approved_docx_and_pdf(tmp_path: Path, capsys) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    database = tmp_path / "jobs.db"
    _, job_id = _database_with_jobs(database)
    output = tmp_path / "tailored"

    exit_code = main([
        "tailor-resume", str(resume), str(job_id), str(output),
        "--database", str(database), "--change", "professional_format", "--approve",
    ])

    assert exit_code == 0
    assert (output / "Example-Python-Software-Intern-tailored.docx").is_file()
    assert (output / "Example-Python-Software-Intern-tailored.pdf").is_file()
    assert "created" in capsys.readouterr().out


def test_report_rejects_invalid_connection_without_echoing_value(monkeypatch,capsys):
    monkeypatch.setenv('DATABASE_URL','invalid-nonsecret-configuration')
    assert main(['scan-report']) == 1
    output=capsys.readouterr()
    assert 'ValueError' in output.err
    assert 'invalid-nonsecret-configuration' not in output.err
