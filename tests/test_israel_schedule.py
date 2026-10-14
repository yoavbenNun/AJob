from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from src.scheduling.israel_schedule import delivery_slot_for_schedule, should_run_scheduled_scan


@pytest.mark.parametrize(
    ("cron_expression", "expected"),
    [
        ("17 5 * * *", True),
        ("17 6 * * *", False),
        ("17 8 * * *", True),
        ("17 9 * * *", False),
        ("17 13 * * *", True),
        ("17 14 * * *", False),
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
        ("17 5 * * *", False),
        ("17 6 * * *", True),
        ("17 8 * * *", False),
        ("17 9 * * *", True),
        ("17 13 * * *", False),
        ("17 14 * * *", True),
    ],
)
def test_winter_schedule_selects_utc_plus_two_slots(
    cron_expression: str, expected: bool
) -> None:
    winter = datetime(2026, 12, 1, 20, 35, tzinfo=timezone.utc)
    assert should_run_scheduled_scan(cron_expression, winter) is expected


def test_runner_delay_does_not_change_intended_slot() -> None:
    delayed_start = datetime(2026, 10, 13, 19, 35, tzinfo=timezone.utc)
    assert should_run_scheduled_scan("17 13 * * *", delayed_start) is True
    assert delivery_slot_for_schedule("17 13 * * *", delayed_start) == (
        "2026-10-13T16:17:00+03:00"
    )


def test_retry_maps_to_same_canonical_delivery_slot() -> None:
    reference = datetime(2026, 7, 1, 14, 55, tzinfo=timezone.utc)
    assert delivery_slot_for_schedule("17 13 * * *", reference) == (
        delivery_slot_for_schedule("47 13 * * *", reference)
    )


def test_delay_across_utc_midnight_uses_previous_scheduled_date() -> None:
    delayed_start = datetime(2026, 10, 26, 1, 15, tzinfo=timezone.utc)
    assert should_run_scheduled_scan("17 14 * * *", delayed_start) is True
    assert should_run_scheduled_scan("17 13 * * *", delayed_start) is False


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
    assert should_run_scheduled_scan("17 5 * * *", reference) is summer_slot
    assert should_run_scheduled_scan("17 6 * * *", reference) is winter_slot


def test_invalid_or_combined_cron_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported schedule"):
        should_run_scheduled_scan("0 5,6,8,9,13,14 * * *")


def test_invalid_schedule_minute_is_rejected() -> None:
    with pytest.raises(ValueError, match="minute"):
        should_run_scheduled_scan("60 5 * * *")


def test_cloud_workflow_never_succeeds_without_email_configuration() -> None:
    workflow = Path(".github/workflows/scheduled-scan.yml").read_text(encoding="utf-8")
    assert "for name in SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD SMTP_FROM SMTP_TO" in workflow
    assert "Scan career pages without email" not in workflow
    assert "python -m src.main scan --email-summary" in workflow
    assert 'cron: "0 ' not in workflow
    expressions = re.findall(r'- cron: "([^"]+)"', workflow)
    assert expressions == [
        "17 5 * * *", "17 6 * * *", "17 8 * * *",
        "17 9 * * *", "17 13 * * *", "17 14 * * *",
        "47 5 * * *", "47 6 * * *", "47 8 * * *",
        "47 9 * * *", "47 13 * * *", "47 14 * * *",
    ]
    assert '--delivery-slot "${{ steps.schedule_gate.outputs.delivery_slot }}"' in workflow
