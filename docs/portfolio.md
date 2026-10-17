# AJob — Resume and Interview Notes

## Personal Projects entry

**AJob — Automated Job Discovery and Review Platform**

- Built a Python/FastAPI workspace that combines browser and ATS-feed discovery,
  canonical-URL deduplication, and persistent PostgreSQL/SQLite storage.
- Implemented explainable resume matching and eligibility filters, keeping resume
  processing local and requiring approval before generating application artifacts.
- Coordinated scheduled cloud scans and SMTP notifications with persistent delivery
  slots, retry handling, and security-focused automated tests.

**Technologies:** Python, FastAPI, Playwright, PostgreSQL, SQLite, Docker,
GitHub Actions, Supabase, Jinja2, JavaScript, pytest.

## Interview discussion points

- Why cloud scheduling and persistent storage are separate from the local UI.
- How canonical URL identity prevents duplicate discoveries while preserving status.
- How delayed schedules map to intended Israel-time slots across DST transitions.
- Why SMTP delivery is at-least-once rather than an exactly-once promise.
- How a reduced cloud profile differs from raw resume/contact data.
- How tests enforce certificate validation, local origin boundaries, and approvals.

The screenshots use fictional data. Avoid presenting synthetic scores, configured
company counts, or test counts as performance or placement outcomes. Add the public
repository URL only after publication is explicitly approved.
