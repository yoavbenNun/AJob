"""SMTP scan-summary notifications configured exclusively through environment variables."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class EmailJobRecommendation:
    title: str
    company: str
    location: str
    job_url: str
    score: int
    eligible: bool
    reason: str
    eligibility_status: str = 'student_eligible'
    eligibility_reason: str = ''


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """Validated SMTP configuration; never log this object's password."""

    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool = True


def load_smtp_settings(environ: Mapping[str, str] | None = None) -> SmtpSettings:
    """Load required SMTP settings from the process environment.

    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, and SMTP_TO
    are required. SMTP_USE_TLS defaults to true and accepts true or false.
    """
    values = os.environ if environ is None else environ
    required = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_TO",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError("Missing required SMTP environment variable(s): " + ", ".join(missing))

    try:
        port = int(values["SMTP_PORT"])
    except ValueError as error:
        raise ValueError("SMTP_PORT must be an integer.") from error
    if not 1 <= port <= 65_535:
        raise ValueError("SMTP_PORT must be between 1 and 65535.")

    tls_value = values.get("SMTP_USE_TLS", "true").strip().casefold()
    if tls_value not in {"true", "false"}:
        raise ValueError("SMTP_USE_TLS must be true or false.")
    if tls_value == "false" and values["SMTP_HOST"].strip().casefold() not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("SMTP_USE_TLS=false is restricted to a local development server.")
    return SmtpSettings(
        host=values["SMTP_HOST"].strip(),
        port=port,
        username=values["SMTP_USERNAME"].strip(),
        password=values["SMTP_PASSWORD"],
        sender=values["SMTP_FROM"].strip(),
        recipient=values["SMTP_TO"].strip(),
        use_tls=tls_value == "true",
    )


def compose_scan_summary(
    settings: SmtpSettings,
    summary,
    recommendations: tuple[EmailJobRecommendation, ...] | None = None,
) -> EmailMessage:
    """Build a plain-text scan summary without including application data."""
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = settings.recipient
    new_jobs = tuple(getattr(summary, "new_jobs", ()))
    if recommendations is not None:
        recommendations = tuple(item for item in recommendations
                                if item.eligible and item.eligibility_status != 'ineligible')
    notified_count = len(new_jobs) if recommendations is None else sum(
        item.eligibility_status == 'student_eligible' for item in recommendations)
    message["Subject"] = f"AJob: {notified_count} new matching job(s)"
    possible_count = sum(item.eligibility_status == 'potentially_eligible' for item in (recommendations or ()))
    if possible_count:
        message.replace_header('Subject', f'{message["Subject"]}; {possible_count} possible match(es)')
    if recommendations is not None:
        groups = []
        for status, heading in (('student_eligible', 'Student eligible'),
                                ('potentially_eligible', 'Possible Matches — confirm eligibility')):
            entries = [
                f"- {item.score}/100 - {item.title} at {item.company}\n"
                f"  {item.location} | {heading}\n"
                f"  {item.eligibility_reason}\n  {item.reason}\n  {item.job_url}"
                for item in sorted(recommendations, key=lambda item: item.score, reverse=True)
                if item.eligibility_status == status
            ]
            if entries:
                groups.append(heading + '\n' + '\n'.join(entries))
        details = '\n\n'.join(groups) or "No new jobs passed both resume and commute eligibility checks."
    else:
        details = "\n".join(
            f"- {job.title} at {job.company}\n  {job.location}\n  {job.job_url}"
            for job in new_jobs
        ) or "No new matching jobs were discovered."
    eligibility_summary = (f"Possible matches requiring review: {possible_count}\n"
                           if recommendations is not None else
                           "Eligibility not assessed; discovered roles require review.\n")
    message.set_content(
        f"AJob scan {getattr(summary, 'status', 'complete')}.\n\n"
        f"Companies scanned: {summary.companies_scanned}\n"
        f"Candidates found: {summary.jobs_found}\n"
        f"Relevant roles saved: {summary.jobs_saved}\n"
        f"New relevant roles: {len(new_jobs)}\n"
        f"{eligibility_summary}"
        f"Company failures: {summary.failures}\n"
        f"Failure reason: {getattr(summary, 'failure_reason', '') or 'none'}\n"
        f"\n{details}\n"
    )
    return message


def send_scan_summary(
    settings: SmtpSettings,
    summary,
    recommendations: tuple[EmailJobRecommendation, ...] | None = None,
    smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
) -> None:
    """Send a scan summary using explicit TLS when enabled."""
    message = compose_scan_summary(settings, summary, recommendations)
    with smtp_factory(settings.host, settings.port, timeout=30) as client:
        if settings.use_tls:
            client.starttls(context=ssl.create_default_context())
        if settings.username:
            client.login(settings.username, settings.password)
        client.send_message(message)


class DeliveryError(RuntimeError):
    def __init__(self, error, uncertain):
        super().__init__(type(error).__name__)
        self.uncertain = uncertain


def send_prepared_summary(settings, message, smtp_factory=smtplib.SMTP):
    """A disconnect after DATA starts may mean accepted mail; never auto-retry it."""
    started = accepted = False
    try:
        with smtp_factory(settings.host, settings.port, timeout=30) as client:
            if settings.use_tls:
                client.starttls(context=ssl.create_default_context())
            if settings.username:
                client.login(settings.username, settings.password)
            started = True
            refused = client.send_message(message)
            if refused:
                # A returned refusal dictionary means OTHER recipients may have
                # accepted DATA. Retrying the whole message could duplicate it.
                raise smtplib.SMTPException('Partial recipient acceptance')
            accepted = True
    except Exception as error:
        # QUIT failure does not undo a positive DATA acknowledgement.
        if accepted:
            return
        definite = isinstance(error, (smtplib.SMTPDataError,
            smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused))
        raise DeliveryError(error, started and not definite) from error
