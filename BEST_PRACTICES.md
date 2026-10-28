# Project Best Practices

This file records durable lessons discovered while building AJob. Read it before
making changes and update it when a new failure mode or
reusable lesson appears.

- A successful empty ATS feed does not prove career-site coverage. Verify the
  employer's current listing links after recruiting migrations, and distinguish
  observed rejection decisions from filtering stages that were not evaluated.

- List and detail match views must use the same active resume revision and job
  evidence. Key presentation caches by both, never by job ID alone; test score
  reuse and invalidation when either source changes.

- Guard programmatic pytest runners with `if __name__ == '__main__'` on Windows;
  spawned test workers must not recursively start another complete pytest run.

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
- A Windows dashboard launcher should use the project virtual environment by
  absolute path and probe the local URL before starting Uvicorn. This avoids
  duplicate servers and port conflicts when the shortcut is clicked twice.
- A dashboard launcher should identify the current app through a stable health
  endpoint and exercise a representative detail route, not only the home page.
  Serialize concurrent launches and stop an unhealthy listener only when its PID
  was recorded by the launcher; never kill an unrelated process sharing the port.
- Include the app startup time in health and compare it with the dashboard source
  files, including JavaScript, in the Windows launcher. A healthy process can still
  serve stale templates or assets after an update; restart it only when the tracked
  AJob PID is behind.
- When a local launcher selects a configured storage backend, report that backend
  from the health endpoint and include it in readiness checks. Otherwise a healthy
  server can keep serving from the wrong database after configuration changes.
- A desktop double-click can create two launcher processes. Coalesce concurrent
  invocations with a non-blocking mutex and hold it briefly after opening the URL;
  a waiting mutex only serializes duplicate browser opens.
- Version local static asset URLs when shipping visible CSS changes. A reused
  browser tab can otherwise keep stale styling even after the server restarts.
- Keep non-ASCII local paths in a UTF-8 ignored settings file instead of shortcut
  arguments; Windows shortcut automation can silently corrupt Unicode arguments.

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
- Career sites may use plain `.pagination a.next` links without accessibility
  labels. Test real DOM pagination and ignore disabled links. Verify country
  filters from actual results: a location-looking query parameter may be ignored.
- When an acquired company's recruiting moves to its parent, represent the shared
  coverage explicitly and scan that board once instead of duplicating listings.
- Do not claim a source is supported when its page exposes no usable job data.
  Disable it explicitly and document the need for a dedicated adapter.
- Workday search landing pages can render no job anchors even when individual
  job-detail URLs are public. Verify rendered listing anchors before enabling a
  Workday source; otherwise keep it disabled pending a dedicated adapter.
- Workday employers use different early-career labels. Search a small bounded set
  of student, intern, junior, and graduate software phrases and deduplicate by URL;
  one deep `student software` query misses legitimate entry-level roles.
- Keep exactly one configuration entry per company. A later duplicate silently
  wins in name-indexed views and can make a verified source appear disabled.

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
- Hardware employers and accepted Electrical/Computer Engineering degrees do not
  establish a hardware role: classify responsibilities separately from candidate
  degree eligibility. Reject explicit RTL/ASIC/physical-design roles even when
  their tooling includes Python; inspect generic student titles for actual
  software-development responsibilities before accepting them.
- Candidate-context language such as "currently pursuing a degree" is reliable
  early-career evidence; a bare "pursuing" in company prose is not. Keep degree
  eligibility wording such as "related technical field" open to related majors.

## Data safety

- A dashboard database role needs job read access and lifecycle-status updates,
  not schema ownership. Verify required columns without DDL at dashboard startup;
  keep PostgreSQL schema migrations in the scanner's privileged initialization.
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
- Do not present formatting or reordering suggestions as score improvements.
  Show that the score stays unchanged and explain that only accurate new resume
  evidence, followed by a fresh match, can change it.
- Do not award fit points merely because a resume section exists. Experience and
  project points require explicit evidence tied to recognized job skills.
- Never turn a missing skill into a resume claim. Suggest learning it or adding it
  only when the user can support it truthfully.
- Resume uploads must be size-bounded, structurally validated, written through a
  same-directory temporary file, and published under a server-generated name.
  Preserve prior versions and never expose configured filesystem paths in HTML.
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
- A degree being pursued is not an awarded degree. Interpret qualifications in
  context, including completion, preferred requirements and student alternatives.
  Keep ambiguous eligibility reviewable and visible separately in dashboard/mail;
  never delete discoveries or change application status when matching rules change.
- Enforce a degree-field mismatch only for explicit closed hard requirements and
  recognized candidate majors. Wording such as "related field" or "equivalent"
  must remain eligible for human review instead of causing a false rejection.
- The default "relevant" dashboard must combine role classification, active
  resume eligibility, and a meaningful resume-match score; keep an explicit
  all-discoveries view for lower-fit and education-ineligible jobs. When resume
  matches are available, sort the displayed jobs by score descending. Isolate
  match failures per posting so one malformed job cannot disable the filter.
- Keep commute eligibility separate from professional fit. Never recommend a job
  when its location or route cannot be verified; mark it for manual review.
- Keep foreign discoveries in storage for auditability, but default the dashboard
  to explicit Israel and safe-remote locations. Provide an opt-in all-locations
  view instead of deleting historical rows.
- Driving estimates must name their routing source and limitations. Do not present
  an OSRM estimate as live-traffic travel time.
- Extract a candidate location only from an explicit resume-header location line.
  Do not persist the home location, coordinates, or route details in the jobs DB.
- Detail pages can contain a reliable location even when listing cards do not.
  Prefer explicit accessibility markers such as `place` over guessing from prose.
