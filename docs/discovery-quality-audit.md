# Discovery quality audit (2026-10-31)

Baseline: main 101ac905, including Workday diagnostics PR #17. No production
scans, database access, status changes or Auto-Dismiss were performed for this
audit. Counts below describe observed collection and role filtering, not final
candidate eligibility or dashboard fit.

## Scheduled health

Run [41209632824](https://github.com/yoavbenNun/AJob/actions/runs/41209632824)
processed the 16:17 Israel window: full, 159 saves/refreshes, zero source failures,
one new discovery (Stratasys), notification sent, one scan and one mail attempt.
All 51 source workers, including NVIDIA, Intel, KLA, F5, Motorola Solutions,
Red Hat, Clarivate, Marvell and Cisco, reported full source completion. This does
not mean unlimited pagination or complete worldwide vacancy coverage.

Four later scheduled wake-ups (41210786872, 41211773645, 41214402823,
41215203820) retained the same full/sent afternoon window and the prior morning
final failure. No new Workday decoding failure was observed. The morning recovery
failure remains a separate incident; the later full scan does not repair it.
SMTP sent records acceptance, not inbox delivery. PR #17 cannot establish the
historical upstream trigger in the absence of a recurrence with response metadata.

## Coverage and quality limitations

- Sampled full scans on Oct 30 (41013457073, 41032649930, 41070942641) and Oct31
  (41188262059, 41209632824) repeatedly recorded zero raw Melio listings. A limited
  public check found its configured Greenhouse API returning jobs=[]/total=0 and
  its board exposing no job links. Search indexes still list older vacancies;
  cached search results do not prove an active feed defect. Do not infer closure
  or disable the employer solely from this observation.
- 35 employers had zero accepted roles in both sampled Oct31 full scans, despite
  most returning raw listings. This is not proof of broken collection or false
  negatives. Qualcomm's global_discovered=0 is not an empty feed: six raw listings
  and one accepted role were recorded in the afternoon scan.
- Workday query/page limits can truncate coverage. Source completion and coverage
  completeness are different; masked boolean values in GitHub logs must not be
  guessed. Future query expansion needs an actual missed posting and budget tests.
- The afternoon closure report was report_only:250 checked,20 open,1 closed,
  229 unknown, zero auto-dismissed and11 manual-status skips. Expiry coverage is
  incomplete; no private job identities were inspected or changed.
- Existing URL canonicalization/upsert and cross-query deduplication tests cover
  duplicate prevention. This audit does not claim a production-wide duplicate
  inventory without reading private data.

## Proven filter defect

A generic Engineering Student role saying "This role does not involve software
development" previously passed the detail evidence rule. Negation was checked for
code duties, but not DETAIL_SOFTWARE_TERMS. The fix targets that evidence path,
not profile, score, global thresholds or source configuration. Regression cases
cover negative duties, affirmative contrasts, requirements-only text and target
student titles. Synthetic reproduction establishes the rule bug, not that a
specific production vacancy was affected.

The official Stratasys Software Embedded Student posting remains visible at
https://careers.stratasys.com/job/Rehovot-Software-Embedded-Student-IL/1444873900/
and in its official software category. The afternoon run recorded its new
discovery. This is a positive discovery example, not a guarantee of eligibility
or a recommendation based only on the role title.

Potential generic-language false negatives without explicit recognized duties
remain research candidates, not grounds to broaden filters. Next steps are a
verified official missed-posting benchmark, observation of Workday coverage bounds,
and follow-up on Melio's official recruiting links if its empty feed persists.
