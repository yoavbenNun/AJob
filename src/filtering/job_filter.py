"""Deterministic filtering for early-career software roles."""

import re
from dataclasses import dataclass

from src.models import JobPosting


SOFTWARE_TERMS = (
    "software",
    "developer",
    "full stack",
    "frontend",
    "front end",
    "backend",
    "back end",
    "mobile engineer",
    "devops",
    "site reliability",
    "machine learning engineer",
    "qa automation",
)
EARLY_CAREER_TERMS = (
    "intern",
    "internship",
    "student",
    "new grad",
    "new graduate",
    "new graduates",
    "graduate program",
    "junior",
    "entry level",
    "entry-level",
    "apprentice",
    "early career",
)
SENIOR_TERMS = (
    "senior",
    "sr.",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "architect",
    "head of",
)
NON_ENGINEERING_ROLE_TERMS = (
    "analyst",
    "coordinator",
    "marketing",
    "product manager",
    "program manager",
    "recruiter",
    "sales",
)


@dataclass(frozen=True, slots=True)
class FilterDecision:
    relevant: bool
    reason: str


def classify_job(job: JobPosting) -> FilterDecision:
    """Classify a role using title plus enriched detail-page evidence."""
    potential = prefilter_job(job)
    if not potential.relevant:
        return potential

    title = _normalize(job.title)
    if any(_contains_phrase(title, term) for term in EARLY_CAREER_TERMS):
        return FilterDecision(True, "title contains an early-career marker")

    details = _normalize(f"{job.description} {job.requirements}")
    if any(_contains_phrase(details, term) for term in EARLY_CAREER_TERMS):
        return FilterDecision(True, "job details contain an early-career marker")
    if re.search(r"\b(?:0|zero)\s*(?:-|to)\s*[123]\s+years?\b", details):
        return FilterDecision(True, "job details allow zero years of experience")
    return FilterDecision(False, "no early-career evidence was found")


def prefilter_job(job: JobPosting) -> FilterDecision:
    """Identify detail pages worth opening without claiming final relevance."""
    title = _normalize(job.title)
    if any(_contains_phrase(title, term) for term in NON_ENGINEERING_ROLE_TERMS):
        return FilterDecision(False, "title describes a non-engineering role")
    if not any(_contains_phrase(title, term) for term in SOFTWARE_TERMS):
        return FilterDecision(False, "title is not software-oriented")
    if any(_contains_phrase(title, term) for term in SENIOR_TERMS) or re.search(
        r"\b(?:ii|iii|iv)\b", title
    ):
        return FilterDecision(False, "title contains a senior-level marker")
    return FilterDecision(True, "software-oriented candidate")


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("\u2013", "-").split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(
        rf"(?<![\w]){re.escape(phrase)}(?![\w])",
        text,
    ) is not None
