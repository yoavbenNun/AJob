-- MANUAL ONLY. Review and execute as the trusted migration administrator.
-- Prerequisites: scanner job table with RLS enabled, Supabase auth.uid(),
-- authenticator and supabase_auth_admin. No legacy dashboard role is needed.
-- This script never changes managed roles, extension objects or scanner policies.
-- Add ajob_api to PostgREST exposed schemas, then audit EVERY exposed schema
-- (including existing public/GraphQL surfaces) through the real gateway. Reject
-- reachable generic privileged RPCs; preserve legitimate existing API clients.
-- NOLOGIN is essential: PUBLIC extension privileges cannot safely be removed on
-- hosted Supabase, so this design deliberately grants no direct SQL login.
-- Seed the approved Auth UUID only as the trusted table owner, in one reviewed
-- transaction: BEGIN; ALTER TABLE ajob_private.data_api_owner NO FORCE ROW LEVEL
-- SECURITY; INSERT INTO ajob_private.data_api_owner(singleton,auth_user_id)
-- VALUES(true,<reviewed UUID>); ALTER TABLE ajob_private.data_api_owner FORCE ROW
-- LEVEL SECURITY; COMMIT. Never seed a real UUID from an automated migration.
-- Configure ajob_private.custom_access_token_hook as the Supabase Auth hook.
-- Do not substitute a service-role key for the user's password-authenticated JWT.
BEGIN;
DO $$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['ajob_cloud_api','ajob_api_owner'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS', role_name);
            EXECUTE format('COMMENT ON ROLE %I IS ''AJob Data API v1''',role_name);
        ELSIF (SELECT shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=role_name)
            IS DISTINCT FROM 'AJob Data API v1' THEN
            RAISE EXCEPTION 'Existing unrelated Data API role requires review: %',role_name;
        END IF;
        IF EXISTS (SELECT FROM pg_roles WHERE rolname=role_name AND
            (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolinherit))
            OR EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=role_name)
        THEN
            RAISE EXCEPTION 'Unsafe existing Data API role: %', role_name;
        END IF;
    END LOOP;
END $$;

-- PostgreSQL 16+ gives a role creator ADMIN but SET/INHERIT FALSE. Transfer and
-- reapplication need temporary SET/INHERIT. Scope changes to this grantor's row;
-- preserve other grantors' grants and restore our original options exactly.
DO $$ DECLARE can_set boolean; can_inherit boolean; migration_user text:=current_user; BEGIN
    PERFORM set_config('ajob.migration_membership','',true);
    IF current_setting('server_version_num')::integer>=160000 THEN
        IF NOT pg_has_role(migration_user,'ajob_api_owner','SET') OR NOT pg_has_role(migration_user,'ajob_api_owner','USAGE') THEN
            EXECUTE 'SELECT set_option,inherit_option FROM pg_auth_members WHERE roleid=''ajob_api_owner''::regrole AND member=$1::regrole AND grantor=$1::regrole'
                INTO can_set,can_inherit USING migration_user;
            PERFORM set_config('ajob.migration_membership',CASE WHEN can_set IS NULL THEN 'revoke_self_grant' ELSE 'restore_self_grant' END,true);
            PERFORM set_config('ajob.migration_old_set',COALESCE(can_set,false)::text,true);
            PERFORM set_config('ajob.migration_old_inherit',COALESCE(can_inherit,false)::text,true);
            EXECUTE format('GRANT ajob_api_owner TO %I WITH SET TRUE GRANTED BY %I',migration_user,migration_user);
            EXECUTE format('GRANT ajob_api_owner TO %I WITH INHERIT TRUE GRANTED BY %I',migration_user,migration_user);
        END IF;
    ELSIF NOT pg_has_role(migration_user,'ajob_api_owner','MEMBER') THEN
        EXECUTE format('GRANT ajob_api_owner TO %I',migration_user);
        PERFORM set_config('ajob.migration_membership','revoke',true);
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS ajob_api;
DO $$ BEGIN
    IF obj_description('ajob_api'::regnamespace,'pg_namespace') IS DISTINCT FROM 'AJob Data API v1'
        AND (EXISTS (SELECT FROM pg_proc WHERE pronamespace='ajob_api'::regnamespace)
            OR EXISTS (SELECT FROM pg_class WHERE relnamespace='ajob_api'::regnamespace)) THEN
        RAISE EXCEPTION 'Existing unrelated ajob_api schema requires review';
    END IF;
