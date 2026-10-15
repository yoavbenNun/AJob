from types import SimpleNamespace

import pytest

from src.notifications.recipient_settings import (
    update_cloud_email_recipient,
    validate_notification_email,
)


def test_notification_email_validation_normalizes_and_rejects_invalid_values() -> None:
    assert validate_notification_email(" owner@example.com ") == "owner@example.com"
    with pytest.raises(ValueError, match="valid email"):
        validate_notification_email("not-an-email")


def test_cloud_recipient_update_uses_stdin_not_process_arguments(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.notifications.recipient_settings.shutil.which", lambda name: "gh")
    monkeypatch.setattr("src.notifications.recipient_settings.subprocess.run", fake_run)

    update_cloud_email_recipient("owner@example.com")

    assert captured["command"] == ["gh", "secret", "set", "SMTP_TO", "--repo", "yoavbenNun/AJob"]
    assert captured["input"] == "owner@example.com"
    assert "owner@example.com" not in " ".join(captured["command"])
