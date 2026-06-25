# Distribution Expansion Criteria

This document defines adoption signals, ownership, and go/no-go checkpoints for post-launch distribution expansion.
Last updated: 2026-05-29.

## Scope

Channels evaluated:

- Helm chart
- Homebrew tap
- cockpit npm package

## Ownership Model

- Decision owner: Core maintainers
- Technical owner for packaging pipeline: Infra maintainers
- Technical owner for release validation and docs: Repo maintainers for each artifact

## Common Entry Gate (All Channels)

All channels must pass these shared criteria before launch work starts:

1. Last two release cycles complete without critical rollback.
2. Current release artifacts are signed/provenanced and validation runbooks are current.
3. Support boundaries are documented and publicly linked.
4. A maintainer is explicitly assigned for ongoing triage and update ownership.

## Channel Criteria

## Helm Chart

Adoption signals:

- At least 10 distinct self-host deploy requests or issues asking for Helm-based install.
- At least 3 external users validate compose deployment and request Kubernetes parity.

Go/no-go checkpoints:

1. Chart values can configure backend, cockpit, Postgres/AGE dependency mode, and secrets sources.
2. Upgrade path between two consecutive minor versions is validated in CI.
3. Rollback procedure is documented and tested.

Owner assignment:

- Primary: Infra maintainers
- Secondary: Backend maintainers

## Homebrew Tap

Adoption signals:

- At least 20 CLI-first users request macOS/Linux package-manager install.
- `pip install graphclaw` support burden shows repeated virtualenv friction reports.

Go/no-go checkpoints:

1. Bottles and formula install cleanly on current macOS versions.
2. Formula checksum update process is automated in release flow.
3. CLI smoke tests run on installed artifact in CI.

Owner assignment:

- Primary: Backend maintainers
- Secondary: Infra maintainers

## cockpit npm Package

Adoption signals:

- At least 5 external requests to embed cockpit components or consume published UI artifacts.
- Clear use case exists beyond dockerized full-UI deployment.

Go/no-go checkpoints:

1. Public API surface is defined and semver stability policy is documented.
2. Package excludes internal-only routes, test fixtures, and private configuration paths.
3. Vulnerability scanning and dependency policy checks pass for publish candidate.

Owner assignment:

- Primary: Cockpit maintainers
- Secondary: Core maintainers

## Decision Process

1. Review adoption signals quarterly.
2. If any channel meets entry gate plus channel-specific signals, open an implementation issue for that channel.
3. Execute one channel at a time to avoid release-surface expansion risk.
4. Record decision and evidence links in launch planning tracker.
