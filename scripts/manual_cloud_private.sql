-- MANUAL deployment migration: never sourced by scanner/dashboard initialization.
-- Run as a trusted schema owner after reviewing the deployment checklist.
-- Single-owner dashboard: application authentication is required separately.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ajob_dashboard') THEN
        CREATE ROLE ajob_dashboard NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS ajob_private;
REVOKE ALL ON SCHEMA ajob_private FROM PUBLIC;
CREATE TABLE IF NOT EXISTS ajob_private.personal_notes (
    job_id BIGINT PRIMARY KEY REFERENCES public.job_postings(id),
    note TEXT NOT NULL CHECK (length(note) BETWEEN 1 AND 2000)
);
CREATE TABLE IF NOT EXISTS ajob_private.job_dismissal_history (
    job_id BIGINT PRIMARY KEY REFERENCES public.job_postings(id),
    previous_status TEXT NOT NULL CHECK (previous_status IN ('new','reviewed','applied','rejected'))
);
REVOKE ALL ON ALL TABLES IN SCHEMA ajob_private FROM PUBLIC;
-- Remove common Supabase API grants, including project default privileges.
DO $$ DECLARE role_name TEXT; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA ajob_private FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA ajob_private FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
ALTER TABLE ajob_private.personal_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.personal_notes FORCE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.job_dismissal_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.job_dismissal_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dashboard_notes ON ajob_private.personal_notes;
CREATE POLICY dashboard_notes ON ajob_private.personal_notes TO ajob_dashboard USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS dashboard_undo ON ajob_private.job_dismissal_history;
CREATE POLICY dashboard_undo ON ajob_private.job_dismissal_history TO ajob_dashboard USING (true) WITH CHECK (true);
GRANT USAGE ON SCHEMA ajob_private, public TO ajob_dashboard;
GRANT SELECT, INSERT, UPDATE, DELETE ON ajob_private.personal_notes,
    ajob_private.job_dismissal_history TO ajob_dashboard;
GRANT SELECT ON public.job_postings TO ajob_dashboard;
GRANT UPDATE(status) ON public.job_postings TO ajob_dashboard;
-- Existing deployments may already enable job RLS. Add only the server role's
-- policies; preserve the table's RLS setting and every scanner/API policy.
DROP POLICY IF EXISTS ajob_dashboard_job_read ON public.job_postings;
CREATE POLICY ajob_dashboard_job_read ON public.job_postings
    FOR SELECT TO ajob_dashboard USING (true);
DROP POLICY IF EXISTS ajob_dashboard_job_status ON public.job_postings;
CREATE POLICY ajob_dashboard_job_status ON public.job_postings
    FOR UPDATE TO ajob_dashboard USING (true) WITH CHECK (true);
-- Separately provision a non-owner LOGIN role and GRANT ajob_dashboard TO it.
-- Do not use postgres/service_role/scanner credentials for the dashboard.
-- Keep ajob_private outside API exposed schemas; anonymous API roles get no access.
COMMIT;
