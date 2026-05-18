-- infrastructure/postgres/init/01-schema.sql
-- Creates the graphclaw property graph, node/edge labels, and embedding table.
-- Requires 00-extensions.sql to have run first.

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Property graph --------------------------------------------------------------

SELECT create_graph('graphclaw');

-- Node labels (vertex types) --
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

-- Phase 2 node labels --
SELECT create_vlabel('graphclaw', 'OrganizationNode');
SELECT create_vlabel('graphclaw', 'WorkspaceNode');

-- Edge labels (relationship types) --
SELECT create_elabel('graphclaw', 'DEPENDS_ON');
SELECT create_elabel('graphclaw', 'SPAWNED_FROM');
SELECT create_elabel('graphclaw', 'FOLLOW_UP_FOR');
SELECT create_elabel('graphclaw', 'BLOCKS');
SELECT create_elabel('graphclaw', 'ASSIGNED_TO');
SELECT create_elabel('graphclaw', 'OWNED_BY');
SELECT create_elabel('graphclaw', 'APPLIES_TO');
SELECT create_elabel('graphclaw', 'PART_OF');
SELECT create_elabel('graphclaw', 'REFERRED_BY');

-- Phase 2 edge labels --
SELECT create_elabel('graphclaw', 'MEMBER_OF');
SELECT create_elabel('graphclaw', 'ADMIN_OF');
SELECT create_elabel('graphclaw', 'BELONGS_TO_ORG');
SELECT create_elabel('graphclaw', 'SCOPED_TO_WS');

-- Phase 3: Visibility grant node
SELECT create_vlabel('graphclaw', 'VisibilityGrantNode');

-- Phase 3: Visibility grant edge
SELECT create_elabel('graphclaw', 'GRANTS_ACCESS_TO');

-- Embedding storage -----------------------------------------------------------
-- Stores pre-computed embedding vectors for graph nodes (primarily tasks).
-- The application uses OpenAI's text-embedding-3-small model (1536 dimensions)
-- by default. Override via EMBEDDING_MODEL env var, but the dimension must remain
-- 1536 to use the existing IVFFlat index.
--
-- node_id mirrors the AGE vertex id cast to TEXT.

CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id      TEXT        PRIMARY KEY,
    embedding    vector(1536) NOT NULL,
    computed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- IVFFlat index for approximate nearest-neighbour search (cosine distance).
-- lists=100 is a reasonable default for early development data volumes.
CREATE INDEX IF NOT EXISTS node_embeddings_embedding_idx
    ON node_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
