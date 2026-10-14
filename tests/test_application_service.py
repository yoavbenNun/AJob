from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter

from src.applications.service import (
    CandidateApplicationProfile,
    build_package_preview,
    validate_candidate_profile,
    verify_package_preview,
)
from src.models import JobPosting


def _profile(**overrides) -> CandidateApplicationProfile:
    values = {
        "full_name": "Dana Developer",
        "email": "dana@example.com",
        "phone": "+972 50-123-4567",
        "location": "Tel Aviv, Israel",
        "work_authorization": "authorized",
        "availability": "Immediately",
        "linkedin_url": "https://linkedin.com/in/dana",
        "github_url": "https://github.com/dana",
    }
    values.update(overrides)
    return CandidateApplicationProfile(**values)


def _job() -> JobPosting:
    return JobPosting(
        "Software Intern", "Example", "Tel Aviv", "https://example.com/jobs/1", id=7
    )


def _resume_pair(tmp_path: Path) -> tuple[Path, Path]:
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


def test_profile_validation_reports_missing_and_invalid_fields() -> None:
    missing = validate_candidate_profile(
        _profile(email="bad", work_authorization="unanswered", github_url="http://unsafe")
    )
    assert "Valid email" in missing
    assert "Work authorization" in missing
    assert "Valid Github Url" in missing


def test_preview_fingerprint_detects_changed_resume(tmp_path: Path) -> None:
    pdf, docx = _resume_pair(tmp_path)
    preview = build_package_preview(_job(), _profile(), pdf, docx, "Short note")
    pdf.write_bytes(b"changed")

    with pytest.raises(ValueError, match="changed after preview"):
        verify_package_preview(preview, _job())


def test_preview_requires_matching_pdf_docx_pair(tmp_path: Path) -> None:
    pdf, docx = _resume_pair(tmp_path)
    other = tmp_path / "other.docx"
    other.write_bytes(docx.read_bytes())

    with pytest.raises(ValueError, match="matching pair"):
        build_package_preview(_job(), _profile(), pdf, other, "")


def test_preview_rejects_files_with_only_resume_extensions(tmp_path: Path) -> None:
    pdf = tmp_path / "resume.pdf"
    docx = tmp_path / "resume.docx"
    pdf.write_bytes(b"not a pdf")
    docx.write_bytes(b"not a docx")

    with pytest.raises(ValueError, match="verified PDF and DOCX"):
        build_package_preview(_job(), _profile(), pdf, docx, "")
