# Contact People (AJ-005)

## What works, and what does not

The local dashboard renders compact cards from explicitly reviewed contacts,
with source links, review dates, company/relevance labels, and initials when a
permitted local photograph is unavailable. It produces editable deterministic
drafts in three styles. Copying is a separate user action; opening a profile
does not send anything. There is no AI service, automatic LinkedIn access,
connection request, message delivery, or automatic draft persistence.

**CONTACT_DISCOVERY_DEPENDENCY remains open.** No authorized automatic people
discovery provider is configured. A missing evidence file produces the empty
state. The local file source is an operator-reviewed import, not proof that AJob
can discover people. Syntax validation cannot prove that a person's employment,
name or profile is true: the operator must check the cited evidence and reuse
permission. Never populate the file with guesses or synthetic demonstration data.

Existing contacts, private notes and approved outreach drafts in SQLite are not
deleted or migrated. Old manual relationship labels alone do not qualify.
The existing trusted evidence callback remains compatible, but is an internal
reviewed-source hook, not an automatic discoverer.

## Local reviewed source

By default the file is `contact_people.json` beside the local outreach database
(normally `data/contact_people.json`). A code-level `contact_people_path` argument
can select another local file. No file is created automatically. The file and
optional local avatars are ignored by Git. Keep them private, never in commits,
fixtures, screenshots of the real dashboard, or shared build contexts.

The only safe empty starting content is:

```json
{"version": 1, "contacts": []}
```

Each contact record requires:

| Field | Rule |
| --- | --- |
| job_url | Exact stored job URL; this is not a numeric local job ID |
| full_name | Independently checked full name |
| company | Must match the posting company |
| profile_url | Actual HTTPS LinkedIn personal /in/ URL |
| headline | Supported professional role, or empty |
| role_category | direct_team, role_relevant, recruiter, or company_employee |
| evidence_url | Public HTTPS provenance link without credentials, port or query secrets |
| association_basis | Specific explanation of evidence linking the person to this role/company |
| verification_status | reviewed, explicitly asserted by the operator |
| source_type | official_company_page, official_job_posting, or authorized_provider |
| reuse_permitted | true only after checking the source's conditions/permission |
| last_verified_at | Timezone-aware ISO date/time, no more than 30 days old |
| avatar_url | Optional /static/contact-avatars/filename.png, .jpg or .webp |
| avatar_reuse_permitted | true only if that local image may be reused |

Do not copy a LinkedIn photo just because it is publicly visible. No remote
avatar URLs are loaded or proxied. An unavailable photo falls back to initials.

Direct team evidence ranks ahead of other role-specific evidence, recruiters,
then company-only associations. A company employee is never relabeled as a team
member. Within a tier newer evidence wins, then supported headline and a stable
name/identity ordering. Normalized HTTPS profile identities deduplicate www,
permitted country-subdomain variants, tracking query parameters and trailing
slashes. Company/search URLs, HTTP, credentials, ports and disguised hosts fail
closed. We do not fetch profile or evidence URLs.

The source reads at most 512 KB and 200 records. An in-memory cache expires after
30 seconds, including deletions and failures; timestamps are rechecked on each
render. Expired, wrong-company, unreviewed or unsupported rows do not render.
A broken/unreadable JSON source shows source unavailable without breaking the
job page. Remove the local file to remove its contacts after at most 30 seconds.
No contact data is stored in the jobs database or sent outside the computer.

## Draft safety

The server resolves the selected contact from current evidence again; posted
names, headlines or company claims are ignored. Requests require the local CSRF
token and have a 4 KB body limit. Provider exceptions return generic errors,
never raw responses, credentials, paths or contact data in logs.

