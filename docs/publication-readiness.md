# Publication Readiness — 2026-10-18

This document records local preparation, not an authorization to publish or a
guarantee that every runtime configuration is secure. The repository remains private.
No cloud scan, real email, secret replacement, history rewrite, push, or merge was
performed during this preparation.

## Security changes

- SMTP validates the certificate chain and hostname before authenticating;
  plaintext SMTP is limited to local development servers.
- The dashboard allows loopback Host values only, rejects cross-origin mutations,
  retains per-process CSRF tokens, and sets anti-framing, no-sniff, no-referrer,
  no-store, and Content Security Policy headers. It remains a local single-user
  workspace, not an internet-facing authenticated service.
- Git exclusions cover credentials, private documents, local stores, backups,
  logs, and existing scratch files. Existing personal/untracked files were not
  deleted. The local credential-import helper is explicitly excluded.
- Docker configuration is allowlisted to `config/companies.yaml`; local credentials
  and settings are excluded from the build context.
- Official GitHub Actions references are pinned to verified commit IDs and checkout
  credentials are not persisted. A separate security workflow performs offline
  secret checks and dependency advisory checks; Dependabot configuration is added.

Workflow changes are validated through an owner-approved normal push. GitHub's
security settings and production secrets are not modified by these source changes.

## Owner approvals and publication gate

On 2026-10-18 the owner explicitly approved retaining the original Git history,
including author/committer email addresses, and approved a normal commit/push to
private `main` after validation. The addresses are not reproduced here. No history
rewrite or secret replacement is authorized or necessary based on the audit.

The earlier AGENTS.md efficiency update is a separate local change and is excluded
from the security/portfolio commit. Personal files and scratch output stay ignored.

Before a visibility change:

- Require successful CI, security, and container checks for the pushed commit.
- Inspect existing scheduled runs read-only; do not dispatch production scans or mail.
- Consider branch protection, secret scanning/push protection, and dependency alerts
  where supported. No repository setting is changed by this preparation itself.
- Obtain explicit owner approval to make the repository public. That approval has
  **not** been given.

Push events trigger only CI and security checks. The container smoke test calls
`scan --dry-run` before storage initialization; it does not fetch job sites, access
production data, or send mail. The scheduled workflow has only schedule and manual
triggers. Future normally scheduled runs use the latest main code as before.

## Known boundaries

- Read-only inspection before the private-main push found expanded scan run
  `39022056163` cancelled at the 75-minute job limit (2026-10-17). A later scan
  `39022998010` also ended cancelled. Recent green cron runs include DST-inapplicable
  skips, which are not evidence of completed discovery or email delivery. This
  pre-existing operational issue requires a focused diagnosis before claiming
  reliable completion across all configured sources; no production scan was
  dispatched or timeout/schedule changed during this preparation.
- The Supabase audit connector's OAuth refresh was unavailable during the final
  read-only run check, so the current delivery ledger could not be revalidated.

- Package advisories change over time. A clean pip-audit result is scoped to the
  audited installed versions, not every version permitted by dependency ranges.
- The cloud DATABASE_URL secret cannot be read through GitHub's secret API; the
  scanner's effective database role and live TLS settings were not revalidated.
- Database least privilege, Supabase RLS/Vault configuration, token renewal, SMTP
  delivery, and scheduler behavior remain operational responsibilities. Production
  changes and end-to-end sends require a separately approved check.
- The Docker daemon was unavailable locally, so an image build/run could not be
  completed. Packaging regression tests check the config allowlist and ignore rules.
- The FastAPI test client emits an upstream deprecation notice about httpx. It is
  not a security failure; changing HTTP clients is outside this preparation.
- Screenshots are generated from fictional data with external requests blocked.
  They show interface behavior, not live opportunities or measured outcomes.

## Verification results

- **Automated tests:** final isolated full-suite run: 289 passed; one Windows
  symlink-privilege test skipped. The temporary harness includes public config
  fixtures and explicit pytest discovery boundaries. No production data was used.
- **SMTP/browser security:** focused regression tests passed; no real SMTP login,
  message, or recipient update was performed.
- **Dependencies:** pip-audit 2.10.1 initially reported advisories in local pip
  25.0.1 and pytest 8.4.2. Upgraded locally to pip 26.2.1 and pytest 9.1.1;
  the follow-up audit covered 45 installed distributions with zero known findings.
  Requirements, CI, and container bootstrap constraints now require fixed versions.
  The cloud's existing environment was not changed.
- **Secrets:** detect-secrets 1.5.0 (27 detector plugins) checked tracked/selected
  nonignored working-tree source and all 345 blobs reachable from publication
  branches/remotes/tags across 39 commits. No unresolved secret findings. Two exact
  example fingerprints are exempted: the synthetic SMTP password and the historical
  README connection placeholder. Detector self-checks distinguish ordinary code
  from a synthetic provider token; credential verification is disabled.
- **Privacy:** all four PNGs were visually reviewed and contain only synthetic
  content. The owner explicitly approved retaining existing author-email metadata.
- **Packaging/docs:** ignore-rule/config-copy regression tests, YAML parsing,
  README local-link checks, `pip check`, and `git diff --check` passed.
- **Launcher:** the actual desktop shortcut was launched; GET-only checks verified
  HTTP 200, anti-framing/no-store headers, and HTTP 400 for an untrusted Host.
- **Independent review:** no material findings remained; the missing readiness
  link identified during review was added.

**Publication gate:** source and demo preparation are complete within the audited
scope, and the owner accepted the original history's author metadata. Publication
requires green CI/security/container checks on the private main commit and a
separate explicit visibility approval. Consult that commit's Actions results for
post-push validation; this report does not claim production email delivery was tested.
