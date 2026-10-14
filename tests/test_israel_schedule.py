from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scheduling.israel_schedule import should_run_scheduled_scan


@pytest.mark.parametrize(
    ("cron_expression", "expected"),
    [
        ("0 5 * * *", True),
        ("0 6 * * *", False),
        ("0 8 * * *", True),
        ("0 9 * * *", False),
        ("0 13 * * *", True),
        ("0 14 * * *", False),
    ],
)
def test_summer_schedule_selects_utc_plus_three_slots(
    cron_expression: str, expected: bool
) -> None:
    summer = datetime(2026, 7, 1, 20, 35, tzinfo=timezone.utc)
    assert should_run_scheduled_scan(cron_expression, summer) is expected


@pytest.mark.parametrize(
    ("cron_expression", "expected"),
    [
        ("0 5 * * *", False),
        ("0 6 * * *", True),
        ("0 8 * * *", False),
        ("0 9 * * *", True),
        ("0 13 * * *", False),
        ("0 14 * * *", True),
    ],
)
def test_winter_schedule_selects_utc_plus_two_slots(
    cron_expression: str, expected: bool
) -> None:
    winter = datetime(2026, 12, 1, 20, 35, tzinfo=timezone.utc)
    assert should_run_scheduled_scan(cron_expression, winter) is expected


def test_runner_delay_does_not_change_intended_slot() -> None:
    delayed_start = datetime(2026, 10, 13, 19, 35, tzinfo=timezone.utc)
    assert should_run_scheduled_scan("0 13 * * *", delayed_start) is True


def test_delay_across_utc_midnight_uses_previous_scheduled_date() -> None:
    delayed_start = datetime(2026, 10, 26, 1, 15, tzinfo=timezone.utc)
    assert should_run_scheduled_scan("0 14 * * *", delayed_start) is True
    assert should_run_scheduled_scan("0 13 * * *", delayed_start) is False


@pytest.mark.parametrize(
    ("reference", "summer_slot", "winter_slot"),
    [
        (datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc), True, False),
        (datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc), False, True),
    ],
)
def test_israel_dst_transition_dates(
    reference: datetime, summer_slot: bool, winter_slot: bool
) -> None:
    assert should_run_scheduled_scan("0 5 * * *", reference) is summer_slot
    assert should_run_scheduled_scan("0 6 * * *", reference) is winter_slot


def test_invalid_or_combined_cron_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported schedule"):
        should_run_scheduled_scan("0 5,6,8,9,13,14 * * *")


def test_cloud_workflow_never_succeeds_without_email_configuration() -> None:
    workflow = Path(".github/workflows/scheduled-scan.yml").read_text(encoding="utf-8")
    assert "for name in SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD SMTP_FROM SMTP_TO" in workflow
    assert "Scan career pages without email" not in workflow
    assert "python -m src.main scan --email-summary" in workflow
