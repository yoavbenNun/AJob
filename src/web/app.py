"""Local web dashboard for browsing and reviewing job postings.

The dashboard is deliberately a presentation layer: storage remains behind the
repository factory and matching remains in its existing domain service.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs
import hashlib
import json
import os
import secrets
import time

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.matching.resume_matcher import match_resume_to_job
from src.models import JOB_STATUSES, JobPosting
from src.models import OutreachContact
from src.outreach.service import draft_outreach_message, rank_contact
from src.resume.resume_parser import parse_resume
from src.resume.tailored_resume import (
    build_tailoring_proposal,
    create_resume_artifacts,
)
from src.storage.repository_factory import create_job_repository
from src.storage.outreach_repository import OutreachRepository


WEB_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))


def create_app(
    database_path: str | Path = Path("data/jobs.db"),
    resume_directory: str | Path | None = None,
    outreach_database_path: str | Path | None = None,
) -> FastAPI:
    """Create a local dashboard backed by the configured job repository."""
    repository = create_job_repository(database_path)
    repository.initialize()
    outreach_path = Path(outreach_database_path) if outreach_database_path else Path("data/outreach.db")
    outreach_repository = OutreachRepository(outreach_path)
    outreach_repository.initialize()
    csrf_token = secrets.token_urlsafe(32)
    allowed_resume_root = Path(resume_directory).resolve() if resume_directory else None
    tailored_resume_root = allowed_resume_root / "tailored" if allowed_resume_root else None
    proposal_sessions: dict[str, tuple[float, int, Path, str]] = {}
    outreach_sessions: dict[str, tuple[float, int, int, str]] = {}

    app = FastAPI(title="AI Job Search Agent", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        status_filter: str = Query("all", alias="status"),
        company: str = "",
        query: str = "",
        relevance: str = "relevant",
        updated: int | None = None,
    ):
        if status_filter != "all" and status_filter not in JOB_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown job status")
        if relevance not in {"all", "relevant"}:
            raise HTTPException(status_code=400, detail="Unknown relevance filter")

        all_jobs = repository.list_all()
        jobs = _filter_jobs(
            all_jobs,
            status_filter=status_filter,
            company=company,
            query=query,
            relevance=relevance,
        )
        companies = sorted({job.company for job in all_jobs}, key=str.casefold)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": jobs,
                "companies": companies,
                "statuses": JOB_STATUSES,
                "selected_status": status_filter,
                "selected_company": company,
                "query": query,
                "relevance": relevance,
                "updated": updated,
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int, updated: int | None = None):
        job = _get_job_or_404(repository, job_id)
        return _detail_response(request, job, updated=updated, csrf_token=csrf_token,
                                resume_matching_enabled=allowed_resume_root is not None,
                                **_outreach_context(outreach_repository, job))

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
        body = draft_outreach_message(job, contact)
        fingerprint = _outreach_fingerprint(job, contact, body)
        token = secrets.token_urlsafe(32)
        outreach_sessions[token] = (time.monotonic(), job_id, contact_id, fingerprint)
        _expire_outreach_sessions(outreach_sessions)
        return _detail_response(
            request, job, csrf_token=csrf_token,
            resume_matching_enabled=allowed_resume_root is not None,
            outreach_preview={"contact": contact, "body": body, "token": token},
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
                                    **_outreach_context(outreach_repository, job))
        contact = outreach_repository.get_contact(session[2], job_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        body = draft_outreach_message(job, contact)
        fingerprint = _outreach_fingerprint(job, contact, body)
        if not secrets.compare_digest(fingerprint, session[3]):
            return _detail_response(request, job, outreach_error="Job or contact changed after preview.",
                                    csrf_token=csrf_token,
                                    resume_matching_enabled=allowed_resume_root is not None,
                                    **_outreach_context(outreach_repository, job))
        outreach_repository.save_approved_draft(job_id, contact.id, body, fingerprint)
        outreach_sessions.pop(token, None)
        return RedirectResponse(url=f"/jobs/{job_id}#outreach", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/jobs/{job_id}/status")
    async def update_job_status(request: Request, job_id: int):
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        _validate_csrf(form, csrf_token)
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
            return _detail_response(request, job, match_error="Set RESUME_DIRECTORY to enable dashboard matching.", csrf_token=csrf_token, resume_matching_enabled=False)
        if not resume_path.strip():
            return _detail_response(
                request,
                job,
                match_error="Enter a local resume path to match this job.",
                csrf_token=csrf_token,
                resume_matching_enabled=True,
            )
        try:
            resolved_resume = _resolve_resume_path(resume_path, allowed_resume_root)
            profile = parse_resume(resolved_resume)
            result = match_resume_to_job(profile, job)
        except (OSError, ValueError) as error:
            return _detail_response(request, job, match_error=f"Match unavailable: {error}", csrf_token=csrf_token, resume_matching_enabled=True)
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
            )
        return _detail_response(
            request,
            job,
            tailored_resume_names=(artifacts.docx_path.name, artifacts.pdf_path.name),
            csrf_token=csrf_token,
            resume_matching_enabled=True,
        )

    return app


def _detail_response(request: Request, job: JobPosting, **context: object):
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"job": job, "statuses": JOB_STATUSES, **context},
    )


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


def _outreach_context(repository: OutreachRepository, job: JobPosting) -> dict[str, object]:
    contacts = repository.list_contacts_for_job(job.id)
    ranked = sorted((rank_contact(job, contact) for contact in contacts),
                    key=lambda item: (-item.score, item.contact.display_name.casefold()))
    return {"outreach_contacts": ranked, "outreach_drafts": repository.list_approved_drafts(job.id)}


def _get_job_or_404(repository, job_id: int) -> JobPosting:
    job = repository.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _filter_jobs(
    jobs: list[JobPosting], *, status_filter: str, company: str, query: str, relevance: str
) -> list[JobPosting]:
    company_key = company.casefold().strip()
    query_key = query.casefold().strip()
    return [
        job
        for job in jobs
        if (relevance == "all" or job.is_relevant)
        and (status_filter == "all" or job.status == status_filter)
        and (not company_key or job.company.casefold() == company_key)
        and (
            not query_key
            or query_key in " ".join((job.title, job.company, job.location)).casefold()
        )
    ]


app = create_app(resume_directory=os.getenv("RESUME_DIRECTORY"))
