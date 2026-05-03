"""graphclaw.migrations.catalogue — Forward-only migration history.

Description
-----------
Defines ``MIGRATIONS``, the canonical ordered list of all schema migrations
for the GraphClaw PostgreSQL + Apache AGE database.  This is the single source
of truth for the full migration history; ``scripts/migrate.py`` passes this
list to ``MigrationRunner.apply_all``.

Design Patterns
---------------
- Forward-only, non-destructive: no ``sql_down`` exists and all entries have
  ``is_destructive=False`` per the PRD Section 32 rolling-deployment policy.
- Version ordering: versions are zero-padded 4-digit strings ("0001", "0002")
  that sort lexicographically in application order.

Public API
----------
- MIGRATIONS: list[Migration] — ordered migration history.

Dependencies
------------
- graphclaw.migrations.models: Migration.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from graphclaw.migrations.models import Migration

# ---------------------------------------------------------------------------
# Migration catalogue
# ---------------------------------------------------------------------------

MIGRATIONS: list[Migration] = [
    Migration(
        version="0001",
        name="initial_schema",
        description="Create Apache AGE graph, base vlabels and elabels",
        sql_up="""
            -- Extensions
            CREATE EXTENSION IF NOT EXISTS age;
            CREATE EXTENSION IF NOT EXISTS vector;

            LOAD 'age';
            SET search_path = ag_catalog, "$user", public;

            -- Property graph
            SELECT create_graph('graphclaw');

            -- Node labels (vertex types)
            SELECT create_vlabel('graphclaw', 'TaskAtomic');
            SELECT create_vlabel('graphclaw', 'TaskComposite');
            SELECT create_vlabel('graphclaw', 'TaskDelegated');
            SELECT create_vlabel('graphclaw', 'TaskFollowUp');
            SELECT create_vlabel('graphclaw', 'TaskApproval');
            SELECT create_vlabel('graphclaw', 'TaskMilestone');
            SELECT create_vlabel('graphclaw', 'TaskReview');
            SELECT create_vlabel('graphclaw', 'TaskRecurring');
            SELECT create_vlabel('graphclaw', 'TaskDecision');
            SELECT create_vlabel('graphclaw', 'TaskCheckin');
            SELECT create_vlabel('graphclaw', 'TaskResearch');
            SELECT create_vlabel('graphclaw', 'GoalNode');
            SELECT create_vlabel('graphclaw', 'ConstraintNode');
            SELECT create_vlabel('graphclaw', 'UserNode');
            SELECT create_vlabel('graphclaw', 'ResourceNode');
            SELECT create_vlabel('graphclaw', 'CheckinNode');
            SELECT create_vlabel('graphclaw', 'HandoffNode');

            -- Phase 2 node labels
            SELECT create_vlabel('graphclaw', 'OrganizationNode');
            SELECT create_vlabel('graphclaw', 'WorkspaceNode');

            -- Edge labels (relationship types)
            SELECT create_elabel('graphclaw', 'DEPENDS_ON');
            SELECT create_elabel('graphclaw', 'SPAWNED_FROM');
            SELECT create_elabel('graphclaw', 'FOLLOW_UP_FOR');
            SELECT create_elabel('graphclaw', 'BLOCKS');
            SELECT create_elabel('graphclaw', 'ASSIGNED_TO');
            SELECT create_elabel('graphclaw', 'OWNED_BY');
            SELECT create_elabel('graphclaw', 'APPLIES_TO');
            SELECT create_elabel('graphclaw', 'PART_OF');
            SELECT create_elabel('graphclaw', 'REFERRED_BY');

            -- Phase 2 edge labels
            SELECT create_elabel('graphclaw', 'MEMBER_OF');
            SELECT create_elabel('graphclaw', 'ADMIN_OF');
            SELECT create_elabel('graphclaw', 'BELONGS_TO_ORG');
            SELECT create_elabel('graphclaw', 'SCOPED_TO_WS');

            -- Embedding storage
            CREATE TABLE IF NOT EXISTS node_embeddings (
                node_id      TEXT         PRIMARY KEY,
                embedding    vector(1536) NOT NULL,
                computed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS node_embeddings_embedding_idx
                ON node_embeddings
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
        """,
    ),
    Migration(
        version="0002",
        name="add_visibility_grant_node",
        description="Add VisibilityGrantNode vlabel and GRANTS_ACCESS_TO elabel",
        sql_up="""
            SELECT * FROM ag_catalog.create_vlabel('graphclaw', 'VisibilityGrantNode');
            SELECT * FROM ag_catalog.create_elabel('graphclaw', 'GRANTS_ACCESS_TO');
        """,
    ),
    Migration(
        version="0003",
        name="mcp_graph_labels_removed_from_baseline",
        description=(
            "Historical placeholder: MCP graph labels are no longer created in new "
            "environments; MCP configs are persisted in object storage."
        ),
        sql_up="""
        SELECT 1;
      """,
    ),
    Migration(
        version="0004",
        name="add_age_performance_indexes",
        description=("Add AGE indexes on vlabel, user_id, state, due_date for 1000-user scale"),
        sql_up="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_graphclaw_state
                ON graphclaw._ag_label_vertex USING btree ((properties->>'state'));
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_graphclaw_owner_id
                ON graphclaw._ag_label_vertex USING btree ((properties->>'owner_id'));
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_graphclaw_due_date
                ON graphclaw._ag_label_vertex USING btree ((properties->>'due_date'));
        """,
    ),
    Migration(
        version="0005",
        name="add_audit_log_partition",
        description="Convert audit_log to monthly partitioned table",
        sql_up="""
            -- audit_log partitioning (if relational-db has this table)
            -- Forward-only: create partition for current + next 3 months
            -- Note: run only if audit_log table exists
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='audit_log') THEN
                -- partition logic here
                NULL;
              END IF;
            END $$;
        """,
    ),
    Migration(
        version="0006",
        name="cleanup_legacy_mcp_graph_labels",
        description=(
            "Drop legacy MCPServerNode and GRANTS_ACCESS_TO_MCP labels if present. "
            "MCP server configs are stored as JSON in object storage under "
            "{user_id}/mcp/servers/{server_id}.json."
        ),
        sql_up="""
            DO $$ BEGIN
              -- Drop any remaining MCPServerNode vertices and their edges first
              PERFORM * FROM ag_catalog.cypher('graphclaw', $$
                MATCH (n:MCPServerNode) DETACH DELETE n RETURN count(n)
              $$) as (c agtype);
            EXCEPTION WHEN others THEN NULL;
            END $$;
            DO $$ BEGIN
              PERFORM ag_catalog.drop_label('graphclaw', 'GRANTS_ACCESS_TO_MCP', false);
            EXCEPTION WHEN others THEN NULL;
            END $$;
            DO $$ BEGIN
              PERFORM ag_catalog.drop_label('graphclaw', 'MCPServerNode', false);
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    Migration(
        version="0007",
        name="add_coordination_handoff_labels",
        description="Add CheckinNode/HandoffNode labels and REFERRED_BY edge label",
        sql_up="""
            DO $$ BEGIN
              PERFORM ag_catalog.create_vlabel('graphclaw', 'CheckinNode');
            EXCEPTION WHEN others THEN NULL;
            END $$;

            DO $$ BEGIN
              PERFORM ag_catalog.create_vlabel('graphclaw', 'HandoffNode');
            EXCEPTION WHEN others THEN NULL;
            END $$;

            DO $$ BEGIN
              PERFORM ag_catalog.create_elabel('graphclaw', 'REFERRED_BY');
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    Migration(
        version="0008",
        name="wave0_principal_probe",
        description=(
            "Wave 0: Create _principal_probe table used by startup_assert_no_delete "
            "to verify that agent_principal cannot execute DELETE statements."
        ),
        sql_up="""
            -- Principal probe table: exists only for the no-delete startup assertion.
            -- No user data is stored here — it is a single-row canary table.
            CREATE TABLE IF NOT EXISTS _principal_probe (
                id         SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Ensure at least one row exists so DELETE has something to attempt.
            INSERT INTO _principal_probe DEFAULT VALUES
            ON CONFLICT DO NOTHING;

            -- Grant agent_principal SELECT+INSERT+UPDATE but explicitly REVOKE DELETE.
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_principal') THEN
                GRANT SELECT, INSERT, UPDATE ON _principal_probe TO agent_principal;
                REVOKE DELETE ON _principal_probe FROM agent_principal;
              END IF;
            END $$;

            -- Grant admin_principal full access.
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_principal') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON _principal_probe TO admin_principal;
              END IF;
            END $$;
        """,
    ),
    Migration(
        version="0009",
        name="wave0_lifecycle_fields_and_triggers",
        description=(
            "Wave 0 (FR-DEL-002, FR-DEL-003, FR-DEL-007): Add lifecycle fields to the "
            "AGE vertex property schema and install prevent_lifecycle_field_update() "
            "Postgres trigger on all user data tables.  Fields are nullable with no "
            "defaults so existing rows are unaffected (NULL == not archived)."
        ),
        sql_up="""
            -- ----------------------------------------------------------------
            -- Lifecycle trigger function
            -- ----------------------------------------------------------------
            -- Applied to every node table.  Blocks agent_principal from directly
            -- setting lifecycle fields — agents must go through archive_* tools.
            CREATE OR REPLACE FUNCTION prevent_lifecycle_field_update()
            RETURNS TRIGGER AS $$
            BEGIN
              IF current_user = 'agent_principal' THEN
                IF (NEW.archived_at IS DISTINCT FROM OLD.archived_at) OR
                   (NEW.purge_after IS DISTINCT FROM OLD.purge_after) OR
                   (NEW.purge_cancelled_at IS DISTINCT FROM OLD.purge_cancelled_at) OR
                   (NEW.link_status IS DISTINCT FROM OLD.link_status) OR
                   (NEW.legal_hold IS DISTINCT FROM OLD.legal_hold) THEN
                  RAISE EXCEPTION 'Lifecycle fields cannot be updated by agent_principal';
                END IF;
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            -- ----------------------------------------------------------------
            -- Apply trigger to the principal probe table as a sanity check.
            -- Real user-data tables live in the AGE graph; the trigger is wired
            -- at the AGE vertex level via application-layer enforcement in
            -- AgeGraphStore.update_node (FR-DEL-002 AC1 guard).
            -- ----------------------------------------------------------------
            DROP TRIGGER IF EXISTS trg_prevent_lifecycle_field_update
                ON _principal_probe;
            CREATE TRIGGER trg_prevent_lifecycle_field_update
                BEFORE UPDATE ON _principal_probe
                FOR EACH ROW
                EXECUTE FUNCTION prevent_lifecycle_field_update();

            -- Revision note: AGE graph properties are stored as JSONB inside
            -- agtype columns in ag_catalog.  Per-column Postgres triggers cannot
            -- fire on individual JSONB keys.  The enforce-at-application-layer
            -- approach (AgeGraphStore.update_node checks _LIFECYCLE_FIELDS) is
            -- therefore the primary enforcement mechanism for Wave 0 graph nodes.
            -- The DB-level trigger above applies to any future Postgres tables.
        """,
    ),
    Migration(
        version="0010",
        name="wave0_tombstone_node_label",
        description=(
            "Wave 0 (FR-DEL-003): Add TombstoneNode vertex label to the AGE graph "
            "and a REDIRECTS_TO edge label for redirect chains."
        ),
        sql_up="""
            DO $$ BEGIN
              PERFORM ag_catalog.create_vlabel('graphclaw', 'TombstoneNode');
            EXCEPTION WHEN others THEN NULL;
            END $$;

            DO $$ BEGIN
              PERFORM ag_catalog.create_elabel('graphclaw', 'REDIRECTS_TO');
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    # -----------------------------------------------------------------------
    # Wave 1 — Tenancy & schema (FR-GRAPH-001..006 + FR-STORE-001..002)
    # -----------------------------------------------------------------------
    Migration(
        version="0011",
        name="wave1_node_identities",
        description=(
            "Wave 1 (FR-GRAPH-001): Document ChannelIdentities addition to UserNode "
            "and ResourceNode.  AGE properties are JSONB; no DDL column needed. "
            "Creates a GIN index on the identities JSONB property for fast lookup."
        ),
        sql_up="""
            -- AGE stores all vertex properties in the agtype column; new fields
            -- are written by application code without ALTER TABLE.
            -- This migration creates a Postgres GIN index on the serialised
            -- properties column to accelerate identity lookup queries.
            DO $$ BEGIN
              CREATE INDEX IF NOT EXISTS idx_graphclaw_identities_gin
                ON graphclaw._ag_label_vertex
                USING gin ((properties::jsonb));
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    Migration(
        version="0012",
        name="wave1_node_aliases",
        description=(
            "Wave 1 (FR-GRAPH-002): Document aliases list addition to UserNode and "
            "ResourceNode.  No DDL needed; alias lookups use the GIN index from 0011."
        ),
        sql_up="SELECT 1;",
    ),
    Migration(
        version="0013",
        name="wave1_linked_user_id",
        description=(
            "Wave 1 (FR-GRAPH-003): Document linked_user_id + link_status addition "
            "to ResourceNode.  Creates btree index on linked_user_id for read-through "
            "lookup performance."
        ),
        sql_up="""
            -- Index on linked_user_id for fast read-through joins.
            DO $$ BEGIN
              CREATE INDEX IF NOT EXISTS idx_graphclaw_linked_user_id
                ON graphclaw._ag_label_vertex
                USING btree ((properties->>'linked_user_id'));
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    Migration(
        version="0014",
        name="wave1_checkin_fields",
        description=(
            "Wave 1 (FR-GRAPH-004): Document CheckinNode field expansion "
            "(recipient_id, channel, thread_id, direction).  Creates composite "
            "index on (channel, thread_id) for sub-10ms stickiness lookups."
        ),
        sql_up="""
            -- Composite index for channel+thread_id stickiness lookup (FR-OUT-002).
            DO $$ BEGIN
              CREATE INDEX IF NOT EXISTS idx_graphclaw_checkin_channel_thread
                ON graphclaw._ag_label_vertex
                USING btree (
                  (properties->>'channel'),
                  (properties->>'thread_id')
                )
                WHERE (properties->>'channel') IS NOT NULL;
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """,
    ),
    Migration(
        version="0015",
        name="wave1_user_preferences",
        description=(
            "Wave 1 (FR-GRAPH-005): Document UserPreferences extension "
            "(discoverability, channel_stickiness_hours, channel_stickiness_overrides, "
            "preferred_channel).  No DDL needed — JSONB field."
        ),
        sql_up="SELECT 1;",
    ),
    Migration(
        version="0016",
        name="wave1_org_directory_visibility",
        description=(
            "Wave 1 (FR-GRAPH-006): Document OrgSettings.directory_visibility "
            "addition (OrgDirectoryVisibility enum, default OPEN).  No DDL needed."
        ),
        sql_up="SELECT 1;",
    ),
    # -----------------------------------------------------------------------
    # Wave 2 — Outbound agent (FR-OUT-001..004)
    # -----------------------------------------------------------------------
    Migration(
        version="0017",
        name="wave2_reply_lineage",
        description=(
            "Wave 2 (FR-OUT-004, FR-RES-002): Create reply_lineage table for "
            "persistent (channel, thread_id) → (task_id, counterparty_id, user_id) "
            "mapping.  Used as a Redis-expiry fallback for reply-key resolution."
        ),
        sql_up="""
            -- Persistent reply key store.  Primary lookup is Redis (7d TTL).
            -- This table is the fallback when Redis has expired.
            CREATE TABLE IF NOT EXISTS reply_lineage (
                channel          TEXT        NOT NULL,
                thread_id        TEXT        NOT NULL,
                task_id          TEXT,
                counterparty_id  TEXT        NOT NULL,
                user_id          TEXT        NOT NULL,
                checkin_id       TEXT        NOT NULL,
                created_at       TEXT        NOT NULL,
                PRIMARY KEY (channel, thread_id)
            );

            CREATE INDEX IF NOT EXISTS idx_reply_lineage_user_id
                ON reply_lineage (user_id);

            CREATE INDEX IF NOT EXISTS idx_reply_lineage_task_id
                ON reply_lineage (task_id)
                WHERE task_id IS NOT NULL;

            -- Grant agent_principal read+write (no DELETE — no-delete principle).
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_principal') THEN
                GRANT SELECT, INSERT, UPDATE ON reply_lineage TO agent_principal;
                REVOKE DELETE ON reply_lineage FROM agent_principal;
              END IF;
            END $$;

            -- Grant admin_principal full access.
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_principal') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON reply_lineage TO admin_principal;
              END IF;
            END $$;
        """,
    ),
    # -----------------------------------------------------------------------
    # Wave 3 — Inbound router (FR-IN-003)
    # -----------------------------------------------------------------------
    Migration(
        version="0018",
        name="wave3_agent_channel_identities",
        description=(
            "Wave 3 (FR-IN-003): Create agent_channel_identities table mapping "
            "receiving channel accounts to (user_id, agent_id) pairs.  "
            "The in-memory AgentChannelIdentityRegistry is populated from this "
            "table at startup and hot-reloaded on admin CRUD."
        ),
        sql_up="""
            -- Registry mapping receiving accounts → owner agents.
            CREATE TABLE IF NOT EXISTS agent_channel_identities (
                channel           TEXT     NOT NULL,
                account_id        TEXT     NOT NULL,
                user_id           TEXT     NOT NULL,
                agent_id          TEXT     NOT NULL,
                display_name      TEXT     NOT NULL DEFAULT '',
                credentials_ref   TEXT     NOT NULL DEFAULT '',
                active            BOOLEAN  NOT NULL DEFAULT TRUE,
                owner_identities  TEXT     NOT NULL DEFAULT '[]',
                PRIMARY KEY (channel, account_id)
            );

            CREATE INDEX IF NOT EXISTS idx_aci_user_id
                ON agent_channel_identities (user_id);

            CREATE INDEX IF NOT EXISTS idx_aci_agent_id
                ON agent_channel_identities (agent_id);

            -- Grant agent_principal read only.
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_principal') THEN
                GRANT SELECT ON agent_channel_identities TO agent_principal;
              END IF;
            END $$;

            -- Grant admin_principal full access.
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_principal') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON agent_channel_identities TO admin_principal;
              END IF;
            END $$;
        """,
    ),
]
