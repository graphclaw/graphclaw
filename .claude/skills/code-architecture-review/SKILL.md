---
name: code-architecture-review
description: >
  Review Python code for modularity, design pattern usage, and architectural quality.
  Use when reviewing code for structural concerns: module boundaries, separation of concerns,
  SOLID principles, dependency injection, coupling/cohesion analysis, layer architecture compliance,
  and design pattern identification. Triggers on: "review architecture", "check modularity",
  "design patterns", "code structure review", "coupling analysis".
---

# Code Architecture Review

## Review Procedure

For each Python source file or module under review:

### 1. Modularity Assessment

Evaluate each file against these criteria (score 1-5 each):

- **Single Responsibility**: Does the file/class have one clear purpose?
- **Cohesion**: Do all functions/methods in the file relate to the same concern?
- **Coupling**: Are dependencies explicit, minimal, and injected rather than hardcoded?
- **Interface Clarity**: Are public APIs well-defined? Are internal helpers prefixed with `_`?
- **Module Boundaries**: Does the module expose a clean `__init__.py` surface?

Flag violations:
- God classes (>300 lines or >10 public methods)
- Circular imports
- Mixed abstraction levels in a single function
- Hardcoded dependencies that should be injected

### 2. Design Pattern Identification

Identify which patterns ARE used and which SHOULD be used:

**Patterns to look for:**
- **Repository Pattern** — Data access abstracted behind interface
- **Strategy Pattern** — Interchangeable algorithms (scoring factors)
- **State Machine** — Explicit state transitions with guards
- **Factory Pattern** — Object creation abstracted from usage
- **Observer/Event** — Decoupled event handling
- **Facade** — Simplified interface to complex subsystem
- **Template Method** — Base class defines skeleton, subclasses override steps
- **Dependency Injection** — Dependencies passed in, not created internally
- **Builder Pattern** — Complex object construction step-by-step

For each pattern found, note:
- Where it's applied correctly
- Where it's applied but could be improved
- Where it's missing but would improve the code

### 3. SOLID Principles Check

For each module, verify:

| Principle | Check |
|-----------|-------|
| **S** — Single Responsibility | One reason to change per class/module |
| **O** — Open/Closed | Extensible without modification (use base classes, protocols) |
| **L** — Liskov Substitution | Subtypes substitutable for base types |
| **I** — Interface Segregation | No client forced to depend on unused methods |
| **D** — Dependency Inversion | Depend on abstractions, not concretions |

### 4. Layer Architecture Compliance

Verify the dependency flow follows:

```
CLI → Agent → Scoring/State → Models → DB
         ↘                        ↗
          └──────────────────────┘
```

Flag any reverse dependencies (e.g., models importing from CLI, DB importing from scoring).

### 5. Output Format

Produce a structured report per module:

```markdown
## Module: `<module_name>`

### Modularity Score: X/5
- Single Responsibility: X/5 — <notes>
- Cohesion: X/5 — <notes>
- Coupling: X/5 — <notes>

### Design Patterns
- **Used**: <pattern> in <location> — <assessment>
- **Recommended**: <pattern> for <reason>

### SOLID Violations
- <principle>: <description> in <file:line>

### Action Items
1. <specific actionable improvement>
```
