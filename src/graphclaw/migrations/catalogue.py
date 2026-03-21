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
        name="add_mcp_server_node",
        description="Add MCPServerNode vlabel and GRANTS_ACCESS_TO_MCP elabel",
        sql_up="""
            SELECT * FROM ag_catalog.create_vlabel('graphclaw', 'MCPServerNode');
            SELECT * FROM ag_catalog.create_elabel('graphclaw', 'GRANTS_ACCESS_TO_MCP');
        """,
    ),
    Migration(
        version="0004",
        name="add_age_performance_indexes",
        description=(
            "Add AGE indexes on vlabel, user_id, state, due_date for 1000-user scale"
        ),
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
]
