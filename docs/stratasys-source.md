# Stratasys source verification — 31 October 2026

## Source and scope

The employer was absent from all 51 configured entries (50 enabled) on base main
`64554b1`, which includes PR #14. The new entry enables the employer's official
all-jobs search: <https://careers.stratasys.com/search/>. It does not duplicate
Objet/Stratasys under another configured employer name.

No public JSON API was established. Two public official surfaces were verified:

- <https://careers.stratasys.com/sitemap.xml>: machine-readable job URL discovery.
- The server-rendered search: active listing membership, exact titles, explicit
  locations and result-range pagination, 25 postings per page today.

The engineering page advertises `/services/rss/category/?catid=8908700`, but
robots.txt disallows `/services/`. That RSS was not requested. No challenge,
authentication or CAPTCHA bypass is implemented.

The adapter reads the sitemap and enumerates the complete allowed search within
its page budget, then fetches scoped details only for preliminary role candidates.
Listing-only/sitemap-only differences are observable metrics, not grounds for
discarding new listings or declaring old jobs closed. No closure snapshot is
emitted and no Auto-Dismiss behavior is enabled or changed.

## Live observations

One read-only adapter validation returned:

| Metric | Observed |
| --- | ---: |
| Active listing rows / unique posting IDs | 62 / 62 |
| Listing pages | 3 |
| Sitemap unique jobs | 62 |
| Listing/sitemap membership differences | 0 |
| Preliminary candidates with fetched details | 3 |
| Requests, including sitemap and details | 7 |
| Role-relevant jobs | 1 |
| Role-relevant jobs with verified Israel location | 1 |

These are source-verification counts, not production discoveries or a forecast of
future openings. No database, email or production scan command was involved.
Sitemap lastmod values were identical batch metadata and are not used as posting
or discovery dates.

## Verified role and eligibility

[Software Embedded Student](https://careers.stratasys.com/job/Rehovot-Software-Embedded-Student-IL/1444873900/)
remained present in both the active listing and sitemap and returned its actual
detail page, including requisition **55642**. The URL's stable posting identity is
**1444873900**. The role is C/C++ product/embedded development, not manual QA.

The adapter returns `Rehovot, IL, Israel` from the explicit SuccessFactors location
field. Country-code expansion applies only to the trailing country slot of the
observed city/region/country format: `Chicago, IL, US` remains foreign. Missing
locations remain `Not specified`; URL city names are never treated as evidence.

The unchanged role prefilter, final classifier, description validator and Israel
location predicate accept this posting. Source-specific canonical section labels
are added before exact `Must have for this role:` and `Advantages:` headings,
preserving source wording and keeping advantages optional for shared extraction.

Candidate prerequisites still include strong C/C++ knowledge, embedded/integration
familiarity, at least two study years remaining and three morning working days.
The user's general study/availability information appears compatible but is not a
final hiring/eligibility decision. Synthetic fixtures establish that:

- Expected graduation year 2028 alone stays `potentially_eligible` because the
  exact two-year calendar threshold is unresolved.
- An explicit date at or after 2028-10-31 is accepted at the frozen test date
  2026-10-31; 2027 is rejected.
- C/C++ proficiency, real project evidence, morning availability and an agreed
  interpretation of academic versus calendar years still require human review.

No personal resume, reduced cloud profile or secret was read/modified to force a
match. No shared filtering or eligibility rules were relaxed.

## Identity, failures and bounds

- Deduplicate desktop/mobile links and page results by numeric posting ID.
  Persist through the existing canonical-URL repository; upserts retain manual
  status. Requisition and original official URL are retained, without schema edits.
- Validate listing/detail title and URL identity. Missing essential title,
  requisition or description fails visibly; missing location remains unknown.
- Reject external/disallowed redirects, malformed sitemap/counts, moving totals,
  repeated pages and incomplete pagination; never turn a budget cutoff into full
  success. No automatic retry on rate limits or access errors.
- Bounds: five listing pages, twelve candidate details, 2 MB per response and
  request timeout at most fifteen seconds. A ninety-second cooperative budget is
  checked during streamed body reads and before completion. The existing parent
  worker supplies hard process termination for socket/header stalls; it is not
  replaced by an incorrect claim of a strict ninety-second network SLA.
- All network failures remain isolated by the existing official-source dispatcher
  and source worker. Another source can continue after this source fails.

## Validation and integration

Synthetic fixtures cover SuccessFactors structure, alternate/future roles,
pagination, duplicates, invalid/missing fields, retained manual status, location,
eligibility, redirects, HTTP429, source isolation, byte/time/detail/page budgets.
They never contact the employer, cloud DB or SMTP. A fresh independent reviewer
approved the implementation after its deadline finding was fixed.

Local results: 35 source-specific tests passed; the relevant regression selection
passed 228 tests; the complete suite passed 807 tests with 65 environment-dependent
skips. A final source/fixture rerun passed after public-URL scanner false positives
were narrowly annotated. Offline secret audit inspected 182 tracked/new files with
zero remaining findings. The live final adapter completed its seven requests in
about 6.3 seconds. PR CI/Security results are the publication gate, not inferred
from these local skips.

The only dashboard-related edit is the test's literal configured-source count
from 50 to 51; no dashboard/UI/authentication/notes/launcher/runtime/deployment
code is changed. Configuration and that test may overlap with the parallel chat
and must be reconciled before merge. The earlier employer research report stays
untouched in its separate worktree.

Before production adoption: merge only after integration with the parallel work,
confirm a subsequent already-scheduled scan's new-source metrics, and keep the
candidate's exact graduation date, morning availability and practical C/C++ skills
reviewable. This PR intentionally does not trigger a production scan, migrate a
database, change user statuses/notes, send email or enable Auto-Dismiss.
