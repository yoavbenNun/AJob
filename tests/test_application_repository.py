import sqlite3

import pytest
from src.applications.service import CandidateApplicationProfile, build_package_preview
from src.models import JobPosting
from src.storage.application_repository import ApplicationRepository
from docx import Document
from pypdf import PdfWriter


def _resume_pair(tmp_path):
    pdf = tmp_path / "resume.pdf"
    docx = tmp_path / "resume.docx"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf.open("wb") as output:
        writer.write(output)
    document = Document()
    document.add_paragraph("Resume")
    document.save(docx)
    return pdf, docx


def test_repository_stores_profile_package_and_manual_submission(tmp_path) -> None:
    repository = ApplicationRepository(tmp_path / "applications.db")
    repository.initialize()
    profile = CandidateApplicationProfile(
        full_name="Dana Developer", email="dana@example.com", phone="0501234567",
        location="Tel Aviv", work_authorization="authorized", availability="Immediately",
    )
    pdf, docx = _resume_pair(tmp_path)
    job = JobPosting("Software Intern", "Example", "Tel Aviv", "https://example/jobs/1", id=9)
    preview = build_package_preview(job, profile, pdf, docx, "Hello")

    repository.save_profile(profile)
    package_id = repository.save_approved_package(preview)

    assert repository.get_profile() == profile
    assert repository.list_packages_for_job(9, job.job_url)[0]["submission_status"] == "prepared"
    assert repository.mark_submitted_manually(package_id, 9, job.job_url) is True
    package = repository.list_packages_for_job(9, job.job_url)[0]
    assert package["submission_status"] == "submitted_manually"
    assert package["submitted_at"]
    assert repository.mark_submitted_manually(package_id, 9, job.job_url) is False


def test_manual_submission_requires_package_job_ownership(tmp_path) -> None:
    repository = ApplicationRepository(tmp_path / "applications.db")
    repository.initialize()
    profile = CandidateApplicationProfile(
        full_name="Dana Developer", email="dana@example.com", phone="0501234567",
        location="Tel Aviv", work_authorization="authorized", availability="Immediately",
    )
    pdf, docx = _resume_pair(tmp_path)
    preview = build_package_preview(
        JobPosting("Software Intern", "Example", "Tel Aviv", "https://example/jobs/1", id=9),
        profile, pdf, docx, "",
    )
    package_id = repository.save_approved_package(preview)

    assert repository.mark_submitted_manually(package_id, 10, preview.job_url) is False
    assert repository.list_packages_for_job(9, preview.job_url)[0]["submission_status"] == "prepared"


def test_package_ownership_requires_stable_job_url(tmp_path) -> None:
    repository = ApplicationRepository(tmp_path / "applications.db")
    repository.initialize()
    profile = CandidateApplicationProfile(
        full_name="Dana Developer", email="dana@example.com", phone="0501234567",
        location="Tel Aviv", work_authorization="authorized", availability="Immediately",
    )
    pdf, docx = _resume_pair(tmp_path)
    preview = build_package_preview(
        JobPosting("Intern", "Example", "Tel Aviv", "https://example/jobs/original", id=9),
        profile, pdf, docx, "",
    )
    package_id = repository.save_approved_package(preview)

    replacement_url = "https://example/jobs/replacement"
    assert repository.list_packages_for_job(9, replacement_url) == []
    assert repository.mark_submitted_manually(package_id, 9, replacement_url) is False


def test_manual_submission_rejects_files_changed_after_approval(tmp_path) -> None:
    repository = ApplicationRepository(tmp_path / "applications.db")
    repository.initialize()
    profile = CandidateApplicationProfile(
        full_name="Dana Developer", email="dana@example.com", phone="0501234567",
        location="Tel Aviv", work_authorization="authorized", availability="Immediately",
    )
    pdf, docx = _resume_pair(tmp_path)
    job = JobPosting("Intern", "Example", "Tel Aviv", "https://example/jobs/1", id=9)
    package_id = repository.save_approved_package(
        build_package_preview(job, profile, pdf, docx, "")
    )
    pdf.write_bytes(b"changed after approval")

    with pytest.raises(ValueError, match="changed after package approval"):
        repository.mark_submitted_manually(package_id, 9, job.job_url)
    assert repository.list_packages_for_job(9, job.job_url)[0]["submission_status"] == "prepared"


def test_initialize_migrates_legacy_application_package_schema(tmp_path) -> None:
    database = tmp_path / "applications.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE application_packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                job_title TEXT NOT NULL,
                company TEXT NOT NULL,
                job_url TEXT NOT NULL,
                resume_pdf_path TEXT NOT NULL,
                resume_docx_path TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                cover_note TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                submission_status TEXT NOT NULL DEFAULT 'prepared'
            );
            """
        )
        connection.execute(
            """INSERT INTO application_packages
            (job_id, job_title, company, job_url, resume_pdf_path, resume_docx_path,
             profile_json, cover_note, fingerprint, approved_at, submission_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (9, "Intern", "Example", "https://example/jobs/legacy",
             str(tmp_path / "legacy.pdf"), str(tmp_path / "legacy.docx"), "{}", "",
             "legacy", "2026-01-01T00:00:00+00:00", "prepared"),
        )

    ApplicationRepository(database).initialize()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(application_packages)"
            )
        }
    assert columns["submitted_at"]
    assert columns["resume_pdf_sha256"][3] == 1
    assert columns["resume_docx_sha256"][3] == 1
    repository = ApplicationRepository(database)
    legacy = repository.list_packages_for_job(9, "https://example/jobs/legacy")
    assert len(legacy) == 1
    with pytest.raises(ValueError, match="changed after package approval"):
        repository.mark_submitted_manually(legacy[0]["id"], 9, legacy[0]["job_url"])