END $$;
COMMENT ON SCHEMA ajob_api IS 'AJob Data API v1';
REVOKE ALL ON SCHEMA ajob_api FROM PUBLIC, anon, authenticated, service_role;
CREATE SCHEMA IF NOT EXISTS ajob_private;
REVOKE ALL ON SCHEMA ajob_private FROM ajob_cloud_api;
GRANT USAGE ON SCHEMA ajob_api TO ajob_cloud_api;
GRANT USAGE ON SCHEMA ajob_private, public, auth TO ajob_api_owner;
-- Required only while transferring new function ownership; removed atomically.
GRANT CREATE ON SCHEMA ajob_api, ajob_private TO ajob_api_owner;
GRANT EXECUTE ON FUNCTION auth.uid() TO ajob_api_owner;
GRANT ajob_cloud_api TO authenticator;

-- Fail before overwriting an unrelated hook/helper or policy. Reapplication is
-- allowed only for objects explicitly marked by this migration.
DO $$ DECLARE object_record record; BEGIN
    IF NOT EXISTS (SELECT FROM pg_class WHERE oid='public.job_postings'::regclass AND relrowsecurity) THEN
        RAISE EXCEPTION 'Enable jobs RLS manually after reviewing scanner policies first';
    END IF;
    FOR object_record IN SELECT p.oid,p.oid::regprocedure AS identity FROM pg_proc p
        WHERE (p.pronamespace='ajob_private'::regnamespace AND p.proname IN
        ('data_api_allowed','data_api_require_owner','custom_access_token_hook','data_api_job_json'))
        OR p.pronamespace='ajob_api'::regnamespace LOOP
        IF obj_description(object_record.oid,'pg_proc') IS DISTINCT FROM 'AJob Data API v1' THEN
            RAISE EXCEPTION 'Existing unrelated function: %',object_record.identity;
        END IF;
    END LOOP;
    FOR object_record IN SELECT p.oid,p.polname AS identity FROM pg_policy p
        WHERE p.polname IN ('data_api_identity_read','data_api_job_read','data_api_job_status','data_api_owner')
        AND (p.polrelid='public.job_postings'::regclass OR p.polrelid IN
            (SELECT oid FROM pg_class WHERE relnamespace='ajob_private'::regnamespace)) LOOP
        IF obj_description(object_record.oid,'pg_policy') IS DISTINCT FROM 'AJob Data API v1' THEN
            RAISE EXCEPTION 'Existing unrelated policy: %',object_record.identity;
        END IF;
    END LOOP;
END $$;

-- Create missing private state, while preserving pre-existing legacy grants.
-- Project-default API grants on NEW tables are removed in this transaction.
DO $$ DECLARE table_name text; definition text; BEGIN
    FOR table_name,definition IN VALUES
        ('personal_notes','(job_id bigint PRIMARY KEY REFERENCES public.job_postings(id), note text NOT NULL CHECK(length(note) BETWEEN 1 AND 2000))'),
        ('job_dismissal_history','(job_id bigint PRIMARY KEY REFERENCES public.job_postings(id), previous_status text NOT NULL CHECK(previous_status IN (''new'',''reviewed'',''applied'',''rejected'')))'),
        ('dashboard_sessions','(token_hash text PRIMARY KEY CHECK(token_hash ~ ''^[0-9a-f]{64}$''), csrf_token text NOT NULL, created_at double precision NOT NULL, last_seen double precision NOT NULL, expires_at double precision NOT NULL)'),
        ('dashboard_login_throttle','(singleton boolean PRIMARY KEY CHECK(singleton), window_start double precision NOT NULL, attempts integer NOT NULL CHECK(attempts>=0))')
    LOOP
        IF to_regclass('ajob_private.'||table_name) IS NULL THEN
            EXECUTE format('CREATE TABLE ajob_private.%I %s',table_name,definition);
            IF table_name='dashboard_login_throttle' THEN
                EXECUTE 'INSERT INTO ajob_private.dashboard_login_throttle VALUES(true,0,0)';
            END IF;
            EXECUTE format('REVOKE ALL ON ajob_private.%I FROM PUBLIC, anon, authenticated, service_role',table_name);
            EXECUTE format('ALTER TABLE ajob_private.%I ENABLE ROW LEVEL SECURITY',table_name);
            EXECUTE format('ALTER TABLE ajob_private.%I FORCE ROW LEVEL SECURITY',table_name);
        ELSIF NOT EXISTS (SELECT FROM pg_class WHERE oid=to_regclass('ajob_private.'||table_name)
            AND relkind='r' AND relrowsecurity AND relforcerowsecurity) THEN
            RAISE EXCEPTION 'Existing private table % requires reviewed FORCE RLS',table_name;
        END IF;
    END LOOP;
