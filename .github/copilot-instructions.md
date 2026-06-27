# Copilot Instructions - GraphClaw Fast-Lane Guardrails

These instructions are mandatory for the current solo-maintainer development mode.

## Primary Objective

Deliver faster by shipping small, verified increments with clear traceability:

- direct push is the default path for routine work
- commit frequently in logical blocks
- use PRs only when risk, scope, or collaboration requires it
- keep release safety, security scanning, and automation intact

## Active Operating Mode (Solo Maintainer)

While there is a single active maintainer:

- direct pushes to `main` are allowed for the repo owner
- opening a PR for every request is not required
- DCO is not a blocking branch-protection requirement
- Conventional Commit messages remain mandatory

Re-enable strict multi-maintainer policy (PR-only, required approvals, and required DCO) when another active maintainer is onboarded.

## Commit Cadence (Required)

1. Commit after each completed logical block (one fix, one behavior change, or one coherent refactor).
2. Prefer multiple small commits over one mixed-scope commit.
3. Push frequently so GitHub history reflects real progress.
4. If a request includes multiple independent blocks, use separate commits for each block.

## Do

1. Use `main` as the default integration branch.
2. Start from a tracked issue or wave item when one exists; require issues for multi-PR efforts and long-running epics.
3. Keep Conventional Commit style in all commits and PR titles.
4. Run targeted checks for the changed area before each logical-block commit.
5. Run the full backend quality gate for release-sensitive, cross-cutting, or high-risk changes:
   - `ruff check --fix src/ tests/ && ruff format src/ tests/ && pytest tests/`
6. Use PRs for risky, cross-cutting, collaborative, or release-sensitive changes.
7. Keep docs and evidence synchronized when behavior, contracts, or operations change.
8. Use release-please and release workflows as the canonical release path.
9. Validate published release artifacts (PyPI, GHCR, attestations, docs links).

## Do Not

1. Do not force-push or rewrite `main` history.
2. Do not skip validation entirely; run at least targeted checks before pushing.
3. Do not bundle unrelated work into one catch-all commit.
4. Do not bypass required checks for PR-based risky work unless emergency procedure is documented.
5. Do not create ad-hoc release tags outside release automation unless emergency procedure is documented.
6. Do not close tracked issues without evidence and resolution notes.

## Fast Delivery Flow (Default)

1. Confirm scope and split work into logical blocks.
2. Implement one logical block.
3. Run targeted validation for that block.
4. Commit immediately using Conventional Commit format.
5. Push directly to `main` for routine solo work.
6. Repeat per logical block until the request is complete.
7. Open a PR only when risk, coordination, or release impact justifies review.

## PR Decision Rule

Open a PR when any of the following are true:

- change is risky, cross-cutting, or hard to roll back
- change affects release flow, security, auth, data migration, or public contracts
- change needs asynchronous review from another maintainer

Otherwise, direct push with logical-block commits is the expected fast path.

## Release Flow Requirements

1. Release PRs are created and managed by release-please.
2. Release publication happens through release workflows.
3. Post-release validation must include install/runtime and artifact checks.
4. Any release exception must be documented in issue comments and evidence docs.

## Conflict Rule

If instructions conflict across docs, apply the stricter rule that preserves:

- repository safety and traceability
- test and quality standards
- release integrity
