"""Command-line entry point for the AI Job Search Agent."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from collections.abc import Sequence
from dataclasses import asdict

from src.config_loader import load_companies
from src.location.commute import (
    OpenStreetMapDrivingProvider,
    evaluate_commute,
    resolve_job_destination,
)
from src.matching.resume_matcher import match_resume_to_job
from src.notifications.email_notifier import (
    EmailJobRecommendation,
    load_smtp_settings,
    send_scan_summary,
)
from src.models import JOB_STATUSES
from src.scrapers.careers_scraper import preview_companies, scan_companies
from src.resume.resume_parser import parse_resume
from src.resume.safe_profile import build_safe_profile, load_safe_profile_json
from src.resume.tailored_resume import create_tailored_resume
from src.storage.repository_factory import create_job_repository

try:
    from psycopg import Error as PostgresError
except ImportError:
    class PostgresError(Exception):
        """Fallback type when optional PostgreSQL support is not installed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local AI job search assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="Scan enabled career pages")
    scan_parser.add_argument("--config", type=Path, default=Path("config/companies.yaml"))
    scan_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    scan_parser.add_argument("--dry-run", action="store_true", help="Print targets only")
    scan_parser.add_argument(
        "--company", action="append",
        help="Scan only this configured company (repeat for multiple companies)",
    )
    scan_parser.add_argument("--headed", action="store_true", help="Show the browser window")
    scan_parser.add_argument(
        "--timeout-ms", type=int, default=30_000, help="Per-page navigation timeout"
    )
    scan_parser.add_argument(
        "--max-pages", type=int, default=2, help="Maximum listing pages per company"
    )
    scan_parser.add_argument(
        "--email-summary",
        action="store_true",
        help="Send a scan summary using SMTP_* environment variables",
    )
    resume_parser = subparsers.add_parser(
        "parse-resume", help="Parse a local resume without sending it anywhere"
    )
    resume_parser.add_argument("resume", type=Path, help="TXT, Markdown, PDF, or DOCX file")
    resume_parser.add_argument(
        "--json", action="store_true", help="Print the complete parsed profile as JSON"
    )
    profile_parser = subparsers.add_parser(
        "export-safe-profile",
        help="Export privacy-reduced JSON for cloud matching (no contact details or prose)",
    )
    profile_parser.add_argument("resume", type=Path)
    match_parser = subparsers.add_parser(
        "match", help="Rank stored jobs against a local resume"
    )
    match_parser.add_argument("resume", type=Path, help="TXT, Markdown, PDF, or DOCX file")
    match_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    match_parser.add_argument("--job-id", type=int, help="Match only one stored job")
    match_parser.add_argument(
        "--origin",
        help="Override the city/country extracted from the resume header",
    )
    match_parser.add_argument(
        "--skip-commute-check",
        action="store_true",
        help="Disable driving eligibility (results require manual location review)",
    )
    match_parser.add_argument(
        "--limit", type=int, default=10, help="Maximum matches shown in all-job mode"
    )
    match_parser.add_argument("--json", action="store_true", help="Print stable JSON output")
    list_parser = subparsers.add_parser("list", help="List stored jobs and their lifecycle status")
    list_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    list_parser.add_argument(
        "--status", choices=JOB_STATUSES, default="new",
        help="Lifecycle status to show (default: new)",
    )
    list_parser.add_argument("--json", action="store_true", help="Print stable JSON output")
    status_parser = subparsers.add_parser("status", help="Set a stored job's lifecycle status")
    status_parser.add_argument("job_id", type=int, help="Stored job ID")
    status_parser.add_argument("status", choices=JOB_STATUSES, help="New lifecycle status")
    status_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    tailor_parser = subparsers.add_parser(
        "tailor-resume", help="Create a factual DOCX for one job after human approval"
    )
    tailor_parser.add_argument("resume", type=Path)
    tailor_parser.add_argument("job_id", type=int)
    tailor_parser.add_argument("output", type=Path)
    tailor_parser.add_argument("--database", type=Path, default=Path("data/jobs.db"))
    tailor_parser.add_argument(
        "--approve",
        action="store_true",
        help="Confirm that you reviewed the target and approve file generation",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        companies = load_companies(args.config)
        if args.company:
            requested = {name.casefold() for name in args.company}
            companies = [company for company in companies if company.name.casefold() in requested]
            found = {company.name.casefold() for company in companies}
            missing = requested - found
            if missing:
                print(
                    "Scan failed: unknown configured company: " + ", ".join(sorted(missing)),
                    file=sys.stderr,
                )
                return 1
        if args.dry_run:
            preview_companies(companies)
            return 0

        try:
            repository = create_job_repository(args.database)
            repository.initialize()
            summary = scan_companies(
                companies,
                repository,
                headless=not args.headed,
                timeout_ms=args.timeout_ms,
                max_pages=max(1, args.max_pages),
            )
        except (PostgresError, RuntimeError, sqlite3.Error) as error:
            print(f"Scan failed: storage unavailable ({type(error).__name__}).", file=sys.stderr)
            return 1
        print(
            f"Scan complete: {summary.companies_scanned} companies, "
            f"{summary.jobs_found} candidates, {summary.jobs_relevant} relevant, "
            f"{summary.failures} failures."
        )
        if args.email_summary:
            try:
                recommendations = _cloud_recommendations(summary)
                send_scan_summary(load_smtp_settings(), summary, recommendations)
                print("Email summary sent.")
            except (OSError, ValueError) as error:
                print(f"Email summary failed: {error}", file=sys.stderr)
                return 1
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
    if args.command == "export-safe-profile":
        try:
            print(build_safe_profile(parse_resume(args.resume)).to_json())
            return 0
        except (FileNotFoundError, OSError, ValueError) as error:
            print(f"Safe profile export failed: {error}", file=sys.stderr)
            return 1
    if args.command == "match":
        return _run_match(args)
    if args.command == "list":
        return _run_list(args)
    if args.command == "status":
        return _run_status(args)
    if args.command == "tailor-resume":
        return _run_tailor_resume(args)
    return 1


def _run_match(args: argparse.Namespace) -> int:
    try:
        if args.limit < 1:
            raise ValueError("--limit must be at least 1")
        if not os.getenv("DATABASE_URL") and not args.database.is_file():
            raise FileNotFoundError(f"Job database not found: {args.database}")

        profile = parse_resume(args.resume)
        origin = args.origin or profile.location
        repository = create_job_repository(args.database)
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
            jobs = repository.list_relevant_by_status("new")

        provider = None if args.skip_commute_check else OpenStreetMapDrivingProvider()
        assessed = []
        for job in jobs:
            destination = resolve_job_destination(job.location, job.description)
            job.location = destination
            commute = evaluate_commute(origin, destination, provider)
            match = match_resume_to_job(profile, job)
            assessed.append((job, match, commute, match.eligible and commute.eligible))
        ranked = sorted(
            assessed,
            key=lambda item: (
                not item[3],
                item[2].status == "unknown",
                -item[1].score,
                -(item[0].id or 0),
            ),
        )
        manual_review = []
        if args.job_id is None:
            manual_review = [
                item for item in ranked
                if item[1].eligible and item[2].status == "unknown"
            ][: args.limit]
            ranked = [item for item in ranked if item[3]][: args.limit]

        if args.json:
            payload = {
                "schema_version": 2,
                "matches": [
                    _match_payload(job, result, commute, overall_eligible)
                    for job, result, commute, overall_eligible in ranked
                ],
                "manual_review": [
                    _match_payload(job, result, commute, overall_eligible)
                    for job, result, commute, overall_eligible in manual_review
                ],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _print_matches(ranked)
            if manual_review:
                print("\nManual location review:")
                _print_matches(manual_review, empty_message="")
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error, PostgresError) as error:
        print(f"Match failed: {error}", file=sys.stderr)
        return 1


def _run_list(args: argparse.Namespace) -> int:
    try:
        if not os.getenv("DATABASE_URL") and not args.database.is_file():
            raise FileNotFoundError(f"Job database not found: {args.database}")
        repository = create_job_repository(args.database)
        repository.initialize()
        jobs = repository.list_relevant_by_status(args.status)
        if args.json:
            print(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": job.id,
                                "title": job.title,
                                "company": job.company,
                                "location": job.location,
                                "job_url": job.job_url,
                                "status": job.status,
                            }
                            for job in jobs
                        ]
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        elif not jobs:
            print("No stored jobs found.")
        else:
            for job in jobs:
                print(f"{job.id}. [{job.status}] {job.title} at {job.company}")
                print(f"   {job.location} | {job.job_url}")
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error, PostgresError) as error:
        print(f"List failed: {error}", file=sys.stderr)
        return 1


