-- =============================================================================
-- GraphClaw: audit_log monthly partitioning
-- Creates the audit_log table as a partitioned table (PARTITION BY RANGE on
-- created_at) and pre-creates partitions for the current month + next 3 months.
-- Forward-only: adding new month partitions is non-destructive.
-- Run monthly via cron or migration 0005.
-- =============================================================================

-- Create parent table if not exists
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    ip_address  TEXT,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Create index on user_id for fast per-user queries
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action  ON audit_log (action, created_at DESC);

-- Helper: create a monthly partition
-- Call this script monthly, or let migration 0005 run it.
-- Example: for 2026-03 (current month per project context)
DO $$
DECLARE
    month_start DATE;
    month_end   DATE;
    partition_name TEXT;
BEGIN
    FOR i IN 0..3 LOOP
        month_start := DATE_TRUNC('month', NOW()) + (i || ' months')::INTERVAL;
        month_end   := month_start + '1 month'::INTERVAL;
        partition_name := 'audit_log_' || TO_CHAR(month_start, 'YYYY_MM');

        IF NOT EXISTS (
            SELECT 1 FROM pg_class WHERE relname = partition_name
        ) THEN
            EXECUTE FORMAT(
                'CREATE TABLE %I PARTITION OF audit_log FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );
            RAISE NOTICE 'Created partition: %', partition_name;
        ELSE
            RAISE NOTICE 'Partition already exists: %', partition_name;
        END IF;
    END LOOP;
END $$;
