"""Validation and immutable snapshots for application packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from src.models import JobPosting


WORK_AUTHORIZATION_VALUES = {"authorized", "needs_sponsorship", "not_authorized", "unanswered"}
PUBLIC_PROFILE_FIELDS = ("linkedin_url", "github_url")


@dataclass(frozen=True, slots=True)
class CandidateApplicationProfile:
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    work_authorization: str = "unanswered"
    availability: str = ""
    salary_expectation: str = ""
    linkedin_url: str = ""
    github_url: str = ""


@dataclass(frozen=True, slots=True)
class ApplicationPackagePreview:
    job_id: int
    job_title: str
    company: str
    job_url: str
    resume_pdf_path: str
    resume_docx_path: str
    resume_pdf_sha256: str
    resume_docx_sha256: str
    profile: CandidateApplicationProfile
    cover_note: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ResumePairOption:
    id: str
    label: str
    pdf_path: Path
    docx_path: Path


def validate_candidate_profile(profile: CandidateApplicationProfile) -> tuple[str, ...]:
    missing: list[str] = []
    for field_name, label in (
        ("full_name", "Full name"), ("email", "Email"), ("phone", "Phone"),
        ("location", "Location"), ("availability", "Availability"),
    ):
        if not getattr(profile, field_name).strip():
            missing.append(label)
    if profile.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", profile.email.strip()):
        missing.append("Valid email")
    if profile.work_authorization not in WORK_AUTHORIZATION_VALUES or profile.work_authorization == "unanswered":
        missing.append("Work authorization")
    for field_name in PUBLIC_PROFILE_FIELDS:
        value = getattr(profile, field_name).strip()
        if value and not re.fullmatch(r"https://[^\s]+", value, flags=re.IGNORECASE):
            missing.append(f"Valid {field_name.replace('_', ' ').title()}")
    return tuple(dict.fromkeys(missing))


def build_package_preview(job: JobPosting, profile: CandidateApplicationProfile,
                          resume_pdf_path: Path, resume_docx_path: Path,
                          cover_note: str) -> ApplicationPackagePreview:
    missing = validate_candidate_profile(profile)
    if missing:
        raise ValueError("Complete these required fields: " + ", ".join(missing))
    if not resume_pdf_path.is_file() or resume_pdf_path.suffix.casefold() != ".pdf":
        raise ValueError("Choose a verified PDF and DOCX resume pair.")
    if not resume_docx_path.is_file() or resume_docx_path.suffix.casefold() != ".docx":
        raise ValueError("Choose a verified PDF and DOCX resume pair.")
    if resume_pdf_path.stem != resume_docx_path.stem:
        raise ValueError("Resume PDF and DOCX must be a matching pair.")
    _validate_resume_files(resume_pdf_path, resume_docx_path)
    clean_note = cover_note.strip()
    if len(clean_note) > 3000:
        raise ValueError("Cover note must be 3000 characters or fewer.")
    payload = {
        "job": [job.id, job.title, job.company, job.job_url],
        "profile": asdict(profile),
        "resume_pdf_path": str(resume_pdf_path),
        "resume_docx_path": str(resume_docx_path),
        "resume_pdf_sha256": _sha256_file(resume_pdf_path),
        "resume_docx_sha256": _sha256_file(resume_docx_path),
        "cover_note": clean_note,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if job.id is None:
        raise ValueError("Application packages require a stored job.")
    return ApplicationPackagePreview(
        job_id=job.id, job_title=job.title, company=job.company, job_url=job.job_url,
        resume_pdf_path=str(resume_pdf_path), resume_docx_path=str(resume_docx_path),
        resume_pdf_sha256=payload["resume_pdf_sha256"],
        resume_docx_sha256=payload["resume_docx_sha256"],
        profile=profile, cover_note=clean_note,
        fingerprint=fingerprint,
    )


def verify_package_preview(
    preview: ApplicationPackagePreview, job: JobPosting
) -> ApplicationPackagePreview:
    """Rebuild an approved preview from server-held facts and current files."""
    try:
        rebuilt = build_package_preview(
            job,
            preview.profile,
            Path(preview.resume_pdf_path),
            Path(preview.resume_docx_path),
            preview.cover_note,
        )
    except (OSError, ValueError) as error:
        raise ValueError("Job, profile, or resume files changed after preview.") from error
    if not hmac.compare_digest(rebuilt.fingerprint, preview.fingerprint):
        raise ValueError("Job, profile, or resume files changed after preview.")
    return rebuilt


def discover_resume_pairs(tailored_root: Path | None) -> tuple[ResumePairOption, ...]:
    """List direct, matching resume pairs without exposing paths as form values."""
    if tailored_root is None or not tailored_root.is_dir():
        return ()
    try:
        root = tailored_root.resolve()
    except OSError:
        return ()
    grouped: dict[str, dict[str, list[Path]]] = {}
    try:
        candidates = tuple(root.iterdir())
    except OSError:
        return ()
    for candidate in candidates:
        suffix = candidate.suffix.casefold()
        if suffix not in {".pdf", ".docx"}:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                continue
        except (OSError, ValueError):
            continue
        grouped.setdefault(candidate.stem, {}).setdefault(suffix, []).append(resolved)

    options: list[tuple[float, ResumePairOption]] = []
    for label, files in grouped.items():
        pdf_files = files.get(".pdf", [])
        docx_files = files.get(".docx", [])
        if len(pdf_files) != 1 or len(docx_files) != 1:
            continue
        pdf_path, docx_path = pdf_files[0], docx_files[0]
        pair_id = hashlib.sha256(
            f"{pdf_path.name}\0{docx_path.name}".encode("utf-8")
        ).hexdigest()
        try:
            newest = max(pdf_path.stat().st_mtime, docx_path.stat().st_mtime)
        except OSError:
            continue
        options.append((newest, ResumePairOption(pair_id, label, pdf_path, docx_path)))
    options.sort(key=lambda item: (-item[0], item[1].label.casefold()))
    return tuple(option for _, option in options)


def _validate_resume_files(pdf_path: Path, docx_path: Path) -> None:
    """Reject renamed or damaged files before showing an approval preview."""
    try:
        if not PdfReader(pdf_path).pages:
            raise ValueError
        Document(docx_path)
    except Exception as error:
        raise ValueError("Choose a verified PDF and DOCX resume pair.") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