def _run_status(args: argparse.Namespace) -> int:
    try:
        if not os.getenv("DATABASE_URL") and not args.database.is_file():
            raise FileNotFoundError(f"Job database not found: {args.database}")
        repository = create_job_repository(args.database)
        repository.initialize()
        job = repository.update_status(args.job_id, args.status)
        if job is None:
            raise ValueError(f"Job ID {args.job_id} was not found")
        print(f"Job {job.id} status updated to {job.status}.")
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error, PostgresError) as error:
        print(f"Status update failed: {error}", file=sys.stderr)
        return 1


def _run_tailor_resume(args: argparse.Namespace) -> int:
    try:
        if not args.approve:
            raise PermissionError(
                "Tailored resume generation is blocked until --approve is provided."
            )
        if not os.getenv("DATABASE_URL") and not args.database.is_file():
            raise FileNotFoundError(f"Job database not found: {args.database}")
        repository = create_job_repository(args.database)
        repository.initialize()
        job = repository.get_by_id(args.job_id)
        if job is None:
            raise ValueError(f"Job ID {args.job_id} was not found")
        if not job.is_relevant:
            raise ValueError(f"Job ID {args.job_id} is not currently relevant")
        profile = parse_resume(args.resume)
        match = match_resume_to_job(profile, job)
        output = create_tailored_resume(profile, job, match, args.output, approved=True)
        print(f"Approved tailored resume created: {output}")
        return 0
    except (
        FileExistsError,
        FileNotFoundError,
        OSError,
        PermissionError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
        PostgresError,
    ) as error:
        print(f"Tailored resume failed: {error}", file=sys.stderr)
        return 1


