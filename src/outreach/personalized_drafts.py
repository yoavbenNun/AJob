"""Deterministic drafts only. No AI, remote requests, inference or persistence."""

import re

from src.models import JobPosting
from src.outreach.contact_cards import ContactCard

STYLES = {"short", "inquiry", "referral"}
SHORT_LIMIT = 200  # Conservative free-account limit; recheck LinkedIn before use.


def _one_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _characters(value: str) -> int:
    # Conservative and identical to the browser's UTF-16 textarea counter.
    return len(value.encode("utf-16-le")) // 2


def personalized_draft(job: JobPosting, contact: ContactCard, style: str, revision: int = 0) -> dict:
    if style not in STYLES or not isinstance(revision, int) or not 0 <= revision <= 1000:
        raise ValueError("Invalid draft style or revision")
    name = _one_line(contact.display_name.split()[0], 35)
    title, company = _one_line(job.title, 160), _one_line(job.company, 100)
    greeting = f"Hi {name},"
    if style == "short":
        tail = (" I'd appreciate connecting. Thank you!" if revision % 2 == 0
                else " May we connect? Thank you!")
        body = f"{greeting} I'm interested in the {title} role at {company}.{tail}"
        if _characters(body) > SHORT_LIMIT:
            # Use a complete, shorter template rather than slicing a sentence.
            body = f"{greeting} I'm interested in an opening at {company}.{tail}"
        if _characters(body) > SHORT_LIMIT:
            body = f"{greeting} I'm interested in a role at your company.{tail}"
        return {"body": body, "limit": SHORT_LIMIT, "style": style}
    context = f"I'm interested in the {title} opening at {company}."
    role = _one_line(contact.role_title, 200)
    if role:
        context += f" I saw your listed role, {role}, and hoped to ask a brief question."
    # Only quote a complete source line; no claim of personal competence.
    lines = re.split(r"[\r\n]+", job.description)
    fact = next((line.strip() for line in lines
                 if 20 <= len(line.strip()) <= 160
                 and re.match(r"(?i)^(?:you will |develop |build |implement |maintain )", line.strip())
                 and line.strip().endswith((".", "!", "?"))), "")
    if fact:
        context += f' The posting states: "{fact}"'
    if style == "referral":
        question = (
            "If you feel it is appropriate after reviewing my background, would you be "
            "comfortable considering a referral, or pointing me to the right contact? "
            "I understand if you are not able to help."
        )
    elif contact.reason == "Company recruiter":
        question = "Could you share whether applications for this role are still being reviewed and the best way to apply?"
    else:
        question = "If you are familiar with this opening, could you share a brief insight about the work or point me to the appropriate contact?"
    closing = ("Thank you for your time." if revision % 2 == 0
               else "I appreciate your consideration. Thank you.")
    return {"body": f"{greeting}\n\n{context}\n\n{question}\n\n{closing}",
            "limit": 2000, "style": style}
