---
name: test-inventory-maintenance
description: Cross-cutting skill for maintaining test file headers, test IDs, and inventory.md indexes across both graphclaw and graphclaw-cockpit. Use whenever a test file is added, modified, or deleted. Enforces the ID format, header structure, and inventory registration.
---

# Test Inventory Maintenance

## When to use
- Adding a new test file in either repo
- Modifying an existing test file (update `Last reviewed` date and `Cases covered`)
- Deleting a test file (remove from inventory.md)
- Reviewing a PR that touches test files

---

## The three obligations (in order)

1. **Write the file header first** — before writing any test function
2. **Allocate a test ID** — before the header is complete
3. **Register in inventory.md** — before committing

---

## Test ID format

```
GC-<L>-<DOM>-<W>-<NNN>
```

| Field | Values |
|---|---|
| `<L>` | `U` unit · `C` component · `K` contract · `I` integration · `E` e2e · `L` load · `A` agent eval |
| `<DOM>` | Domain code (see table below) |
| `<W>` | Build wave: `W08`, `W12`, `W16`, etc. |
| `<NNN>` | Zero-padded 3-digit sequence within (L, DOM, W) |

### Domain codes
| Code | Area |
|---|---|
| `SCO` | Scoring |
| `STA` | State machine |
| `INB` | Inbound |
| `TRG` | Triggers |
| `GRA` | Graph |
| `AUT` | Auth |
| `CHT` | Chat |
| `CAN` | Canvas |
| `MKT` | Marketplace |
| `A2A` | A2A |
| `MCP` | MCP |
| `SKL` | Skills |
| `API` | API routes |
| `JNY` | Journey (multi-step) |
| `ORC` | Orchestrator (evals) |
| `CLI` | CLI commands |
| `INT` | Intelligence |
| `ADM` | Admin |
| `STG` | Settings |
| `INF` | Infrastructure |
| `GWY` | Gateway |
| `WRK` | Workers |
| `LLM` | LLM |
| `TSK` | Tasks (cockpit components) |

---

## How to allocate an ID

1. Open the relevant `inventory.md` for the test root (e.g., `tests/integration/inventory.md` or `e2e/inventory.md`)
2. Find the last used `NNN` for your `(L, DOM, W)` triple
3. Assign `NNN + 1`, zero-padded to 3 digits
4. If this is the first test for this `(L, DOM, W)`, start at `001`

---

## inventory.md format

```markdown
# Test Inventory — <directory name>

| ID | Scenario (1 line) | File |
|---|---|---|
| GC-I-API-W11-007 | Task create persists to graph and audit | [test_task_lifecycle.py](test_task_lifecycle.py) |
| GC-E-INT-W16-004 | Save to MinIO persists content | [specs/intelligence/intelligence.spec.ts](specs/intelligence/intelligence.spec.ts) |

_Last regenerated: YYYY-MM-DD by `scripts/regen_inventory.py` / `scripts/regen-inventory.mjs`._
```

Rules:
- Scenario column: **one line, under 80 chars**. Describe the observable outcome, not the implementation.
- File column: relative path from the inventory.md location, as a markdown link.
- Sort entries by ID within each table.

---

## When to update inventory.md

| Action | Required update |
|---|---|
| Add a test file | Add all its IDs to the relevant inventory.md |
| Add a `test_*` / `it()` to existing file | Add the new ID, update header's `Cases covered` |
| Rename a test file | Update the file path in inventory.md |
| Delete a test file | Remove all its rows from inventory.md |
| Change a test description | Update `Cases covered` in header AND `Scenario` in inventory |

---

## Regeneration commands

```bash
# graphclaw (Python)
python scripts/regen_inventory.py

# graphclaw-cockpit (Node)
node scripts/regen-inventory.mjs
```

These walk the test trees, parse headers, and emit inventory.md files. Run after any test change. CI fails if the inventory is stale (non-empty diff after regen).

---

## Header lint commands

```bash
# graphclaw
python scripts/check_test_headers.py

# graphclaw-cockpit
node scripts/check-test-headers.mjs
```

These verify:
- File starts with the canonical header block
- All required fields present (ID, Scenario, PRD, Build wave, Layer, Owner, Last reviewed, Cases covered)
- ID matches the regex `GC-[ULKIASLE]-[A-Z]{2,4}-W\d+-\d{3}`
- ID is registered in the inventory

---

## Backfill grace (legacy files)

Legacy test files without headers get a warning from the lint script, not an error. Do not block PRs on legacy files. When you touch a legacy file for any reason, add the header as part of that same PR. Per-domain upgrade from warn to error once all files in that domain have headers.

---

## Quick checklist (per file)

```
[ ] Header written (all fields filled)
[ ] ID allocated and unique
[ ] ID registered in the relevant inventory.md
[ ] Cases covered list matches actual test functions
[ ] Last reviewed date is today
[ ] regen_inventory run, no diff
[ ] header_lint passes (no errors on this file)
```