def _match_payload(job, result, commute, overall_eligible) -> dict:
    return {
        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "job_url": job.job_url,
        },
        "match": asdict(result),
        "location_check": asdict(commute),
        "overall_eligible": overall_eligible,
    }


def _cloud_recommendations(summary) -> tuple[EmailJobRecommendation, ...] | None:
    encoded = os.getenv("CANDIDATE_PROFILE_JSON", "").strip()
    if not encoded:
        return None
    profile = load_safe_profile_json(encoded).to_resume_profile()
    provider = OpenStreetMapDrivingProvider()
    recommendations = []
    for job in summary.new_jobs:
        result = match_resume_to_job(profile, job)
        destination = resolve_job_destination(job.location, job.description)
        commute = evaluate_commute(profile.location, destination, provider)
        reasons = list(result.strengths[:1])
        if commute.reason:
            reasons.append(commute.reason)
        overall_eligible = result.eligible and commute.eligible
        if not overall_eligible:
            continue
        recommendations.append(
            EmailJobRecommendation(
                title=job.title,
                company=job.company,
                location=destination,
                job_url=job.job_url,
                score=result.score,
                eligible=True,
                reason=" ".join(reasons),
            )
        )
    return tuple(recommendations)


def _print_matches(ranked, empty_message: str = "No stored jobs are currently eligible to recommend.") -> None:
    if not ranked:
        if empty_message:
            print(empty_message)
        return
    for index, (job, result, commute, overall_eligible) in enumerate(ranked, start=1):
        print(f"{index}. {job.title} at {job.company} - {result.score}/100")
        print(f"   {job.location} | {job.job_url}")
        print(f"   Overall eligibility: {'eligible' if overall_eligible else 'not recommended'}")
        duration = f" ({commute.minutes} minutes by car)" if commute.minutes is not None else ""
        print(f"   Location: {commute.status}{duration} - {commute.reason}")
        for disqualifier in result.disqualifiers:
            print(f"   Disqualifier: {disqualifier}")
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
