# Documentation Governance

## Scope

This policy defines how documentation is authored, updated, archived, and validated in the backend repository.

## Canonical Sources

- Product requirements: `docs/graphclaw-requirements.md`
- Build status and sequencing: `docs/planning/build-plan.md`
- Architecture contracts: `docs/architecture/`
- Testing policy: `docs/testing/master-strategy.md`

## Required Update Triggers

Update documentation in the same change whenever you modify:

- API endpoints, payloads, or auth behavior.
- Architecture boundaries, factories, interfaces, or plugin backends.
- Message broker queues, logging event schemas, or storage paths.
- Testing gates, conventions, or inventory registration rules.
- Build phase status, completion markers, or workstream scope.

## Archive Policy

- Use archive-first workflow for superseded docs.
- Move outdated documents into `docs/archive/` with date and cohort naming.
- Do not hard-delete until links and references are validated.

## Redirect Policy

- Track every move or rename in `docs/redirects.md`.
- Keep redirect mappings through at least one release cycle.

## Quality Gates For Documentation Changes

Before merge, run:

- `ruff check --fix src/ tests/`
- `ruff format src/ tests/`
- `pytest tests/`

Also verify:

- Updated docs have no contradictory phase/status claims.
- New links resolve correctly.
- Source-of-truth docs are updated when derivative docs are touched.

## Commit Discipline

- Use requirement or wave scoped commits: `feat(wave-N): description`.
- Do not mix unrelated documentation migrations into feature commits.
- Include concise migration notes in commit body for moved/archived files.

## Drift Audit Cadence

- Run a monthly documentation drift review.
- Reconcile `CLAUDE.md`, `docs/planning/build-plan.md`, and canonical PRD status claims.
- Validate inventory docs and test ID registries remain synchronized.
