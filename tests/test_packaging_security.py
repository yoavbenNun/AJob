"""Prevent accidental inclusion of personal files in source and Docker context."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_sensitive_paths_are_ignored_without_ignoring_documentation():
    paths = [".env", ".env.local", "config/private.key", "config/service-account.json",
             "data/resume.pdf", "data/backup.sqlite", "config/database_url.cred", "debug.log"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--", *paths],
                            cwd=ROOT, text=True, capture_output=True, check=True)
    assert set(result.stdout.splitlines()) == set(paths)
    result = subprocess.run(["git", "check-ignore", "--no-index", "--", "README.md", "docs/images/dashboard.png"],
                            cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1


def test_docker_config_is_allowlisted():
    dockerfile = (ROOT / "Dockerfile").read_text()
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "COPY config/companies.yaml ./config/companies.yaml" in dockerfile
    assert "COPY config ./config" not in dockerfile
    assert "config/*" in ignore
    assert "!config/companies.yaml" in ignore
    assert "*.cred" in ignore