- Career pages often leak navigation labels and duplicate headings into captured
  descriptions. Remove only exact, known page-chrome lines for display, preserve
  every factual job line, and test that cleanup never rewrites stored source text.

## Notifications and scheduled runs

- Pass an explicit `ssl.create_default_context()` to SMTP STARTTLS before login;
  encryption without certificate and hostname validation does not authenticate
  the server. Keep plaintext SMTP restricted to loopback development servers.
- Loopback binding alone is not a browser trust boundary. Allowlist local Host
  values, reject cross-origin mutations, keep CSRF checks, and deny framing.
  Exercise these protections without calling real recipient or mail services.
- Portfolio screenshots must use disposable synthetic stores and resumes, remove
  cloud configuration before importing the default app, and block external browser
  traffic. Never capture the personal dashboard as a shortcut to demo assets.
- Test the secret scanner against ordinary source and a synthetic token before
  scanning history. detect-secrets' eager string API differs from its file API;
  use normal file scanning and bound redacted findings to avoid unusable output.
- When running pytest from an isolated temporary working directory, set explicit
  project root/confcutdir boundaries and copy required public config fixtures.
  Otherwise parent discovery can stall and relative fixture paths fail even when
  the code is correct; never solve isolation by connecting tests to real cloud data.

- Keep SMTP settings exclusively in environment variables or secret stores; do not
  place credentials in YAML, source code, images, or test fixtures that resemble
  real credentials.
- Store Windows-only local service credentials with current-user DPAPI encryption,
  exclude the encrypted file from Git, and decrypt only into the launched process
  environment. Do not place secrets in launcher command-line arguments.
- When launching Windows PowerShell from PowerShell 7, import its Security module
  from its own PSHOME explicitly; inherited module paths can break DPAPI cmdlets.
  Validate Supabase TLS with its official database CA and verify-full, rather
  than disabling certificate verification when the system trust store rejects it.
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
- When a local settings form updates a hosted secret, pass the secret value over
  standard input rather than process arguments so it does not appear in command
  history or process listings. Preserve the old value when validation or the
  remote update fails.

## Job detail presentation

- Condensed job summaries must stay source-faithful, preserve short technical and
  eligibility facts, and keep the complete captured source available behind an
  explicit disclosure. Never trade a hard requirement for cleaner typography.

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
- Show configured scan coverage from the same company configuration used by the
  scanner. Do not infer the source list from stored jobs: an enabled company with
  zero discoveries still runs, while paused companies must be labeled separately.
- Keep disclosure popovers in their owning `<details>` subtree so the summary and
  panel stay semantically associated. Use the Popover API for top-layer display,
  synchronize light-dismiss back to `details.open`, and keep an outside-click
  fallback for browsers without Popover API support.

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
- Reject missing or oversized upload request lengths before multipart parsing, and
  validate that upload directories are real descendants rather than links or
  junctions before writing sensitive resume files.

# Cloud schedule retries

- pg_net's pending request table can expose Authorization headers through
  platform-owned PUBLIC grants that project postgres cannot revoke. For requests
  carrying reusable credentials, use Supabase's synchronous `http` extension or
  move dispatch outside the database; never rely on a successful-looking REVOKE.
  Verify the installed extension's API with a real request: Supabase may lack
  newer upstream helpers such as `http_headers`; typed header arrays work.

- Independent dispatchers must pass the same canonical Israel delivery-slot key
  as GitHub cron, validated before scanning. A blank manual dispatch intentionally
  bypasses scheduled deduplication; never use it for automated retries. Keep the
  dispatcher credential restricted to repository Actions write access in Vault.

- GitHub cron can be delayed or dropped. Use the existing independent pg_cron
  watchdog and durable DB windows to select current work. Keep only one pending
  GitHub wake-up; do not queue every retry with `queue: max`. Coalescing wake-ups
  is safe only when the DB, rather than an individual event, owns pending work.
- Scan and mail retries are separate. Bound attempts/backoff; expire/fence leases.
  Protect scan claiming with a short transaction lock, not a session lock held
  for the whole scan: an expired but connected owner must not block later windows.

## Durable scan notification boundaries

- Commit each discovery and its outbox row atomically. Scan completion and SMTP
  acceptance are separate states. An interrupted DATA exchange is uncertain;
  never expire that state back to pending. Use frozen messages and reconcile it.
- Bound whole source workers, not just individual HTTP calls. Only the parent
  writes DB state, so killing a stuck browser cannot strand discoveries in memory.
- Session advisory locks require a session pooler or direct PostgreSQL connection;
  transaction pooling cannot safely provide this scanner ownership guarantee.

- Freeze batch membership atomically at preparation, including an empty batch.
  Freezing only the rendered message allows a concurrent retry to attach unseen
  jobs while another sender renders, silently consuming jobs absent from its mail.
- Validate PostgreSQL concurrency with separate connections/processes and injected
  transaction failures. Verify session-lock release through an actual session
  pooler; SQLite success or direct PostgreSQL tests alone do not establish it.
- Test rollback semantics, not only additive schema access: old job storage can
  remain compatible while its sender ignores the durable outbox. Pause/drain mixed
  versions and preserve pending/uncertain state; do not replay mail after restore.

- On client-rendered career sites, wait for the scoped search response rather than
  a fixed delay. Keep collection relevance separate from user application status.

- Workday listing locationsText can be an aggregate count. Read explicit detail
  location/additionalLocations and preserve semicolon-separated alternatives;
  neither a location count nor a URL city slug establishes geography.

- Prefer the complete public feed actually used by an employer career page over
  its paginated cards. Project only job fields; extended ATS metadata can contain
  unrelated staff details. Decode only verified city identifiers; unknown IDs
  remain unverified, and collection does not establish candidate eligibility.
