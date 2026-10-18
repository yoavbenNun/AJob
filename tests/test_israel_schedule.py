from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from src.scheduling.israel_schedule import (
    delivery_slot_for_schedule,
    should_run_scheduled_scan,
    validate_dispatched_slot,
)


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
    expected_slot = delivery_slot_for_schedule("17 13 * * *", reference)
    for minute in (27, 37, 47, 57):
        assert should_run_scheduled_scan(f"{minute} 13 * * *", reference)
        assert delivery_slot_for_schedule(f"{minute} 13 * * *", reference) == expected_slot


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


@pytest.mark.parametrize("month,offset,utc_hour", [(7, "+03:00", 5), (12, "+02:00", 6)])
def test_external_dispatch_shares_cron_delivery_identity(month, offset, utc_hour) -> None:
    reference = datetime(2026, month, 1, 12, tzinfo=timezone.utc)
    value = f"2026-{month:02d}-01T08:17:00{offset}"
    assert validate_dispatched_slot(value, reference) == delivery_slot_for_schedule(
        f"17 {utc_hour} * * *", reference
    )


@pytest.mark.parametrize("value", [
    "2026-07-01T08:17:00",             # no timezone
    "2026-07-01T08:17:00+02:00",       # wrong DST offset
    "2026-07-01T05:17:00+00:00",       # same instant, noncanonical key
    "2026-07-01T08:27:00+03:00",       # retries must use primary key
    "2026-07-01T09:17:00+03:00",       # unconfigured hour
    "2026-07-01T08:17:01+03:00",       # extra seconds
    "2026-07-01T16:17:00+03:00",       # future
    "2026-06-30T08:17:00+03:00",       # expired
    "2026-07-01T08:17:00+03:00\nrun_scan=true",
])
def test_external_dispatch_rejects_invalid_slots(value) -> None:
    with pytest.raises(ValueError):
        validate_dispatched_slot(value, datetime(2026, 7, 1, 12, tzinfo=timezone.utc))


def test_external_dispatch_slot_passed_as_environment_not_shell_expression() -> None:
    workflow = Path(".github/workflows/scheduled-scan.yml").read_text(encoding="utf-8")
    assert "DISPATCH_DELIVERY_SLOT: ${{ inputs.delivery_slot }}" in workflow
    assert '--delivery-slot "$DISPATCH_DELIVERY_SLOT"' in workflow


def test_cloud_workflow_never_succeeds_without_email_configuration() -> None:
    workflow = Path(".github/workflows/scheduled-scan.yml").read_text(encoding="utf-8")
    assert "Require email configuration" not in workflow  # mail configuration cannot prevent discovery
    assert "Scan career pages without email" not in workflow
    assert "python -m src.main scheduled-tick" in workflow
    assert 'cron: "0 ' not in workflow
    expressions = re.findall(r'- cron: "([^"]+)"', workflow)
    assert len(expressions) == 12
    assert {expression.split()[0] for expression in expressions} == {
        "17", "47"
    }
    assert {expression.split()[1] for expression in expressions} == {
        "5", "6", "8", "9", "13", "14"
    }
    assert "timeout-minutes: 75" in workflow
    assert "queue: max" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "python -m src.main scan-report" in workflow
