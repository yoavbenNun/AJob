"""Read-only projection of saved contacts backed by separately reviewed evidence."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
import re
from urllib.parse import urlsplit

from src.models import JobPosting, OutreachContact


class ContactRelevance(IntEnum):
    DIRECT_TEAM = 0
    ROLE_RELEVANT_TEAM = 1
    COMPANY_RECRUITER = 2


@dataclass(frozen=True, slots=True)
class ReviewedContactEvidence:
    """Supplied by a trusted reviewer/source, never derived from legacy labels."""

    job_url: str
    profile_url: str
    relevance: ContactRelevance
    display_name: str
    role_title: str = ""


@dataclass(frozen=True, slots=True)
class ContactCard:
    profile_url: str
    display_name: str
    role_title: str
    reason: str
    company: str = ""
    evidence_url: str = ""
    source_type: str = "reviewed_local_evidence"
    last_verified_at: str = ""
    avatar_url: str = ""

    @property
    def identity(self) -> str:
        return self.profile_url

    @property
    def initials(self) -> str:
        return "".join(word[0] for word in self.display_name.split()[:2]).upper()


def canonical_profile_url(value: str) -> str | None:
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or not re.fullmatch(
        r"(?:www\.|[a-z]{2}\.)?linkedin\.com", parsed.netloc.lower()
    ):
        return None
    match = re.fullmatch(r"/in/([a-zA-Z0-9][a-zA-Z0-9-]{0,199})/?", parsed.path)
    if not match:
        return None
    return f"https://www.linkedin.com/in/{match[1].lower()}"


def contact_cards(
    job: JobPosting,
    contacts: Iterable[OutreachContact],
    evidence: Iterable[ReviewedContactEvidence],
) -> tuple[ContactCard, ...]:
    if job.id is None:
        return ()
    saved_urls = {
        canonical_profile_url(contact.profile_url)
        for contact in contacts
        if contact.job_id == job.id
    }
    candidates = []
    for item in evidence:
        if item.job_url != job.job_url or not isinstance(item.relevance, ContactRelevance):
            continue
        url = canonical_profile_url(item.profile_url)
        name, title = item.display_name.strip(), item.role_title.strip()
        if not url or url not in saved_urls or not name:
            continue
        reason = ("Company recruiter" if item.relevance == ContactRelevance.COMPANY_RECRUITER
                  else "Relevant team member")
        candidates.append((item.relevance, not title, name.casefold(), url,
                           ContactCard(url, name, title, reason, company=job.company)))
    # Prefer stronger evidence; within the same tier prefer a supported title.
    cards: dict[str, ContactCard] = {}
    for _, _, _, url, card in sorted(candidates, key=lambda row: row[:4]):
        cards.setdefault(url, card)
    return tuple(cards.values())
