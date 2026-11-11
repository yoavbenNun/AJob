"""Local web dashboard for browsing and reviewing job postings.

The dashboard is deliberately a presentation layer: storage remains behind the
repository factory and matching remains in its existing domain service.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import sqlite3
import threading
from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from src.web.security import LocalSecurityMiddleware

from src.applications.service import (
    ApplicationPackagePreview,
    CandidateApplicationProfile,
    build_package_preview,
    discover_resume_pairs,
    verify_package_preview,
)
from src.config_loader import load_companies
from src.matching.resume_matcher import match_resume_to_job
from src.location.commute import is_israel_or_remote_location
from src.models import JOB_STATUSES, JobPosting
from src.models import OutreachContact
from src.notifications.recipient_settings import (
    update_cloud_email_recipient,
    validate_notification_email,
)
from src.outreach.contact_cards import ReviewedContactEvidence, contact_cards
from src.outreach.discovery import ContactResults, ReviewedFileSource
from src.outreach.personalized_drafts import personalized_draft
from src.outreach.service import (
    draft_outreach_message,
    validate_approved_body,
)
from src.resume.resume_parser import parse_resume
from src.resume.resume_manager import MAX_RESUME_BYTES, get_active_resume, save_active_resume
from src.resume.tailored_resume import (
    build_tailoring_proposal,
    create_resume_artifacts,
)
from src.storage.repository_factory import create_job_repository
from src.storage.application_repository import ApplicationRepository
from src.storage.outreach_repository import OutreachRepository
from src.storage.postgres_job_repository import PostgresJobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.storage.dismissal_repository import DismissalRepository


WEB_ROOT = Path(__file__).parent
MIN_DASHBOARD_MATCH_SCORE = 30
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
templates.env.filters["status_label"] = lambda value: "Dismissed" if value == "ignored" else value.capitalize()
COMPANY_LOGOS = {
    "amazon": "amazon.svg",
    "apple": "apple.svg",
    "google": "google.svg",
    "microsoft": "microsoft.svg",
}


def create_app(
    database_path: str | Path = Path("data/jobs.db"),
    resume_directory: str | Path | None = None,
    outreach_database_path: str | Path | None = None,
    application_database_path: str | Path | None = None,
    notification_recipient_updater: Callable[[str], None] | None = update_cloud_email_recipient,
    company_config_path: str | Path | None = None,
    personal_notes_database_path: str | Path | None = None,
    *,
    cloud_services=None,
    reviewed_contact_evidence: Callable[[JobPosting], tuple[ReviewedContactEvidence, ...]] | None = None,
    contact_people_path: str | Path | None = None,
) -> FastAPI:
    """Create a local dashboard backed by the configured job repository."""
    repository = cloud_services.jobs if cloud_services else create_job_repository(database_path)
    if isinstance(repository, PostgresJobRepository):
        repository.verify_schema()
    else:
        repository.initialize()
    outreach_path = Path(outreach_database_path) if outreach_database_path else Path("data/outreach.db")
    outreach_repository = None if cloud_services else OutreachRepository(outreach_path)
    if outreach_repository is not None:
        outreach_repository.initialize()
    application_path = (
        Path(application_database_path)
        if application_database_path
        else Path("data/applications.db")
    )
    application_repository = None if cloud_services else ApplicationRepository(application_path)
    if application_repository is not None:
        application_repository.initialize()
    notes_path = (Path(personal_notes_database_path) if personal_notes_database_path
                  else Path(database_path).parent / 'personal_notes.db')
    notes_repository = cloud_services.notes if cloud_services else PersonalNotesRepository(notes_path)
    notes_repository.initialize()
    dismissals = cloud_services.dismissals if cloud_services else DismissalRepository(notes_path)
    dismissals.initialize()
    dismissal_lock = threading.Lock()
    csrf_token = secrets.token_urlsafe(32)
    def request_csrf(request):
        return request.state.csrf_token if cloud_services else csrf_token
    # Presentation cache only: no scores or candidate data are persisted.
    # Both screens use the same resume revision and job evidence as the key.
    match_cache = {}
    match_cache_lock = threading.Lock()

    def current_match(profile, resume, job):
        revision = resume.stat()
        key = (str(resume), revision.st_mtime_ns, revision.st_size, job.title,
               job.company, job.location, job.job_url, job.description, job.requirements)
        with match_cache_lock:
            cached = match_cache.get(key)
        if cached is not None:
            return cached
        result = match_resume_to_job(profile, job)
        with match_cache_lock:
            if len(match_cache) >= 256:
                match_cache.pop(next(iter(match_cache)))
            match_cache[key] = result
        return result

    app_started_at = datetime.now(timezone.utc)
    allowed_resume_root = Path(resume_directory).resolve() if resume_directory else None
    tailored_resume_root = allowed_resume_root / "tailored" if allowed_resume_root else None
    project_root = Path(__file__).resolve().parents[2]
    company_config = (
        Path(company_config_path)
        if company_config_path
        else project_root / "config" / "companies.yaml"
    )
    proposal_sessions: dict[str, tuple[float, int, Path, str]] = {}
    outreach_sessions: dict[str, tuple[float, int, int, str]] = {}
    outreach_approval_sessions: dict[str, tuple[float, int, int, str, str, str]] = {}
    application_sessions: dict[
        str, tuple[float, int, ApplicationPackagePreview]
    ] = {}

    app = FastAPI(title="AJob", docs_url=None, redoc_url=None)
    app.state.reviewed_contact_evidence = reviewed_contact_evidence
    app.state.contact_source = None if cloud_services else ReviewedFileSource(
        Path(contact_people_path) if contact_people_path else outreach_path.parent / "contact_people.json"
    )
    if not cloud_services:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
        app.add_middleware(LocalSecurityMiddleware)
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

    @app.get("/health", include_in_schema=False)
    def health():
        jobs = repository.list_all()
        job_id = next((job.id for job in jobs if job.id is not None), None)
        storage = "postgres" if isinstance(repository, PostgresJobRepository) else "sqlite"
        return {
            "name": "AJob",
            "status": "ok",
            "job_id": job_id,
            "storage": storage,
            "started_at": app_started_at.isoformat(),
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        status_filter: str = Query("all", alias="status"),
        company: str = "",
        query: str = "",
        relevance: str = "relevant",
        location_scope: str = Query("israel", alias="location"),
        updated: int | None = None,
        resume_updated: int | None = None,
        resume_error: str = "",
        email_updated: int | None = None,
        email_error: str = "",
    ):
        selected_statuses = tuple(dict.fromkeys(
            "ignored" if value == "dismissed" else value
            for value in request.query_params.getlist("status") if value and value != "all"))
        if any(value not in JOB_STATUSES for value in selected_statuses):
            raise HTTPException(status_code=400, detail="Unknown job status")
        if relevance not in {"all", "relevant", "possible"}:
            raise HTTPException(status_code=400, detail="Unknown relevance filter")
        if location_scope not in {"all", "israel"}:
            raise HTTPException(status_code=400, detail="Unknown location filter")

        all_jobs = repository.list_all()
        configured_companies = load_companies(company_config)
        scanned_companies = [
            configured.name for configured in configured_companies if configured.enabled
        ]
        paused_companies = [
            configured.name for configured in configured_companies if not configured.enabled
        ]
        active_resume = None if cloud_services else get_active_resume(allowed_resume_root)
        job_matches = {}
        resume_match_failed_ids = set()
        resume_match_error = False
        if active_resume is not None:
            try:
                active_profile = parse_resume(active_resume)
            except Exception:
                active_profile = None
                resume_match_error = True
            if active_profile is not None:
                for job in all_jobs:
                    if job.id is None:
                        continue
                    try:
                        job_matches[job.id] = current_match(active_profile, active_resume, job)
                    except Exception:
                        # A malformed posting should not disable matching for every job.
                        resume_match_failed_ids.add(job.id)
                        continue
        candidate_eligible_ids = (
            {
                job_id for job_id, match in job_matches.items()
                if match.eligible and match.score >= MIN_DASHBOARD_MATCH_SCORE
            }
            if active_resume is not None and active_profile is not None
            else None
        )
        jobs = _filter_jobs(
            all_jobs,
            status_filter=selected_statuses,
            company=company,
            query=query,
            relevance=relevance,
            location_scope=location_scope,
            candidate_eligible_ids=candidate_eligible_ids,
        )
        if relevance == 'possible':
            jobs = [job for job in jobs if job.id in job_matches
                    and job_matches[job.id].eligibility_status == 'potentially_eligible']
        # IDs are allocated on insertion; discovered_at is refreshed by scans.
        status_priority = {"applied": 0, "new": 1, "reviewed": 2}
        jobs.sort(key=lambda job: (status_priority.get(job.status, 3), -(job.id or 0)))
        domestic_jobs = [
            job for job in all_jobs if is_israel_or_remote_location(job.location)
        ]
        visible_pool = all_jobs if location_scope == "all" else domestic_jobs
        companies = sorted({job.company for job in visible_pool}, key=str.casefold)

        def is_candidate_relevant(job: JobPosting) -> bool:
            return job.status != "ignored" and job.is_relevant and (
                candidate_eligible_ids is None
                or job.id in candidate_eligible_ids
            )

        dashboard_stats = {
            "relevant": sum(is_candidate_relevant(job) for job in domestic_jobs),
            "new": sum(
                is_candidate_relevant(job) and job.status == "new"
                for job in domestic_jobs
            ),
            "reviewed": sum(job.status == "reviewed" for job in domestic_jobs),
            "applied": sum(job.status == "applied" for job in domestic_jobs),
        }
        resume_errors = {
            "disabled": "Resume updates require RESUME_DIRECTORY.",
            "invalid": "The resume could not be saved. Choose a valid PDF or DOCX up to 10 MB.",
        }
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": jobs,
                "personal_notes": notes_repository.for_jobs(job.id for job in jobs),
                "companies": companies,
                "scanned_companies": scanned_companies,
                "paused_companies": paused_companies,
                "statuses": JOB_STATUSES,
                "selected_status": "all" if not selected_statuses else selected_statuses[0],
                "selected_statuses": selected_statuses,
                "selected_company": company,
                "query": query,
                "relevance": relevance,
                "location_scope": location_scope,
                "updated": updated,
                "dashboard_stats": dashboard_stats,
                "csrf_token": request_csrf(request),
                "cloud_mode": cloud_services is not None,
                "resume_upload_enabled": allowed_resume_root is not None,
                "active_resume_name": active_resume.name if active_resume else None,
                "resume_updated": resume_updated,
                "resume_error": resume_errors.get(resume_error, ""),
                "job_matches": job_matches,
                "resume_match_failed_ids": resume_match_failed_ids,
                "resume_match_error": resume_match_error,
                "company_logos": COMPANY_LOGOS,
                "email_update_enabled": notification_recipient_updater is not None,
                "email_updated": email_updated,
                "email_error": email_error,
            },
        )

    @app.post('/jobs/{job_id}/dismissal')
    async def change_dismissal(request: Request, job_id: int):
        form = await request.form()
        if not secrets.compare_digest(str(form.get('csrf_token', '')), request_csrf(request)):
            raise HTTPException(status_code=403, detail='Invalid form token')
        action = form.get('action')
        if action not in {'dismiss', 'restore'}:
            raise HTTPException(status_code=400, detail='Unknown dismissal action')
        if cloud_services:
            result = dismissals.change_status(job_id, action)
            if result is None:
                raise HTTPException(status_code=404, detail='Job not found')
            return result
        # Serialize local history + cloud status changes; conditional updates also
        # protect against status edits by another dashboard process.
        with dismissal_lock:
            job = _get_job_or_404(repository, job_id)
            target = 'ignored'
            try:
                if action == 'dismiss' and job.status != 'ignored':
                    dismissals.remember(job_id, job.status)
                elif action == 'restore' and job.status == 'ignored':
                    target = dismissals.previous(job_id) or 'reviewed'
                else:
                    return {'status': job.status, 'changed': False}
                changed = repository.update_status(job_id, target, expected_status=job.status)
                if changed is None:
                    raise HTTPException(status_code=409, detail='The job changed. Refresh and try again.')
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=503, detail='Could not save this change. Please try again.') from None
        return {'status': target, 'changed': True}

    @app.post('/jobs/{job_id}/personal-note')
    async def save_personal_note(request: Request, job_id: int):
        form = await request.form()
        if not secrets.compare_digest(str(form.get('csrf_token', '')), request_csrf(request)):
            raise HTTPException(status_code=403, detail='Invalid form token')
        _get_job_or_404(repository, job_id)
        try:
            note = notes_repository.save(job_id, form.get('note', ''))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except Exception:
            raise HTTPException(status_code=503, detail='Could not save your note. Keep your text and try again.') from None
        return {'note': note}

    @app.post("/settings/notification-email")
    async def update_notification_email(request: Request):
        form = await request.form()
        submitted_token = str(form.get("csrf_token", ""))
        if not secrets.compare_digest(submitted_token, csrf_token):
            raise HTTPException(status_code=403, detail="Invalid form token")
        if notification_recipient_updater is None:
            return RedirectResponse(url="/?email_error=unavailable", status_code=status.HTTP_303_SEE_OTHER)
        try:
            recipient = validate_notification_email(str(form.get("notification_email", "")))
            notification_recipient_updater(recipient)
        except ValueError:
            return RedirectResponse(url="/?email_error=invalid", status_code=status.HTTP_303_SEE_OTHER)
        except (OSError, RuntimeError):
            return RedirectResponse(url="/?email_error=failed", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/?email_updated=1", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/resume")
    async def upload_resume(request: Request):
        if allowed_resume_root is None:
            return RedirectResponse(url="/?resume_error=disabled", status_code=status.HTTP_303_SEE_OTHER)
        try:
            content_length = request.headers.get("content-length", "")
            if not content_length.isdigit() or int(content_length) > MAX_RESUME_BYTES + 1_000_000:
                raise ValueError("The upload is too large.")
            form = await request.form()
            submitted_token = str(form.get("csrf_token", ""))
            if not secrets.compare_digest(submitted_token, csrf_token):
                raise HTTPException(status_code=403, detail="Invalid form token")
            upload = form.get("resume_file")
            if upload is None or not getattr(upload, "filename", ""):
                raise ValueError("Choose a resume file.")
            content = await upload.read(MAX_RESUME_BYTES + 1)
            await upload.close()
            save_active_resume(allowed_resume_root, upload.filename, content)
        except HTTPException:
            raise
        except (OSError, ValueError, RuntimeError):
            return RedirectResponse(url="/?resume_error=invalid", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/?resume_updated=1", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int, updated: int | None = None):
        job = _get_job_or_404(repository, job_id)
        match_context = {}
        resume = None if cloud_services else get_active_resume(allowed_resume_root)
        if resume is not None:
            try:
                match_context['match_result'] = current_match(parse_resume(resume), resume, job)
            except Exception:
                match_context['match_error'] = 'The active resume could not be analyzed. Refresh or choose a valid resume.'
        return _detail_response(request, job, updated=updated, csrf_token=request_csrf(request),
                                cloud_mode=cloud_services is not None,
                                resume_matching_enabled=allowed_resume_root is not None,
                                **match_context,
                                **(_outreach_context(outreach_repository, job) if not cloud_services else {}))

    @app.post("/jobs/{job_id}/contacts/draft")
    async def contact_draft(request: Request, job_id: int):
        if outreach_repository is None:
            raise HTTPException(status_code=404)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                raise HTTPException(status_code=413)
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(status_code=400, detail="Invalid draft request") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("csrf_token"), str):
            raise HTTPException(status_code=400, detail="Invalid draft request")
        if not payload["csrf_token"].isascii():
            raise HTTPException(status_code=403, detail="Invalid form token")
        _validate_csrf({"csrf_token": [payload["csrf_token"]]}, request_csrf(request))
        job = _get_job_or_404(repository, job_id)
        results = _contact_results(request, job, outreach_repository.list_contacts_for_job(job_id))
        contact = next((card for card in results.cards
                        if card.identity == payload.get("profile_url")), None)
        if contact is None:
            raise HTTPException(status_code=404, detail="Verified contact is not available")
        try:
            return personalized_draft(job, contact, payload.get("style"), payload.get("revision", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid draft options") from None

    @app.get("/jobs/{job_id}/application", response_class=HTMLResponse)
    def application_packages(request: Request, job_id: int):
        if application_repository is None:
            raise HTTPException(status_code=404)
        return _application_response(
            request, _get_job_or_404(repository, job_id), application_repository,
            csrf_token, tailored_resume_root,
        )

    @app.post("/jobs/{job_id}/application/preview", response_class=HTMLResponse)
    async def preview_application(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        if tailored_resume_root is None:
            return _application_response(
                request, job, application_repository, csrf_token,
                None, "Set RESUME_DIRECTORY to prepare an application package."
            )
        profile = CandidateApplicationProfile(
            full_name=form.get("full_name", [""])[0].strip(),
            email=form.get("email", [""])[0].strip(),
            phone=form.get("phone", [""])[0].strip(),
            location=form.get("location", [""])[0].strip(),
            work_authorization=form.get("work_authorization", ["unanswered"])[0],
            availability=form.get("availability", [""])[0].strip(),
            salary_expectation=form.get("salary_expectation", [""])[0].strip(),
            linkedin_url=form.get("linkedin_url", [""])[0].strip(),
            github_url=form.get("github_url", [""])[0].strip(),
        )
        try:
            pair_id = form.get("resume_pair", [""])[0].strip()
            pair = next(
                (item for item in discover_resume_pairs(tailored_resume_root)
                 if hmac.compare_digest(item.id, pair_id)),
                None,
            )
            if pair is None:
                raise ValueError("Choose an available tailored resume pair.")
            preview = build_package_preview(
                job, profile, pair.pdf_path, pair.docx_path,
                form.get("cover_note", [""])[0],
            )
        except (OSError, ValueError) as error:
            return _application_response(
                request, job, application_repository, csrf_token,
                tailored_resume_root, str(error), application_profile=profile,
            )
        token = secrets.token_urlsafe(32)
        application_sessions[token] = (time.monotonic(), job_id, preview)
        _expire_application_sessions(application_sessions)
        return _application_response(
            request, job, application_repository, csrf_token,
            tailored_resume_root, application_preview=preview, application_token=token,
            application_profile=profile,
        )

    @app.post("/jobs/{job_id}/application/approve")
    async def approve_application(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        _expire_application_sessions(application_sessions)
        token = form.get("application_token", [""])[0]
        session = application_sessions.get(token)
        if session is None or session[1] != job_id:
            return _application_response(
                request, job, application_repository, csrf_token,
                tailored_resume_root,
                "The application preview expired. Create a new preview.",
            )
        try:
            verified = verify_package_preview(session[2], job)
        except (OSError, ValueError) as error:
            application_sessions.pop(token, None)
            return _application_response(
                request, job, application_repository, csrf_token,
                tailored_resume_root, str(error),
            )
        application_repository.save_profile(verified.profile)
        application_repository.save_approved_package(verified)
        application_sessions.pop(token, None)
        return RedirectResponse(
            url=f"/jobs/{job_id}/application", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/jobs/{job_id}/application/packages/{package_id}/submitted")
    async def record_manual_submission(request: Request, job_id: int, package_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        if form.get("confirm_submission", [""])[0] != "yes":
            return _application_response(
                request, job, application_repository, csrf_token,
                tailored_resume_root,
                "Confirm that you submitted this application manually.",
            )
        try:
            marked = application_repository.mark_submitted_manually(
                package_id, job_id, job.job_url
            )
        except ValueError as error:
            return _application_response(
                request, job, application_repository, csrf_token,
                tailored_resume_root, str(error),
            )
        if not marked:
            raise HTTPException(status_code=404, detail="Prepared application not found")
        repository.update_status(job_id, "applied")
        return RedirectResponse(
            url=f"/jobs/{job_id}/application", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/jobs/{job_id}/outreach/contacts", response_class=HTMLResponse)
    async def add_outreach_contact(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        try:
            outreach_repository.save_contact(OutreachContact(
                job_id=job_id,
                display_name=form.get("display_name", [""])[0],
                role_title=form.get("role_title", [""])[0],
                company=form.get("company", [""])[0],
                profile_url=form.get("profile_url", [""])[0],
                relationship=form.get("relationship", ["employee"])[0],
                location=form.get("location", [""])[0],
                notes=form.get("notes", [""])[0],
            ))
        except ValueError as error:
            return _detail_response(request, job, outreach_error=str(error), csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_application_context(application_repository, job, tailored_resume_root),
                                    **_outreach_context(outreach_repository, job))
        return RedirectResponse(url=f"/jobs/{job_id}#outreach", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/jobs/{job_id}/outreach/preview", response_class=HTMLResponse)
    async def preview_outreach(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        try:
            contact_id = int(form.get("contact_id", [""])[0])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid contact")
        contact = outreach_repository.get_contact(contact_id, job_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        kind = form.get("kind", ["full_message"])[0]
        try:
            body = draft_outreach_message(job, contact, kind)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        fingerprint = _outreach_fingerprint(job, contact, body)
        token = secrets.token_urlsafe(32)
        outreach_sessions[token] = (time.monotonic(), job_id, contact_id, fingerprint)
        _expire_outreach_sessions(outreach_sessions)
        return _detail_response(
            request, job, csrf_token=csrf_token,
            resume_matching_enabled=allowed_resume_root is not None,
            outreach_preview={"contact": contact, "body": body, "kind": kind, "token": token},
            **_application_context(application_repository, job, tailored_resume_root),
            **_outreach_context(outreach_repository, job),
        )

    @app.post("/jobs/{job_id}/outreach/approve", response_class=HTMLResponse)
    async def approve_outreach(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        token = form.get("outreach_token", [""])[0]
        session = outreach_sessions.get(token)
        if session is None or session[1] != job_id or time.monotonic() - session[0] > 1800:
            return _detail_response(request, job, outreach_error="The outreach preview expired.",
                                    csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_application_context(application_repository, job, tailored_resume_root),
                                    **_outreach_context(outreach_repository, job))
        contact = outreach_repository.get_contact(session[2], job_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        kind = form.get("kind", [""])[0]
        try:
            generated_body = draft_outreach_message(job, contact, kind)
            body = validate_approved_body(form.get("body", [""])[0], kind)
        except ValueError as error:
            return _detail_response(request, job, outreach_error=str(error), csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_application_context(application_repository, job, tailored_resume_root),
                                    **_outreach_context(outreach_repository, job))
        fingerprint = _outreach_fingerprint(job, contact, generated_body)
        if not secrets.compare_digest(fingerprint, session[3]):
            return _detail_response(request, job, outreach_error="Job or contact changed after preview.",
                                    csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_application_context(application_repository, job, tailored_resume_root),
                                    **_outreach_context(outreach_repository, job))
        confirmation_token = secrets.token_urlsafe(32)
        exact_fingerprint = _outreach_fingerprint(job, contact, f"{kind}\n{body}")
        outreach_approval_sessions[confirmation_token] = (
            time.monotonic(), job_id, contact.id, kind, body, exact_fingerprint
        )
        outreach_sessions.pop(token, None)
        _expire_outreach_approval_sessions(outreach_approval_sessions)
        return _detail_response(
            request, job, csrf_token=csrf_token,
            resume_matching_enabled=allowed_resume_root is not None,
            outreach_confirmation={"body": body, "kind": kind, "token": confirmation_token},
            **_application_context(application_repository, job, tailored_resume_root),
            **_outreach_context(outreach_repository, job),
        )

    @app.post("/jobs/{job_id}/outreach/confirm")
    async def confirm_outreach(request: Request, job_id: int):
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        _expire_outreach_approval_sessions(outreach_approval_sessions)
        token = form.get("confirmation_token", [""])[0]
        session = outreach_approval_sessions.get(token)
        if session is None or session[1] != job_id:
            return _detail_response(request, job, outreach_error="The exact-message approval expired.",
                                    csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_application_context(application_repository, job, tailored_resume_root),
                                    **_outreach_context(outreach_repository, job))
        contact = outreach_repository.get_contact(session[2], job_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        exact_fingerprint = _outreach_fingerprint(job, contact, f"{session[3]}\n{session[4]}")
        if not secrets.compare_digest(exact_fingerprint, session[5]):
            raise HTTPException(status_code=409, detail="Job or contact changed after review")
        outreach_repository.save_approved_draft(
            job_id, contact.id, session[4], exact_fingerprint, session[3]
        )
        outreach_approval_sessions.pop(token, None)
        return RedirectResponse(url=f"/jobs/{job_id}#outreach", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/jobs/{job_id}/outreach/drafts/{draft_id}/activity")
    async def update_outreach_activity(request: Request, job_id: int, draft_id: int):
        _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        action = form.get("action", [""])[0]
        follow_up_at = form.get("follow_up_at", [""])[0]
        if follow_up_at:
            try:
                date.fromisoformat(follow_up_at)
            except ValueError as error:
                raise HTTPException(status_code=400, detail="Invalid follow-up date") from error
        try:
            updated = outreach_repository.update_draft_activity(
                draft_id, job_id, action, follow_up_at,
                form.get("response_note", [""])[0],
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not updated:
            raise HTTPException(status_code=404, detail="Approved draft not found")
        return RedirectResponse(url=f"/jobs/{job_id}#outreach", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/jobs/{job_id}/status")
    async def update_job_status(request: Request, job_id: int):
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, request_csrf(request))
        requested_status = form.get("status", [""])[0]
        if requested_status not in JOB_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown job status")
        if repository.update_status(job_id, requested_status) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return RedirectResponse(url=f"/jobs/{job_id}?updated=1", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/jobs/{job_id}/match", response_class=HTMLResponse)
    async def match_job(request: Request, job_id: int):
        """Show a non-persistent, local deterministic match for one job."""
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        resume_path = form.get("resume_path", [""])[0]
        if allowed_resume_root is None:
            return _detail_response(
                request, job,
                match_error="Set RESUME_DIRECTORY to enable dashboard matching.",
                csrf_token=csrf_token, resume_matching_enabled=False,
                **_application_context(application_repository, job, tailored_resume_root),
                **_outreach_context(outreach_repository, job),
            )
        try:
            if resume_path.strip():
                resolved_resume = _resolve_resume_path(resume_path, allowed_resume_root)
            else:
                resolved_resume = get_active_resume(allowed_resume_root)
                if resolved_resume is None:
                    raise ValueError("Upload or choose a resume before calculating a match.")
            profile = parse_resume(resolved_resume)
            result = match_resume_to_job(profile, job)
        except (OSError, ValueError) as error:
            return _detail_response(
                request, job, match_error=f"Match unavailable: {error}",
                csrf_token=csrf_token, resume_matching_enabled=True,
                **_application_context(application_repository, job, tailored_resume_root),
                **_outreach_context(outreach_repository, job),
            )
        proposal_token = secrets.token_urlsafe(32)
        proposal_sessions[proposal_token] = (
            time.monotonic(), job_id, resolved_resume,
            _proposal_fingerprint(
                resolved_resume, job, build_tailoring_proposal(profile, job, result)
            ),
        )
        _expire_proposal_sessions(proposal_sessions)
        return _detail_response(
            request,
            job,
            match_result=result,
            tailoring_proposal=build_tailoring_proposal(profile, job, result),
            proposal_token=proposal_token,
            csrf_token=csrf_token,
            resume_matching_enabled=True,
            **_application_context(application_repository, job, tailored_resume_root),
            **_outreach_context(outreach_repository, job),
        )

    @app.post("/jobs/{job_id}/tailor", response_class=HTMLResponse)
    async def tailor_job_resume(request: Request, job_id: int):
        """Create a separate tailored DOCX only after an explicit local approval."""
        job = _get_job_or_404(repository, job_id)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
        if allowed_resume_root is None or tailored_resume_root is None:
            return _detail_response(
                request,
                job,
                tailoring_error="Set RESUME_DIRECTORY to enable tailored resume generation.",
                csrf_token=csrf_token,
                resume_matching_enabled=False,
                **_application_context(application_repository, job, tailored_resume_root),
                **_outreach_context(outreach_repository, job),
            )
        _expire_proposal_sessions(proposal_sessions)
        proposal_token = form.get("proposal_token", [""])[0]
        session = proposal_sessions.get(proposal_token)
        if session is None or session[1] != job_id or time.monotonic() - session[0] > 1800:
            return _detail_response(
                request, job,
                tailoring_error="The tailoring preview expired. Calculate the match again.",
                csrf_token=csrf_token,
                resume_matching_enabled=True,
                **_application_context(application_repository, job, tailored_resume_root),
                **_outreach_context(outreach_repository, job),
            )
        approved_change_ids = tuple(dict.fromkeys(form.get("approved_change_id", [])))
        try:
            resolved_resume = _resolve_resume_path(str(session[2]), allowed_resume_root)
            profile = parse_resume(resolved_resume)
            match = match_resume_to_job(profile, job)
            proposal = build_tailoring_proposal(profile, job, match)
            if _proposal_fingerprint(resolved_resume, job, proposal) != session[3]:
                raise PermissionError(
                    "The resume or job changed after preview. Calculate the match again."
                )
            allowed_ids = {change.id for change in proposal.changes}
            if not approved_change_ids or not set(approved_change_ids) <= allowed_ids:
                raise PermissionError("Select only changes shown in the current preview.")
            artifacts = create_resume_artifacts(
                profile, job, match, tailored_resume_root,
                approved=True,
                approved_change_ids=approved_change_ids,
            )
            proposal_sessions.pop(proposal_token, None)
        except (OSError, ValueError, PermissionError) as error:
            return _detail_response(
                request,
                job,
                tailoring_error=f"Tailored resume unavailable: {error}",
                csrf_token=csrf_token,
                resume_matching_enabled=True,
                **_application_context(application_repository, job, tailored_resume_root),
                **_outreach_context(outreach_repository, job),
            )
        return _detail_response(
            request,
            job,
            tailored_resume_names=(artifacts.docx_path.name, artifacts.pdf_path.name),
            csrf_token=csrf_token,
            resume_matching_enabled=True,
            **_application_context(application_repository, job, tailored_resume_root),
            **_outreach_context(outreach_repository, job),
        )

    return app


def _application_response(
    request: Request,
    job: JobPosting,
    application_repository: ApplicationRepository,
    csrf_token: str,
    tailored_resume_root: Path | None,
    application_error: str | None = None,
    **context: object,
):
    return templates.TemplateResponse(
        request,
        "application_packages.html",
        {
            "job": job,
            "csrf_token": csrf_token,
            "application_error": application_error,
            **_application_context(application_repository, job, tailored_resume_root),
            **context,
        },
    )


def _detail_response(request: Request, job: JobPosting, **context: object):
    saved_contacts = context.pop("saved_outreach_contacts", ())
    people = _contact_results(request, job, saved_contacts)
    extracted_requirements = _description_requirements(job.description) if not job.requirements.strip() else {}
    if not job.requirements.strip() and not extracted_requirements:
        extracted_requirements = _prose_requirements(job.description)
    from urllib.parse import urlencode
    contact_search_url = 'https://www.linkedin.com/search/results/people/?' + urlencode(
        {'keywords': f'{job.company} {job.title}'})
    match = context.get('match_result')
    match_supported = bool(match and match.job_skills and len(job.description.strip()) >= 150
                           and (job.requirements.strip() or extracted_requirements))
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "contact_cards": people.cards,
            "contact_state": people.state,
            "contact_search_url": contact_search_url,
            "match_supported": match_supported,
            "statuses": JOB_STATUSES,
            "company_logos": COMPANY_LOGOS,
            "role_summary": _summarize_role(job) or _source_role_preview(job),
            "requirement_lines": _summarize_captured_lines(
                job.requirements, job,
                keywords=(
                    "required", "must", "experience", "years", "degree", "student",
                    "knowledge", "proficien", "authorization", "citizen", "graduate",
                    "availability", "location", "language", "python", "java", "c++", "c#",
                ),
                limit=7,
            ),
            "full_requirement_lines": _clean_captured_lines(job.requirements, job),
            "extracted_requirements": extracted_requirements,
            **context,
        },
    )


def _contact_results(request: Request, job: JobPosting, saved_contacts=()) -> ContactResults:
    # Legacy trusted evidence hook is retained; it is not an automatic discoverer.
    evidence = request.app.state.reviewed_contact_evidence
    try:
        if evidence is not None:
            cards = contact_cards(job, saved_contacts, evidence(job))
            return ContactResults(cards, "ready" if cards else "empty")
        source = request.app.state.contact_source
        return source.for_job(job) if source is not None else ContactResults()
    except Exception:
        # Never leak provider exceptions, profile data or local paths into HTML.
        return ContactResults(state="unavailable")


def _clean_captured_lines(text: str, job: JobPosting) -> list[str]:
    """Remove obvious career-site chrome while preserving captured job facts."""
    ignored = {
        "skip to main content",
        "amazon jobs home page",
        "my career",
        "apply now",
        "description",
        "requirements",
        "basic qualifications",
        "preferred qualifications",
        job.title.casefold().strip(),
    }
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^(?:[•*-]\s+)", "", raw_line.strip()).strip()
        key = line.casefold()
        if not line or key in ignored or re.fullmatch(r"job id:\s*[\w-]+", key):
            continue
        if cleaned and cleaned[-1].casefold() == key:
            continue
        cleaned.append(line)
    return cleaned


# Extract duties verbatim; qualifiers remain attached to the original sentence.
_ROLE_DUTY = re.compile(
    r"^(?:(?:in this (?:role|position)|as (?:an?|our) [^,.:;]{1,80}),\s*)?"
    r"(?:(?:you(?:'ll|’ll| will| are)?|the (?:intern|student|engineer) will|"
    r"the (?:role|position) is)\s+)?"
    r"(?:(?:be )?responsible for\s+)?"
    r"(?:build(?:ing)?|design(?:ing)?|develop(?:ing)?|implement(?:ing)?|"
    r"maintain(?:ing)?|writ(?:e|ing)|test(?:ing)?|debug(?:ging)?|automat(?:e|ing)|"
    r"integrat(?:e|ing)|deploy(?:ing)?|operat(?:e|ing)|monitor(?:ing)?|support(?:ing)?|"
    r"analyz(?:e|ing)|collaborat(?:e|ing)|investigat(?:e|ing)|improv(?:e|ing)|"
    r"work(?:ing)? (?:on|with))\b",
    re.IGNORECASE,
)
_ROLE_ABBREVIATION = re.compile(
    r"(?:\b(?:e\.g|i\.e|B\.Sc|M\.Sc|Ph\.D|Dr|Mr|Ms)|\b[A-Z])\.$",
    re.IGNORECASE,
)


def _role_sentences(line: str) -> list[str]:
    """Split plain prose regardless of case, retaining common abbreviations."""
    sentences: list[str] = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+(?=\S)", line):
        if _ROLE_ABBREVIATION.search(line[start:boundary.start()]):
            continue
        sentences.append(line[start:boundary.start()])
        start = boundary.end()
    sentences.append(line[start:])
    return sentences


# Only explicit headings delimit sections; unknown headings terminate extraction.
_SECTION_HEADINGS = {
    "role": r"(?:about the role|role overview|the role|job overview)",
    "duties": r"(?:responsibilities|what you(?:'|’)ll do|what you will do|how you(?:'|’)ll make an impact)",
    "required": r"(?:(?:minimum|basic|required)\s+)?(?:requirements|qualifications)|what you(?:'|’)ll need|what you will need|what we(?:'|’)re looking for|what we are looking for|to thrive in this role,? you(?:'|’)ll need",
    "preferred": r"(?:preferred qualifications|preferred requirements|nice to have|bonus points if you have)",
    "other": r"(?:why .+|about (?!the role).+|equal opportunity|benefits|who we are)",
}

def _description_sections(description: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current = intro
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = line.rstrip(":?").strip()
        kind = next((name for name, pattern in _SECTION_HEADINGS.items()
                     if re.fullmatch(pattern, heading, re.I)), None)
        # Unknown standalone headings end, rather than leak into, a section.
        if kind is None and (line.endswith(":") or line.endswith("?")) and len(line) < 100:
            kind = "other"
        if kind:
            current = []
            sections.append((kind, current))
        else:
            current.append(line)
    return intro, sections


def _description_requirements(description: str) -> dict[str, list[str]]:
    _, sections = _description_sections(description)
    result: dict[str, list[str]] = {}
    for kind, lines in sections:
        if kind in {"required", "preferred"} and lines:
            result.setdefault(kind, []).extend(re.sub(r"^[•*-]\s+", "", line) for line in lines)
    return result


def _prose_requirements(description: str) -> dict[str, list[str]]:
    """Use only explicit candidate requirements, without rewriting the source."""
    found = []
    for line in description.splitlines():
        for sentence in _role_sentences(line.strip()):
            sentence = sentence.strip()
            if (len(sentence) <= 320 and re.search(r'\b(?:you must|must have|we require)\b', sentence, re.I)
                    and not re.search(r'\b(?:preferred|nice to have|not required)\b', sentence, re.I)
                    and sentence not in found):
                found.append(sentence)
    return {'required': found[:7]} if found else {}


def _source_role_preview(job: JobPosting) -> str:
    """Show a whole source paragraph, preserving adjacent qualifications."""
    for paragraph in re.split(r'\n\s*\n', job.description):
        paragraph = ' '.join(line.strip() for line in paragraph.splitlines()).strip()
        sentences = _role_sentences(paragraph)
        if (len(paragraph) <= 850 and 1 <= len(sentences) <= 3
                and all(s.endswith(('.', '!', '?')) for s in sentences)
                and any(_ROLE_DUTY.match(s) for s in sentences)
                and not re.search(r'\b(?:requirements|qualifications|must have|you must|experience preferred)\b', paragraph, re.I)):
            return paragraph
    return ''


def _section_role_preview(job: JobPosting) -> str:
    intro, sections = _description_sections(job.description)
    # A known heading provides a reliable boundary for an opening paragraph.
    if not sections:
        return ""
    candidates = [intro[-1]] if intro and len(_role_sentences(intro[-1])) in (2, 3) else []
    candidates += [" ".join(lines) for kind, lines in sections
                   if kind in {"role", "duties"} and len(lines) in (1, 2, 3)]
    for candidate in candidates:
        result = _summarize_role(job, _text=candidate)
        if result:
            return result
    return ""


def _summarize_role(job: JobPosting, *, _text: str | None = None) -> str:
    """Return a source passage when its local context fits; otherwise fail closed."""
    if _text is None:
        preview = _section_role_preview(job)
        if preview:
            return preview
    filler = {"the", "a", "an", "and", "or", "with", "for", "to", "on", "of",
              "our", "your", "their", "work"}
    lines: list[str] = []
    for line in _clean_captured_lines(job.description if _text is None else _text, job):
        line = re.sub(r"^\d+[.)]\s+", "", " ".join(line.split()))
        if line.casefold().rstrip(":") in {"responsibilities", "about the role", "what you'll do"}:
            continue
        if (
            lines and _ROLE_DUTY.match(lines[-1])
            and not re.search(r"[.!?:]$", lines[-1])
            and not _ROLE_DUTY.match(line)
            and (line[0].islower() or re.search(r"\b(?:with|using|for|and|to)$", lines[-1]))
        ):
            lines[-1] += " " + line
        else:
            lines.append(line)

    sentences = [sentence for line in lines for sentence in _role_sentences(line)]
    if not 2 <= len(sentences) <= 3:
        return ""
    if any(len(sentence) > 420 for sentence in sentences):
        return ""

    duty_count = 0
    for sentence in sentences:
        # Terminal connectors and modals leave an open grammatical dependency,
        # even if the captured source already includes punctuation.
        if re.search(
            r"\b(?:with|for|and|or|to|of|using|by|that|when|if|unless|because|"
            r"will|would|can|could|may|might|must|should|shall)[.!?]?$",
            sentence, re.IGNORECASE,
        ):
            return ""
        duty = _ROLE_DUTY.match(sentence)
        if not duty:
            # Unknown unterminated prose may be a truncated restriction.
            if sentence[-1] not in ".!?":
                return ""
            continue
        responsibility = sentence[duty.end():].strip()
        if re.match(
            r"(?:(?:your|our|a|the)\s+)?(?:career|future|skills?|experience|knowledge)\b",
            responsibility, re.IGNORECASE,
        ):
            return ""
        objects = re.findall(r"[\w+#]+", responsibility)
        if not any(
            len(word) >= 2 and word.casefold() not in filler
            and not _ROLE_DUTY.fullmatch(word) for word in objects
        ):
            return ""
        duty_count += 1

    # Keep unsupported fragments out too; do not turn a heading into a sentence.
    if duty_count < 2 or any(
        len(re.findall(r"[\w+#]+", sentence)) < 2 for sentence in sentences
    ):
        return ""
    summary = " ".join(text if text[-1] in ".!?" else text + "." for text in sentences)
    return summary if len(summary) <= 850 else ""


def _summarize_captured_lines(
    text: str,
    job: JobPosting,
    *,
    keywords: tuple[str, ...],
    limit: int,
) -> list[str]:
    """Select a short, source-faithful set of high-signal display sentences."""
    candidates: list[str] = []
    for line in _clean_captured_lines(text, job):
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])|\s*[•;]\s*", line)
        for part in parts:
            sentence = re.sub(r"\s+", " ", part).strip()
            if len(sentence) < 3 or not re.search(r"[A-Za-z0-9]", sentence):
                continue
            if len(sentence) > 320:
                continue
            if sentence.casefold() not in {item.casefold() for item in candidates}:
                candidates.append(sentence)

    scored = sorted(
        enumerate(candidates),
        key=lambda item: (
            -sum(keyword in item[1].casefold() for keyword in keywords),
            item[0],
        ),
    )[:limit]
    return [candidates[index] for index, _ in sorted(scored)]


def _validate_csrf(form: dict[str, list[str]], expected: str) -> None:
    if not secrets.compare_digest(form.get("csrf_token", [""])[0], expected):
        raise HTTPException(status_code=403, detail="Invalid form token")


def _resolve_resume_path(resume_path: str, allowed_resume_root: Path) -> Path:
    if not resume_path.strip():
        raise ValueError("Enter a local resume path.")
    resolved_resume = Path(resume_path).resolve()
    if resolved_resume != allowed_resume_root and allowed_resume_root not in resolved_resume.parents:
        raise ValueError("Resume path must be inside RESUME_DIRECTORY.")
    return resolved_resume


def _expire_proposal_sessions(
    sessions: dict[str, tuple[float, int, Path, str]], max_age_seconds: int = 1800
) -> None:
    cutoff = time.monotonic() - max_age_seconds
    for token in [token for token, value in sessions.items() if value[0] < cutoff]:
        sessions.pop(token, None)


def _expire_application_sessions(
    sessions: dict[str, tuple[float, int, ApplicationPackagePreview]],
    max_age_seconds: int = 1800,
) -> None:
    cutoff = time.monotonic() - max_age_seconds
    for token in [token for token, value in sessions.items() if value[0] < cutoff]:
        sessions.pop(token, None)


def _proposal_fingerprint(resume_path: Path, job: JobPosting, proposal) -> str:
    payload = {
        "resume_sha256": hashlib.sha256(resume_path.read_bytes()).hexdigest(),
        "job": [
            job.title, job.company, job.location, job.description,
            job.requirements, job.job_url,
        ],
        "scores": [proposal.before_score, proposal.after_score],
        "changes": [
            [change.id, change.original, change.proposed, change.evidence, change.score_delta]
            for change in proposal.changes
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _outreach_fingerprint(job: JobPosting, contact: OutreachContact, body: str) -> str:
    payload = [job.id, job.title, job.company, job.job_url, contact.id,
               contact.display_name, contact.role_title, contact.company,
               contact.profile_url, body]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _expire_outreach_sessions(sessions: dict[str, tuple[float, int, int, str]]) -> None:
    cutoff = time.monotonic() - 1800
    for token in [key for key, value in sessions.items() if value[0] < cutoff]:
        sessions.pop(token, None)


def _expire_outreach_approval_sessions(
    sessions: dict[str, tuple[float, int, int, str, str, str]]
) -> None:
    cutoff = time.monotonic() - 1800
    for token in [key for key, value in sessions.items() if value[0] < cutoff]:
        sessions.pop(token, None)


def _outreach_context(repository: OutreachRepository, job: JobPosting) -> dict[str, object]:
    return {"saved_outreach_contacts": repository.list_contacts_for_job(job.id)}


def _application_context(
    repository: ApplicationRepository,
    job: JobPosting,
    tailored_resume_root: Path | None,
) -> dict[str, object]:
    return {
        "application_enabled": tailored_resume_root is not None,
        "application_profile": repository.get_profile(),
        "application_packages": repository.list_packages_for_job(job.id, job.job_url),
        "application_resume_pairs": discover_resume_pairs(tailored_resume_root),
    }


def _get_job_or_404(repository, job_id: int) -> JobPosting:
    job = repository.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _filter_jobs(
    jobs: list[JobPosting], *, status_filter: str | tuple[str, ...], company: str, query: str,
    relevance: str, location_scope: str,
    candidate_eligible_ids: set[int] | None = None,
) -> list[JobPosting]:
    selected = {status_filter} if isinstance(status_filter, str) else set(status_filter)
    selected.discard("all")
    company_key = company.casefold().strip()
    query_key = query.casefold().strip()
    return [
        job
        for job in jobs
        if (
            relevance == "all"
            or (
                job.is_relevant
                and (
                    relevance == 'possible'
                    or candidate_eligible_ids is None
                    or job.id in candidate_eligible_ids
                )
            )
        )
        and (location_scope == "all" or is_israel_or_remote_location(job.location))
        and (job.status in selected if selected else job.status != "ignored")
        and (not company_key or job.company.casefold() == company_key)
        and (
            not query_key
            or query_key in " ".join((job.title, job.company, job.location)).casefold()
        )
    ]


def __getattr__(name):
    # Importing the factory from the cloud entry point must not create local
    # databases or inspect resumes. Preserve the desktop Uvicorn app target.
    if name == "app":
        instance = create_app(resume_directory=os.getenv("RESUME_DIRECTORY"))
        globals()[name] = instance
        return instance
    raise AttributeError(name)
