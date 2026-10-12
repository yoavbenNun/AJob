import json
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


def _database_with_jobs(path: Path) -> tuple[JobRepository, int]:
    repository = JobRepository(path)
    repository.initialize()
    relevant_id = repository.save(
        JobPosting(
            "Python Software Intern",
            "Example",
            "Remote",
            "https://example.com/jobs/1",
            description="Build Python services with FastAPI and PostgreSQL.",
            requirements="Python, FastAPI, PostgreSQL, Git",
        )
    )
    repository.save(
        JobPosting(
            "Frontend Intern",
            "Other",
            "Remote",
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
            "Remote",
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

    exit_code = main([
        "match", str(resume), "--database", str(database), "--json",
        "--skip-commute-check",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["matches"] == []
    assert payload["manual_review"][0]["job"]["location"] == "Tel Aviv, Israel"


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
            "location": "Remote",
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
