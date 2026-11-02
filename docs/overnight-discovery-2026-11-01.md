# Bounded discovery-quality sprint: 2026-11-01

Baseline: main dc647c5. Research used public employer endpoints only. No Production
scanner, database, credentials, emails, status changes, Auto-Dismiss, alert-dedup
activation, cloud/dashboard code, Cron or migrations were used or changed.

## A. Workday: no proven open-role false negative

33 bounded public HTTP requests sampled NVIDIA, Intel, KLA (Israel and Search
boards), Marvell, F5 and Cisco. Robots checks allowed the sampled API paths.
The default two-page limit was NOT increased. Source completeness under that
budget is not proof of worldwide or unrestricted career-board coverage.

Observed NVIDIA `software intern` pages: first total=791 with20 rows; second
total=0 with20 new rows. Intel:273/20 then0/20 new rows. The existing fallback to
page length correctly handles these inconsistent totals. Added regression cases
guard the second page, explicit caps, unique counts and cross-query duplication.
No Workday runtime code change was justified.

Comparison examples from official search indexes:

- KLA Software Engineering Student2635341-1:
  https://kla.wd1.myworkdayjobs.com/en-US/Search/job/Migdal-Haemek-Israel/Software-Engineering-Student_2635341-1
  Detail API403; stopped without bypass. Current student queries did not establish
  this posting open. Switching the configured board based on a cached hit would
  be speculative. Both boards currently exposed a different research-student role.
- Intel Software Development StudentJR0279646:
  https://intel.wd1.myworkdayjobs.com/External/job/Israel-Haifa/Software-Development-Student_JR0279646
  Detail API404 on the indexed path; no verified current vacancy.
- Intel Wi-Fi Driver Software Developer StudentJR0284349:
  https://intel.wd1.myworkdayjobs.com/en-US/External/job/Israel-Petah-Tikva/Intel-Wi-Fi-Driver-Software-Developer-Student_JR0284349
  Detail API404 on the indexed path; no verified current vacancy.

Marvell's sampled Israeli student postings were hardware/legal. F5 had no Israeli
student result in the sample; Cisco's student results were foreign. No JSON decode
or429 response appeared in this bounded Workday sample. None of these observations
establishes complete employer coverage or final candidate eligibility.

## B. Proven existing-source defect: Melio ATS migration

Official provenance:
https://melio.com/careers/ redirects to https://www.lifeatmelio.com/ which embeds
https://careers.meliopayments.com/. Its public application bundle explicitly
switches recruiting providers after2026-10-28T21:00:00Z. The old Greenhouse feed
returns HTTP200 with jobs=[] and total=0; the current public Ashby board returns
15 listed jobs,15 unique UUIDs/URLs and8 Tel Aviv location labels.

Verified public feed:
https://api.ashbyhq.com/posting-api/job-board/melio-payments
Provider contract: https://developers.ashbyhq.com/docs/public-job-posting-api

The API robots endpoint returned401; the career-app robots endpoint returned an
HTML SPA fallback rather than a robots policy. No authentication/bypass was used.
Collection uses only the explicitly documented public posting API that the
employer's current app uses, not protected/internal endpoints.

Repair: migrate only the existing Melio configuration entry and use a bounded
public-feed adapter. Validate UUID identity and exact official board/job URLs,
deduplicate by canonical URL, preserve source and location/description evidence.
Malformed feeds/HTTP errors/redirects/unsupported pagination remain failures;
truly empty or all-unlisted feeds can be empty without becoming closure evidence.
All-invalid nonempty feeds fail; mixed-invalid feeds retain good jobs with a
coverage-limited metric. Parent source isolation/budgets are unchanged.

Current student-development yield is ZERO, not15. The two Data Engineer postings
require3+years; a Senior Backend posting requires7+years. A generic open-application
posting does not prove a student vacancy. This repair restores employer coverage
for future openings, without adding a company or relaxing the role filter.

Old Greenhouse rows and manual statuses are untouched. No closure snapshot is
generated and cross-ATS jobs are not automatically merged without identity proof.
Existing rows may require later human review; no Production inventory was read.

## C. Proven role-quality false positive: Elbit

The current public employer feed at https://elbitsystemscareer.com/cron/jobs.json
contained550 postings with33 titles containing a student/intern substring (that
coarse research count is not the classifier's word-boundary decision). Projected
only public job fields; no staff metadata was printed or retained.

Software Quality Student: https://elbitsystemscareer.com/job/?jid=20964
describes audits, standards/process compliance and document approval. The old
classifier accepted it because its title contained software+student. A curated
short-excerpt fixture reproduces the defect. The narrow fix requires actual code
or software/automation-development duties for this title family; mere process
references to software development do not suffice. Real coded-test duties,
affirmative contrasts and unrelated experience/supervision negation are tested.
No global scores, eligibility profile or thresholds changed.

Other checked Elbit examples:
-20928 System Engineering Student / Algorithms Team: has explicit coding duties
  and allows Software Engineering students; existing classification accepts it.
-21093 Algorithms Student: requires MSc/PhD; no filter broadening based only on
  Python or algorithms vocabulary is justified for the bachelor's student.
-20460 Avionics Systems Engineering Student: scripting duties but explicit
  Electrical Engineering/Aeronautics requirement; role type and degree eligibility
  remain separate. No candidate-profile relaxation was made.

Stratasys Software Embedded Student remains a positive official discovery example:
https://careers.stratasys.com/job/Rehovot-Software-Embedded-Student-IL/1444873900/
It is not claimed as newly recovered by this sprint or a guaranteed candidate fit.

## Scope and next high-value step

Two implementation repairs only: Melio existing-source migration and Elbit-style
process-quality false positives. Workday gained guard tests, not speculative
runtime changes. No current open student-development vacancy was proved missing
in the sampled Workday requests. Do not represent restored raw Melio listings as
new appropriate jobs.

Next: build a small verified-current Israeli student-posting benchmark, comparing
official detail identity against bounded search/facet results. A real missed job
can justify changing query allocation or pagination within measured source budgets.
Unknown/blocked details and cached search hits remain research leads, not proof.
