# Deprecation Policy

This policy defines how GraphClaw deprecates APIs, behaviors, and configuration fields.

## Baseline Rule

A deprecated item must remain available for at least one full MINOR release window before removal.

## Deprecation Lifecycle

1. Announce
- Mark the item as deprecated in docs and release notes.
- Provide a clear replacement path.

2. Warning Period
- Keep backward compatibility during at least one MINOR cycle.
- Add explicit warnings where practical (logs, docs, API notes).

3. Removal
- Remove only after the warning window completes.
- Document removal and migration steps in release notes.

## Required Documentation

Every deprecation must include:

- What is deprecated.
- Why it is deprecated.
- What replaces it.
- Earliest planned removal release.

## Emergency Exceptions

Security or compliance incidents may require accelerated removal. In that case:

- Explain the risk and reason for acceleration.
- Provide mitigation or migration guidance immediately.
- Record the exception in release notes.
