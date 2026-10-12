"""Deterministic contact ranking and factual outreach drafting."""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit, urlunsplit

from src.models import JobPosting, OutreachContact, RankedContact


RELATIONSHIP_POINTS = {"hiring_team": 25, "recruiter": 25, "employee": 10, "alumni": 10, "other": 0}
DRAFT_KINDS = {"connection_note", "full_message"}


def validate_profile_url(value: str) -> str:
    """Return a normalized public HTTP(S) profile URL without query tracking."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Profile URL must be a public HTTP(S) URL.")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname not in {"linkedin.com", "www.linkedin.com"}:
        raise ValueError("For this MVP, use a public LinkedIn profile URL.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def rank_contact(job: JobPosting, contact: OutreachContact) -> RankedContact:
    score = 0
    reasons: list[str] = []
    if contact.company.strip().casefold() == job.company.strip().casefold():
        score += 35
        reasons.append("Works at the hiring company")
    relationship_points = RELATIONSHIP_POINTS.get(contact.relationship, 0)
    if relationship_points:
        score += relationship_points
        reasons.append(f"Relevant relationship: {contact.relationship.replace('_', ' ')}")
    job_words = _meaningful_words(job.title)
    shared = sorted(job_words & _meaningful_words(contact.role_title))
    if shared:
        score += 20
        reasons.append("Role overlap: " + ", ".join(shared[:3]))
    if contact.location.strip() and _locations_overlap(contact.location, job.location):
        score += 10
        reasons.append("Same listed location")
    return RankedContact(contact=contact, score=min(score, 100), reasons=tuple(reasons))


def build_contact_searches(job: JobPosting) -> tuple[dict[str, str], ...]:
    """Build user-opened searches without fetching or scraping profile data."""
    searches = (
        ("Recruiters", f'site:linkedin.com/in "{job.company}" recruiter talent acquisition Israel'),
        ("Hiring managers", f'site:linkedin.com/in "{job.company}" engineering manager "{job.title}"'),
        ("Relevant team members", f'site:linkedin.com/in "{job.company}" "{job.title}"'),
        ("University alumni", f'site:linkedin.com/in "{job.company}" software engineer university alumni'),
    )
    return tuple({"label": label, "url": f"https://www.google.com/search?q={quote_plus(query)}"}
                 for label, query in searches)


def draft_outreach_message(job: JobPosting, contact: OutreachContact,
                           kind: str = "full_message") -> str:
    """Create a short message using only stored job/contact facts."""
    if kind not in DRAFT_KINDS:
        raise ValueError("Unknown outreach draft type.")
    greeting = f"Hi {contact.display_name.strip()}," if contact.display_name.strip() else "Hi,"
    if contact.company.strip().casefold() == job.company.strip().casefold():
        context = f"noticed that you work there as {contact.role_title.strip() or 'a team member'}"
    else:
        context = (
            f"noticed your work as {contact.role_title.strip() or 'a team member'} "
            f"at {contact.company.strip()}"
        )
    if kind == "connection_note":
        return (
            f"Hi {contact.display_name.strip()}, I saw the {job.title} opening at "
            f"{job.company} and {context}. I would be glad to connect and hear any "
            "brief insight you may have. Thank you!"
        )[:300]
    return (
        f"{greeting}\n\n"
        f"I saw the {job.title} opening at {job.company} and {context}. "
        "I am considering applying and would appreciate any brief insight you can share "
        "about the role or team. If appropriate, I would also be grateful if you could "
        "take a quick look at my resume.\n\n"
        "Thank you for your time."
    )


def validate_approved_body(body: str, kind: str) -> str:
    """Validate the exact user-edited text they explicitly approve."""
    if kind not in DRAFT_KINDS:
        raise ValueError("Unknown outreach draft type.")
    cleaned = body.strip()
    limit = 300 if kind == "connection_note" else 2000
    if not cleaned:
        raise ValueError("Message cannot be empty.")
    if len(cleaned) > limit:
        raise ValueError(f"Message must be {limit} characters or fewer.")
    return cleaned


def _meaningful_words(value: str) -> set[str]:
    ignored = {"and", "the", "for", "of", "at", "a", "an", "senior", "junior", "student", "intern"}
    return {word for word in re.findall(r"[a-z0-9+#.]+", value.casefold()) if len(word) > 2 and word not in ignored}


def _locations_overlap(left: str, right: str) -> bool:
    left_parts = {part.strip().casefold() for part in re.split(r"[,/|-]", left) if part.strip()}
    right_parts = {part.strip().casefold() for part in re.split(r"[,/|-]", right) if part.strip()}
    return bool(left_parts & right_parts)
