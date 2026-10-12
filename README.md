# AI Job Search Agent

AI Job Search Agent is a local-first assistant for discovering relevant student,
intern, and junior software roles. The long-term goal is to compare roles with a
resume, explain fit, and prepare tailored application material while keeping all
rewrites and submissions behind explicit human approval.

## Current scope

The current MVP provides:

- YAML configuration for target companies
- typed `Company` and `JobPosting` domain models
- SQLite persistence for discovered jobs
- a Playwright scanner that visits enabled career pages
- configurable URL-pattern extraction of job links
- bounded pagination through listing pages
- deterministic filtering for early-career software roles
- full detail-page text and requirements-section extraction for accepted roles
- local TXT, Markdown, PDF, and DOCX resume parsing
- normalized contact details, resume sections, and technical skills
- deterministic resume-to-job scoring from 1 to 100
- transparent score breakdown, matched skills, missing skills, strengths, and gaps
- evidence-backed resume tailoring suggestions that do not invent experience
- resume-header location extraction and driving-commute eligibility
- automatic rejection of clearly foreign roles and a 90/120 minute commute policy
- human-readable and stable JSON matching output
- isolated per-company failures and SQLite persistence
- a dry-run mode that prints targets without launching a browser
- lifecycle tracking for new, reviewed, applied, ignored, and rejected jobs
- PostgreSQL/Supabase storage selected through `DATABASE_URL`
- email summaries with optional privacy-reduced cloud scoring
- a scheduled Docker/GitHub Actions deployment
- a local FastAPI dashboard for review and status updates
- human-approved DOCX generation that preserves existing resume evidence
- a local outreach workspace for ranking user-supplied public contacts and approving message drafts

It does **not** scrape LinkedIn, send messages, call paid APIs, or submit applications. Remote execution requires
your GitHub, Supabase, and SMTP secrets before it can run in the cloud.

## Architecture

The project uses a small layered structure:

- `config/`: user-editable target company definitions
- `src/models.py`: domain objects shared by the application
- `src/config_loader.py`: YAML-to-domain-model boundary
- `src/storage/`: SQLite and PostgreSQL persistence
- `src/scrapers/`: Playwright browsing and normalized job-link extraction
- `src/matching/`, `src/resume/`, `src/notifications/`: scoring, approved resume
  generation, safe cloud profiles, and email delivery
- `src/web/`: local FastAPI dashboard
- `src/outreach/`: deterministic contact ranking and factual message drafting
- `tests/`: tests for behavior already implemented

The scanner depends on domain models rather than storage or matching logic. This
keeps Playwright extraction, filtering, and resume matching independently testable
as they are added.

## Setup

Python 3.11 or newer is recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Playwright uses Chromium to load JavaScript-heavy career pages.

## Run

From the project root:

```powershell
python -m src.main scan --dry-run
python -m src.main scan
python -m src.main parse-resume path\to\resume.pdf
python -m src.main parse-resume path\to\resume.pdf --json
python -m src.main match path\to\resume.pdf
python -m src.main match path\to\resume.pdf --job-id 42
python -m src.main match path\to\resume.pdf --json --limit 20
python -m src.main match path\to\resume.pdf --origin "Rishon Lezion, Israel"
python -m src.main list --status new
python -m src.main status 42 applied
python -m src.main export-safe-profile path\to\resume.pdf
python -m src.main tailor-resume path\to\resume.pdf 42 output --change professional_format --approve
```

Set `RESUME_DIRECTORY` to the folder containing resumes, then start the local
dashboard with `uvicorn src.web.app:app --reload` and open
`http://127.0.0.1:8000`. Dashboard matching accepts only files inside that folder,
uses POST forms with CSRF protection, and does not put file paths in URLs. The
dashboard shows each factual change with its original text, proposed text, and
supporting evidence. Only checked changes are applied. Approval creates separate,
professionally formatted DOCX and PDF files in `RESUME_DIRECTORY\tailored`; the
master resume is never changed. The dashboard has no user authentication and must
not be exposed to the public internet.

Each job page also has a local outreach workspace. Add a public professional
profile you found yourself, review its explainable relevance score, preview a
factual message, and explicitly approve the draft. Contact notes and approved
drafts are stored in `data/outreach.db`, separate from cloud job data. The app
never fetches LinkedIn pages or sends a message; open the profile and send the
approved text manually.

Use custom paths if needed:

```powershell
python -m src.main scan --config config/companies.yaml --database data/jobs.db
```

### Optional email summary

