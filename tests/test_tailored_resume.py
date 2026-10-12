from pathlib import Path

import pytest
from docx import Document

from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text
from src.resume.tailored_resume import create_tailored_resume


def _inputs():
    profile = parse_resume_text(
        "Dana Developer\ndana@example.com\nSKILLS\nPython, Git, Docker\n"
        "EXPERIENCE\nBuilt a Python service.\nEDUCATION\nB.Sc. Computer Science"
    )
    job = JobPosting(
        "Python Intern", "Example", "Remote", "https://example.com/jobs/1",
        requirements="Python and Docker",
    )
    return profile, job, match_resume_to_job(profile, job)


def test_tailored_resume_requires_explicit_approval(tmp_path: Path) -> None:
    profile, job, match = _inputs()

    with pytest.raises(PermissionError, match="explicit approval"):
        create_tailored_resume(profile, job, match, tmp_path / "resume.docx", approved=False)


def test_tailored_resume_uses_only_existing_content_and_reorders_skills(tmp_path: Path) -> None:
    profile, job, match = _inputs()
    output = tmp_path / "resume.docx"

    create_tailored_resume(profile, job, match, output, approved=True)

    text = "\n".join(paragraph.text for paragraph in Document(output).paragraphs)
    assert "Target role: Python Intern at Example" in text
    assert "Python, Docker, Git" in text
    assert "Built a Python service." in text
    assert "Kubernetes" not in text


def test_tailored_resume_refuses_to_overwrite(tmp_path: Path) -> None:
    profile, job, match = _inputs()
    output = tmp_path / "resume.docx"
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        create_tailored_resume(profile, job, match, output, approved=True)
