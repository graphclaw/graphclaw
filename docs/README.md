# GraphClaw Documentation Hub

This index is the entry point for backend documentation and follows the same taxonomy model as the cockpit repository.

## Start Here

1. Product requirements: `docs/graphclaw-requirements.md`
2. Execution tracker: `docs/planning/build-plan.md`
3. Architecture map: `docs/architecture/README.md`
4. Active requirement bundles: `docs/requirements/`
5. Testing strategy: `docs/testing/master-strategy.md`
6. A2A API-plane alignment spike: `docs/planning/a2a-api-plane-alignment-spike.md`
7. Public roadmap (Now/Next/Later): `docs/planning/public-roadmap.md`
8. Distribution expansion criteria: `docs/planning/distribution-expansion-criteria.md`

## Taxonomy

- `docs/graphclaw-requirements.md`: canonical PRD.
- `docs/graphclaw-review-notes.md`: review findings and historical issue tracking.
- `docs/architecture/`: stable architecture references.
- `docs/requirements/`: active implementation requirement bundles.
- `docs/testing/`: test strategy, ADRs, scenario catalogs, and registries.
- `docs/governance/`: documentation lifecycle and quality policy.
- `docs/archive/`: historical records and completed-wave evidence.

## Source-of-Truth Matrix

- Product behavior and constraints: `docs/graphclaw-requirements.md`
- Delivery status and phase progression: `docs/planning/build-plan.md`
- A2A plane canonicalization spike artifacts: `docs/planning/a2a-api-plane-alignment-spike.md`
- Public roadmap and near-term priorities: `docs/planning/public-roadmap.md`
- Distribution expansion checkpoints: `docs/planning/distribution-expansion-criteria.md`
- System architecture and interfaces: `docs/architecture/`
- Active implementation tasks: `docs/requirements/`
- Testing conventions and gates: `docs/testing/master-strategy.md`
- Historical trail and completion evidence: `docs/archive/build-timeline.md`

## Maintenance Rules

- Archive first, remove later: move superseded docs into `docs/archive/` before deletion.
- Any file move or rename must be recorded in `docs/redirects.md`.
- If implementation changes API, architecture, logging, or tests, update the corresponding source-of-truth doc in the same change.
- Follow governance policy in `docs/governance/documentation-governance.md`.
