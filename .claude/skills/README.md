# GraphClaw Skills Index

This index tracks backend skills and conservative deprecation candidates.

## Core Domain Skills

| Skill Folder | Domain | Status |
|---|---|---|
| `graphclaw-state-machine/` | Task lifecycle state model | active |
| `graphclaw-scoring-algorithm/` | Scoring engine logic | active |
| `inbound-protocol-patterns/` | Inbound resolution and signal extraction | active |
| `trigger-engine-patterns/` | Trigger scheduling and cadence | active |
| `message-broker-patterns/` | Broker abstractions and queue patterns | active |
| `skill-agent-runtime/` | Skill runtime execution model | active |
| `storage-abstractions/` | Storage, secrets, and logging abstractions | active |
| `age-cypher-patterns/` | Apache AGE query conventions | active |

## Testing and Quality Skills

| Skill Folder | Domain | Status | Marker |
|---|---|---|---|
| `graphclaw-pytest-patterns/` | General pytest conventions | active |  |
| `graphclaw-test-patterns/` | Additional test conventions | active | deprecate-candidate (overlap with pytest patterns) |
| `graphclaw-integration-tests/` | Integration test patterns | active |  |
| `graphclaw-contract-tests/` | OpenAPI contract testing | active |  |
| `graphclaw-cli-tests/` | CLI test patterns | active |  |
| `graphclaw-test-scripts/` | Script conventions for validation/smoke checks | active |  |
| `graphclaw-agent-evals/` | Agent evaluation framework | active |  |
| `test-inventory-maintenance/` | Shared test inventory conventions | shared-active |  |

## Tooling and Schema Skills

| Skill Folder | Domain | Status |
|---|---|---|
| `graphclaw-docker-dev/` | Local Docker environment conventions | active |
| `graphclaw-ruff-conventions/` | Lint and formatting policy | active |
| `graphclaw-pydantic-schemas/` | Pydantic schema conventions | active |
| `graphclaw-cli-patterns/` | CLI patterns and command design | active |

## Generic Review Skills

These are useful but overlap across repositories.

- `code-architecture-review/` — active, deprecate-candidate for future shared/global location.
- `code-best-practices/` — active, deprecate-candidate for future shared/global location.
- `code-comment-review/` — active, deprecate-candidate for future shared/global location.
- `code-file-headers/` — active, deprecate-candidate for future shared/global location.
- `code-security-review/` — active, deprecate-candidate for future shared/global location.
- `code-simplification/` — active, deprecate-candidate for future shared/global location.

## Policy

- No removals in this phase.
- Use deprecate-candidate markers to prepare staged consolidation.
