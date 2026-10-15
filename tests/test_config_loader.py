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


def test_default_company_sources_use_safe_enabled_and_disabled_entries() -> None:
    loaded = load_companies("config/companies.yaml")
    companies = {company.name: company for company in loaded}

    assert len(companies) == len(loaded)

    assert companies["Amazon"].enabled is True
    assert companies["Amazon"].careers_url.endswith("country=ISR")
    assert companies["Amazon"].job_url_patterns == ("/en/jobs/",)
    assert companies["Apple"].enabled is True
    assert companies["Apple"].careers_url.endswith("location=israel-ISR")
    assert companies["Apple"].job_url_patterns == ("/en-il/details/",)
    assert companies["NVIDIA"].enabled is True
    assert companies["Intel"].enabled is True
    for name in ("KLA", "F5", "Motorola Solutions", "Red Hat", "Clarivate", "Qualcomm"):
        assert companies[name].enabled is True
        assert "myworkdayjobs.com" in companies[name].careers_url
    assert companies["monday.com"].enabled is True
    assert companies["Wix"].enabled is False
