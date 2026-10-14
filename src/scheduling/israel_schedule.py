"""Validate scheduled UTC scan slots against Israel local time."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


ISRAEL_TIMEZONE = ZoneInfo("Asia/Jerusalem")
TARGET_LOCAL_HOURS = frozenset({8, 11, 16})
SCHEDULE_MINUTES = frozenset({17, 47})
CRON_PATTERN = re.compile(r"^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$")


def should_run_scheduled_scan(
    cron_expression: str,
    reference_utc: datetime | None = None,
) -> bool:
    """Return whether a distinct UTC cron maps to a target Israel hour.

    ``reference_utc`` supplies the calendar date used for the daylight-saving
    calculation. Its clock time is deliberately ignored so a delayed GitHub
    runner cannot cause a valid scheduled scan to be skipped.
    """
    match = CRON_PATTERN.fullmatch(cron_expression.strip())
    if match is None:
        raise ValueError(f"Unsupported schedule expression: {cron_expression!r}")

    utc_minute = int(match.group(1))
    utc_hour = int(match.group(2))
    if not 0 <= utc_minute <= 59:
        raise ValueError(f"UTC schedule minute must be between 0 and 59: {utc_minute}")
    if not 0 <= utc_hour <= 23:
        raise ValueError(f"UTC schedule hour must be between 0 and 23: {utc_hour}")

    reference = reference_utc or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("reference_utc must be timezone-aware")
    reference = reference.astimezone(timezone.utc)
    intended_utc = datetime.combine(
        reference.date(), time(hour=utc_hour, minute=utc_minute), tzinfo=timezone.utc
    )
    if intended_utc > reference:
        intended_utc -= timedelta(days=1)
    intended_local = intended_utc.astimezone(ISRAEL_TIMEZONE)
    return (
        intended_local.hour in TARGET_LOCAL_HOURS
        and intended_local.minute in SCHEDULE_MINUTES
    )


def delivery_slot_for_schedule(
    cron_expression: str,
    reference_utc: datetime | None = None,
) -> str | None:
    """Return the canonical Israel delivery slot for a primary or retry cron."""
    match = CRON_PATTERN.fullmatch(cron_expression.strip())
    if match is None:
        raise ValueError(f"Unsupported schedule expression: {cron_expression!r}")
    utc_minute = int(match.group(1))
    utc_hour = int(match.group(2))
    reference = (reference_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    intended_utc = datetime.combine(
        reference.date(), time(hour=utc_hour, minute=utc_minute), tzinfo=timezone.utc
    )
    if intended_utc > reference:
        intended_utc -= timedelta(days=1)
    intended_local = intended_utc.astimezone(ISRAEL_TIMEZONE)
    if not should_run_scheduled_scan(cron_expression, reference):
        return None
    canonical = intended_local.replace(minute=17, second=0, microsecond=0)
    return canonical.isoformat()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether a UTC cron slot maps to an Israel scan hour"
    )
    parser.add_argument("cron_expression")
    args = parser.parse_args(argv)
    try:
        should_run = should_run_scheduled_scan(args.cron_expression)
    except ValueError as error:
        parser.error(str(error))
    slot = delivery_slot_for_schedule(args.cron_expression)
    print(slot if should_run else "false")
    return 0 if should_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
