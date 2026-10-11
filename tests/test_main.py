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
    assert payload["schema_version"] == 1
    assert "resume_path" not in payload
    assert len(payload["matches"]) == 2
    assert payload["matches"][0]["job"]["company"] == "Example"
    assert payload["matches"][0]["match"]["score"] > payload["matches"][1]["match"]["score"]
    assert "raw_text" not in payload
    assert "dana@example.com" not in json.dumps(payload)


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
