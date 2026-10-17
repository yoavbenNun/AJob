from dataclasses import dataclass

import pytest
import ssl

from src.notifications.email_notifier import (
    EmailJobRecommendation,
    compose_scan_summary,
    load_smtp_settings,
    send_scan_summary,
)
from src.models import JobPosting


ENV = {
    "SMTP_HOST": "smtp.example.test",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "agent@example.test",
    "SMTP_PASSWORD": "not-a-real-secret",
    "SMTP_FROM": "agent@example.test",
    "SMTP_TO": "owner@example.test",
}


@dataclass
class Summary:
    companies_scanned: int = 2
    jobs_found: int = 6
    jobs_saved: int = 3
    failures: int = 1
    new_jobs: tuple[JobPosting, ...] = ()


def test_load_smtp_settings_requires_complete_environment() -> None:
    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        load_smtp_settings({key: value for key, value in ENV.items() if key != "SMTP_PASSWORD"})


def test_load_smtp_settings_validates_port_and_tls() -> None:
    with pytest.raises(ValueError, match="integer"):
        load_smtp_settings({**ENV, "SMTP_PORT": "not-a-port"})
    with pytest.raises(ValueError, match="true or false"):
        load_smtp_settings({**ENV, "SMTP_USE_TLS": "sometimes"})


def test_compose_scan_summary_has_expected_headers_and_counts() -> None:
    settings = load_smtp_settings(ENV)
    message = compose_scan_summary(settings, Summary())

    assert message["From"] == ENV["SMTP_FROM"]
    assert message["To"] == ENV["SMTP_TO"]
    assert message["Subject"] == "AJob: 0 new matching job(s)"
    assert "Relevant roles saved: 3" in message.get_content()
    assert ENV["SMTP_PASSWORD"] not in message.as_string()


def test_send_scan_summary_uses_tls_authentication_and_mocked_smtp() -> None:
    calls: list[object] = []

    class FakeSmtp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def starttls(self, *, context):
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname is True
            calls.append("starttls")

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("message", message["Subject"]))

    def smtp_factory(host, port, timeout):
        calls.append(("connect", host, port, timeout))
        return FakeSmtp()

    send_scan_summary(load_smtp_settings(ENV), Summary(), smtp_factory=smtp_factory)

    assert calls == [
        ("connect", "smtp.example.test", 587, 30),
        "starttls",
        ("login", "agent@example.test", "not-a-real-secret"),
        ("message", "AJob: 0 new matching job(s)"),
    ]


def test_plaintext_smtp_is_only_available_for_local_development() -> None:
    with pytest.raises(ValueError, match="local development"):
        load_smtp_settings({**ENV, "SMTP_USE_TLS": "false"})
    assert not load_smtp_settings({**ENV, "SMTP_HOST": "127.0.0.1", "SMTP_USE_TLS": "false"}).use_tls


def test_certificate_failure_aborts_before_smtp_login() -> None:
    class UntrustedSmtp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def starttls(self, *, context):
            raise ssl.SSLCertVerificationError("Untrusted test certificate")
        def login(self, *args):
            pytest.fail("Credentials must not be sent after TLS failure")
        def send_message(self, *args):
            pytest.fail("Mail must not be sent after TLS failure")
    with pytest.raises(ssl.SSLCertVerificationError):
        send_scan_summary(load_smtp_settings(ENV), Summary(), smtp_factory=lambda *a, **kw: UntrustedSmtp())


def test_compose_scan_summary_lists_new_jobs() -> None:
    settings = load_smtp_settings(ENV)
    summary = Summary(
        new_jobs=(
            JobPosting(
                "Software Intern", "Example", "Tel Aviv, Israel",
                "https://example.test/jobs/1",
            ),
        )
    )

    message = compose_scan_summary(settings, summary)
    content = message.get_content()

    assert message["Subject"] == "AJob: 1 new matching job(s)"
    assert "Software Intern at Example" in content
    assert "https://example.test/jobs/1" in content


def test_ranked_email_counts_only_eligible_recommendations() -> None:
    settings = load_smtp_settings(ENV)
    recommendation = EmailJobRecommendation(
        title="Python Intern",
        company="Example",
        location="Tel Aviv, Israel",
        job_url="https://example.test/jobs/1",
        score=82,
        eligible=True,
        reason="Matches Python. Commute is within policy.",
    )

    message = compose_scan_summary(settings, Summary(), (recommendation,))

    assert message["Subject"] == "AJob: 1 new matching job(s)"
    assert "82/100 - Python Intern" in message.get_content()


def test_ranked_email_does_not_fall_back_to_unfiltered_jobs() -> None:
    settings = load_smtp_settings(ENV)
    summary = Summary(
        new_jobs=(
            JobPosting("Foreign Intern", "Example", "Warsaw", "https://example.test/1"),
        )
    )

    message = compose_scan_summary(settings, summary, ())

    assert message["Subject"] == "AJob: 0 new matching job(s)"
    assert "No new jobs passed both resume and commute" in message.get_content()
    assert "Foreign Intern" not in message.get_content()
