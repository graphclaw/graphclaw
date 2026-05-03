"""graphclaw.agent.policies — Per-user policy file substrate (FR-POL-001, FR-STORE-002).

Description
-----------
Loads, caches, and evaluates per-user agent policy files stored in MinIO at
``{user_id}/agents/{agent_id}/policies/*.md``.  Each file has a YAML frontmatter
block parsed into a typed Pydantic schema and a plain markdown body loaded
verbatim into the agent system prompt.

Design Patterns
---------------
- Loader: reads from StorageClient; Redis-cached for 15 min (same TTL as profile.md).
- Schemas: one Pydantic model per policy type with typed frontmatter fields.
- Evaluator: stateless function that returns allow/escalate given an intent + policy.

Public API
----------
- PolicyLoader: async load/cache per-user policy files.
- evaluate_outbound_intent: pre-LLM gate for outbound actions (FR-OUT-003).
"""
