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
- Do not treat navigation labels such as "Learn more" or "Apply now" as locations.
  Store `Not specified` when location evidence is unreliable.
- Isolate failures per company so one slow, changed, or blocked site does not abort
  the entire scan.
- Bound pagination and page timeouts. Deduplicate jobs by canonical URL across pages.
- Confirm that listing content actually changed after clicking a pagination control;
  a visible or enabled next button does not prove navigation succeeded.
- Do not claim a source is supported when its page exposes no usable job data.
  Disable it explicitly and document the need for a dedicated adapter.

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

## Data safety

- Use idempotent URL-based upserts for discovered jobs.
- Never delete or rewrite an existing jobs database merely because filtering rules
  changed. Introduce an explicit migration or relevance state when needed.
- Treat legacy rows as relevance-unknown during schema migration. Do not silently
  rank old unfiltered discoveries as approved job matches.
- Resume changes and application submission must always require human approval.
- Keep match scoring deterministic and explainable before adding an LLM. Every
  point should map to visible resume/job evidence.
- Do not award fit points merely because a resume section exists. Experience and
  project points require explicit evidence tied to recognized job skills.
- Never turn a missing skill into a resume claim. Suggest learning it or adding it
  only when the user can support it truthfully.
- Do not include raw resume text or contact details in match output or logs.
- Keep resume matching independent of storage and network services so the same
  domain service can run locally, in a container, or behind a future API.
