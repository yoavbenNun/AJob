"""Load and validate company configuration from YAML."""

from pathlib import Path
from typing import Any

import yaml

from src.models import Company


def load_companies(config_path: str | Path) -> list[Company]:
    """Return configured companies, raising a clear error for invalid entries."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as config_file:
        raw_config: Any = yaml.safe_load(config_file) or {}

    entries = raw_config.get("companies") if isinstance(raw_config, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Configuration must contain a 'companies' list.")

    companies: list[Company] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Company entry {index} must be a mapping.")

        name = entry.get("name")
        careers_url = entry.get("careers_url")
        enabled = entry.get("enabled", True)
        job_url_patterns = entry.get("job_url_patterns", ["/job/", "/jobs/"])
        allowed_job_hosts = entry.get("allowed_job_hosts", [])
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Company entry {index} needs a non-empty name.")
        if not isinstance(careers_url, str) or not careers_url.startswith(("http://", "https://")):
            raise ValueError(f"Company entry {index} needs an HTTP(S) careers_url.")
        if not isinstance(enabled, bool):
            raise ValueError(f"Company entry {index} has a non-boolean enabled value.")
        if not isinstance(job_url_patterns, list) or not job_url_patterns or not all(
            isinstance(pattern, str) and pattern for pattern in job_url_patterns
        ):
            raise ValueError(
                f"Company entry {index} needs a non-empty list of job_url_patterns."
            )
        if not isinstance(allowed_job_hosts, list) or not all(
            isinstance(host, str) and host.strip() for host in allowed_job_hosts
        ):
            raise ValueError(
                f"Company entry {index} needs a list of non-empty allowed_job_hosts."
            )

        companies.append(
            Company(
                name=name.strip(),
                careers_url=careers_url,
                enabled=enabled,
                job_url_patterns=tuple(job_url_patterns),
                allowed_job_hosts=tuple(
                    host.strip().casefold() for host in allowed_job_hosts
                ),
            )
        )

    return companies
