"""Privacy-reduced candidate profile for scheduled cloud matching."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict

from src.models import ResumeProfile
from src.matching.eligibility import candidate_is_studying
from src.resume.resume_parser import SKILL_ALIASES, extract_skills


@dataclass(frozen=True, slots=True)
class SafeCandidateProfile:
    """Only matching evidence; excludes name, email, phone, and resume prose."""

    location: str
    skills: tuple[str, ...]
    experience_skills: tuple[str, ...]
    project_skills: tuple[str, ...]
    education_level: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    def to_resume_profile(self) -> ResumeProfile:
        return ResumeProfile(
            source_path="<safe-cloud-profile>",
            name="",
            location=self.location,
            email="",
            phone="",
            skills=self.skills,
            education=self.education_level,
            experience=", ".join(self.experience_skills),
            projects=", ".join(self.project_skills),
            summary="",
            raw_text="",
        )


def build_safe_profile(profile: ResumeProfile) -> SafeCandidateProfile:
    """Reduce a parsed resume to facts required by deterministic matching."""
    return SafeCandidateProfile(
        location=profile.location,
        skills=profile.skills,
        experience_skills=_skills_in_section(profile.experience, profile.skills),
        project_skills=_skills_in_section(profile.projects, profile.skills),
        education_level=(_education_label(profile.education)
                         + (' (in progress)' if candidate_is_studying(profile) else '')),
    )


def load_safe_profile_json(value: str) -> SafeCandidateProfile:
    data = json.loads(value)
    required = {
        "location", "skills", "experience_skills", "project_skills", "education_level"
    }
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("Safe candidate profile has missing or unexpected fields.")
    known = set(SKILL_ALIASES)
    for field in ("skills", "experience_skills", "project_skills"):
        if not isinstance(data[field], list) or not all(
            isinstance(skill, str) and skill in known for skill in data[field]
        ):
            raise ValueError(f"Safe candidate profile field {field!r} is invalid.")
    if not isinstance(data["location"], str) or not isinstance(data["education_level"], str):
        raise ValueError("Safe candidate profile text fields are invalid.")
    return SafeCandidateProfile(
        location=data["location"],
        skills=tuple(data["skills"]),
        experience_skills=tuple(data["experience_skills"]),
        project_skills=tuple(data["project_skills"]),
        education_level=data["education_level"],
    )


def _skills_in_section(section: str, skills: tuple[str, ...]) -> tuple[str, ...]:
    found = set(extract_skills(section))
    return tuple(skill for skill in skills if skill in found)


def _education_label(education: str) -> str:
    lowered = education.casefold()
    if any(term in lowered for term in ("phd", "ph.d", "doctor")):
        return "PhD"
    if any(term in lowered for term in ("master", "m.sc", "msc")):
        return "Master's"
    if any(term in lowered for term in ("bachelor", "b.sc", "bsc")):
        return "Bachelor's"
    return ""
