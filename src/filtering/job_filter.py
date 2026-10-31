"""Deterministic filtering for early-career software roles."""

import re
from dataclasses import dataclass

from src.models import JobPosting
from src.matching.eligibility import student_acceptance_evidence


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
    "firmware",
    "embedded software",
)
HARDWARE_ROLE_TERMS = (
    "electrical engineer", "electrical engineering", "hardware engineer",
    "hardware engineering", "asic", "rtl", "vlsi", "physical design",
    "design verification", "silicon validation", "board design", "circuit design",
    "analog", "rf engineer", "fpga",
)
HARDWARE_FOCUS_TERMS = (
    "electrical engineer", "electrical engineering", "hardware engineer",
    "hardware engineering", "rtl", "vlsi", "physical design",
    "design verification", "silicon validation", "board design", "circuit design",
    "analog", "rf engineer", "fpga",
)
DETAIL_SOFTWARE_TERMS = (
    "software development", "develop software",
    "developing software", "build software", "building software", "write software",
    "writing software", "build and maintain scripts", "python-based tooling",
    "develop data pipelines", "maintain data pipelines", "low-level software", "backend", "frontend",
    "firmware development", "develop firmware", "web applications",
)
EARLY_CAREER_TERMS = (
    "intern",
    "internship",
    "internships",
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
    details = _normalize(f"{job.description} {job.requirements}")
    software_title = any(_contains_phrase(title, term) for term in SOFTWARE_TERMS)
    hardware_focused_title = any(
        _contains_phrase(title, term) for term in HARDWARE_FOCUS_TERMS
    )
    if not software_title or hardware_focused_title:
        responsibility_text = re.split(
            r"minimum qualifications|basic qualifications|preferred qualifications|"
            r"^\s*:?\s*(?:(?:job|minimum|basic|preferred|required)\s+)?"
            r"(?:requirements(?:\s+and\s+qualifications)?|qualifications(?:\s+and\s+requirements)?)"
            r"\s*(?::|$)|\brequirements\s*:",
            job.description, maxsplit=1, flags=re.IGNORECASE | re.MULTILINE,
        )[0]
        responsibilities = _normalize(responsibility_text)
        develops_code = any(
            re.search(r"\b(?:develop(?:ing)?|writ(?:e|ing))\s+(?:production\s+)?code\b", clause)
            and not re.search(r"\b(?:not|never|no|without)\b|\b\w+n['’]t\b", clause)
            for clause in (_normalize(part) for part in re.split(r"[.;!\n]", responsibility_text))
        )
        develops_automation = (
            _contains_phrase(title, "automation")
            and re.search(
                r"(?<!not )(?<!never )\b(?:develop(?:ing)?|build(?:ing)?|implement(?:ing)?)\b"
                r"[^.;!\n]{0,80}\b(?:test\s+)?automation\s+"
                r"(?:frameworks?|infrastructure|tools?)\b",
                responsibilities,
            )
        )
        # A rejected duty is not evidence of development work. Check separate
        # responsibility clauses so a negative contrast does not hide a later
        # positive duty, and keep "not only" as affirmative wording.
        software_clauses = (
            _normalize(re.sub(r"\bnot only\b", "", part, flags=re.IGNORECASE))
            for part in re.split(r"[.;!\n]|\bbut\b", responsibility_text, flags=re.IGNORECASE)
        )
        develops_software = any(
            any(_positive_software_phrase(clause, term) for term in DETAIL_SOFTWARE_TERMS)
            for clause in software_clauses
        )
        if not develops_code and not develops_automation and not develops_software:
            return FilterDecision(False, "no software-development responsibilities were found")
    if any(_contains_phrase(title, term) for term in EARLY_CAREER_TERMS):
        return FilterDecision(True, "title contains an early-career marker")

    if student_acceptance_evidence(details):
        return FilterDecision(True, "job details explicitly accept ongoing studies")

    if re.search(
        r"\b(?:currently pursuing\b[^.;\n]{0,80}\b(?:bachelor|master|b\.sc|m\.sc|degree|phd|ph\.d)|currently enrolled|pursuing (?:an? |your )?(?:bachelor|master|b\.sc|m\.sc|degree|phd|ph\.d)|students? (?:in|of|who)|"
        r"suitable for (?:students?|new graduates)|"
        r"(?:internship|intern|new graduate|new grad|entry.level|early career) (?:position|role|program)|"
        r"(?:open to|designed for|for) (?:recent |new )?graduates?|"
        r"(?:recent |new )graduates? (?:are welcome|are encouraged|may apply))\b",
        details,
    ):
        return FilterDecision(True, "job details contain an early-career marker")
    if re.search(r"\b(?:0|zero)\s*(?:-|to)\s*[123]\s+years?\b", details):
        return FilterDecision(True, "job details allow zero years of experience")
    return FilterDecision(False, "no early-career evidence was found")


def prefilter_job(job: JobPosting) -> FilterDecision:
    """Identify detail pages worth opening without claiming final relevance."""
    title = _normalize(job.title)
    if any(_contains_phrase(title, term) for term in NON_ENGINEERING_ROLE_TERMS):
        return FilterDecision(False, "title describes a non-engineering role")
    software_title = any(_contains_phrase(title, term) for term in SOFTWARE_TERMS)
    explicit_software_role = any(_contains_phrase(title, term) for term in (
        "software engineer", "software engineering", "software developer",
        "software development", "firmware engineer", "firmware developer",
        "firmware development",
    ))
    hardware_focused = any(_contains_phrase(title, term) for term in HARDWARE_FOCUS_TERMS)
    if any(_contains_phrase(title, term) for term in HARDWARE_ROLE_TERMS) and (
        not software_title or (hardware_focused and not explicit_software_role)
    ):
        return FilterDecision(False, "title describes a hardware or electrical role")
    if not software_title and not any(
        _contains_phrase(title, term) for term in EARLY_CAREER_TERMS
    ):
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


def _positive_software_phrase(clause: str, phrase: str) -> bool:
    for match in re.finditer(rf"(?<![\w]){re.escape(phrase)}(?![\w])", clause):
        # Bind negation to the duty, not unrelated experience/supervision or
        # hardware clauses before it. This is deliberately not general NLU.
        if re.search(
            r"\b(?:not|never|no|without|\w+n['’]t)\s+"
            r"(?:(?:involve|involving|include|including|any|be|being|required|"
            r"expected|responsible|related|for|to|doing|perform|performing|focus|on)\s+){0,5}$",
            clause[:match.start()],
        ):
            continue
        if re.match(r"\s+(?:(?:is|are|will be)\s+)?(?:not|never)\b", clause[match.end():]):
            continue
        return True
    return False
