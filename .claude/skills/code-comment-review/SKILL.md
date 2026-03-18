---
name: code-comment-review
description: >
  Review code comments for meaningfulness, accuracy, and completeness. Ensure comments
  explain WHY not WHAT, docstrings follow conventions, and inline comments add value.
  Use when reviewing comment quality, checking docstring completeness, or auditing
  documentation within code. Triggers on: "review comments", "check docstrings",
  "comment quality", "documentation review", "are comments meaningful".
---

# Code Comment Review

## Review Criteria

### 1. Docstring Completeness

Every public function, class, and method MUST have a docstring. Evaluate:

- **Present**: Does a docstring exist?
- **Purpose**: Does it explain WHAT the function does (not HOW)?
- **Parameters**: Are all parameters documented with types and descriptions?
- **Returns**: Is the return value documented?
- **Raises**: Are raised exceptions documented?
- **Examples**: For complex functions, is a usage example included?

Preferred format (Google-style or NumPy-style, be consistent):
```python
def score_task(task: TaskNode, context: ScoringContext) -> ScoreExplanation:
    """Compute the priority score for a task using the 7-factor algorithm.

    Parameters
    ----------
    task:
        The task node to score.
    context:
        Graph context including dependencies, blockers, and constraints.

    Returns
    -------
    ScoreExplanation with factor breakdown, modifiers, and final score.

    Raises
    ------
    ValueError
        If task is in a terminal state (COMPLETE/CANCELLED).
    """
```

### 2. Comment Quality Assessment

Rate each comment as:

- **Valuable**: Explains WHY, provides context a reader wouldn't know
- **Redundant**: Restates what the code already says (`# increment i` above `i += 1`)
- **Stale**: Doesn't match current code behavior
- **Missing**: Complex logic without any explanation

Flag:
- Comments that restate the code: `# Get the node` above `get_node()`
- Commented-out code blocks (should be deleted, not commented)
- TODO/FIXME/HACK without ticket reference or explanation
- Comments that reference removed features or old variable names

### 3. Inline Comment Placement

- Comments should precede the code they describe, not trail on the same line (unless very short)
- Block comments for multi-line explanations
- No excessive commenting of obvious code
- Strategic comments at decision points, workarounds, and non-obvious logic

### 4. Module-Level Documentation

Each module (`__init__.py` or main module file) should have:
- Module docstring explaining the module's purpose
- Public API documentation (what's exported)
- Relationship to other modules if non-obvious

### 5. Comment Anti-Patterns

Flag these:
```python
# BAD: Restates code
x = x + 1  # Add 1 to x

# BAD: Stale comment
# Returns a list of active tasks
def get_all_tasks():  # Actually returns ALL tasks now
    ...

# BAD: Commented-out code
# old_score = compute_v1(task)
score = compute_v2(task)

# GOOD: Explains WHY
# AGE does not support parameterized queries inside $$ blocks,
# so we must escape values manually to prevent injection.
eid = _escape(node_id)

# GOOD: Documents a non-obvious decision
# Use SelectorEventLoop on Windows because psycopg's async
# driver is incompatible with ProactorEventLoop.
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

## Output Format

```markdown
## File: `<path>`

### Comment Quality Score: X/10

| Metric | Count |
|--------|-------|
| Public functions missing docstrings | X |
| Redundant comments | X |
| Stale comments | X |
| Missing explanatory comments | X |
| Valuable comments | X |

### Issues
1. **[Missing Docstring]** `function_name()` at line X — needs parameter and return docs
2. **[Redundant]** Line X: "<comment>" — restates the code, remove
3. **[Stale]** Line X: "<comment>" — doesn't match current behavior

### Recommendations
1. <specific improvement>
```
