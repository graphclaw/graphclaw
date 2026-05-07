# GraphClaw Agents Index

This index tracks backend agent definition files and their current lifecycle status.

## Current Utility Agents

| Agent File | Primary Use | Status | Notes |
|---|---|---|---|
| `architect-reviewer.md` | Architecture review and system-level quality checks | active | Use for structural review cycles. |
| `documentation-reviewer.md` | Documentation quality and consistency checks | active | Use for doc drift and quality audits. |
| `opus-reviewer.md` | High-level review and architecture decisions | active | Keep as strategic reviewer profile. |
| `phase1-reviewer.md` | Historical phase-specific review profile | active | deprecate-candidate if phase-specific review profiles are consolidated. |

## Workstream Agents (Phase Build Profiles)

| Agent File | Scope | Status | Marker |
|---|---|---|---|
| `ws-a-database.md` | Database and AGE workstream | active | legacy-phase-profile |
| `ws-b-models.md` | Data models and schema workstream | active | legacy-phase-profile |
| `ws-c-docker.md` | Docker/infrastructure workstream | active | legacy-phase-profile |
| `ws-d-scoring-state.md` | Scoring and state machine workstream | active | legacy-phase-profile |
| `ws-e-cli-agent.md` | CLI and orchestration workstream | active | legacy-phase-profile |
| `ws-f-channel-gateway.md` | Channel gateway workstream | active | legacy-phase-profile |
| `ws-g-trigger-engine.md` | Trigger engine workstream | active | legacy-phase-profile |
| `ws-h-skill-runtime.md` | Skill runtime workstream | active | legacy-phase-profile |
| `ws-i-storage-logging.md` | Storage and logging workstream | active | legacy-phase-profile |
| `ws-j-inbound-protocol.md` | Inbound protocol workstream | active | legacy-phase-profile |
| `ws-k-briefing-status.md` | Briefing/status workstream | active | legacy-phase-profile |

## Conservative Deprecation Markers

- Workstream agent files are retained and marked `legacy-phase-profile`.
- These are deprecate-candidates for eventual consolidation into a smaller reusable agent set.
- No removals occur in this phase.

## Policy

- Keep all existing agent files intact while references migrate.
- Any future rename, merge, or removal must be preceded by updates in docs redirect maps and this index.
