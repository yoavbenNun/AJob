"""Domain models for companies and discovered job postings."""

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


JOB_STATUSES = ("new", "reviewed", "applied", "ignored", "rejected")


@dataclass(frozen=True, slots=True)
class Company:
    """A configured company career page."""

    name: str
    careers_url: str
    enabled: bool = True
    job_url_patterns: tuple[str, ...] = ("/job/", "/jobs/")
    allowed_job_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed_job_hosts:
            host = urlsplit(self.careers_url).hostname
            object.__setattr__(self, "allowed_job_hosts", (host,) if host else ())


@dataclass(slots=True)
class JobPosting:
    """A normalized job discovered on a career page."""

    title: str
    company: str
    location: str
    job_url: str
    description: str = ""
    requirements: str = ""
    source: str = ""
    discovered_at: datetime | None = None
    id: int | None = None
    is_relevant: bool = True
    status: str = "new"

    def __post_init__(self) -> None:
        if self.discovered_at is None:
            self.discovered_at = datetime.now(timezone.utc)
        if self.status not in JOB_STATUSES:
            allowed = ", ".join(JOB_STATUSES)
            raise ValueError(f"Invalid job status {self.status!r}; expected one of: {allowed}")


@dataclass(frozen=True, slots=True)
class ResumeProfile:
    """Normalized, local representation of a parsed resume."""

    source_path: str
    name: str
    location: str
    email: str
    phone: str
    skills: tuple[str, ...]
    education: str
    experience: str
    projects: str
    summary: str
    raw_text: str
