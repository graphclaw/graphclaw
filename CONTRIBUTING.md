# Contributing to GraphClaw

Thank you for contributing to GraphClaw.

## Quick Start

1. Fork the repository and create a branch from `main`.
2. Make focused changes with tests.
3. Open a pull request using a Conventional Commit style title.
4. Add a DCO sign-off to every commit.

## Development Setup

Use the commands documented in README and CLAUDE.md for your environment.

Backend quality gate before opening a PR:

```bash
ruff check --fix src/ tests/
ruff format src/ tests/
pytest tests/
```

## Pull Request Requirements

- PR title must follow Conventional Commits (examples: `feat: ...`, `fix: ...`, `docs: ...`).
- All CI checks must pass.
- Include or update tests when behavior changes.
- Update docs when user-facing behavior changes.

## DCO Sign-off (Required)

All commits must include a sign-off line:

```text
Signed-off-by: Your Name <your-email@example.com>
```

Easiest method:

```bash
git commit -s -m "fix: describe change"
```

## Security Reports

Do not open public issues for vulnerabilities.
Use GitHub Security Advisories or email security@graphclaw.ai.

## Code of Conduct

By participating, you agree to the Code of Conduct in `CODE_OF_CONDUCT.md`.
