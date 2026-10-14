# Project Best Practices

This file records durable lessons discovered while building the AI Job Search
Agent. Read it before making changes and update it when a new failure mode or
reusable lesson appears.

## Environment and verification

- Do not assume `python` is globally available. Use the project `.venv` after
  setup, and locate the workspace runtime when bootstrapping it.
- Installing the Playwright Python package does not install browser binaries. Run
  `playwright install chromium` and verify the executable can launch.
- A timed-out Playwright installation may leave registered but incomplete browser
  directories. Confirm installation by launching Chromium, not only by listing it.
- Keep generated SQLite databases, virtual environments, caches, and bytecode out
  of Git.
- In PowerShell conditions, wrap cmdlet calls such as `Test-Path` in parentheses
  before combining them with `-and` or `-or`; otherwise the operator may be parsed
  as a cmdlet parameter.

## Agent workflow and context

- Define observable success criteria before non-trivial changes and stop when they
  are met. Do not add speculative architecture for later phases.
- Keep changes surgical and avoid unrelated refactors or formatting churn.
- Load context progressively: start with applicable instructions and directly
  relevant files, then expand based on evidence.
- Preserve context headroom for validation and failures by bounding searches, file
  reads, logs, and test output.
- Use Caveman only as an opt-in terse response skill. Do not use its proxy in this
  project unless it is separately approved and benchmarked.
- Do not stack LeanCTX, Headroom, or other context-transforming proxies without an
  isolated comparison of correctness, privacy, reversibility, and actual savings.

## Career-site scraping

- Career-site URL structures and rendered markup change. Keep job URL patterns in
  configuration and verify them against live rendered links before relying on them.
- Allowlist job-link hosts per company, including explicit external ATS hosts.
  Matching a URL path alone is not enough to make an external link safe to fetch.
- Some valid job anchors have no visible text. Use a deterministic URL-slug title
  fallback, while preferring rendered titles when available.
- ATS listings can link to the same job from both its title and a generic `Apply`
  button. Deduplicate by URL while retaining the most descriptive anchor text;
  first-link-wins can silently discard the real title.
- Do not treat navigation labels such as "Learn more" or "Apply now" as locations.
  Store `Not specified` when location evidence is unreliable.
- Isolate failures per company so one slow, changed, or blocked site does not abort
  the entire scan.
- Bound pagination and page timeouts. Deduplicate jobs by canonical URL across pages.
- Canonicalize query parameter ordering and encoding before deduplication. `%20`
  and `+` can represent the same query value and must not create duplicate jobs.
- Confirm that listing content actually changed after clicking a pagination control;
  a visible or enabled next button does not prove navigation succeeded.
- Do not claim a source is supported when its page exposes no usable job data.
  Disable it explicitly and document the need for a dedicated adapter.
- Workday search landing pages can render no job anchors even when individual
  job-detail URLs are public. Verify rendered listing anchors before enabling a
  Workday source; otherwise keep it disabled pending a dedicated adapter.

## Relevance filtering

- Separate cheap title-based prefiltering from final classification based on full
  job details. This avoids opening clearly irrelevant roles without assuming every
  unlevelled software title is junior.
- Every relevance decision must have a human-readable reason.
- Match classification terms on word or phrase boundaries. Raw substring checks can
  confuse words such as `internally` with `intern` or `leadership` with `lead`.
- Require positive early-career evidence and reject clear seniority markers.
- Test both false negatives and false positives, especially titles where words such
  as "developer" describe a program or audience rather than the job itself.
- Boundary-aware matching still requires explicit common inflections. Test plural
  program titles such as `Internships`; a singular `internship` rule will not match
  them by design.
- Do not treat prose such as "mentor junior team members" as evidence that the
  role itself is junior. Detail-page early-career markers need candidate context.

## Data safety

- Use idempotent URL-based upserts for discovered jobs.
- Never delete or rewrite an existing jobs database merely because filtering rules
  changed. Introduce an explicit migration or relevance state when needed.
- Treat legacy rows as relevance-unknown during schema migration. Do not silently
  rank old unfiltered discoveries as approved job matches.
- Resume changes and application submission must always require human approval.
- Treat resume-change approval and application submission as separate consent
  events. A generated resume never authorizes sending an external application;
  show the complete form and require a final submission approval.
- Application-package approval must be short-lived and single-use. Recompute its
  fingerprint from the current stored job and current resume file hashes before
  persistence; never trust paths, profile fields, or hashes posted back by the browser.
- Enumerate application files server-side and expose only opaque pair identifiers in
  forms. Re-enumerate on submission so stale, forged, nested, or symlink-escaped paths
  never become trusted merely because the browser posted them.
- Never trust proposed resume text or scores posted by the browser. Recompute the
  proposal from the stored job and allowed local resume, and accept only stable
  server-known change IDs.
- Preflight every requested output and publish a DOCX/PDF pair from temporary
  files only after both pass structural verification. A partial export must not
  look like a completed application artifact.
- Local-only web interfaces still need CSRF protection. Never place resume paths
  in query strings, and restrict dashboard file access to an explicit directory.
- Every job-detail response must rebuild the application and outreach workspace
  context. POST responses that render the page directly must not silently disable
  unrelated sections by omitting their template state.
- Storage adapters must agree on canonical URL identity. Legacy canonicalization
  may update unambiguous rows, but duplicate conflicts require a manual merge and
  must never be silently deleted.
