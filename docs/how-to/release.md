# Release Process (Maintainers)

This guide defines the backend release flow for graphclaw.

## Prerequisites

- You have maintainer write access to graphclaw/graphclaw.
- Branch protection checks on main are green.
- PR title and commits follow Conventional Commits + DCO.
- Local quality gate passes:
  - ruff check --fix src/ tests/
  - ruff format src/ tests/
  - pytest tests/

## Standard Release Flow

1. Merge the release-please pull request on main.
2. Confirm release-please creates the release tag (vX.Y.Z).
3. The release workflow (release.yml) runs automatically on tag push.
4. Verify all release jobs succeed:
  - Build sdist + wheel
  - Build and push Docker image
  - Publish to PyPI
  - Attach Python artifacts to GitHub Release
5. Verify artifacts:
  - PyPI: https://pypi.org/project/graphclaw/
  - GHCR: ghcr.io/graphclaw/graphclaw
  - GitHub Release assets include python-dist, SBOM, and attestations.

## Required Post-Release Checks

- pip install graphclaw==X.Y.Z succeeds in a clean environment.
- docker pull ghcr.io/graphclaw/graphclaw:X.Y.Z succeeds.
- docker pull ghcr.io/graphclaw/graphclaw:latest succeeds.
- Release notes are present and accurate.

## Failure Handling

- Transient attestation failure (example: Rekor 502): rerun failed jobs for the same run.
- Duplicate version on PyPI: bump version and trigger a new release tag.
- GHCR push failure: verify package write permissions and rerun.

## Notes

- PyPI publishing uses trusted publishing (OIDC), not a static PyPI API token.
- Keep release workflow changes minimal and auditable.
