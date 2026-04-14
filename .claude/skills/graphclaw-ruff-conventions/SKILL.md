---
name: graphclaw-ruff-conventions
description: Ruff lint and format conventions for GraphClaw. Use before any commit, PR, or when CI lint/format checks fail.
---

# GraphClaw Ruff Conventions

## CI Enforcement

The CI pipeline runs two ruff steps — both must pass:

1. `ruff check src/ tests/` — lint (unused imports, unsorted imports, f-strings, etc.)
2. `ruff format --check src/ tests/` — formatting (line length, spacing, etc.)

Failing either step blocks the build.

## Before Every Commit

Always run both in sequence:

```bash
ruff check --fix src/ tests/
ruff format src/ tests/
```

Then verify clean:

```bash
ruff check src/ tests/ && ruff format --check src/ tests/
```

Both must produce no errors before committing.

## Common Violations

| Code | Description | Fix |
|------|-------------|-----|
| `F401` | Unused import | Remove the import |
| `I001` | Import block unsorted | `ruff check --fix` auto-sorts |
| `F811` | Redefined unused import | Remove the duplicate |
| `F541` | f-string without placeholders | Remove the `f` prefix |
| `UP035` | Use `collections.abc` instead of `typing` | `ruff check --fix` auto-fixes |
| `UP037` | Remove quotes from type annotation | `ruff check --fix` auto-fixes |

## noqa Directives

Use specific rule codes — bare `# noqa` or `# noqa: unreachable` are invalid:

```python
# WRONG
yield  # noqa: unreachable

# CORRECT
yield  # noqa: RET901
```

## Scope

Always run against the full `src/ tests/` — running on individual files risks missing related violations that cause CI to fail on the next push.
