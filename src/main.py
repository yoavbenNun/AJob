"""Command-line entry point for the AI Job Search Agent."""

import argparse
import json
from pathlib import Path
from collections.abc import Sequence
from dataclasses import asdict

from src.config_loader import load_companies
from src.scrapers.careers_scraper import preview_companies, scan_companies
from src.resume.resume_parser import parse_resume
from src.storage.job_repository import JobRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local AI job search assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="Scan enabled career pages")
    scan_parser.add_argument("--config", type=Path, default=Path("config/companies.yaml"))
    scan_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    scan_parser.add_argument("--dry-run", action="store_true", help="Print targets only")
    scan_parser.add_argument("--headed", action="store_true", help="Show the browser window")
    scan_parser.add_argument(
        "--timeout-ms", type=int, default=30_000, help="Per-page navigation timeout"
    )
    scan_parser.add_argument(
        "--max-pages", type=int, default=2, help="Maximum listing pages per company"
    )
    resume_parser = subparsers.add_parser(
        "parse-resume", help="Parse a local resume without sending it anywhere"
    )
    resume_parser.add_argument("resume", type=Path, help="TXT, Markdown, PDF, or DOCX file")
    resume_parser.add_argument(
        "--json", action="store_true", help="Print the complete parsed profile as JSON"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        companies = load_companies(args.config)
        if args.dry_run:
            preview_companies(companies)
            return 0

        repository = JobRepository(args.database)
        repository.initialize()
        summary = scan_companies(
            companies,
            repository,
            headless=not args.headed,
            timeout_ms=args.timeout_ms,
            max_pages=max(1, args.max_pages),
        )
        print(
            f"Scan complete: {summary.companies_scanned} companies, "
            f"{summary.jobs_found} candidates, {summary.jobs_relevant} relevant, "
            f"{summary.failures} failures."
        )
        return 1 if summary.failures else 0
    if args.command == "parse-resume":
        profile = parse_resume(args.resume)
        if args.json:
            print(json.dumps(asdict(profile), indent=2, ensure_ascii=False))
        else:
            print(f"Name: {profile.name or 'Not found'}")
            print(f"Email: {profile.email or 'Not found'}")
            print(f"Phone: {profile.phone or 'Not found'}")
            print(f"Skills ({len(profile.skills)}): {', '.join(profile.skills) or 'None found'}")
            print(f"Education section: {'yes' if profile.education else 'no'}")
            print(f"Experience section: {'yes' if profile.experience else 'no'}")
            print(f"Projects section: {'yes' if profile.projects else 'no'}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