END $$;

-- Structural compatibility is mandatory; do not adopt a same-named unrelated
-- table, a nullable security field, or an unconstrained notes/session store.
DO $$ DECLARE item record; BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('personal_notes','job_id','bigint'),('personal_notes','note','text'),
        ('job_dismissal_history','job_id','bigint'),('job_dismissal_history','previous_status','text'),
        ('dashboard_sessions','token_hash','text'),('dashboard_sessions','csrf_token','text'),
        ('dashboard_sessions','created_at','double precision'),('dashboard_sessions','last_seen','double precision'),
        ('dashboard_sessions','expires_at','double precision'),('dashboard_login_throttle','singleton','boolean'),
        ('dashboard_login_throttle','window_start','double precision'),('dashboard_login_throttle','attempts','integer')
    ) AS columns(table_name,column_name,type_name) LOOP
        IF NOT EXISTS (SELECT FROM pg_attribute WHERE attrelid=to_regclass('ajob_private.'||item.table_name)
            AND attname=item.column_name AND attnotnull AND NOT attisdropped AND format_type(atttypid,atttypmod)=item.type_name) THEN
            RAISE EXCEPTION 'Incompatible private field %.%',item.table_name,item.column_name;
        END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES ('personal_notes','job_id'),('job_dismissal_history','job_id'),
        ('dashboard_sessions','token_hash'),('dashboard_login_throttle','singleton')) AS keys(table_name,column_name) LOOP
        IF NOT EXISTS (SELECT FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid
            AND c.conkey=ARRAY[a.attnum] WHERE c.conrelid=to_regclass('ajob_private.'||item.table_name)
            AND c.contype='p' AND a.attname=item.column_name) THEN
            RAISE EXCEPTION 'Missing private primary key: %',item.table_name;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='ajob_private.personal_notes'::regclass
        AND contype='c' AND convalidated AND pg_get_constraintdef(oid) LIKE '%length(note)%2000%')
        OR NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='ajob_private.dashboard_sessions'::regclass
        AND contype='c' AND convalidated AND pg_get_constraintdef(oid) LIKE '%^[0-9a-f]{64}$%') THEN
        RAISE EXCEPTION 'Private notes/session constraints require review';
    END IF;
END $$;

DO $$ BEGIN
    IF to_regclass('ajob_private.data_api_owner') IS NOT NULL
        AND obj_description(to_regclass('ajob_private.data_api_owner'),'pg_class') IS DISTINCT FROM 'AJob Data API v1' THEN
        RAISE EXCEPTION 'Existing unrelated identity configuration table requires review';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS ajob_private.data_api_owner (
    singleton boolean PRIMARY KEY CHECK (singleton),
    auth_user_id uuid NOT NULL UNIQUE
);
COMMENT ON TABLE ajob_private.data_api_owner IS 'AJob Data API v1';
REVOKE ALL ON ajob_private.data_api_owner FROM PUBLIC, anon, authenticated, service_role, ajob_cloud_api;
ALTER TABLE ajob_private.data_api_owner ENABLE ROW LEVEL SECURITY;
ALTER TABLE ajob_private.data_api_owner FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS data_api_identity_read ON ajob_private.data_api_owner;
CREATE POLICY data_api_identity_read ON ajob_private.data_api_owner FOR SELECT TO ajob_api_owner USING (true);
COMMENT ON POLICY data_api_identity_read ON ajob_private.data_api_owner IS 'AJob Data API v1';
GRANT SELECT ON ajob_private.data_api_owner TO ajob_api_owner;

