"""Explainable, deterministic resume-to-job matching."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import JobPosting, ResumeProfile
from src.resume.resume_parser import SKILL_ALIASES
from src.matching.eligibility import assess_student_eligibility


SKILL_POINTS = 60
EXPERIENCE_POINTS = 20
PROJECT_POINTS = 15
EDUCATION_POINTS = 5

EDUCATION_FIELD_ALIASES = {
    "computer science": ("computer science",),
    "software engineering": ("software engineering",),
    "computer engineering": ("computer engineering",),
    "electrical engineering": ("electrical engineering",),
}


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One transparent contribution to a match score."""

    points: int
    maximum: int
    reason: str


@dataclass(frozen=True, slots=True)
class MatchBreakdown:
    """The four deterministic components that total a match score."""

    skills: ScoreComponent
    experience: ScoreComponent
    projects: ScoreComponent
    education: ScoreComponent

    @property
    def total(self) -> int:
        """Return the unbounded sum of component points."""
        return sum(
            component.points
            for component in (self.skills, self.experience, self.projects, self.education)
        )


@dataclass(frozen=True, slots=True)
class MatchResult:
    """A score and evidence-based guidance for one resume and job posting."""

    score: int
    eligible: bool
    disqualifiers: tuple[str, ...]
    minimum_score_applied: bool
    breakdown: MatchBreakdown
    job_skills: tuple[str, ...]
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    strengths: tuple[str, ...]
    gaps: tuple[str, ...]
    tailoring_suggestions: tuple[str, ...]
    eligibility_status: str
    eligibility_reasons: tuple[str, ...]
    technical_score: int

    @property
    def eligibility_label(self) -> str:
        return {
            'student_eligible': 'Student eligible',
            'potentially_eligible': 'Potentially eligible',
            'ineligible': 'Ineligible',
        }[self.eligibility_status]


def match_resume_to_job(resume: ResumeProfile, job: JobPosting) -> MatchResult:
    """Score a resume against a posting without inferring unprovided experience.

    Skill requirements mentioned in the posting account for 60 points. Existing
    experience, project, and education sections account for the remaining 40
    points only when the resume itself contains that evidence. The returned score
    is always an integer from 1 through 100.
    """
    job_text = "\n".join((job.title, job.description, job.requirements))
    required_skills = _extract_known_skills(job_text)
    resume_skills = set(resume.skills)
    matched_skills = tuple(skill for skill in required_skills if skill in resume_skills)
    missing_skills = tuple(skill for skill in required_skills if skill not in resume_skills)

    skill_component = _skill_component(required_skills, matched_skills)
    experience_component = _section_component(
        resume.experience,
        matched_skills,
        EXPERIENCE_POINTS,
        "experience",
    )
    project_component = _section_component(
        resume.projects,
        matched_skills,
        PROJECT_POINTS,
        "projects",
    )
    education_component, education_disqualifiers = _education_component(
        resume.education, resume.summary, job.title, job.requirements
    )
    breakdown = MatchBreakdown(
        skills=skill_component,
        experience=experience_component,
        projects=project_component,
        education=education_component,
    )
    minimum_score_applied = breakdown.total == 0
    score = max(1, min(100, breakdown.total))
    technical_score = score
    eligibility_status, eligibility_reasons = assess_student_eligibility(resume, job)
    if education_disqualifiers:
        eligibility_status = 'ineligible'
        eligibility_reasons = education_disqualifiers + eligibility_reasons
    disqualifiers = eligibility_reasons if eligibility_status == 'ineligible' else ()
    eligible = eligibility_status != 'ineligible'
    if not eligible:
        score = min(score, 25)
    strengths = _strengths(resume, matched_skills, experience_component, project_component)
    gaps = _gaps(resume, required_skills, missing_skills)
    suggestions = _tailoring_suggestions(
        resume,
        matched_skills,
        missing_skills,
        required_skills,
    )
    return MatchResult(
        score=score,
        eligible=eligible,
        disqualifiers=disqualifiers,
        minimum_score_applied=minimum_score_applied,
        breakdown=breakdown,
        job_skills=required_skills,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        strengths=strengths,
        gaps=gaps,
        tailoring_suggestions=suggestions,
        eligibility_status=eligibility_status,
        eligibility_reasons=eligibility_reasons,
        technical_score=technical_score,
    )


