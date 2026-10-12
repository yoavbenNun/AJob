"""Human-approved, deterministic resume tailoring for one stored job."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from src.matching.resume_matcher import MatchResult
from src.models import JobPosting, ResumeProfile


def create_tailored_resume(
    profile: ResumeProfile,
    job: JobPosting,
    match: MatchResult,
    output_path: str | Path,
    *,
    approved: bool,
) -> Path:
    """Create a factual DOCX only after explicit human approval.

    The deterministic version reorders already-supported skills and preserves the
    candidate's original section text. It never invents claims or silently
    overwrites an existing file.
    """
    if not approved:
        raise PermissionError("Tailored resume generation requires explicit approval.")
    target = Path(output_path)
    if target.suffix.casefold() != ".docx":
        raise ValueError("Tailored resume output must use the .docx extension.")
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading(profile.name or "Resume", level=0)
    contact = " | ".join(value for value in (profile.location, profile.email, profile.phone) if value)
    if contact:
        document.add_paragraph(contact)
    document.add_paragraph(f"Target role: {job.title} at {job.company}")

    if profile.summary:
        _add_section(document, "Summary", profile.summary)

    ordered_skills = tuple(match.matched_skills) + tuple(
        skill for skill in profile.skills if skill not in match.matched_skills
    )
    if ordered_skills:
        _add_section(document, "Technical Skills", ", ".join(ordered_skills))
    if profile.experience:
        _add_section(document, "Experience", profile.experience)
    if profile.projects:
        _add_section(document, "Projects", profile.projects)
    if profile.education:
        _add_section(document, "Education", profile.education)

    document.save(target)
    return target


def _add_section(document: Document, heading: str, content: str) -> None:
    document.add_heading(heading, level=1)
    for block in (part.strip() for part in content.splitlines()):
        if block:
            document.add_paragraph(block)

