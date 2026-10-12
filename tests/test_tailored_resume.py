from pathlib import Path

import pytest
from docx import Document

from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text
from src.resume.tailored_resume import (
    build_tailoring_proposal,
    create_tailored_resume,
    tailored_resume_filename,
)


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


def test_tailoring_proposal_keeps_score_truthful_and_names_job_output() -> None:
    profile, job, match = _inputs()

    proposal = build_tailoring_proposal(profile, job, match)

    assert proposal.before_score == proposal.after_score == match.score
    assert proposal.reordered_skills == ("Python", "Docker", "Git")
    assert any("No new skills" in safeguard for safeguard in proposal.safeguards)
    assert tailored_resume_filename(job) == "Example-Python-Intern-tailored.docx"


def test_tailored_resume_cannot_replace_master_resume(tmp_path: Path) -> None:
    profile, job, match = _inputs()
    master = tmp_path / "master.docx"
    profile = profile.__class__(
        source_path=str(master), name=profile.name, location=profile.location,
        email=profile.email, phone=profile.phone, skills=profile.skills,
        education=profile.education, experience=profile.experience,
        projects=profile.projects, summary=profile.summary, raw_text=profile.raw_text,
    )

    with pytest.raises(ValueError, match="separate from the master"):
        create_tailored_resume(profile, job, match, master, approved=True)
