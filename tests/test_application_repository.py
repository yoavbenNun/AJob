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

    import pytest
    with pytest.raises(ValueError, match="changed after package approval"):
        repository.mark_submitted_manually(package_id, 9, job.job_url)
    assert repository.list_packages_for_job(9, job.job_url)[0]["submission_status"] == "prepared"