- Keep match scoring deterministic and explainable before adding an LLM. Every
  point should map to visible resume/job evidence.
- Do not award fit points merely because a resume section exists. Experience and
  project points require explicit evidence tied to recognized job skills.
- Never turn a missing skill into a resume claim. Suggest learning it or adding it
  only when the user can support it truthfully.
- Do not include raw resume text or contact details in match output or logs.
- Keep resume matching independent of storage and network services so the same
  domain service can run locally, in a container, or behind a future API.
- Validate parsing against the rendered resume, not extracted text alone. Styled
  PDF text can split a person's name or heading into separate extraction lines.
- PDF text extraction does not preserve hyperlink annotations. Extract and carry
  explicit LinkedIn, portfolio, and project URLs separately so regenerated resumes
  do not silently replace useful links with dead labels.
- Calibrate the canonical skill dictionary with real resume vocabulary, but keep
  aliases boundary-aware to avoid substring false positives.
- Treat hard eligibility requirements separately from fit score. A strong skill
  overlap must not rank a PhD-only role above an eligible bachelor's-level role.
- Enforce a degree-field mismatch only for explicit closed hard requirements and
  recognized candidate majors. Wording such as "related field" or "equivalent"
  must remain eligible for human review instead of causing a false rejection.
- Keep commute eligibility separate from professional fit. Never recommend a job
  when its location or route cannot be verified; mark it for manual review.
- Driving estimates must name their routing source and limitations. Do not present
  an OSRM estimate as live-traffic travel time.
- Extract a candidate location only from an explicit resume-header location line.
  Do not persist the home location, coordinates, or route details in the jobs DB.
- Detail pages can contain a reliable location even when listing cards do not.
  Prefer explicit accessibility markers such as `place` over guessing from prose.

## Notifications and scheduled runs

- Keep SMTP settings exclusively in environment variables or secret stores; do not
  place credentials in YAML, source code, images, or test fixtures that resemble
  real credentials.
- Make notification delivery explicit and test SMTP interactions with injected
  clients. A scheduled GitHub-hosted runner has ephemeral local storage, so it
  cannot reliably determine which jobs are new without a persistent store.
- A cloud workflow whose purpose includes email must fail visibly when any SMTP
  secret is missing. Do not silently fall back to a successful no-email scan.
- Cloud matching should use a privacy-reduced profile containing only location,
  canonical skills, section-level skill evidence, and education level. Never put
  contact details or raw resume prose in repository files or workflow logs.
- GitHub Actions cron expressions are evaluated in UTC and do not follow Israel
  daylight-saving transitions. For fixed Israel local scan times, schedule both
  possible UTC offsets as distinct cron entries and gate using the intended
  `github.event.schedule` slot, not the runner's actual start hour. Otherwise a
  delayed GitHub runner can skip every expensive step while reporting success.
  Manual dispatch must bypass that gate. GitHub may still delay starts, so do not
  represent this as a hard real-time guarantee.
- Avoid scheduling GitHub Actions at minute zero. Hosted cron load can delay or
  drop top-of-hour runs; use a non-zero minute and verify actual run creation,
  not merely that the workflow is active.
- Give each scheduled notification a stable local-time slot stored in the
  persistent database. Retry from a second cron and atomically claim the slot so
  a successful primary run turns the retry into a no-op. Treat SMTP delivery as
  at-least-once: a network failure after server acceptance can still duplicate a
  message, so never promise mathematically exact-once email.
- Treat hosted-runner deprecation annotations as maintenance work even when a run
  succeeds. Keep first-party actions on a runtime-supported major version.

## Career platform adapters

- Workday listing pages may expose no useful rendered anchors while their public
  careers JSON endpoint remains available. Keep that API handling in a dedicated
  adapter and validate the tenant, site, returned paths, and allowed host.
- A search-engine result is evidence that a job detail page exists, not proof that
  the configured listing can enumerate it. Keep sources disabled until the actual
  listing path or official feed is verified end to end.
- Require a persistent `DATABASE_URL` for scheduled cloud scans; do not silently
  fall back to runner-local SQLite. GitHub Actions scheduled cron is UTC and does
  not support a per-schedule timezone field, so document daylight-saving effects.
- Do not make automatic PostgreSQL migrations delete or merge existing job rows.
  URL normalization must preserve user-managed lifecycle status and audit history.

## Professional outreach

- Do not scrape LinkedIn, automate logged-in browsing, or send connection requests
  and messages. Accept user-supplied public profile URLs and keep sending manual.
- Store outreach contacts separately from scheduled cloud job data. Minimize
  personal data, strip tracking query parameters, and never fetch profile URLs.
- Treat draft approval and actual sending as separate consent events. Recompute
  drafts from server-known job/contact facts and bind approval to a short-lived
  fingerprint so stale previews cannot be approved.
- Treat edited outreach text as user-authored. Validate its type and length,
  require explicit approval of the exact text, preserve that approved snapshot,
  and store copied/sent/replied events separately without inferring delivery.
- Search shortcuts may construct public search-engine URLs but must never fetch,
  parse, or enrich profile results. Opening a result and selecting a contact stays
  under the user's control.
- Add a `.dockerignore` before building locally; build context can otherwise carry
  resumes, databases, environment files, and repository history even when the
  Dockerfile does not copy those paths into the final image.
