# Copilot Instructions - GraphClaw Lifecycle Guardrails

These instructions are mandatory for all future development waves, release cycles, and maintenance sessions.

## Primary Objective

Keep delivery consistent with the PR-first software lifecycle established during launch hardening:

- issue-driven planning
- branch-based implementation
- PR-only merge path to main
- evidence-backed issue closeout
- release discipline through automation

## Do

1. Use main as the only default integration branch.
2. Start every non-trivial change from a tracked issue or wave item.
3. Create short-lived branches from origin/main for all work.
4. Use Conventional Commit style in PR titles and commits.
5. Use DCO sign-off for commits (`git commit -s`).
6. Open a PR for every code, docs, CI, or config change.
7. Link each PR to its tracking issue and describe scope and validation.
8. Run the backend quality gate before merge:
   - `ruff check --fix src/ tests/ && ruff format src/ tests/ && pytest tests/`
9. Keep launch/evidence docs synchronized with implementation changes when applicable.
10. Verify required checks are green before merge.
11. Use squash merge and delete feature branches after merge.
12. Post evidence and resolution notes on issues before closing them.
13. Record explicit waiver notes when closing with accepted exceptions.
14. Use release-please and release workflows as the canonical release path.
15. Validate release artifacts after publish (PyPI, GHCR, attestations, docs links as applicable).

## Do Not

1. Do not push directly to main.
2. Do not commit directly on main for normal feature development.
3. Do not use master for new work, workflow filters, or documentation references.
4. Do not bypass PR checks unless there is a documented maintainer-approved emergency.
5. Do not merge PRs with failing required checks.
6. Do not close tracking issues without evidence, links, or explicit waiver rationale.
7. Do not create ad-hoc release tags outside release automation unless emergency procedure is documented.
8. Do not leave parent tracker issues stale after child issue state changes.

## Standard Delivery Flow

1. Confirm issue scope and done criteria.
2. Branch from origin/main.
3. Implement incrementally with tests.
4. Update documentation and evidence in the same change set.
5. Run local quality gate.
6. Open PR with linked issue and validation notes.
7. Wait for required checks.
8. Merge through PR (squash), then delete branch.
9. Add closeout comment to issue with links to PR, evidence, and any waivers.
10. Close issue only after verified completion.

## Release Flow Requirements

1. Release PRs are created/managed by release-please.
2. Release publication occurs through release workflows.
3. Post-release validation must include install and artifact checks.
4. Any release exception must be documented in issue comments and evidence docs.

## Conflict Rule

If instructions conflict across docs, apply the stricter rule that preserves:

- branch protection intent
- PR-first workflow
- test and quality gates
- evidence-backed traceability
