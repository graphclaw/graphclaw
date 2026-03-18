---
name: code-best-practices
description: >
  Review Python code for adherence to coding best practices including PEP 8, type hints,
  error handling, logging, testing conventions, and Pythonic idioms. Use when checking code
  quality standards, enforcing conventions, or auditing for anti-patterns. Triggers on:
  "best practices", "code quality", "PEP 8", "coding standards", "anti-patterns", "code audit".
---

# Code Best Practices Review

## Review Checklist

### 1. Type Safety & Annotations

- All public functions have complete type hints (params + return)
- Use `from __future__ import annotations` for forward refs
- Prefer `X | None` over `Optional[X]` (Python 3.10+)
- Use `TypeAlias`, `Protocol`, or `ABC` for interface contracts
- Generic types use modern syntax: `list[str]` not `List[str]`
- Pydantic models use proper field types with validators

Flag:
- Functions with `Any` return type that could be narrowed
- Missing type hints on public methods
- `# type: ignore` without explanation

### 2. Error Handling

- Custom exception hierarchy for domain errors (not bare `Exception`)
- `try/except` blocks catch specific exceptions, never bare `except:`
- Context managers (`async with`) for resource cleanup
- Errors include actionable context (IDs, states, what was attempted)
- No silent swallowing of exceptions without logging

Flag:
- `except Exception as e: pass`
- Overly broad exception catches
- Missing cleanup in error paths (connections, files)
- Re-raising without context (`raise` vs `raise ... from e`)

### 3. Logging & Observability

- Use `logging.getLogger(__name__)` per module
- Structured logging with `extra={}` dict, not f-string interpolation in log calls
- Log levels appropriate: DEBUG for internals, INFO for operations, WARNING for recoverable, ERROR for failures
- Sensitive data never logged (passwords, tokens, PII)
- Operation boundaries logged (entry/exit of significant operations)

Flag:
- `print()` statements (should be `logger.*`)
- `logger.info(f"User {user_id} ...")` — should use `extra=` or `%s`
- Missing error logging in except blocks

### 4. Pythonic Idioms

- Use comprehensions over `map`/`filter` where readable
- Use `dataclasses` or `Pydantic` over raw dicts for structured data
- Use `enum.Enum` for fixed sets of values
- Use `pathlib.Path` over `os.path`
- Use context managers for resource management
- Use `itertools` / `collections` where appropriate
- Prefer `any()`/`all()` over manual loops for boolean checks

Flag:
- Manual dict construction that could be a comprehension
- String concatenation in loops (use `join`)
- Mutable default arguments (`def f(x=[])`)
- Using `type()` for type checks instead of `isinstance()`

### 5. Async Best Practices

- All DB operations are async with proper connection pooling
- No blocking calls inside async functions
- Connection pools properly acquired and released
- Use `async with` for all connection management
- Avoid `asyncio.run()` inside already-running loops

Flag:
- `time.sleep()` in async code (use `asyncio.sleep()`)
- Synchronous I/O in async functions
- Missing `await` on coroutines
- Connection leaks (acquire without release)

### 6. Testing Practices

- Tests follow Arrange-Act-Assert (AAA) pattern
- Each test tests one behavior
- Test names describe the scenario: `test_<unit>_<scenario>_<expected>`
- Fixtures used for shared setup, not copy-paste
- Mocks/patches applied at the correct level
- Integration tests separated from unit tests

Flag:
- Tests with multiple unrelated assertions
- Missing edge case coverage (None, empty, boundary values)
- Tests that depend on execution order
- Hardcoded test data that should be fixtures

### 7. Output Format

```markdown
## File: `<path>`

### Best Practices Score: X/10

| Category | Score | Issues |
|----------|-------|--------|
| Type Safety | X/10 | <count> issues |
| Error Handling | X/10 | <count> issues |
| Logging | X/10 | <count> issues |
| Pythonic Idioms | X/10 | <count> issues |
| Async Practices | X/10 | <count> issues |

### Issues Found
1. **[Category]** Line X: <description> → <fix>

### Recommendations
1. <actionable improvement>
```
