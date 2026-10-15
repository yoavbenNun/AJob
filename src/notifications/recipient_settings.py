"""Human-approved updates to the cloud email recipient secret."""

from __future__ import annotations

import re
import shutil
import subprocess


GITHUB_REPOSITORY = "yoavbenNun/AJob"


def validate_notification_email(email: str) -> str:
    """Return a normalized recipient or reject malformed input."""
    recipient = email.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient):
        raise ValueError("Enter a valid email address.")
    return recipient


def update_cloud_email_recipient(email: str) -> None:
    """Replace SMTP_TO through the authenticated GitHub CLI without logging it."""
    recipient = validate_notification_email(email)
    gh_path = shutil.which("gh")
    if gh_path is None:
        raise RuntimeError("GitHub CLI is unavailable on this computer.")
    try:
        result = subprocess.run(
            [gh_path, "secret", "set", "SMTP_TO", "--repo", GITHUB_REPOSITORY],
            input=recipient,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("GitHub recipient update failed.") from error
    if result.returncode != 0:
        raise RuntimeError("GitHub rejected the recipient update.")
