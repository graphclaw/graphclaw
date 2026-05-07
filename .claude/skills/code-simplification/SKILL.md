---
name: code-simplification
description: >
  Simplify convoluted Python code to improve readability for human developers.
  Reduce cognitive complexity, flatten nested logic, extract helpers, and improve naming.
  Use when code is hard to follow, has deep nesting, long functions, unclear variable names,
  or excessive abstraction. Triggers on: "simplify", "too complex", "hard to read",
  "refactor for readability", "reduce complexity", "clean up code".
---

# Code Simplification

## Complexity Indicators to Flag

### Cognitive Complexity
- Functions > 20 lines of logic (excluding docstrings/comments)
- Nesting depth > 3 levels
- More than 3 conditional branches in a single function
- Boolean expressions with > 2 operators
- Functions with > 5 parameters

### Readability Blockers
- Single-letter variable names outside comprehensions/lambdas
- Abbreviated names that require domain knowledge (`cp_mult` → `critical_path_multiplier`)
- Double negatives (`if not is_not_valid`)
- Complex ternary expressions spanning multiple lines
- Magic numbers without named constants

### Over-Engineering
- Abstraction layers with only one implementation
- Inheritance where composition would be simpler
- Generic type parameters that are always the same concrete type
- Design patterns applied where a simple function would suffice

## Simplification Techniques

### 1. Extract and Name
```python
# BEFORE
if task.state not in (TaskState.COMPLETE, TaskState.CANCELLED) and task.deadline and (task.deadline - now).days < 2:
    ...

# AFTER
def is_urgent_active_task(task, now):
    is_active = task.state not in TERMINAL_STATES
    is_near_deadline = task.deadline and (task.deadline - now).days < 2
    return is_active and is_near_deadline
```

### 2. Early Return (Guard Clauses)
```python
# BEFORE
def process(task):
    if task is not None:
        if task.state == TaskState.ACTIVE:
            if task.assignee:
                return do_work(task)
    return None

# AFTER
def process(task):
    if task is None:
        return None
    if task.state != TaskState.ACTIVE:
        return None
    if not task.assignee:
        return None
    return do_work(task)
```

### 3. Replace Conditionals with Dispatch
```python
# BEFORE
if factor == "timeline":
    score = compute_timeline(ctx)
elif factor == "dependency":
    score = compute_dependency(ctx)
elif factor == "blocker":
    score = compute_blocker(ctx)

# AFTER
FACTOR_FUNCTIONS = {
    "timeline": compute_timeline,
    "dependency": compute_dependency,
    "blocker": compute_blocker,
}
score = FACTOR_FUNCTIONS.get(factor)(ctx)
```

### 4. Simplify Data Transformations
```python
# BEFORE
results = []
for row in rows:
    props = _extract_properties(row[0])
    if props.get("state") == "ACTIVE":
        results.append(props)

# AFTER
results = [
    _extract_properties(row[0])
    for row in rows
    if _extract_properties(row[0]).get("state") == "ACTIVE"
]
```

## Output Format

For each file reviewed, produce:

```markdown
## File: `<path>`

### Complexity Score: X/10 (1=simple, 10=very complex)

### Simplification Opportunities

| # | Location | Issue | Technique | Impact |
|---|----------|-------|-----------|--------|
| 1 | func:line | Deep nesting (4 levels) | Guard clauses | High |

### Proposed Changes
<specific code before/after for each significant simplification>
```

## Rules

- Never simplify at the cost of correctness
- Preserve all existing behavior (refactor, don't rewrite)
- Keep changes minimal and reviewable — one concern per change
- If a function is complex because the domain IS complex, add a clarifying comment instead of forcing simplification
- Prioritize changes by impact: high-traffic code paths first
