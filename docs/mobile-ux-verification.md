# Mobile verification and safe home-screen preparation

Source baseline: `64554b1`, 31 October 2026. All browser work used disposable
synthetic jobs, notes and a demo resume on random loopback ports. External browser
traffic was blocked. No user notes/status/resume, cloud DB or live mail changed.

## Observed issues and changes

| Observation | Change | Scope |
| --- | --- | --- |
| Phone search input computed at 13.6px, note/status controls below 16px | Editable phone controls use 16px; viewport still permits user zoom | Reduces the known iOS focus-zoom risk; real keyboard behavior needs device testing |
| Detail navigation targets measured 33.5px high | 44px mobile targets; disclosure markers retained | Phone layouts only |
| Needs review's only link was 28px high | Entire existing metric card is a semantic link to the same New filter | No extra vertical space or filtering changes |
| Note positioning used layout height only | Size/position against VisualViewport offsets and dimensions; listen to resize/scroll | Keeps a scrollable editor in the visible area; preserves drafts and existing Save/Cancel behavior |

The Balanced cards, Applied → New → Reviewed ordering, ranking, eligibility and
collection logic are unchanged. Existing responsive filters remain collapsed on
phones. No dashboard endpoints, Host/Origin/CSRF protections or auth settings changed.

## Measurements and coverage

Chromium before/after with identical synthetic data:

| Width | First job before | First job after | Page overflow |
| --- | ---: | ---: | --- |
| 320 | 773.7px | 773.7px | None |
| 375 | 720.1px | 720.1px | None |
| 390 | 687.1px | 687.1px | None |
| 430 | 687.1px | 687.1px | None |
| 768 | 675.0px | 675.0px | None |
| 1280 | 715.4px | 715.4px | None |

New browser tests run in Chromium and WebKit with touch-enabled iPhone-size
contexts: ordering, 320/375/390/430 layouts, note draft/cancel/save/reload, failed
save retention, plain-text injection handling, status updates, Dismiss/Undo/Restore,
multi-select filters, safe external links, simulated keyboard/zoom geometry and
fallback without Popover/VisualViewport APIs. Existing browser regressions cover
768/1280/1440 desktop/tablet sizes, focus/reduced motion and real same-origin form
headers. CI installs both engines so WebKit coverage is not silently absent.

WebKit on Windows/Linux is not Safari on a physical iPhone. Real keyboard toolbar,
pinch zoom, safe areas and Home Screen installation remain device acceptance items.
No login/logout/session-expiry tests can establish cloud readiness yet: the current
app has no authenticated session implementation. See the separate security design.

## Home screen and privacy

Both pages include local manifest/theme/Apple metadata and local PNG icons.
Manifest start/scope/ID are `/`, without job IDs, queries or private data. No
service worker, offline store, private response cache or remote font was added.
Existing `Cache-Control: no-store`, CSP and external-link protections remain.
The icon source is the repository SVG; PNG exports are 180, 192 and 512 pixels.

This does not make the desktop server available to a phone with the PC off.
Home Screen use still requires network access and an independently hosted,
approved HTTPS/authenticated service. No public access was enabled.

## Reproduce

Use a clean disposable checkout without private config/data, and unset cloud/SMTP
configuration before importing the default app. Do not run these commands from
the personal runtime: the default app initializes local stores on import. From
that disposable checkout, install the test browsers with
`python -m playwright install chromium webkit`; run
`python -m pytest tests/test_mobile_workspace_browser.py tests/test_web_app_metadata.py`.
The separate existing web/security/browser regression suite must also pass.

Synthetic before/after screenshots are local audit artifacts, intentionally not
published with personal runtime state. Production data migration and hosting are
not part of this PR; see [cloud readiness](mobile-cloud-readiness.md).
