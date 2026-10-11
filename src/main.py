"""Command-line entry point for the AI Job Search Agent."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from collections.abc import Sequence
from dataclasses import asdict

from src.config_loader import load_companies
from src.matching.resume_matcher import match_resume_to_job
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
    match_parser = subparsers.add_parser(
        "match", help="Rank stored jobs against a local resume"
    )
    match_parser.add_argument("resume", type=Path, help="TXT, Markdown, PDF, or DOCX file")
    match_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    match_parser.add_argument("--job-id", type=int, help="Match only one stored job")
    match_parser.add_argument(
        "--limit", type=int, default=10, help="Maximum matches shown in all-job mode"
    )
    match_parser.add_argument("--json", action="store_true", help="Print stable JSON output")
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
    if args.command == "match":
        return _run_match(args)
    return 1


def _run_match(args: argparse.Namespace) -> int:
    try:
        if args.limit < 1:
            raise ValueError("--limit must be at least 1")
        if not args.database.is_file():
            raise FileNotFoundError(f"Job database not found: {args.database}")

        profile = parse_resume(args.resume)
        repository = JobRepository(args.database)
        repository.initialize()
        if args.job_id is not None:
            job = repository.get_by_id(args.job_id)
            if job is None:
                raise ValueError(f"Job ID {args.job_id} was not found")
            if not job.is_relevant:
                raise ValueError(
                    f"Job ID {args.job_id} has not passed the current relevance filter"
                )
            jobs = [job]
        else:
            jobs = repository.list_relevant()

        ranked = sorted(
            ((job, match_resume_to_job(profile, job)) for job in jobs),
            key=lambda item: (-item[1].score, -(item[0].id or 0)),
        )
        if args.job_id is None:
            ranked = ranked[: args.limit]

        if args.json:
            payload = {
                "schema_version": 1,
                "matches": [
                    {
                        "job": {
                            "id": job.id,
                            "title": job.title,
                            "company": job.company,
                            "location": job.location,
                            "job_url": job.job_url,
                        },
                        "match": asdict(result),
                    }
                    for job, result in ranked
                ],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _print_matches(ranked)
        return 0
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Match failed: {error}", file=sys.stderr)
        return 1


def _print_matches(ranked) -> None:
    if not ranked:
        print("No stored jobs to match.")
        return
    for index, (job, result) in enumerate(ranked, start=1):
        print(f"{index}. {job.title} at {job.company} - {result.score}/100")
        print(f"   {job.location} | {job.job_url}")
        print(f"   Matched skills: {', '.join(result.matched_skills) or 'None'}")
        print(f"   Missing skills: {', '.join(result.missing_skills) or 'None identified'}")
        for strength in result.strengths:
            print(f"   Strength: {strength}")
        for gap in result.gaps:
            print(f"   Gap: {gap}")
        print("   Tailoring suggestions:")
        for suggestion in result.tailoring_suggestions:
            print(f"   - {suggestion}")


if __name__ == "__main__":
    raise SystemExit(main())
