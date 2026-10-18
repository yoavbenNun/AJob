-- Read-only: run after a scheduled scan. Empty results mean no instrumented run yet.
-- Discovery counts may include repeats across queries. Unique counts use posting
-- URLs. new_jobs_saved counts actual DB inserts, independently of the scope gap.
-- A NULL gap means the global comparison was incomplete; it is not proof of gain.
SELECT r.slot_key, r.status AS scan_status, m.company,
       (m.metrics::jsonb->'collection'->>'focused_discovered')::int AS focused_discoveries,
       (m.metrics::jsonb->'collection'->>'global_discovered')::int AS global_discoveries,
       (m.metrics::jsonb->'collection'->>'focused_unique')::int AS focused_unique,
       (m.metrics::jsonb->'collection'->>'global_unique')::int AS global_unique,
       (m.metrics::jsonb->'collection'->>'focused_israel_unique')::int AS focused_israel_unique,
       (m.metrics::jsonb->'collection'->>'global_israel_unique')::int AS global_israel_unique,
       (m.metrics::jsonb->'collection'->>'focused_only_israel_unique')::int AS additional_israel_within_page_cap,
       (m.metrics::jsonb->>'new_jobs_saved')::int AS new_jobs_inserted,
       (m.metrics::jsonb->'collection'->>'duplicates_avoided')::int AS duplicates_avoided,
       m.metrics::jsonb->'collection'->>'location_filter_status' AS location_filter,
       (m.metrics::jsonb->'collection'->>'comparison_complete')::boolean AS comparison_complete,
       (m.metrics::jsonb->'collection'->>'page_limit_reached')::boolean AS page_limit_reached,
       (m.metrics::jsonb->'collection'->>'focused_budget_exhausted')::boolean AS focused_budget_exhausted
FROM scan_source_metrics m JOIN scan_runs r USING (run_id)
WHERE r.run_id = (
    SELECT s.run_id FROM scan_runs s
    WHERE EXISTS (
        SELECT 1 FROM scan_source_metrics sm
        WHERE sm.run_id=s.run_id
          AND sm.metrics::jsonb->'collection' ? 'focused_israel_unique'
    )
    ORDER BY s.started_at DESC LIMIT 1
)
AND m.metrics::jsonb->'collection' ? 'focused_israel_unique'
ORDER BY m.company;