Pass `--email-summary` to send one post-scan summary via SMTP. This is opt-in;
without the flag no email is sent. Configuration is exclusively through process
environment variables, never configuration files: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_TO` are required.
`SMTP_USE_TLS` defaults to `true` and may be set to `false` only for SMTP servers
that do not support STARTTLS. Do not commit these values.

The repository includes a scheduled GitHub Actions workflow. It requires a
`DATABASE_URL` repository secret for a persistent PostgreSQL database; without
it, the workflow fails before scanning rather than silently using the runner's
ephemeral SQLite filesystem. For Supabase, use its PostgreSQL connection string
with `sslmode=require` (for example,
`postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require`) and keep the
entire value in the secret store. Do not print or commit it. Add the SMTP values
as repository secrets only when email delivery is desired. The workflow container
has Chromium and its Playwright dependencies installed.

For ranked cloud emails, store the one-line output of `export-safe-profile` as the
`CANDIDATE_PROFILE_JSON` repository secret. It excludes name, email, phone, and
free-form resume prose.

GitHub Actions cron expressions use UTC and do not support a per-schedule time
zone. The workflow schedules both possible UTC offsets for each target hour,
then uses the runner's `Asia/Jerusalem` time-zone data to run scans only during
the `08:00`, `11:00`, and `16:00` Israel hours. This keeps the intended times stable across Israel
daylight-saving changes. A manual **Run workflow** dispatch bypasses this gate.
GitHub may delay scheduled workflow starts during periods of high load; the gate
does not turn delayed GitHub execution into a real-time scheduling guarantee.

Run tests with:

```powershell
python -m pytest
```

The command initializes the SQLite database, reads the company configuration,
visits each enabled page, and stores unique job links. Add `--headed` to watch the
browser or `--timeout-ms 60000` for slow sites.
Use `--max-pages 1` to restrict each company to its first results page.

Microsoft, Google, Amazon, Apple, Intel, NVIDIA, monday.com, and Mobileye are
enabled. Intel and NVIDIA use a dedicated Workday JSON adapter; Mobileye uses
its official Lever student listing. Wix, Check Point, CyberArk, Qualcomm, SAP,
AppsFlyer, and Similarweb remain documented but disabled until an enumerable
official feed or dedicated adapter is verified end to end.

Filtering is deliberately conservative and explainable. A role must have a
software-oriented title, must not contain a clear senior-level marker, and must
show early-career evidence in its title or fetched job details. Career-site
markup changes can still require updates to URL patterns or extraction logic.

Matching is local, deterministic, and explainable. It uses recognized skill
coverage plus evidence in experience, projects, and education. Scores are useful
for ranking, not a guarantee of recruiter decisions. Tailoring output is advice
only and must remain truthful.

Location eligibility is separate from professional fit. The matcher reads an
explicit `City, Country` line from the resume header, or accepts `--origin` as a
private runtime override. Remote roles are accepted, clearly foreign roles are
rejected, driving estimates up to 90 minutes are suitable, 91-120 minutes are
borderline, and anything longer is rejected. Unknown locations require manual
review and are not recommended automatically. Driving estimates use the public
OpenStreetMap Nominatim and OSRM services for low-volume personal use; they do
not include live traffic. The resume origin is never written to match JSON or the
jobs database. Use `--skip-commute-check` only when an explicit manual location
review is intended. JSON output keeps verified recommendations in `matches` and
unverified locations in a separate `manual_review` list.

Running while the computer is off does not require an external AI API. The planned
deployment is a scheduled container that runs scanning in the cloud and writes to
persistent storage. Resume parsing and matching remain local by default for
privacy. See `docs/cloud-roadmap.md`.

## Roadmap

1. **Foundation (complete):** config, models, SQLite storage, dry-run scanner.
2. **Career-page browsing (complete):** Playwright browsing, configurable link
   extraction, persistence, and clear error handling.
3. **Role filtering (complete):** bounded pagination, detail-page extraction, and
   explainable student, intern, junior, and non-senior software-role filtering.
4. **Resume parsing (complete):** extract contact details, normalized skills,
   education, experience, projects, and summary locally.
5. **Matching (complete):** explainable 1–100 job/resume fit scores.
6. **Tailoring suggestions (complete):** precise, evidence-backed resume advice.
7. **Approved document generation (complete):** per-change approval creates
   matching DOCX and PDF files; existing files are never overwritten.
8. **Notifications (complete):** SMTP summaries support safe-profile scoring and
   commute filtering in scheduled runs.
9. **Application assistance:** prepare applications, never submit without approval.
10. **Professional outreach (preparation complete):** manually add public contacts,
    rank them, and approve drafts; discovery and sending remain manual.

## Safety principle

The system may recommend and prepare actions, but resume rewrites and application
submission require two separate explicit approvals. Approving resume changes does
not authorize submitting an application.

Resume parsing is entirely local. Text-based PDFs are supported; scanned-image
PDFs require OCR, which is not included yet. Section and skill extraction uses
transparent rules, so uncommon headings or technologies may need new aliases.
