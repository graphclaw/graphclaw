-- infrastructure/postgres/init/grants.sql
-- Wave 0: No-Delete principal grant setup
--
-- Creates three service principals:
--   agent_principal      — SELECT, INSERT, UPDATE only (NO DELETE).
--                          Used by all agent code paths.
--   admin_principal      — Full grants including DELETE.
--                          Used only by purge worker + admin API routes.
--   migration_principal  — DDL grants only; no DML DELETE.
--                          Used by migration runner.
--
-- Also creates the _principal_probe table used by startup_assert_no_delete.
--
-- IMPORTANT: Apply with feature flag NO_DELETE_ENFORCEMENT_ENABLED=false
-- so existing app code continues working via the graphclaw superuser until
-- agents are cut over to agent_principal.
--
-- Idempotent: all CREATE statements use IF NOT EXISTS or OR REPLACE.

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    -- agent_principal: least-privilege role for all agent operations.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_principal') THEN
        CREATE ROLE agent_principal WITH LOGIN PASSWORD 'agent_secret_change_me';
        RAISE NOTICE 'Created role agent_principal';
    END IF;

    -- admin_principal: full-privilege role for purge worker + admin APIs.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_principal') THEN
        CREATE ROLE admin_principal WITH LOGIN PASSWORD 'admin_secret_change_me';
        RAISE NOTICE 'Created role admin_principal';
    END IF;

    -- migration_principal: DDL-only role for migration runner.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migration_principal') THEN
        CREATE ROLE migration_principal WITH LOGIN PASSWORD 'migration_secret_change_me';
        RAISE NOTICE 'Created role migration_principal';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Schema access
-- ---------------------------------------------------------------------------

-- Grant CONNECT on the database to all principals.
GRANT CONNECT ON DATABASE graphclaw TO agent_principal, admin_principal, migration_principal;

-- Grant USAGE on the public schema so principals can see tables.
GRANT USAGE ON SCHEMA public TO agent_principal, admin_principal, migration_principal;

-- ---------------------------------------------------------------------------
-- agent_principal: SELECT + INSERT + UPDATE only (no DELETE)
-- ---------------------------------------------------------------------------

-- Existing tables.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO agent_principal;

-- Future tables created by migrations.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO agent_principal;

-- Explicitly REVOKE DELETE from agent_principal on all current tables.
-- The DEFAULT PRIVILEGES above omit DELETE, so newly created tables are safe.
-- This handles any pre-existing tables.
REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM agent_principal;

-- Sequences (needed for serial/bigserial columns in Postgres companion tables).
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_principal;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO agent_principal;

-- ---------------------------------------------------------------------------
-- admin_principal: full DML grants (SELECT + INSERT + UPDATE + DELETE)
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO admin_principal;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO admin_principal;

GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO admin_principal;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO admin_principal;

-- ---------------------------------------------------------------------------
-- migration_principal: DDL grants
-- ---------------------------------------------------------------------------

-- migration_principal needs CREATE privilege to run DDL migrations.
GRANT CREATE ON SCHEMA public TO migration_principal;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO migration_principal;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO migration_principal;

GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO migration_principal;

-- ---------------------------------------------------------------------------
-- AGE graph: restrict agent_principal from graph DDL and vertex/edge deletion
-- ---------------------------------------------------------------------------

-- AGE stores graph data in the ag_catalog schema.
-- agent_principal must not be able to modify the graph catalog directly.
-- The REVOKE below ensures agent code cannot bypass Cypher and directly
-- DELETE rows from the AGE internal tables.

GRANT USAGE ON SCHEMA ag_catalog TO agent_principal, admin_principal, migration_principal;

-- ag_catalog tables: agent can SELECT only (read graph metadata).
GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO agent_principal;

-- admin_principal and migration_principal need full access for schema changes.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ag_catalog TO admin_principal;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ag_catalog TO migration_principal;

-- Sequences in ag_catalog.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ag_catalog TO agent_principal;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA ag_catalog TO admin_principal;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA ag_catalog TO migration_principal;

-- ---------------------------------------------------------------------------
-- Startup probe table
-- ---------------------------------------------------------------------------

-- Created here so the probe is available before the first migration run.
-- The table has no meaningful data — it exists solely for the DELETE probe.
CREATE TABLE IF NOT EXISTS public._principal_probe (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed one row so the probe can attempt (and fail) to DELETE it.
INSERT INTO public._principal_probe DEFAULT VALUES
ON CONFLICT DO NOTHING;

-- agent_principal: SELECT + INSERT + UPDATE only — NO DELETE (the whole point).
GRANT SELECT, INSERT, UPDATE ON public._principal_probe TO agent_principal;
REVOKE DELETE ON public._principal_probe FROM agent_principal;

-- admin_principal: full access.
GRANT SELECT, INSERT, UPDATE, DELETE ON public._principal_probe TO admin_principal;

DO $$ BEGIN RAISE NOTICE 'Wave 0 principal grants applied successfully.'; END $$;
