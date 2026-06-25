# Versioning Policy

GraphClaw uses Semantic Versioning (SemVer): MAJOR.MINOR.PATCH.

## SemVer Meaning

- MAJOR: incompatible API or behavior changes.
- MINOR: backward-compatible feature additions.
- PATCH: backward-compatible bug/security fixes.

## Pre-1.0 Note

Before 1.0, MINOR releases may still include breaking changes when required by product evolution. These changes must be clearly documented in release notes.

## Pre-release Tags

Use pre-release tags for validation cycles:

- X.Y.Z-alpha.N
- X.Y.Z-beta.N
- X.Y.Z-rc.N

## Artifact Version Alignment

A backend release tag vX.Y.Z is expected to produce:

- PyPI package: graphclaw==X.Y.Z
- GHCR image tag: ghcr.io/graphclaw/graphclaw:X.Y.Z
- GitHub Release: vX.Y.Z

## Support Window

The target support window is:

- latest MINOR
- previous MINOR

Security fixes should be applied first to currently supported minors.

## Compatibility with Cockpit

Backend and cockpit versions evolve independently. Compatibility should be tracked in deployment docs and release notes when interface changes are introduced.
