-- Manual deployment only; run as schema owner. Runtime role receives table DML,
-- never ownership, DDL, public job writes beyond the explicit status grant.
-- Apply scripts/manual_cloud_private.sql first to provision ajob_dashboard.
BEGIN;
CREATE SCHEMA IF NOT EXISTS ajob_private;
REVOKE ALL ON SCHEMA ajob_private FROM PUBLIC;
CREATE TABLE IF NOT EXISTS ajob_private.dashboard_sessions (
    token_hash TEXT PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    csrf_token TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS ajob_private.dashboard_login_throttle (
    singleton BOOLEAN PRIMARY KEY CHECK (singleton),
    window_start DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 0)
);
-- The owner must be able to seed the singleton when reapplying this migration;
-- restoring FORCE below occurs in this same transaction before publication.
ALTER TABLE ajob_private.dashboard_login_throttle NO FORCE ROW LEVEL SECURITY;
INSERT INTO ajob_private.dashboard_login_throttle VALUES (TRUE, 0, 0)
ON CONFLICT (singleton) DO NOTHING;
REVOKE ALL ON ALL TABLES IN SCHEMA ajob_private FROM PUBLIC;
DO $$ DECLARE role_name TEXT; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA ajob_private FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA ajob_private FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
ALTER TABLE ajob_private.dashboard_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.dashboard_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.dashboard_login_throttle ENABLE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.dashboard_login_throttle FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dashboard_sessions ON ajob_private.dashboard_sessions;
CREATE POLICY dashboard_sessions ON ajob_private.dashboard_sessions TO ajob_dashboard USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS dashboard_login_throttle ON ajob_private.dashboard_login_throttle;
CREATE POLICY dashboard_login_throttle ON ajob_private.dashboard_login_throttle TO ajob_dashboard USING (true) WITH CHECK (true);
GRANT USAGE ON SCHEMA ajob_private TO ajob_dashboard;
GRANT SELECT, INSERT, UPDATE, DELETE ON ajob_private.dashboard_sessions TO ajob_dashboard;
GRANT SELECT, UPDATE ON ajob_private.dashboard_login_throttle TO ajob_dashboard;
COMMIT;
