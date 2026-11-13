# Recover historical matches

Run `python -m src.main reclassify` to preview professional relevance changes
for stored postings. Use `--limit` and the printed `--after-id` cursor to review
each batch. Invalid or unavailable captured evidence stays unchanged.

Before applying, run `reclassify --recovery-preview` with an approved
`CANDIDATE_PROFILE_JSON` to review newly relevant Israeli roles without known
academic conflicts. The shortlist deduplicates canonical URLs within its batch
and excludes ignored, applied and rejected rows. Verify freshness, URL aliases
and delivery history manually. An absent outbox record does not prove that a
historical posting was never emailed.

After separate approval, run `reclassify --apply` with the same cursor and limit.
For PostgreSQL this also requires `--approve-postgres-apply`. Preview first:
unchanged rows do not appear on later shortlists. If a concurrent update made
a classification stale, rerun that batch. This workflow never inserts,
resets or sends notification records and does not run on a schedule.
