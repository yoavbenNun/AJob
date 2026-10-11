from pathlib import Path

import pytest

from src.config_loader import load_companies


def test_load_companies_reads_defaults_and_enabled_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "companies.yaml"
    config_path.write_text(
        """companies:
  - name: Example
    careers_url: https://example.com/jobs
  - name: Disabled
    careers_url: https://example.org/careers
    enabled: false
""",
        encoding="utf-8",
    )

    companies = load_companies(config_path)

    assert [company.name for company in companies] == ["Example", "Disabled"]
    assert companies[0].enabled is True
    assert companies[1].enabled is False
    assert companies[0].job_url_patterns == ("/job/", "/jobs/")


def test_load_companies_rejects_invalid_url(tmp_path: Path) -> None:
    config_path = tmp_path / "companies.yaml"
    config_path.write_text(
        "companies:\n  - name: Example\n    careers_url: not-a-url\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HTTP"):
        load_companies(config_path)


def test_load_companies_rejects_empty_job_patterns(tmp_path: Path) -> None:
    config_path = tmp_path / "companies.yaml"
    config_path.write_text(
        "companies:\n  - name: Example\n    careers_url: https://example.com/jobs\n"
        "    job_url_patterns: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-empty"):
        load_companies(config_path)
