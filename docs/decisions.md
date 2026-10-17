# Engineering Decisions

## Local browser boundary (2026-10-18)

Keep the existing single-user, loopback-only architecture. Add Host allowlisting,
same-origin mutation checks, CSRF, and anti-framing headers rather than presenting
the dashboard as a hosted multi-user product. This preserves the launcher and
forms; public hosting would need a separate authentication/authorization design.

## Portfolio and audit tooling (2026-10-18)

Generate screenshots in a disposable synthetic workspace and block external
browser requests. Install audit tooling in an isolated environment and report
redacted locations. Keep tooling out of the scanner's runtime requirements.
Security workflow proposals do not change the active cloud deployment until an
explicitly approved push.
