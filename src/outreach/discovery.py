"""Bounded, read-only local reviewed evidence source. Never fetches profiles.

This is an operator-reviewed source, not automated people discovery. Records
assert provenance and reuse permission; syntax validation cannot verify truth.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

from src.models import JobPosting
from src.outreach.contact_cards import ContactCard, canonical_profile_url

MAX_BYTES = 512_000
MAX_CONTACTS = 200
MAX_AGE = timedelta(days=30)
SOURCE_TYPES = {"official_company_page", "official_job_posting", "authorized_provider"}
REASONS = {
    "direct_team": (0, "Relevant team member"),
    "role_relevant": (1, "Role-relevant professional"),
    "recruiter": (2, "Company recruiter"),
    "company_employee": (3, "Company association verified"),
}


@dataclass(frozen=True, slots=True)
class ContactResults:
    cards: tuple[ContactCard, ...] = ()
    state: str = "empty"


def safe_evidence_url(value: str) -> str | None:
    """Validate a public source link, never an SSRF fetch target."""
    if not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or parsed.query or parsed.fragment or not host.isascii()
                or not re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", host)):
            return None
        if host.lower() == "linkedin.com" or host.lower().endswith(".linkedin.com"):
            return None  # We do not verify/reuse LinkedIn pages as discovery evidence.
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if host.lower().endswith((".local", ".internal", ".localhost")):
            return None
        return value
    except ValueError:
        return None


def _text(row, key, limit=300):
    value = row.get(key, "")
    if not isinstance(value, str) or len(value) > limit or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("Invalid contact field")
    value.encode("utf-8")  # Reject isolated surrogates before rendering.
    return value.strip()


def reviewed_card(row, job: JobPosting, now: datetime) -> tuple[int, ContactCard] | None:
    if not isinstance(row, dict):
        raise ValueError("Invalid contact record")
    if row.get("job_url") != job.job_url:
        return None
    if (_text(row, "company").casefold() != job.company.strip().casefold()
            or row.get("verification_status") != "reviewed"
            or row.get("reuse_permitted") is not True):
        return None
    profile = canonical_profile_url(_text(row, "profile_url", 2048))
    source = safe_evidence_url(_text(row, "evidence_url", 2048))
    name, title = _text(row, "full_name", 120), _text(row, "headline")
    category = row.get("role_category")
    if (not profile or not source or len(name.split()) < 2
            or row.get("source_type") not in SOURCE_TYPES or category not in REASONS
            or len(_text(row, "association_basis", 1000)) < 10):
        return None
    verified = datetime.fromisoformat(_text(row, "last_verified_at", 50))
    if (verified.tzinfo is None or verified > now + timedelta(minutes=5)
            or now - verified > MAX_AGE):
        return None
    avatar = _text(row, "avatar_url", 200)
    # Local operator-provided photos only: no remote tracking, proxy or copying.
    if not (row.get("avatar_reuse_permitted") is True
            and re.fullmatch(r"/static/contact-avatars/[a-zA-Z0-9_-]+\.(?:png|jpg|webp)", avatar)):
        avatar = ""
    rank, reason = REASONS[category]
    return rank, ContactCard(profile, name, title, reason, job.company, source,
                             row["source_type"], verified.isoformat(), avatar)


class ReviewedFileSource:
    """Caches at most 200 local records for 30s; deletions/edits expire promptly.

    Missing file is an honest empty state. Invalid/unreadable file is unavailable.
    No SQL, network queries, logs of contact details, writes or background worker.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._until = 0.0
        self._rows = ()
        self._state = "empty"

    def _read(self):
        if time.monotonic() < self._until:
            return self._rows, self._state
        rows, state = (), "empty"
        try:
            if self.path.is_symlink():
                raise ValueError("Linked evidence file is not supported")
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Evidence source too large")
            data = json.loads(raw)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("contacts"), list)
                    or len(data["contacts"]) > MAX_CONTACTS):
                raise ValueError("Invalid evidence source")
            rows, state = tuple(data["contacts"]), "ready"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError):
            state = "unavailable"
        self._rows, self._state, self._until = rows, state, time.monotonic() + 30
        return rows, state

    def for_job(self, job: JobPosting) -> ContactResults:
        with self._lock:
            rows, state = self._read()
        if state != "ready":
            return ContactResults(state=state)
        found = []
        now = datetime.now(timezone.utc)
        for row in rows:
            try:
                result = reviewed_card(row, job, now)
            except (ValueError, TypeError, OverflowError):
                continue
            if result is not None:
                found.append(result)
        cards = {}
        for rank, card in sorted(found, key=lambda pair: (
            pair[0], -datetime.fromisoformat(pair[1].last_verified_at).timestamp(),
            not pair[1].role_title, pair[1].display_name.casefold(), pair[1].profile_url,
        )):
            cards.setdefault(card.profile_url, card)
        return ContactResults(tuple(cards.values()), "ready" if cards else "empty")
