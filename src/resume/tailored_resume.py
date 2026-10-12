"""Human-approved, deterministic resume tailoring for one stored job."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from docx import Document

from src.matching.resume_matcher import MatchResult
from src.models import JobPosting, ResumeProfile


@dataclass(frozen=True, slots=True)
class TailoringProposal:
    """A reviewable, factual proposal before a tailored file is created."""

    before_score: int
    after_score: int
    reordered_skills: tuple[str, ...]
    changes: tuple[str, ...]
    safeguards: tuple[str, ...]


def build_tailoring_proposal(
    profile: ResumeProfile, job: JobPosting, match: MatchResult
) -> TailoringProposal:
    """Describe the only supported change without modifying the master resume.

    Reordering skills changes neither the factual evidence nor the deterministic
    score. Reporting that explicitly prevents the preview from implying a
    stronger fit than the source resume supports.
    """
    reordered_skills = _ordered_skills(profile, match)
    changes = (
        ("Keep the existing skill order; no stronger supported ordering was found.",)
        if reordered_skills == profile.skills
        else ("Reorder existing skills so skills recognized in this job appear first.",)
    )
    return TailoringProposal(
        before_score=match.score,
        after_score=match.score,
        reordered_skills=reordered_skills,
        changes=changes,
        safeguards=(
            "No new skills, experience, projects, metrics, or credentials will be added.",
            "Summary, experience, projects, and education remain limited to the parsed master resume content.",
            "The master resume is read only; generation writes a separate DOCX.",
        ),
    )


def tailored_resume_filename(job: JobPosting) -> str:
    """Return a safe, readable DOCX filename for the selected company and role."""
    return f"{_filename_component(job.company, 'company')}-{_filename_component(job.title, 'role')}-tailored.docx"


def tailored_resume_output_path(output_directory: str | Path, job: JobPosting) -> Path:
    """Choose the dedicated tailored-resume path for a job without creating it."""
    return Path(output_directory) / tailored_resume_filename(job)


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
    source = Path(profile.source_path)
    if profile.source_path != "<memory>" and source.resolve() == target.resolve():
        raise ValueError("Tailored resume output must be separate from the master resume.")
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

    ordered_skills = _ordered_skills(profile, match)
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


def _ordered_skills(profile: ResumeProfile, match: MatchResult) -> tuple[str, ...]:
    return tuple(match.matched_skills) + tuple(
        skill for skill in profile.skills if skill not in match.matched_skills
    )


def _filename_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value)
    cleaned = re.sub(r"\s+", "-", cleaned.strip()).strip(".-")
    return cleaned or fallback


__all__ = [
    "TailoringProposal",
    "build_tailoring_proposal",
    "create_tailored_resume",
    "tailored_resume_filename",
    "tailored_resume_output_path",
]
