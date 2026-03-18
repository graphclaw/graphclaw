---
name: code-file-headers
description: >
  Generate and maintain standardized file header comments with metadata for Python source files.
  Headers include: module description, objective, methods list, design patterns used,
  dependencies, and authorship. Use when adding file headers, generating module metadata,
  standardizing file documentation, or onboarding developers to a codebase. Triggers on:
  "add file headers", "file metadata", "module documentation", "standardize headers",
  "file description comments".
---

# Code File Headers

## Header Format

Every Python source file (except `__init__.py` files that are import-only) must have a
standardized header block as the module docstring. The header is a Python docstring at
the top of the file, BEFORE any imports (after `from __future__` if present).

### Template

```python
"""<module_path> — <one-line summary>.

Description
-----------
<2-4 sentence description of the module's objective, what problem it solves,
and how it fits into the larger system architecture.>

Design Patterns
---------------
<List each design pattern used in this file with a brief note on where/how.>
- <Pattern Name>: <where/how applied>

Public API
----------
<List all public classes, functions, and constants with one-line descriptions.>
- <name>: <description>

Dependencies
------------
<List key internal and external dependencies.>
- <module>: <what it's used for>

Notes
-----
<Any important caveats, known limitations, or non-obvious behavior.
Omit this section if there are no special notes.>
"""
```

### Example

```python
"""graphclaw.scoring.engine — 7-factor priority scoring engine.

Description
-----------
Computes composite priority scores for task nodes using a weighted sum of
7 independent scoring factors. Supports critical-path multipliers, score
caching with TTL-based invalidation, and custom user weight overrides.
Central to the agent's task prioritization loop.

Design Patterns
---------------
- Strategy Pattern: Each scoring factor is an independent callable
- Cache-Aside: Scores cached with TTL, invalidated on dependency changes
- Dependency Injection: ScoringContext and cache injected, not created internally

Public API
----------
- ScoringEngine: Main engine class, call `score_task()` to compute priorities
- DEFAULT_WEIGHTS: Dict of default factor weights summing to 1.0

Dependencies
------------
- graphclaw.scoring.factors: Individual factor computation functions
- graphclaw.scoring.cache: TTL-based score cache
- graphclaw.scoring.topology: Chain topology modifier calculations
- graphclaw.models.scoring: ScoreExplanation and ScoringContext models

Notes
-----
Critical-path multiplier (1.5x for P1 goals) is applied AFTER the weighted
sum, not to individual factors. This is per PRD Section 9.4.
"""
```

## Rules

1. **Be accurate** — Only list methods/patterns that actually exist in the file
2. **Be concise** — Description should be 2-4 sentences, not paragraphs
3. **Update on change** — When a file's public API changes, update the header
4. **Skip trivial files** — `__init__.py` files that only re-export don't need full headers
5. **`from __future__` first** — If the file uses `from __future__ import annotations`, that line comes before the docstring. The docstring comes immediately after.
6. **No redundancy** — Don't repeat information available in the class/function docstrings below. The header is a high-level map, not a replacement for inline docs.

## Process

For each Python source file:

1. Read the entire file to understand its purpose
2. Identify all public classes, functions, and constants
3. Identify design patterns in use
4. Identify key dependencies (internal modules and significant external packages)
5. Write the header following the template
6. Insert at the top of the file (after `from __future__` if present)
7. If a module docstring already exists, REPLACE it with the standardized format

## Output

When reviewing (not writing), produce:

```markdown
## File: `<path>`

### Current Header: MISSING / INCOMPLETE / PRESENT
### Recommended Header:
<the complete header docstring to add/replace>
```