def match_resume(resume: ResumeProfile, job: JobPosting) -> MatchResult:
    """Compatibility-friendly shorthand for :func:`match_resume_to_job`."""
    return match_resume_to_job(resume, job)


def _extract_known_skills(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    return tuple(
        canonical
        for canonical, aliases in SKILL_ALIASES.items()
        if any(_contains_alias(lowered, alias.casefold()) for alias in aliases)
    )


def _contains_alias(text: str, alias: str) -> bool:
    return re.search(rf"(?<![\w+#]){re.escape(alias)}(?![\w+#])", text) is not None


def _skill_component(
    required_skills: tuple[str, ...], matched_skills: tuple[str, ...]
) -> ScoreComponent:
    if not required_skills:
        return ScoreComponent(
            points=0,
            maximum=SKILL_POINTS,
            reason="no recognized skill requirements were found in the posting",
        )
    points = round(SKILL_POINTS * len(matched_skills) / len(required_skills))
    return ScoreComponent(
        points=points,
        maximum=SKILL_POINTS,
        reason=(
            f"{len(matched_skills)} of {len(required_skills)} recognized job skills "
            "are listed on the resume"
        ),
    )


def _section_component(
    section: str, matched_skills: tuple[str, ...], maximum: int, label: str
) -> ScoreComponent:
    if not section.strip():
        return ScoreComponent(0, maximum, f"resume has no {label} section content")
    evidenced = tuple(skill for skill in matched_skills if _skill_is_in_text(skill, section))
    if evidenced:
        return ScoreComponent(
            maximum,
            maximum,
            f"{label} section provides evidence for: {', '.join(evidenced)}",
        )
    return ScoreComponent(
        0,
        maximum,
        f"resume has {label} content but no matched job skill is explicit there; no points awarded",
    )


def _skill_is_in_text(skill: str, text: str) -> bool:
    lowered = text.casefold()
    return any(
        _contains_alias(lowered, alias.casefold()) for alias in SKILL_ALIASES[skill]
    )


def _education_component(
    education: str, summary: str, job_title: str, requirements: str
) -> tuple[ScoreComponent, tuple[str, ...]]:
    candidate_level = _candidate_education_level(education, summary)
    required_level = _required_education_level(job_title, requirements)
    if required_level > candidate_level:
        names = {1: "bachelor's", 2: "master's", 3: "PhD"}
        requirement = names[required_level]
        return (
            ScoreComponent(
                0,
                EDUCATION_POINTS,
                f"job appears to require {requirement} education not shown on the resume",
            ),
            (f"Education mismatch: job appears to require a {requirement} degree.",),
        )
    required_fields = _required_education_fields(requirements)
    candidate_fields = _education_fields(education)
    if required_fields and candidate_fields and not set(required_fields).intersection(candidate_fields):
        return (
            ScoreComponent(
                0,
                EDUCATION_POINTS,
                "resume degree field does not match the posting's closed field requirement",
            ),
            (
                "Education field mismatch: job requires "
                f"{', '.join(required_fields)}; resume shows {', '.join(candidate_fields)}.",
            ),
        )
    if education.strip():
        return (
            ScoreComponent(EDUCATION_POINTS, EDUCATION_POINTS, "resume includes education"),
            (),
        )
    return (
        ScoreComponent(0, EDUCATION_POINTS, "resume has no education section content"),
        (),
    )


def _education_level(text: str) -> int:
    normalized = text.casefold()
    if re.search(r"\b(?:ph\.?d\.?|doctorate|doctoral)\b", normalized):
        return 3
    if re.search(r"\b(?:m\.?s\.?|m\.?sc\.?|master'?s?)\b", normalized):
        return 2
    if re.search(r"\b(?:b\.?s\.?|b\.?sc\.?|bachelor'?s?|bsms)\b", normalized):
        return 1
    return 0


def _candidate_education_level(education: str, summary: str) -> int:
    explicit_level = _education_level(f"{education}\n{summary}")
    if explicit_level:
        return explicit_level
    normalized_education = education.casefold()
    normalized_summary = summary.casefold()
    has_institution = re.search(
        r"\b(?:university|college)\b", normalized_education
    ) is not None
    has_study_evidence = "student" in normalized_summary or re.search(
        r"\b20\d{2}\s*[-/]\s*20\d{2}\b", normalized_education
    ) is not None
    if has_institution and has_study_evidence:
        return 1
    return 0


def _required_education_level(title: str, requirements: str) -> int:
    normalized_title = title.casefold()
    title_levels = _degree_levels(normalized_title)
    if title_levels:
        return min(title_levels)

    hard_levels: list[int] = []
    mode = "unknown"
    for clause in _education_requirement_clauses(requirements):
        if _negates_requirement(clause):
            continue
        clause = clause.strip()
        if not clause:
            continue
        if "preferred" in clause or "nice to have" in clause:
            mode = "preferred"
            continue
        if "minimum qualification" in clause or "required" in clause:
            mode = "required"
        levels = _degree_levels(clause)
        has_hard_marker = _has_hard_education_marker(clause)
        if levels and (mode == "required" or has_hard_marker):
            hard_levels.append(min(levels))
    return max(hard_levels, default=0)


def _required_education_fields(requirements: str) -> tuple[str, ...]:
    """Return only explicit, closed degree-field requirements."""
    required: list[str] = []
    mode = "unknown"
    required_degree_context = False
    for clause in _education_requirement_clauses(requirements):
        if _negates_requirement(clause):
            continue
        if "preferred" in clause or "nice to have" in clause:
            mode = "preferred"
            required_degree_context = False
            continue
        if "minimum qualification" in clause or "required" in clause:
            mode = "required"
        fields = _education_fields(clause)
        levels = _degree_levels(clause)
        has_hard_marker = _has_hard_education_marker(clause)
        is_open = re.search(
            r"\b(?:related|similar)\s+(?:(?:technical|engineering|scientific)\s+)?(?:field|fields|discipline|disciplines)\b|"
            r"\bor\s+(?:an?\s+)?equivalent(?:\s+discipline|\s+field)?\b",
            clause,
        ) is not None
        degree_context = bool(levels) or (
            required_degree_context
            and re.match(
                r"^(?:required\s+|accepted\s+)?(?:degree\s+)?"
                r"(?:field|fields|major|majors|discipline|disciplines)\s*:",
                clause,
            ) is not None
        )
        if fields and degree_context and (mode == "required" or has_hard_marker) and not is_open:
            required.extend(fields)
        required_degree_context = bool(levels) and (mode == "required" or has_hard_marker)
    return tuple(dict.fromkeys(required))


def _has_hard_education_marker(clause: str) -> bool:
    return any(
        marker in clause for marker in ("required", "must", "pursuing", "enrolled")
    ) and not _negates_requirement(clause)


def _negates_requirement(clause: str) -> bool:
    return re.search(
        r"\b(?:is\s+not|isn't|isn’t|not)\s+"
        r"(?:(?:strictly|necessarily)\s+)?required\b|"
        r"\bno\b[^.;\n]{0,80}\brequired\b",
        clause,
    ) is not None


def _education_requirement_clauses(requirements: str) -> tuple[str, ...]:
    normalized = requirements.casefold()
    for source, replacement in (
        ("b.sc.", "bsc"), ("m.sc.", "msc"), ("ph.d.", "phd"),
        ("b.s.", "bs"), ("m.s.", "ms"),
    ):
        normalized = normalized.replace(source, replacement)
    return tuple(
        clause.strip()
        for clause in re.split(r"[;\n.]|,?\s+but\s+|,?\s+however\s+", normalized)
        if clause.strip()
    )


def _education_fields(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    return tuple(
        canonical
        for canonical, aliases in EDUCATION_FIELD_ALIASES.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases)
    )


def _degree_levels(text: str) -> list[int]:
    levels: list[int] = []
    if re.search(r"\b(?:bsms|b\.?s\.?|b\.?sc\.?|bachelor'?s?)\b", text):
        levels.append(1)
    if re.search(r"\b(?:m\.?s\.?|m\.?sc\.?|master'?s?)\b", text):
        levels.append(2)
    if re.search(r"\b(?:ph\.?d\.?|doctorate|doctoral)\b", text):
        levels.append(3)
    return levels


def _strengths(
    resume: ResumeProfile,
    matched_skills: tuple[str, ...],
    experience: ScoreComponent,
    projects: ScoreComponent,
) -> tuple[str, ...]:
    strengths: list[str] = []
    if matched_skills:
        strengths.append(f"Matches job skills: {', '.join(matched_skills)}.")
    if experience.points == experience.maximum:
        strengths.append("Experience section explicitly supports matched job skills.")
    if projects.points == projects.maximum:
        strengths.append("Project section explicitly supports matched job skills.")
    if resume.education.strip():
        strengths.append("Education is documented on the resume.")
    return tuple(strengths) or ("No direct match evidence was found by the deterministic rules.",)


def _gaps(
    resume: ResumeProfile,
    required_skills: tuple[str, ...],
    missing_skills: tuple[str, ...],
) -> tuple[str, ...]:
    gaps: list[str] = []
    if missing_skills:
        gaps.append(f"Job skills not listed on the resume: {', '.join(missing_skills)}.")
    if not required_skills:
        gaps.append("The posting has no skill terms recognized by the current matcher.")
    if not resume.experience.strip():
        gaps.append("No experience section content is available to support the fit.")
    if not resume.projects.strip():
        gaps.append("No project section content is available to support the fit.")
    if not resume.education.strip():
        gaps.append("No education section content is available to support the fit.")
    return tuple(gaps)


def _tailoring_suggestions(
    resume: ResumeProfile,
    matched_skills: tuple[str, ...],
    missing_skills: tuple[str, ...],
    required_skills: tuple[str, ...],
) -> tuple[str, ...]:
    suggestions: list[str] = []
    if matched_skills:
        strongest = ", ".join(matched_skills[:4])
        suggestions.append(
            f"Tailor the summary to the target role and emphasize {strongest}; "
            "keep only claims already supported elsewhere in the resume."
        )
    experience_evidence = tuple(
        skill for skill in matched_skills if _skill_is_in_text(skill, resume.experience)
    )
    if experience_evidence:
        suggestions.append(
            "In Experience, expand the existing evidence for "
            f"{', '.join(experience_evidence)} with the action taken and a measurable outcome, "
            "when those details are accurate."
        )
    project_evidence = tuple(
        skill for skill in matched_skills if _skill_is_in_text(skill, resume.projects)
    )
    if project_evidence:
        suggestions.append(
            "In Projects, make the implementation and result for "
            f"{', '.join(project_evidence)} explicit; do not add work that was not performed."
        )
    unsupported_matches = tuple(
        skill
        for skill in matched_skills
        if skill not in experience_evidence and skill not in project_evidence
    )
    if unsupported_matches:
        suggestions.append(
            f"Connect {', '.join(unsupported_matches)} to a real experience or project example "
            "if one exists; otherwise leave it only in the skills section."
        )
    if missing_skills:
        suggestions.append(
            "Do not add "
            f"{', '.join(missing_skills)} unless you can truthfully support the claim; "
            "consider learning it or building a relevant project first."
        )
    if not required_skills:
        suggestions.append(
            "Review the posting manually for role-specific terms not covered by the current skill list."
        )
    return tuple(suggestions) or (
        "Keep the resume factual; this matcher found no specific tailoring evidence.",
    )


__all__ = [
    "MatchBreakdown",
    "MatchResult",
    "ScoreComponent",
    "match_resume",
    "match_resume_to_job",
]