The short invitation uses a conservative **200-character** limit. This fits the
free-account note limit documented by [LinkedIn Help](https://www.linkedin.com/help/linkedin/answer/a563153/personalizing-invitations-to-connect?lang=en);
the user must still check their account's current restrictions. Longer drafts
have an internal 2,000-character editing cap, not a promise about LinkedIn's
message availability. No account or connection status is inferred.

Drafts reference the verified name/headline, company and current job title.
They may quote a bounded complete duty line from the captured description.
Descriptions are untrusted quoted data, not instructions to an AI. There is no
model, prompt execution, remote API or exfiltration. Resume text is not read by
this feature; it does not invent qualifications or automatically share private
resume details. Referral wording is conditional, never a claim of willingness.
Revise chooses another deterministic wording, not a new set of facts.

The draft dialog supports loading, edit, revise, copy, failure retention and
keyboard close/focus return. Drafts stay in the current browser DOM until close
or navigation; no localStorage or new SQL storage is used. Without JavaScript,
profile links and reviewed cards still work, while drafting stays disabled.

## Real-source validation, 9 November 2026

Actual verified hiring contacts found in the bounded five-posting sample: **0**.
This is not five verified-empty employers. Some pages were inaccessible/retired.
No person, profile or photo from the sample was saved.

| Requested/current posting | Official public result | Qualifying contacts |
| --- | --- | --- |
| Taboola Software Engineer Intern, requested local reference 10437 | Captured project fixture identifies Ad Creatives/Tel Aviv. Public [8229871 posting URL](https://www.taboola.com/careers/job/8229871?gh_jid=8229871) redirected to generic listings. Numeric local ID is not a public posting identity. Live contact verification incomplete. | 0 |
| Stratasys Software Embedded Student | [Official page](https://careers.stratasys.com/job/Rehovot-Software-Embedded-Student-IL/1444873900/) readable; no identified hiring person/profile. | 0 |
| Amazon Software Development Student, 10558894 | [Official page](https://www.amazon.jobs/en/jobs/10558894/software-development-student) readable; named technical-video speakers are not evidence of hiring relevance. | 0 |
| Apple Software Engineering Student, 200676939-6109 | [Official page](https://jobs.apple.com/en-il/details/200676939-6109/software-engineering-student?team=STDNT) returned a JS-dependent shell; live contact verification incomplete. | 0 |
| Google Part-Time SWE BS/MS Intern 2027 | [Official page](https://www.google.com/about/careers/applications/jobs/results/103632882076918470-parttime-software-engineering-bsms-intern-2027) returned Job not found. | 0 |

The Taboola CEO quotation and Amazon video speakers were excluded; neither proves
company recruiting responsibility or membership in the role's hiring team.
No search snippet or email-address local part was resolved into a person's identity.

Illustrative Taboola inquiry **template**, not a generated message to a discovered
person:

> Hi [verified first name], I'm interested in the Software Engineer Intern opening
> at Taboola. The captured posting describes the Ad Creatives team in Tel Aviv.
> If you are familiar with this opening, could you share a brief insight or point
> me to the appropriate contact? Thank you for your time.

Known: role/company/team/location from the stored description fixture. Unknown:
recipient identity/profile, current employment, hiring authority, whether the
opening is still active, and referral willingness. The actual UI cannot generate
this for a person until a qualifying reviewed contact exists.

## Provider feasibility

- [LinkedIn Profile API](https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-api)
  is restricted to approved developers, with storage restrictions. Open login
  permissions do not enable arbitrary recruiter discovery. No approved provider
  or credentials exist in AJob.
- [LinkedIn prohibited automation policy](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions):
  no scraping, login automation, CAPTCHA bypass or unapproved API was attempted.
- [Google Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)
  is closed to new customers; existing-customer documentation lists 100 free
  queries/day and $5 per 1,000 thereafter, with retirement on January 1, 2027.
  No service was registered or purchased. Search hits alone would still not
  establish employment, team membership or photo reuse rights.

## Validation and rollout

Unit tests cover provenance gates, wrong company/job, malicious legacy URLs,
dedup/ranking, cache expiry, damaged source, no avatars, drafts and CSRF.
Browser tests use clearly labeled synthetic people, block every external request,
and replace the clipboard with a test-only stub. Desktop/mobile screenshots are
visual QA only, **not evidence of real contact coverage**.

No production migration, scheduler, notification, workflow or updater changes.
After review/merge the normal local runtime update delivers the UI; default
results remain empty until permissioned evidence is supplied. This feature
does not claim to solve contact discovery.