CREATE OR REPLACE FUNCTION ajob_private.data_api_allowed() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog
AS $$
    SELECT COALESCE(
        (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'ajob_cloud_api'
        AND EXISTS (SELECT FROM ajob_private.data_api_owner WHERE singleton AND auth_user_id=auth.uid()), false)
$$;
ALTER FUNCTION ajob_private.data_api_allowed() OWNER TO ajob_api_owner;
COMMENT ON FUNCTION ajob_private.data_api_allowed() IS 'AJob Data API v1';
REVOKE ALL ON FUNCTION ajob_private.data_api_allowed() FROM PUBLIC, anon, authenticated, service_role, ajob_cloud_api;

CREATE OR REPLACE FUNCTION ajob_private.data_api_require_owner() RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog
AS $$ BEGIN
    IF NOT ajob_private.data_api_allowed() THEN
        RAISE EXCEPTION 'Data API access denied' USING ERRCODE='42501';
    END IF;
END $$;
ALTER FUNCTION ajob_private.data_api_require_owner() OWNER TO ajob_api_owner;
COMMENT ON FUNCTION ajob_private.data_api_require_owner() IS 'AJob Data API v1';
REVOKE ALL ON FUNCTION ajob_private.data_api_require_owner() FROM PUBLIC, anon, authenticated, service_role, ajob_cloud_api;

-- Supabase calls this supported hook before signing an access token. Preserve
-- required/custom claims; only the authoritative event user_id selects the role.
CREATE OR REPLACE FUNCTION ajob_private.custom_access_token_hook(event jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
SET statement_timeout='10s' SET lock_timeout='3s'
AS $$
DECLARE claims jsonb := event->'claims'; allowed boolean;
BEGIN
    IF jsonb_typeof(claims) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Missing token claims' USING ERRCODE='22023';
    END IF;
    SELECT EXISTS (SELECT FROM ajob_private.data_api_owner
        WHERE singleton AND auth_user_id=(event->>'user_id')::uuid
        AND auth_user_id=(claims->>'sub')::uuid) INTO allowed;
    IF allowed THEN claims := jsonb_set(claims, '{role}', to_jsonb('ajob_cloud_api'::text)); END IF;
    RETURN jsonb_set(event,'{claims}',claims);
END $$;
ALTER FUNCTION ajob_private.custom_access_token_hook(jsonb) OWNER TO ajob_api_owner;
COMMENT ON FUNCTION ajob_private.custom_access_token_hook(jsonb) IS 'AJob Data API v1';
REVOKE ALL ON FUNCTION ajob_private.custom_access_token_hook(jsonb) FROM PUBLIC, anon, authenticated, service_role, ajob_cloud_api;
GRANT USAGE ON SCHEMA ajob_private TO supabase_auth_admin;
GRANT EXECUTE ON FUNCTION ajob_private.custom_access_token_hook(jsonb) TO supabase_auth_admin;

-- These additive policies never change the jobs table's existing RLS mode or
-- scanner policies. Private tables already require FORCE RLS.
DROP POLICY IF EXISTS data_api_job_read ON public.job_postings;
CREATE POLICY data_api_job_read ON public.job_postings FOR SELECT TO ajob_api_owner USING (ajob_private.data_api_allowed());
COMMENT ON POLICY data_api_job_read ON public.job_postings IS 'AJob Data API v1';
DROP POLICY IF EXISTS data_api_job_status ON public.job_postings;
CREATE POLICY data_api_job_status ON public.job_postings FOR UPDATE TO ajob_api_owner
    USING (ajob_private.data_api_allowed()) WITH CHECK (ajob_private.data_api_allowed());
COMMENT ON POLICY data_api_job_status ON public.job_postings IS 'AJob Data API v1';
GRANT SELECT ON public.job_postings TO ajob_api_owner;
GRANT UPDATE(status) ON public.job_postings TO ajob_api_owner;
DO $$ DECLARE table_name text; BEGIN
    FOREACH table_name IN ARRAY ARRAY['personal_notes','job_dismissal_history','dashboard_sessions','dashboard_login_throttle'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS data_api_owner ON ajob_private.%I',table_name);
        EXECUTE format('CREATE POLICY data_api_owner ON ajob_private.%I TO ajob_api_owner USING (ajob_private.data_api_allowed()) WITH CHECK (ajob_private.data_api_allowed())',table_name);
        EXECUTE format('COMMENT ON POLICY data_api_owner ON ajob_private.%I IS ''AJob Data API v1''',table_name);
    END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON ajob_private.personal_notes,
    ajob_private.job_dismissal_history, ajob_private.dashboard_sessions TO ajob_api_owner;
GRANT SELECT, UPDATE ON ajob_private.dashboard_login_throttle TO ajob_api_owner;

-- Project explicit fields so future scanner/private columns cannot leak.
CREATE OR REPLACE FUNCTION ajob_private.data_api_job_json(j public.job_postings) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('id',j.id,'title',j.title,'company',j.company,
        'location',j.location,'job_url',j.job_url,'description',j.description,
        'requirements',j.requirements,'source',j.source,'discovered_at',j.discovered_at,
        'is_relevant',j.is_relevant,'status',j.status)
$$;
ALTER FUNCTION ajob_private.data_api_job_json(public.job_postings) OWNER TO ajob_api_owner;
COMMENT ON FUNCTION ajob_private.data_api_job_json(public.job_postings) IS 'AJob Data API v1';
REVOKE ALL ON FUNCTION ajob_private.data_api_job_json(public.job_postings) FROM PUBLIC, anon, authenticated, service_role, ajob_cloud_api;

CREATE OR REPLACE FUNCTION ajob_api.ajob_jobs(p_after_id bigint DEFAULT 0,p_limit integer DEFAULT 200) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ DECLARE result jsonb; BEGIN
    PERFORM ajob_private.data_api_require_owner();
    IF p_after_id IS NULL OR p_after_id<0 OR p_limit IS NULL OR p_limit<1 OR p_limit>200 THEN
        RAISE EXCEPTION 'Invalid jobs page' USING ERRCODE='22023';
    END IF;
    SELECT COALESCE(jsonb_agg(page.job ORDER BY page.id),'[]'::jsonb)
    INTO result FROM (SELECT id,ajob_private.data_api_job_json(j) AS job FROM public.job_postings j
        WHERE id>p_after_id ORDER BY id LIMIT p_limit) page;
    RETURN result;
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_job(p_job_id bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    RETURN (SELECT ajob_private.data_api_job_json(j) FROM public.job_postings j WHERE id=p_job_id);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_status(p_job_id bigint,p_status text,p_expected_status text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ DECLARE j public.job_postings; BEGIN
    PERFORM ajob_private.data_api_require_owner();
    IF p_status IS NULL OR p_status NOT IN ('new','reviewed','applied','ignored','rejected') THEN
        RAISE EXCEPTION 'Invalid job status' USING ERRCODE='22023';
    END IF;
    UPDATE public.job_postings SET status=p_status WHERE id=p_job_id
        AND (p_expected_status IS NULL OR status=p_expected_status) RETURNING * INTO j;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN ajob_private.data_api_job_json(j);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_notes(p_job_ids bigint[]) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    IF p_job_ids IS NULL OR cardinality(p_job_ids)>200 THEN
        RAISE EXCEPTION 'Invalid notes page' USING ERRCODE='22023';
    END IF;
    RETURN (SELECT COALESCE(jsonb_object_agg(job_id::text,note),'{}'::jsonb)
        FROM ajob_private.personal_notes WHERE job_id=ANY(p_job_ids));
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_note_save(p_job_id bigint,p_note text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ DECLARE clean text; BEGIN
    PERFORM ajob_private.data_api_require_owner();
    IF p_note IS NULL OR length(p_note)>2000 THEN
        RAISE EXCEPTION 'Notes must be text of at most 2,000 characters.' USING ERRCODE='22023';
    END IF;
    -- Match Python str.strip(), including Unicode whitespace, independent of
    -- the database locale. Length is checked before trimming, as in the store.
    clean := btrim(p_note,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||
        chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||
        chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288));
    IF clean='' THEN DELETE FROM ajob_private.personal_notes WHERE job_id=p_job_id;
    ELSE INSERT INTO ajob_private.personal_notes(job_id,note) VALUES(p_job_id,clean)
        ON CONFLICT(job_id) DO UPDATE SET note=excluded.note;
    END IF;
    RETURN to_jsonb(clean);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_dismiss(p_job_id bigint,p_action text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ DECLARE current_status text; target text; BEGIN
    PERFORM ajob_private.data_api_require_owner();
    IF p_action IS NULL OR p_action NOT IN ('dismiss','restore') THEN
        RAISE EXCEPTION 'Unknown dismissal action' USING ERRCODE='22023';
    END IF;
    SELECT status INTO current_status FROM public.job_postings WHERE id=p_job_id FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF (p_action='dismiss')=(current_status='ignored') THEN
        RETURN jsonb_build_object('status',current_status,'changed',false);
    END IF;
    IF p_action='dismiss' THEN
        INSERT INTO ajob_private.job_dismissal_history(job_id,previous_status) VALUES(p_job_id,current_status)
            ON CONFLICT(job_id) DO UPDATE SET previous_status=excluded.previous_status;
        target := 'ignored';
    ELSE
        SELECT previous_status INTO target FROM ajob_private.job_dismissal_history WHERE job_id=p_job_id;
        IF target IS NULL OR target NOT IN ('new','reviewed','applied','rejected') THEN target:='reviewed'; END IF;
        DELETE FROM ajob_private.job_dismissal_history WHERE job_id=p_job_id;
    END IF;
    UPDATE public.job_postings SET status=target WHERE id=p_job_id;
    RETURN jsonb_build_object('status',target,'changed',true);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_login_reserve(p_now double precision) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    UPDATE ajob_private.dashboard_login_throttle
        SET attempts=CASE WHEN window_start<=p_now-900 THEN 1 ELSE attempts+1 END,
            window_start=CASE WHEN window_start<=p_now-900 THEN p_now ELSE window_start END
        WHERE singleton AND (window_start<=p_now-900 OR attempts<5);
    RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_session_create(p_session_hash text,p_csrf_token text,p_now double precision,p_expires_at double precision) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    PERFORM singleton FROM ajob_private.dashboard_login_throttle WHERE singleton FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Cloud authentication schema is not provisioned'; END IF;
    DELETE FROM ajob_private.dashboard_sessions WHERE expires_at<=p_now;
    DELETE FROM ajob_private.dashboard_sessions WHERE token_hash IN
        (SELECT token_hash FROM ajob_private.dashboard_sessions ORDER BY created_at DESC,token_hash OFFSET 4);
    INSERT INTO ajob_private.dashboard_sessions(token_hash,csrf_token,created_at,last_seen,expires_at)
        VALUES(p_session_hash,p_csrf_token,p_now,p_now,p_expires_at);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_session_get(p_session_hash text,p_now double precision,p_idle_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ DECLARE csrf text; BEGIN
    PERFORM ajob_private.data_api_require_owner();
    UPDATE ajob_private.dashboard_sessions SET last_seen=GREATEST(last_seen,p_now)
        WHERE token_hash=p_session_hash AND expires_at>p_now AND last_seen>p_now-p_idle_seconds
        RETURNING csrf_token INTO csrf;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('csrf_token',csrf);
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_session_revoke(p_session_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    DELETE FROM ajob_private.dashboard_sessions WHERE token_hash=p_session_hash;
END $$;

CREATE OR REPLACE FUNCTION ajob_api.ajob_preflight() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET statement_timeout='10s' SET lock_timeout='3s'
AS $$ BEGIN
    PERFORM ajob_private.data_api_require_owner();
    PERFORM id,title,company,location,job_url,description,requirements,source,discovered_at,is_relevant,status FROM public.job_postings LIMIT 0;
    PERFORM job_id,note FROM ajob_private.personal_notes LIMIT 0;
    PERFORM job_id,previous_status FROM ajob_private.job_dismissal_history LIMIT 0;
    PERFORM token_hash,csrf_token,created_at,last_seen,expires_at FROM ajob_private.dashboard_sessions LIMIT 0;
    PERFORM singleton,window_start,attempts FROM ajob_private.dashboard_login_throttle WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION 'Cloud authentication schema is not provisioned'; END IF;
    RETURN jsonb_build_object('ready',true,'version',1);
END $$;

-- No dynamic SQL is reachable through any API RPC. Revoke default PUBLIC
-- EXECUTE and pin the definer to the restricted role in the same transaction.
DO $$ DECLARE signature regprocedure;
    allowed regprocedure[] := ARRAY[
        'ajob_api.ajob_jobs(bigint,integer)'::regprocedure,'ajob_api.ajob_job(bigint)'::regprocedure,
        'ajob_api.ajob_status(bigint,text,text)'::regprocedure,'ajob_api.ajob_notes(bigint[])'::regprocedure,
        'ajob_api.ajob_note_save(bigint,text)'::regprocedure,'ajob_api.ajob_dismiss(bigint,text)'::regprocedure,
        'ajob_api.ajob_login_reserve(double precision)'::regprocedure,
        'ajob_api.ajob_session_create(text,text,double precision,double precision)'::regprocedure,
        'ajob_api.ajob_session_get(text,double precision,integer)'::regprocedure,
        'ajob_api.ajob_session_revoke(text)'::regprocedure,'ajob_api.ajob_preflight()'::regprocedure];
BEGIN
    IF EXISTS (SELECT FROM pg_proc WHERE pronamespace='ajob_api'::regnamespace AND oid<>ALL(allowed::oid[]))
        OR EXISTS (SELECT FROM pg_class WHERE relnamespace='ajob_api'::regnamespace) THEN
        RAISE EXCEPTION 'Unexpected exposed Data API object; review gateway schema before deployment';
    END IF;
    FOREACH signature IN ARRAY allowed LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO ajob_api_owner',signature);
        EXECUTE format('COMMENT ON FUNCTION %s IS ''AJob Data API v1''',signature);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role',signature);
    END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION ajob_api.ajob_jobs(bigint,integer), ajob_api.ajob_job(bigint),
    ajob_api.ajob_status(bigint,text,text), ajob_api.ajob_notes(bigint[]),
    ajob_api.ajob_note_save(bigint,text), ajob_api.ajob_dismiss(bigint,text),
    ajob_api.ajob_login_reserve(double precision),
    ajob_api.ajob_session_create(text,text,double precision,double precision),
    ajob_api.ajob_session_get(text,double precision,integer),
    ajob_api.ajob_session_revoke(text), ajob_api.ajob_preflight() TO ajob_cloud_api;
REVOKE CREATE ON SCHEMA ajob_api, ajob_private FROM ajob_api_owner;
DO $$ BEGIN
    IF current_setting('ajob.migration_membership',true)='revoke_self_grant' THEN
        EXECUTE format('REVOKE ajob_api_owner FROM %I GRANTED BY %I',current_user,current_user);
    ELSIF current_setting('ajob.migration_membership',true)='restore_self_grant' THEN
        EXECUTE format('GRANT ajob_api_owner TO %I WITH SET %s GRANTED BY %I',current_user,current_setting('ajob.migration_old_set'),current_user);
        EXECUTE format('GRANT ajob_api_owner TO %I WITH INHERIT %s GRANTED BY %I',current_user,current_setting('ajob.migration_old_inherit'),current_user);
    ELSIF current_setting('ajob.migration_membership',true)='revoke' THEN
        EXECUTE format('REVOKE ajob_api_owner FROM %I',current_user);
    END IF;
END $$;
COMMIT;
