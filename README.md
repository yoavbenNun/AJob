# AI Job Search Agent

AI Job Search Agent is a local-first assistant for discovering relevant student,
intern, and junior software roles. The long-term goal is to compare roles with a
resume, explain fit, and prepare tailored application material while keeping all
rewrites and submissions behind explicit human approval.

## Current scope: Phases 5 and 6

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
- human-readable and stable JSON matching output
- isolated per-company failures and SQLite persistence
- a dry-run mode that prints targets without launching a browser

It does **not** modify resumes, call paid APIs, deploy itself to the cloud, or
submit applications.

## Architecture

The project uses a small layered structure:

- `config/`: user-editable target company definitions
- `src/models.py`: domain objects shared by the application
- `src/config_loader.py`: YAML-to-domain-model boundary
- `src/storage/`: SQLite persistence
- `src/scrapers/`: Playwright browsing and normalized job-link extraction
- `src/matching/`, `src/resume/`, `src/notifications/`: reserved for later phases
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
```

Use custom paths if needed:

```powershell
python -m src.main scan --config config/companies.yaml --database data/jobs.db
```

Run tests with:

```powershell
python -m pytest
```

The command initializes the SQLite database, reads the company configuration,
visits each enabled page, and stores unique job links. Add `--headed` to watch the
browser or `--timeout-ms 60000` for slow sites.
Use `--max-pages 1` to restrict each company to its first results page.

Microsoft and Google are the currently verified sources. Wix remains in the YAML
as a disabled example because its positions page does not expose job links in the
rendered anchor markup; it needs a dedicated data adapter in a later phase.

Filtering is deliberately conservative and explainable. A role must have a
software-oriented title, must not contain a clear senior-level marker, and must
show early-career evidence in its title or fetched job details. Career-site
markup changes can still require updates to URL patterns or extraction logic.

Matching is local, deterministic, and explainable. It uses recognized skill
coverage plus evidence in experience, projects, and education. Scores are useful
for ranking, not a guarantee of recruiter decisions. Tailoring output is advice
only and must remain truthful.

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
7. **Approved document generation:** require review before producing a tailored resume.
8. **Notifications:** optionally send new-job summaries through Telegram or email.
9. **Application assistance:** prepare applications, never submit without approval.

## Safety principle

The system may recommend and prepare actions, but resume rewrites and application
submission must always require explicit human approval.

Resume parsing is entirely local. Text-based PDFs are supported; scanned-image
PDFs require OCR, which is not included yet. Section and skill extraction uses
transparent rules, so uncommon headings or technologies may need new aliases.
