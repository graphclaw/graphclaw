# GraphClaw Testing Harness — Build Plan

> Self-contained execution plan. A fresh Claude Code session can pick this up cold and execute. Read this whole file first, then proceed phase-by-phase.

---

## 0. Project context (read first)

### Two related repos

**Backend — `graphclaw`** at `c:\Users\abhis\Projects\graphclaw`
- Python 3.11+, FastAPI, Apache AGE (Postgres graph extension), Redis, MinIO, LiteLLM
- 131 routes under `/app/v1/`, RS256 JWT via OAuth (Google/GitHub/Microsoft), SSE at `/app/v1/events`, WebSocket at `/app/v1/chat/ws`
- Existing test suite: **177 pytest files across 32 categories** in `tests/` (test_agent, test_api, test_infra, test_gateway, test_inbound, test_triggers, test_scoring, test_skills, test_compliance, test_db, test_graph, test_briefing, test_workers, test_llm, test_mcp, test_auth, integration/, load/)
- pytest config in `pyproject.toml`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `norecursedirs` excludes `tests/load`
- Plugins: `pytest-asyncio>=0.23.0`, `pytest-timeout>=2.3.0`, `pytest-cov>=5.0.0`
- Custom marker: `@pytest.mark.integration` — gated by `--run-integration` flag, requires service precheck (Postgres/AGE/Redis/MinIO)
- Existing fakes: `FakeGraphStore`, `FakeStorageClient`, `FakeSecretsClient` in `tests/test_api/conftest.py` and `tests/test_api/test_admin/conftest.py`
- CI: `.github/workflows/ci.yml` (lint+unit+integration with Postgres+Redis sidecars) and `pr-checks.yml` (size + coverage at 60%); also `build-push.yml`
- Existing docs: `docs/test-scenarios.md` (9 scenarios), `tests/load/README.md` (Locust)
- Existing skills under `.claude/skills/`: `graphclaw-test-patterns`, `graphclaw-pydantic-schemas`, `graphclaw-state-machine`, `graphclaw-scoring-algorithm`, `graphclaw-ruff-conventions`, `graphclaw-docker-dev`, `graphclaw-cli-patterns`, plus 9 others
- Cruft to clean: `test_results_final.txt`, `test_results_new.txt`, `verify_changes.py`, `verify_ws_p45_f.py`, `WS-P45-F-SUMMARY.md`, `observations.md`, `observations-done.md` at repo root

**Frontend — `graphclaw-cockpit`** at `c:\Users\abhis\Projects\graphclaw-cockpit`
- React 19 + TS + Vite 6 SPA, shadcn/ui (Radix + Tailwind 4), Cytoscape.js + React Flow, openapi-fetch + TanStack Query v5 + Zustand v5, Monaco, Recharts, React Hook Form + Zod, React Router v7
- Consumes backend at `../graphclaw` via `/app/v1/` REST + SSE + WebSocket
- Three test stacks coexist today:
  1. **Vitest unit** — 47 co-located `*.test.tsx` files, MSW 2.x mocks at [src/test/handlers.ts](c:/Users/abhis/Projects/graphclaw-cockpit/src/test/handlers.ts) (467 LOC), `renderWithProviders` at [src/test/utils.tsx](c:/Users/abhis/Projects/graphclaw-cockpit/src/test/utils.tsx), MSW node server at `src/test/server.ts`. **No coverage thresholds.**
  2. **Playwright E2E** — 18 `.spec.ts` files in `e2e/` organized by feature (admin, agent, canvas, chat, global, graph, intelligence, marketplace, settings). Single `e2e/fixtures/auth.fixture.ts`, no page objects. Config at `playwright.config.ts` (1 worker, 30s timeout). Docker profile `test`.
  3. **Puppeteer + Jest E2E** — 29 spec files in `e2e-puppeteer/` with rich helpers (`api.helper.ts`, `auth.helper.ts`, `browser.helper.ts`, `db.helper.ts`, `minio.helper.ts`, `TestContext` base, `seed-all.ts` / `teardown-all.ts` producing `.seed-manifest.json`). Sequential exec via custom sequencer (backend rate-limit at 300 req/min/user). Docker profile `test-puppeteer`. **Covers `a2a/`, `mcp/`, `skills/` which Playwright does not.**
- **No GitHub Actions workflow.** All test gating is local-only.
- `test-results/` and `playwright-report/` are committed — must be gitignored
- Existing docs: `docs/e2e-test-scenarios.md` (92+ scenarios across 21 files), `build-plan.md` (wave tracker), `docs/prd/16-react-implementation.md` §16.6 has a 3-tier strategy not enforced
- Existing skills under `.claude/skills/`: `cockpit-react-patterns`, `cockpit-playwright-e2e`, `cockpit-api-integration`, `cockpit-docker-deploy`
- Conventions: feature-based structure `src/features/{name}/`, all API via `src/lib/api-client.ts` (openapi-fetch), server state in TanStack Query hooks, client state in Zustand stores at `src/stores/`, tests co-located, commit format `feat(wave-N): description`

### Why this plan exists

Tests accreted across many build waves with no unifying strategy. Three E2E stacks duplicate work. No traceability from PRD acceptance criteria to test files. No coverage thresholds on the frontend. Test artifacts pollute git status. The orchestrator agent (heart of the system) has no behavioral regression coverage. New engineers cannot tell where to add a test or how to name it.

This plan delivers: one E2E framework, a unified pyramid, every test self-documents in its file header, every test root carries an `inventory.md` index, CI gates both repos, and Claude Code skills enforce the conventions automatically so future test work stays on-pattern.

---

## 1. Decisions made (rationale embedded — do not relitigate without strong cause)

### D1. E2E framework: **Playwright. Sunset Puppeteer.**
Playwright wins on engineering merits over Puppeteer:
- Full test runner with fixtures, auto-waiting locators (`getByRole`, `getByText`), trace viewer with DOM/network/console replay, codegen, multi-browser, first-class TS — all built in.
- Puppeteer is browser automation only; you assemble Jest + ts-jest + ESM flags + custom sequencer + `TestContext` base. The current `e2e-puppeteer/` setup uses `--experimental-vm-modules` and a custom `sequencer.cjs` for sequential exec — Playwright's `workers: 1` setting replaces both.
- The Puppeteer suite's value is its helpers (`db.helper.ts`, `minio.helper.ts`, `api.helper.ts`, `browser.helper.ts`) and seed-manifest pattern. These are pure Node modules and port to Playwright fixtures cleanly.
- Single framework reduces CI matrix, helper drift, onboarding cost.
- Migration cost: 2–3 weeks. Acceptable.

ADR file: `cockpit/docs/testing/adr/0001-playwright-over-puppeteer.md`.

### D2. Backend API testing: **pytest + httpx integration tests are the gate. Scripts are NOT in CI.**
- `tests/integration/` contains pytest files with assertions, fixtures, parametrization — runs in CI behind `--run-integration`. **Quality gate.**
- `scripts/api_smoke.py`, `scripts/chat_repl.py` — human dev tools. Print output. **Never wired into CI.** If a script becomes important enough to gate releases, promote it to a pytest integration test and delete the script.
- Decision rule: *should this fail the build if it breaks?* Yes → pytest. No → script.

ADR file: `graphclaw/docs/testing/adr/0004-cli-vs-scripts.md`.

### D3. Unit tests stay in their native runner.
- Backend unit → pytest with FastAPI `TestClient` + dependency overrides to fakes.
- Frontend unit/component → Vitest + RTL + MSW.
- **Never write unit tests in Playwright.** It spawns a browser per test, 100× slower than Vitest, with no offsetting benefit.

### D4. CLI tests use Typer's `CliRunner`, deterministic, every PR.
Lives in `graphclaw/tests/test_cli/`. Distinct from `tests/agent_evals/` which test the chat orchestrator.

### D5. Add L7 — Agent Evals layer.
The orchestrator is the heart of the system. Today no test catches the regression "orchestrator stopped delegating to skills and started answering inline." Solution: a YAML-driven scenario suite using the real LLM with behavioral assertions and LLM-as-judge rubrics.
- Lives in `graphclaw/tests/agent_evals/`.
- Gated by `--run-evals` flag (analogous to `--run-integration`).
- Canary subset (3–5 scenarios marked `eval_canary`) runs on PRs that touch `src/agent/`, `src/skills/`, `src/llm/`, or eval prompts.
- Full suite runs nightly on `main` (02:00 UTC) and on-demand via `workflow_dispatch`.
- Cost cap: $0.50 per nightly run, $0.05 per canary run.

ADR file: `graphclaw/docs/testing/adr/0003-evals-layer.md`.

### D6. Master strategy doc lives in graphclaw.
`graphclaw/docs/testing/master-strategy.md` is canonical (it co-locates with the PRDs at `docs/prd/`). Cockpit's `TESTING.md` links across.

### D7. Test ID scheme: forward-only backfill.
Format `GC-<L>-<DOM>-<W>-<NNN>`. New tests must have IDs registered in inventory. Legacy tests get IDs opportunistically as files are touched. Lint warns (not errors) on legacy. Per-domain promotion to error once that domain is fully backfilled.

---

## 2. Test pyramid (final)

| Layer | graphclaw | cockpit | Purpose | CI |
|---|---|---|---|---|
| L1 Unit | pytest, fakes, no I/O | Vitest co-located, MSW | Pure logic | Every PR |
| L2 Component | n/a | Vitest + RTL + `renderWithProviders` | Component contracts, a11y | Every PR |
| L3 Contract | schemathesis vs `/openapi.json` | Vitest contract test: MSW handlers vs `openapi.json` | FE/BE drift detection | Every PR |
| L4 Integration | pytest + `httpx.AsyncClient`, real services, gated `--run-integration` | n/a | Multi-service truth | Every PR with sidecars |
| L5 E2E | n/a | Playwright + Docker stack | User journeys | Every PR |
| L6 Load | Locust in `tests/load/` | n/a | Throughput / latency | Manual + nightly |
| L7 Agent Evals | pytest + YAML + real LLM + LLM-as-judge | n/a | Orchestrator behavior | Canary on PR, full nightly |
| CLI app | pytest + Typer `CliRunner` | n/a | Typer command behavior | Every PR |
| Manual smoke | `scripts/*.py` | n/a | Human dev | NEVER |

---

## 3. Final folder structure

### graphclaw

```
graphclaw/
├── TESTING.md                             ← entry point (CREATE)
├── tests/
│   ├── inventory.md                       ← top-level rollup (CREATE, autogen)
│   ├── conftest.py                        (existing)
│   ├── fixtures/                          (formalize existing)
│   │   ├── factories.py                   ← make_task, make_goal, make_user
│   │   ├── fakes.py                       ← FakeGraphStore, FakeStorageClient, FakeSecretsClient
│   │   ├── seed_data/
│   │   │   ├── tasks.json
│   │   │   ├── goals.json
│   │   │   └── manifest.py
│   │   ├── minio_seed/                    ← files for MinIO buckets in integration tests
│   │   │   ├── intelligence/
│   │   │   └── attachments/
│   │   └── sql/
│   │       ├── reset.sql
│   │       └── seed.sql
│   ├── unit/                              (existing per-domain dirs stay; this is conceptual layer)
│   ├── contract/                          ← CREATE
│   │   ├── inventory.md
│   │   ├── conftest.py
│   │   └── test_openapi_schema.py         ← schemathesis state-machine
│   ├── integration/                       (existing, expand)
│   │   ├── inventory.md
│   │   ├── test_task_lifecycle.py
│   │   ├── test_inbound_resolution.py
│   │   └── test_journeys/
│   │       └── test_first_user.py
│   ├── agent_evals/                       ← CREATE (L7)
│   │   ├── inventory.md
│   │   ├── conftest.py                    ← real LLM client, session_factory, eval_reporter
│   │   ├── prompts/
│   │   │   ├── orchestrator/
│   │   │   │   ├── 001_create_task_basic.yaml
│   │   │   │   ├── 002_delegate_to_skill.yaml
│   │   │   │   ├── 003_followup_after_inbound.yaml
│   │   │   │   ├── 004_priority_reasoning.yaml
│   │   │   │   ├── 005_clarification_loop.yaml
│   │   │   │   └── 006_refuse_out_of_scope.yaml
│   │   │   └── skills/
│   │   │       ├── 001_email_drafting.yaml
│   │   │       └── 002_briefing_summary.yaml
│   │   ├── rubrics/
│   │   │   ├── helpfulness.md
│   │   │   ├── tool_use_correctness.md
│   │   │   └── refusal_quality.md
│   │   ├── runners/
│   │   │   ├── chat_session.py
│   │   │   ├── assertions.py
│   │   │   └── judge.py
│   │   ├── reports/                       ← gitignored
│   │   ├── test_orchestrator_evals.py
│   │   └── test_skill_evals.py
│   ├── test_cli/                          ← CREATE (Typer CliRunner)
│   │   ├── inventory.md
│   │   ├── test_task_commands.py
│   │   └── test_graph_commands.py
│   ├── test_<domain>/                     (existing 32 categories — preserved)
│   └── load/                              (existing)
│       └── inventory.md                   ← supersedes README.md
├── scripts/
│   ├── api_smoke.py                       ← human API smoke (CREATE)
│   ├── chat_repl.py                       ← human orchestrator chat (CREATE)
│   ├── check_test_headers.py              ← header lint (CREATE)
│   ├── regen_inventory.py                 ← inventory generator (CREATE)
│   ├── check_test_ids.py                  ← ID registry lint (CREATE)
│   └── (existing scripts preserved)
├── docs/
│   └── testing/
│       ├── master-strategy.md             ← canonical pyramid (CREATE, cross-repo source of truth)
│       ├── test-id-registry.md            ← autogen
│       ├── scenario-catalog.md            ← supersedes docs/test-scenarios.md
│       ├── contributing-tests.md          ← decision tree
│       └── adr/
│           ├── 0001-pytest-asyncio-auto.md
│           ├── 0002-fakes-over-mocks.md
│           ├── 0003-evals-layer.md
│           └── 0004-cli-vs-scripts.md
├── .claude/
│   └── skills/
│       ├── graphclaw-pytest-patterns/     ← REWRITE existing graphclaw-test-patterns
│       ├── graphclaw-integration-tests/   ← CREATE
│       ├── graphclaw-contract-tests/      ← CREATE
│       ├── graphclaw-agent-evals/         ← CREATE
│       ├── graphclaw-cli-tests/           ← CREATE
│       ├── graphclaw-test-scripts/        ← CREATE
│       └── test-inventory-maintenance/    ← CREATE (cross-cutting, mirrored in cockpit)
└── .github/
    └── workflows/
        ├── ci.yml                         (existing — modify to add contract + canary-eval jobs)
        ├── pr-checks.yml                  (existing)
        ├── build-push.yml                 (existing)
        └── nightly-evals.yml              ← CREATE (full L7 suite + report artifact)
```

### graphclaw-cockpit

```
graphclaw-cockpit/
├── TESTING.md                             ← entry point (CREATE)
├── src/
│   ├── test/
│   │   ├── inventory.md                   ← unit rollup (CREATE, autogen)
│   │   ├── handlers.ts                    (existing MSW)
│   │   ├── server.ts                      (existing MSW node)
│   │   ├── utils.tsx                      (existing renderWithProviders)
│   │   ├── fixtures/                      ← React component fixture data (CREATE)
│   │   │   ├── tasks.ts
│   │   │   ├── agents.ts
│   │   │   └── users.ts
│   │   └── contract/                      ← CREATE (L3)
│   │       └── handlers.contract.test.ts  ← MSW vs openapi.json
│   └── **/*.test.tsx                      (existing co-located unit tests)
├── e2e/                                   ← single E2E suite (Playwright)
│   ├── inventory.md                       ← E2E rollup (CREATE, autogen)
│   ├── playwright.config.ts               (existing — refactor)
│   ├── README.md                          ← onboarding (CREATE)
│   ├── fixtures/
│   │   ├── auth.fixture.ts                (existing)
│   │   ├── seed.fixture.ts                ← orchestrates seed + teardown (CREATE, port from puppeteer)
│   │   ├── db.fixture.ts                  ← pg client per-worker (CREATE, port)
│   │   ├── minio.fixture.ts               ← S3 client per-worker (CREATE, port)
│   │   ├── api.fixture.ts                 ← typed openapi-fetch client (CREATE, port)
│   │   └── test.ts                        ← merged base export
│   ├── helpers/
│   │   ├── db.ts                          ← port from e2e-puppeteer/helpers/db.helper.ts
│   │   ├── minio.ts                       ← port
│   │   ├── api.ts                         ← port
│   │   └── browser.ts                     ← port
│   ├── seed/
│   │   ├── seed-all.ts                    ← port from e2e-puppeteer/fixtures/seed-all.ts
│   │   ├── teardown-all.ts                ← port
│   │   ├── data/
│   │   │   ├── goals.json
│   │   │   ├── tasks.json
│   │   │   └── agents.json
│   │   └── manifest.types.ts
│   └── specs/                             ← reorganize existing into subdirs by feature
│       ├── auth/
│       ├── canvas/
│       ├── chat/
│       ├── graph/
│       ├── intelligence/
│       ├── marketplace/
│       ├── admin/
│       ├── a2a/                           ← migrated from puppeteer
│       ├── mcp/                           ← migrated
│       ├── skills/                        ← migrated
│       └── settings/
├── e2e-archive/                           ← TEMP: old Puppeteer (rename from e2e-puppeteer/)
├── docs/
│   └── testing/
│       ├── test-strategy.md               ← FE-specific, links to graphclaw master
│       ├── scenario-catalog.md            ← rename from docs/e2e-test-scenarios.md
│       ├── contributing-tests.md
│       └── adr/
│           ├── 0001-playwright-over-puppeteer.md
│           └── 0002-msw-for-component-tests.md
├── scripts/
│   ├── check-test-headers.mjs             (CREATE)
│   ├── regen-inventory.mjs                (CREATE)
│   └── check-test-ids.mjs                 (CREATE)
├── .claude/
│   └── skills/
│       ├── cockpit-vitest-patterns/       ← CREATE (or extend cockpit-react-patterns)
│       ├── cockpit-playwright-e2e/        ← REWRITE existing
│       ├── cockpit-contract-tests/        ← CREATE
│       └── test-inventory-maintenance/    ← CREATE (mirror of graphclaw)
├── .github/
│   └── workflows/
│       └── ci.yml                         ← CREATE (none exists today)
└── .gitignore                             ← MODIFY (add report/coverage/manifest paths)
```

---

## 4. File-header documentation requirement

Every test file MUST start with a structured header. Updating the test forces updating the header. CI lints this. No exceptions for legacy files past the backfill window.

### Python pattern

```python
"""
GC-I-API-W11-007 — Task lifecycle integration

Scenario: A user creates a task via POST /tasks. The system persists it to
the AGE graph and writes an audit row. Listing /tasks returns it.

PRD: docs/prd/03-task-management.md §AC-3.2.1, §AC-3.4.4
Build wave: W11
Layer: L4 Integration
Owner: backend-team
Last reviewed: 2026-05-04

Cases covered:
- Happy path: create → graph node exists → audit row exists
- Validation: missing goal_id returns 422
- Auth: missing token returns 401

Notes:
- Requires --run-integration flag and live Postgres+AGE+Redis+MinIO.
- Resets graph between cases via clean_graph autouse fixture.
"""
```

### TypeScript pattern

```typescript
/**
 * GC-E-CHT-W18-003 — Chat streams tokens to bubble
 *
 * Scenario: User sends a message in /chat, the orchestrator streams tokens
 * via SSE, the message bubble updates incrementally, and the final message
 * persists to the conversation list.
 *
 * PRD: docs/prd/09-chat-interface.md §AC-9.1.3
 * Build wave: W18
 * Layer: L5 E2E
 * Owner: frontend-team
 * Last reviewed: 2026-05-04
 *
 * Cases covered:
 *  - Token streaming visible mid-flight
 *  - Final message saved on stream close
 *  - Stream cancellation on user navigation
 *
 * Notes:
 *  - Requires running docker compose stack.
 *  - Uses seeded conversation from seed-all.ts (manifest.conversations[0]).
 */
```

### Lint behavior (`scripts/check_test_headers.py`, `scripts/check-test-headers.mjs`)
- File starts with the header block.
- Block contains: ID, Scenario, PRD, Build wave, Layer, Owner, Last reviewed, Cases covered.
- ID matches `GC-[ULCKIESLA]-[A-Z]{2,4}-W\d+-\d{3}`.
- ID is registered in the inventory.md at the test-tree root.
- Pre-commit + CI block on failure (with backfill warning grace per §1 D7).

---

## 5. `inventory.md` — scenario index per test root

### Roots that get an inventory
- `graphclaw/tests/inventory.md` (rollup)
- `graphclaw/tests/contract/inventory.md`
- `graphclaw/tests/integration/inventory.md`
- `graphclaw/tests/agent_evals/inventory.md`
- `graphclaw/tests/test_cli/inventory.md`
- `graphclaw/tests/load/inventory.md`
- `graphclaw-cockpit/e2e/inventory.md`
- `graphclaw-cockpit/src/test/inventory.md` (rollup of co-located unit tests)

### Format

```markdown
# Test Inventory — tests/integration

| ID | Scenario (1 line) | File |
|---|---|---|
| GC-I-API-W11-007 | Task create persists to graph and audit | [test_task_lifecycle.py](test_task_lifecycle.py) |
| GC-I-API-W11-008 | Task delete cascades to children | [test_task_lifecycle.py](test_task_lifecycle.py) |
| GC-I-INB-W14-002 | Inbound email matches existing task by thread-id | [test_inbound_resolution.py](test_inbound_resolution.py) |
| GC-I-INB-W14-003 | Inbound email creates new task when no match | [test_inbound_resolution.py](test_inbound_resolution.py) |
| GC-I-JNY-W15-001 | New user signs up, creates first goal, completes task | [test_journeys/test_first_user.py](test_journeys/test_first_user.py) |

_Last regenerated: 2026-05-04 by `scripts/regen_inventory.py`._
```

### Generator behavior
- `scripts/regen_inventory.py` (Python) and `scripts/regen-inventory.mjs` (Node) walk the tree, parse headers, emit markdown.
- Runs in pre-commit. CI step regenerates and fails if diff is non-empty.
- Manual edits get overwritten on next regen.
- The top-level `tests/inventory.md` aggregates leaf inventories grouped by domain with hyperlinks.

---

## 6. Test ID scheme

**Format**: `GC-<L>-<DOM>-<W>-<NNN>`

| Field | Values |
|---|---|
| `<L>` | `U` unit · `C` component · `K` contract · `I` integration · `E` e2e · `L` load · `A` agent eval |
| `<DOM>` | Short domain code (3–4 letters) |
| `<W>` | Build wave (e.g. `W12`) |
| `<NNN>` | Zero-padded sequence within (L, DOM, W) |

### Domain codes (allocate as used)
`SCO` scoring · `STA` state · `INB` inbound · `TRG` triggers · `GRA` graph · `AUT` auth · `CHT` chat · `CAN` canvas · `MKT` marketplace · `A2A` · `MCP` · `SKL` skills · `API` api · `JNY` journey · `ORC` orchestrator · `CLI` cli · `INT` intelligence · `ADM` admin · `STG` settings

### Registry
`graphclaw/docs/testing/test-id-registry.md` — autogenerated from inventories, canonical for both repos.

### Backfill policy
Forward-only. Every NEW test gets an ID. Legacy tests get IDs opportunistically when touched. Per-domain lint upgrade: warn → error after a domain is fully backfilled.

---

## 7. Skills to author

Each skill encodes conventions so future Claude sessions follow them automatically. Standard structure:

```
.claude/skills/<skill-name>/
├── SKILL.md                          ← when-to-use, conventions, examples
├── templates/
│   ├── test-file-header.template.{py|ts}
│   └── scenario.template.yaml        ← evals only
├── examples/
│   └── canonical_test.{py|ts}        ← reference file
└── checklists/
    └── adding-a-test.md              ← steps: pick layer → allocate ID → write header → write test → regen inventory
```

### graphclaw skills (`graphclaw/.claude/skills/`)

| Skill | Triggers | Owns |
|---|---|---|
| `graphclaw-pytest-patterns` (rewrite of existing `graphclaw-test-patterns`) | Writing/reviewing pytest tests anywhere in `tests/` | Fixture conventions, fakes-vs-mocks, async patterns, ID format, header template, inventory workflow |
| `graphclaw-integration-tests` (NEW) | Writing tests in `tests/integration/` | httpx.AsyncClient patterns, `--run-integration` gating, service precheck, journey tests |
| `graphclaw-contract-tests` (NEW) | Writing tests in `tests/contract/` | schemathesis state-machine, OpenAPI conformance |
| `graphclaw-agent-evals` (NEW) | Writing scenarios in `tests/agent_evals/prompts/**` or modifying `src/agent/`, `src/skills/`, `src/llm/` | YAML scenario schema, assertion vocabulary, rubric format, judge invocation, cost/token budgets, canary marker |
| `graphclaw-cli-tests` (NEW) | Writing tests in `tests/test_cli/` | Typer CliRunner patterns, exit code assertions, stdout parsing |
| `graphclaw-test-scripts` (NEW) | Writing files in `scripts/` that touch API, CLI, or chat | Scripts are NOT tests, no assertions required, must NOT be in CI, promote-to-pytest decision rule |

### cockpit skills (`graphclaw-cockpit/.claude/skills/`)

| Skill | Triggers | Owns |
|---|---|---|
| `cockpit-vitest-patterns` (NEW) | Writing `*.test.tsx` next to components | `renderWithProviders`, MSW handler usage, RTL queries (`getByRole` first), header template, inventory workflow |
| `cockpit-playwright-e2e` (REWRITE existing) | Writing under `e2e/` | Playwright fixture system, locator best practices, seed manifest usage, db/minio/api fixtures, header template, inventory workflow |
| `cockpit-contract-tests` (NEW) | Writing in `src/test/contract/` | Loading openapi.json, validating MSW handlers against it |

### Cross-cutting (mirrored in both repos)

| Skill | Triggers | Owns |
|---|---|---|
| `test-inventory-maintenance` | Any test file added/modified | Header format, ID allocation procedure, inventory regen command, CI lint expectations. Spine skill that ties the others together. |

### Skill authoring rules
1. Every skill's SKILL.md MUST include: when-to-use trigger description, the file-header template specific to that layer, the inventory update workflow, the assertion/pattern vocabulary, and at least one canonical example.
2. SKILL.md description field must be specific enough that Claude Code auto-triggers it correctly (e.g., "Use when writing tests in `tests/integration/` or modifying integration test fixtures").
3. Templates must be copy-paste-ready: open template, paste, fill placeholders.
4. Checklists must include the inventory regeneration command for that test root.

---

## 8. Documentation deliverables

### graphclaw

- `TESTING.md` (root) — 6 commands max, links to `docs/testing/`.
- `docs/testing/master-strategy.md` — canonical pyramid, cross-repo source of truth.
- `docs/testing/scenario-catalog.md` — supersedes `docs/test-scenarios.md`, generated from inventories.
- `docs/testing/test-id-registry.md` — autogenerated.
- `docs/testing/contributing-tests.md` — decision tree: layer → file location → ID allocation → header requirements.
- `docs/testing/adr/0001-pytest-asyncio-auto.md`
- `docs/testing/adr/0002-fakes-over-mocks.md`
- `docs/testing/adr/0003-evals-layer.md`
- `docs/testing/adr/0004-cli-vs-scripts.md`

### graphclaw-cockpit

- `TESTING.md` (root) — links to graphclaw `master-strategy.md`.
- `docs/testing/test-strategy.md` — frontend-specific elaboration (Vitest, Playwright, MSW).
- `docs/testing/scenario-catalog.md` — renamed from `docs/e2e-test-scenarios.md`.
- `docs/testing/contributing-tests.md`.
- `docs/testing/adr/0001-playwright-over-puppeteer.md`
- `docs/testing/adr/0002-msw-for-component-tests.md`

---

## 9. Sample artifacts (reference for implementation)

### Sample agent eval scenario

```yaml
# tests/agent_evals/prompts/orchestrator/002_delegate_to_skill.yaml
id: GC-A-ORC-W12-002
title: Orchestrator delegates email-drafting to email skill
description: |
  When the user asks for help drafting an email, the orchestrator must
  delegate to the email-drafting skill rather than answering inline.
  Guards against regression where orchestrator absorbs skill responsibilities.

setup:
  seed_dataset: minimal_v1
  user: dev@example.com

turns:
  - user: |
      Can you draft a follow-up email to the Acme team about the Q2 plan?
      We need a response by Friday.
    assert:
      - tool_called: skill.invoke
      - tool_args_match:
          skill_name: email-drafting
          context.task_type: followup
      - response_does_not_contain: ["Subject:", "Dear "]
      - latency_ms_under: 8000

  - user: "Make it more casual"
    assert:
      - tool_called: skill.invoke
      - tool_args_match:
          skill_name: email-drafting
          revision: true

teardown:
  cleanup_session: true

rubric:
  judge_model: claude-sonnet-4-6
  rubric_file: tool_use_correctness.md
  pass_threshold: 0.8

budget:
  max_tokens: 4000
  max_cost_usd: 0.10
```

### Sample eval test runner

```python
# tests/agent_evals/test_orchestrator_evals.py
"""
GC-A-ORC-W12-* — Orchestrator behavioral eval suite

Scenario: Parametrized over YAML scenarios in prompts/orchestrator/.
Each scenario is a multi-turn chat with behavioral assertions plus
optional LLM-as-judge rubric scoring.

PRD: docs/prd/05-orchestrator.md §AC-5.* (multiple)
Build wave: W12
Layer: L7 Agent Evals
Owner: agent-team
Last reviewed: 2026-05-04
"""
import pytest
from pathlib import Path
from runners.chat_session import load_scenario
from runners.assertions import run_turn_assertions
from runners.judge import judge_session

PROMPTS = list((Path(__file__).parent / "prompts" / "orchestrator").glob("*.yaml"))

pytestmark = [pytest.mark.agent_eval, pytest.mark.slow]

@pytest.mark.parametrize("scenario_path", PROMPTS, ids=lambda p: p.stem)
async def test_orchestrator_scenario(
    scenario_path, llm_client, session_factory, eval_reporter
):
    s = load_scenario(scenario_path)
    session = session_factory(user=s.setup.user, seed=s.setup.seed_dataset)
    transcript = []
    for turn in s.turns:
        response = await session.send(turn.user)
        transcript.append({"user": turn.user, "agent": response})
        run_turn_assertions(turn.assert_, response, session.last_tool_calls)
    if s.rubric:
        verdict = await judge_session(transcript, s.rubric)
        assert verdict.score >= s.rubric.pass_threshold, verdict.feedback
    eval_reporter.record(s.id, transcript, passed=True)
```

### Behavioral assertion vocabulary (eval suite)

- `tool_called: <name>` — agent invoked this tool/skill
- `tool_args_match: {...}` — args contain expected fields (subset match)
- `tool_not_called: <name>`
- `response_contains: [...]` / `response_does_not_contain: [...]`
- `response_matches_regex: ...`
- `response_is_json_with_schema: <schema>`
- `latency_ms_under: N`
- `cost_usd_under: N`
- `turn_count_under: N`
- `judge_score_above: N`

---

## 10. Migration phases (execute in order)

### Phase 0 — Quick wins (1 day)
1. **Cockpit** `.gitignore`: add lines `playwright-report/`, `test-results/`, `.seed-manifest.json`, `coverage/`, `e2e/seed/.seed-manifest.json`.
2. **Cockpit** `git rm -r --cached playwright-report test-results` to untrack committed artifacts.
3. **Backend** `.gitignore`: add `htmlcov/`, `tests/agent_evals/reports/`, `.coverage`.
4. **Backend** move cruft to `docs/archive/`: `test_results_final.txt`, `test_results_new.txt`, `verify_changes.py`, `verify_ws_p45_f.py`, `WS-P45-F-SUMMARY.md`, `observations.md`, `observations-done.md`.
5. **Cockpit** add Vitest coverage thresholds in `vite.config.ts`:
   ```ts
   test: { coverage: { thresholds: { lines: 60, branches: 60, functions: 60, statements: 60 } } }
   ```

### Phase 1 — Documentation skeleton (2–3 days)
6. Write `graphclaw/TESTING.md` and `graphclaw-cockpit/TESTING.md`.
7. Write `graphclaw/docs/testing/master-strategy.md` (the cross-repo canonical doc).
8. Write `graphclaw-cockpit/docs/testing/test-strategy.md` (FE-specific, links across).
9. Write `contributing-tests.md` in both repos with decision tree.
10. Write the four backend ADRs (0001 pytest-asyncio-auto, 0002 fakes-over-mocks, 0003 evals-layer, 0004 cli-vs-scripts).
11. Write the two cockpit ADRs (0001 playwright-over-puppeteer, 0002 msw-for-component-tests).
12. Move existing `graphclaw/docs/test-scenarios.md` content into `docs/testing/scenario-catalog.md` and reformat to the inventory-style table.
13. Move `graphclaw-cockpit/docs/e2e-test-scenarios.md` similarly.

### Phase 2 — Skills authoring (3–4 days)
14. Rewrite `graphclaw/.claude/skills/graphclaw-test-patterns/` as `graphclaw-pytest-patterns/`. Include header template, inventory workflow.
15. Create the five new graphclaw test skills (`graphclaw-integration-tests`, `graphclaw-contract-tests`, `graphclaw-agent-evals`, `graphclaw-cli-tests`, `graphclaw-test-scripts`).
16. Rewrite `graphclaw-cockpit/.claude/skills/cockpit-playwright-e2e/` to drop Puppeteer references and add Playwright fixture system, header template, inventory workflow.
17. Create cockpit skills `cockpit-vitest-patterns`, `cockpit-contract-tests`.
18. Create `test-inventory-maintenance` skill, mirror in both repos.
19. Verify each SKILL.md description triggers correctly by inspection (does the description match the file paths/scenarios it should fire on?).

### Phase 3 — Header + inventory enforcement (3–4 days)
20. Implement `graphclaw/scripts/check_test_headers.py` and `graphclaw/scripts/regen_inventory.py`.
21. Implement `graphclaw-cockpit/scripts/check-test-headers.mjs` and `graphclaw-cockpit/scripts/regen-inventory.mjs`.
22. Run `regen_inventory` against existing test trees in both repos; commit the generated `inventory.md` files (with `TODO` placeholders in the ID column for legacy tests).
23. Wire both scripts into pre-commit hooks (or husky/lint-staged for cockpit).
24. Add CI step that runs `regen` and fails if `git diff --exit-code` is non-empty.
25. Header lint: warn on legacy files (no header), error on missing/malformed header in new/modified files.

### Phase 4 — Playwright consolidation (2–3 weeks)
26. Audit script: build a coverage-diff matrix between `e2e/` (Playwright) and `e2e-puppeteer/` (Puppeteer) — list every test, mark which suite has it, identify Puppeteer-only specs (a2a/, mcp/, skills/ are known unique).
27. Port helpers: `e2e-puppeteer/helpers/db.helper.ts` → `e2e/helpers/db.ts` and wrap in `e2e/fixtures/db.fixture.ts`. Same for `minio`, `api`, `browser`. `TestContext` base concepts collapse into Playwright fixtures.
28. Port seed: `e2e-puppeteer/fixtures/seed-all.ts` → `e2e/seed/seed-all.ts`. Same for `teardown-all.ts` and JSON data files.
29. Reorganize existing `e2e/` flat specs into feature subdirectories (`auth/`, `canvas/`, etc.).
30. Port specs feature-by-feature. Each ported spec gets a fresh ID and inventory entry. Drop manual `waitForSelector` calls in favor of Playwright auto-waiting locators.
31. Once parity is achieved, rename `e2e-puppeteer/` → `e2e-archive/`. Verify CI green on `main` for one cycle. Then delete `e2e-archive/`.
32. Drop from `package.json`: `puppeteer`, `jest`, `ts-jest`, `jest-config`, `--experimental-vm-modules` scripts, custom sequencer dependency, `test:e2e:puppeteer*` scripts. Keep `@playwright/test` as the sole E2E runner.
33. Update `docker-compose.yml`: remove `e2e-puppeteer` service and `--profile test-puppeteer`. Keep only Playwright `e2e` service under `--profile test`.
34. Update `graphclaw-cockpit/CLAUDE.md` to reflect Playwright-only.

### Phase 5 — CI parity (3 days)
35. Create `graphclaw-cockpit/.github/workflows/ci.yml`:
    - Jobs in order: `lint` (eslint) → `typecheck` (tsc) → `unit` (vitest with coverage upload) → `contract` (handlers vs OpenAPI) → `e2e` (Playwright via `docker compose --profile test`) → `header-lint` → `inventory-check`.
    - Artifacts: coverage HTML, Playwright HTML report, traces on failure.
36. Update `graphclaw/.github/workflows/ci.yml`:
    - Add `contract` job (schemathesis against running app).
    - Add `agent-eval-canary` job, conditional on `paths` matching `src/agent/**`, `src/skills/**`, `src/llm/**`, `tests/agent_evals/prompts/**`. Runs `pytest tests/agent_evals -m eval_canary --run-evals`.
    - Add `header-lint` and `inventory-check` jobs.
37. Create `graphclaw/.github/workflows/nightly-evals.yml`:
    - `schedule: [{ cron: '0 2 * * *' }]` and `workflow_dispatch`.
    - Runs `pytest tests/agent_evals --run-evals --report=json`.
    - Uploads `tests/agent_evals/reports/` as artifact.
    - Posts a summary comment to a tracking issue (config TBD).

### Phase 6 — Contract layer (1 week)
38. **Backend**: write `graphclaw/tests/contract/test_openapi_schema.py` using schemathesis state-machine mode against the running FastAPI app. Add `schemathesis` to `pyproject.toml` test extras.
39. **Frontend**: write `graphclaw-cockpit/src/test/contract/handlers.contract.test.ts`. Load `openapi.json` from a known location (either committed copy at `src/test/openapi.json` refreshed by a script, or fetched from a graphclaw build artifact in CI). For each MSW handler in `src/test/handlers.ts`, assert: (a) path+method exists in spec, (b) request body validates against spec request schema, (c) response shape validates against spec response schema.
40. Wire both contract jobs into Phase 5 CI.

### Phase 7 — Agent evals build-out (1–2 weeks)
41. Implement `graphclaw/tests/agent_evals/runners/chat_session.py` — multi-turn session driver using the real LLM client and the existing orchestrator entry point.
42. Implement `graphclaw/tests/agent_evals/runners/assertions.py` with the vocabulary from §9.
43. Implement `graphclaw/tests/agent_evals/runners/judge.py` — LLM-as-judge invocation that reads a rubric markdown file and returns a 0–1 score with feedback text.
44. Author the 6 baseline orchestrator scenarios listed in §3 folder structure.
45. Author the 2 skill scenarios.
46. Author the 3 rubric markdown files (helpfulness, tool_use_correctness, refusal_quality).
47. Mark 3 of the orchestrator scenarios as `pytest.mark.eval_canary` (cheap, fast-ish, deterministic-leaning).
48. Document budget envelopes in `graphclaw/docs/testing/master-strategy.md`: max $0.50 per nightly run, max $0.05 per canary run.
49. Add `pytest --run-evals` flag wiring in `tests/conftest.py` analogous to `--run-integration`.

### Phase 4.5 — Obsolete test audit (1 week, runs in parallel with Phase 4)
After Playwright porting begins but before Puppeteer deletion, audit the existing 177 backend pytest files and 47 cockpit Vitest files for obsolete/duplicate/skipped tests:

- **Scan for skip markers**: grep both repos for `@pytest.mark.skip`, `@pytest.mark.xfail`, `pytest.skip(`, `it.skip(`, `it.todo(`, `describe.skip(`, `.only(` (left-in debug). Catalog each with reason. For every entry: either fix and re-enable, or delete with rationale logged in commit message.
- **Detect tests for deleted features**: walk every test file, grep for the symbols/endpoints/components it imports. Flag any test importing a symbol that no longer exists in `src/`. Delete the test (do not silently leave it skipped).
- **Detect duplicates**: pairs of tests with near-identical scenarios across `e2e/` and `e2e-puppeteer/`. After Phase 4 port, the Playwright version is canonical; the Puppeteer version goes when `e2e-archive/` is deleted. Pairs that exist within the same suite (e.g., two unit tests asserting the same thing) — collapse to one.
- **Detect dead helpers/fixtures**: any function in `tests/fixtures/`, `e2e-puppeteer/helpers/`, `e2e/fixtures/`, `src/test/` not referenced by any test. Delete.
- **Stale snapshot files**: `__snapshots__/` directories where the corresponding test no longer references the snapshot. Regenerate or delete.
- **Verify wave coverage**: every wave marked complete in `graphclaw-cockpit/build-plan.md` should have at least one test referencing it via the wave field in headers. Flag waves with zero tests as gaps to fill.

Output: `docs/testing/audit-2026-Q2.md` with a table of every flagged item, the action taken, and the commit that resolved it. CI does not gate on this; it's a one-time hygiene pass.

### Phase 8 — Coverage ratchet (ongoing across releases)
50. Raise pytest gate from 60% → 70% → 80% across three releases (one per quarter).
51. Same cadence for Vitest.
52. Add per-package coverage badges to `scenario-catalog.md`.

---

## 11. Critical files (manifest)

### CREATE (graphclaw)
- `TESTING.md`
- `docs/testing/master-strategy.md`
- `docs/testing/scenario-catalog.md`
- `docs/testing/test-id-registry.md`
- `docs/testing/contributing-tests.md`
- `docs/testing/adr/0001-pytest-asyncio-auto.md`
- `docs/testing/adr/0002-fakes-over-mocks.md`
- `docs/testing/adr/0003-evals-layer.md`
- `docs/testing/adr/0004-cli-vs-scripts.md`
- `tests/inventory.md` and leaf inventories
- `tests/contract/__init__.py`, `tests/contract/conftest.py`, `tests/contract/test_openapi_schema.py`
- `tests/agent_evals/` — full tree per §3
- `tests/test_cli/` — formalize
- `tests/fixtures/factories.py`, `tests/fixtures/fakes.py`, `tests/fixtures/seed_data/`, `tests/fixtures/minio_seed/`, `tests/fixtures/sql/`
- `scripts/api_smoke.py`, `scripts/chat_repl.py`, `scripts/check_test_headers.py`, `scripts/regen_inventory.py`, `scripts/check_test_ids.py`
- `.claude/skills/graphclaw-pytest-patterns/` (rewrite from `graphclaw-test-patterns`)
- `.claude/skills/graphclaw-integration-tests/`
- `.claude/skills/graphclaw-contract-tests/`
- `.claude/skills/graphclaw-agent-evals/`
- `.claude/skills/graphclaw-cli-tests/`
- `.claude/skills/graphclaw-test-scripts/`
- `.claude/skills/test-inventory-maintenance/`
- `.github/workflows/nightly-evals.yml`

### CREATE (graphclaw-cockpit)
- `TESTING.md`
- `docs/testing/test-strategy.md`
- `docs/testing/scenario-catalog.md`
- `docs/testing/contributing-tests.md`
- `docs/testing/adr/0001-playwright-over-puppeteer.md`
- `docs/testing/adr/0002-msw-for-component-tests.md`
- `e2e/inventory.md`, `src/test/inventory.md`
- `e2e/fixtures/seed.fixture.ts`, `db.fixture.ts`, `minio.fixture.ts`, `api.fixture.ts`, `test.ts`
- `e2e/helpers/db.ts`, `minio.ts`, `api.ts`, `browser.ts`
- `e2e/seed/seed-all.ts`, `teardown-all.ts`, `data/`, `manifest.types.ts`
- `e2e/README.md`
- `src/test/contract/handlers.contract.test.ts`
- `src/test/fixtures/tasks.ts`, `agents.ts`, `users.ts`
- `scripts/check-test-headers.mjs`, `regen-inventory.mjs`, `check-test-ids.mjs`
- `.claude/skills/cockpit-vitest-patterns/`
- `.claude/skills/cockpit-contract-tests/`
- `.claude/skills/test-inventory-maintenance/` (mirror)
- `.github/workflows/ci.yml`

### MODIFY
- `graphclaw-cockpit/.gitignore` — add report/coverage/manifest paths.
- `graphclaw-cockpit/vite.config.ts` (or new `vitest.config.ts`) — coverage thresholds + reporter.
- `graphclaw-cockpit/package.json` — drop `puppeteer`, `jest`, `ts-jest`, `--experimental-vm-modules`, `test:e2e:puppeteer*` scripts. Add `test:contract`, `test:coverage`. Keep `@playwright/test`.
- `graphclaw-cockpit/docker-compose.yml` — remove `e2e-puppeteer` service and `--profile test-puppeteer`.
- `graphclaw-cockpit/CLAUDE.md` — Playwright-only test guidance.
- `graphclaw-cockpit/.claude/skills/cockpit-playwright-e2e/SKILL.md` — full rewrite.
- `graphclaw/.gitignore` — add `htmlcov/`, `tests/agent_evals/reports/`.
- `graphclaw/.github/workflows/ci.yml` — add contract + canary-eval + header-lint + inventory-check jobs.
- `graphclaw/pyproject.toml` — add `schemathesis`, `pyyaml` to test extras; raise coverage gate per ratchet.
- `graphclaw/tests/conftest.py` — add `--run-evals` flag analogous to `--run-integration`.
- `graphclaw/CLAUDE.md` — add testing methodology pointer.

### DELETE (after Phase 4 completes)
- `graphclaw-cockpit/e2e-puppeteer/` (after archive period)
- `graphclaw-cockpit/playwright-report/` (after gitignore + rm-cached)
- `graphclaw-cockpit/test-results/` (same)
- `graphclaw/test_results_final.txt`, `test_results_new.txt`, `verify_changes.py`, `verify_ws_p45_f.py`, `WS-P45-F-SUMMARY.md`, `observations.md`, `observations-done.md` (move to `docs/archive/`)

---

## 12. Cleanup checklist (consolidated — every removal in one place)

This section consolidates every deletion/removal scattered across Phases 0–8 so cleanup is explicit and auditable. Each row notes WHICH phase performs the action so nothing is removed prematurely.

### 12a. Tracked-file cleanup (committed cruft)

| Item | Repo | Phase | Action |
|---|---|---|---|
| `playwright-report/` (committed dir) | cockpit | Phase 0 | `git rm -r --cached`, then `.gitignore` |
| `test-results/` (committed dir) | cockpit | Phase 0 | `git rm -r --cached`, then `.gitignore` |
| `.seed-manifest.json` (if committed) | cockpit | Phase 0 | `.gitignore` |
| `htmlcov/`, `.coverage` | graphclaw | Phase 0 | `.gitignore` |
| `tests/agent_evals/reports/` | graphclaw | Phase 0 | `.gitignore` (created in Phase 7) |
| `test_results_final.txt` | graphclaw | Phase 0 | move to `docs/archive/` then delete after one release |
| `test_results_new.txt` | graphclaw | Phase 0 | move to `docs/archive/` |
| `verify_changes.py` | graphclaw | Phase 0 | move to `docs/archive/` |
| `verify_ws_p45_f.py` | graphclaw | Phase 0 | move to `docs/archive/` |
| `WS-P45-F-SUMMARY.md` | graphclaw | Phase 0 | move to `docs/archive/` |
| `observations.md` | graphclaw | Phase 0 | move to `docs/archive/` |
| `observations-done.md` | graphclaw | Phase 0 | move to `docs/archive/` |

### 12b. Puppeteer suite removal (Phase 4)

| Item | Action | Phase step |
|---|---|---|
| `e2e-puppeteer/` directory | Rename to `e2e-archive/` after parity port. Delete after one green CI cycle on `main`. | Step 31 |
| `e2e-puppeteer/specs/` | Verify each spec has a Playwright equivalent before archiving | Step 26 (audit), Step 30 (port) |
| `e2e-puppeteer/helpers/{db,minio,api,browser,auth}.helper.ts` | Port content into `e2e/helpers/*.ts` + `e2e/fixtures/*.fixture.ts`, then delete originals | Step 27 |
| `e2e-puppeteer/base/TestContext.ts` | Concepts collapse into Playwright fixtures; delete | Step 27 |
| `e2e-puppeteer/fixtures/seed-all.ts`, `teardown-all.ts` | Port to `e2e/seed/` then delete | Step 28 |
| `e2e-puppeteer/jest.config.cjs` | Delete | Step 31 |
| `e2e-puppeteer/sequencer.cjs` | Delete (Playwright `workers: 1` replaces it) | Step 31 |
| `package.json` deps: `puppeteer`, `jest`, `ts-jest`, `@types/jest`, `jest-environment-node` | Remove via `npm uninstall` | Step 32 |
| `package.json` scripts: `test:e2e:puppeteer`, `test:e2e:puppeteer:ci`, `seed:puppeteer`, `teardown:puppeteer` | Remove from package.json | Step 32 |
| `package.json` `--experimental-vm-modules` flag | Remove (only needed for Jest ESM) | Step 32 |
| `docker-compose.yml` `e2e-puppeteer` service | Delete service definition | Step 33 |
| `docker-compose.yml` `--profile test-puppeteer` | Delete profile references | Step 33 |
| `Dockerfile.e2e-puppeteer` (if exists) | Delete | Step 33 |
| `CLAUDE.md` Puppeteer references | Replace with Playwright-only guidance | Step 34 |

### 12c. Skill cleanup

| Item | Action | Phase |
|---|---|---|
| `graphclaw/.claude/skills/graphclaw-test-patterns/` | Rename to `graphclaw-pytest-patterns/`, rewrite content. Old name removed by rename. | Phase 2 step 14 |
| `graphclaw-cockpit/.claude/skills/cockpit-playwright-e2e/` | Full rewrite in place — drop any Puppeteer references, fixture descriptions, etc. | Phase 2 step 16 |
| Any `.claude/skills/cockpit-puppeteer-e2e/` (if it exists) | Delete entire skill directory | Phase 4 step 31 |

### 12d. Obsolete test cleanup (Phase 4.5 audit)

| Item type | Action |
|---|---|
| `@pytest.mark.skip`, `@pytest.mark.xfail`, `pytest.skip(`, `it.skip()`, `it.todo()`, `describe.skip()` | Each requires decision: fix-and-enable OR delete-with-commit-message rationale. No silent skips remain after Phase 4.5. |
| `.only(` left in committed test files | Delete the `.only` (debug leak) |
| Tests importing symbols that no longer exist in `src/` | Delete test file, log in audit doc |
| Duplicate test pairs (Playwright + Puppeteer asserting same scenario) | Resolved automatically by Phase 4 (Puppeteer version goes with `e2e-archive/` deletion) |
| Duplicate assertions within same suite (two unit tests asserting same thing) | Collapse to one |
| Dead helpers/fixtures in `tests/fixtures/`, `src/test/`, `e2e/helpers/` not referenced by any test | Delete |
| Stale `__snapshots__/` files where source test no longer references them | Regenerate or delete |
| `tests/test_load/` (norecursedir target) — verify whether this duplicates `tests/load/` | If duplicate, delete `tests/test_load/`. If distinct, document why both exist. |

Audit output: `graphclaw/docs/testing/audit-2026-Q2.md` with table of every flagged item, action taken, resolving commit.

### 12e. Documentation cleanup

| Item | Action | Phase |
|---|---|---|
| `graphclaw/docs/test-scenarios.md` | Content migrated to `docs/testing/scenario-catalog.md`, original file deleted | Phase 1 step 12 |
| `graphclaw-cockpit/docs/e2e-test-scenarios.md` | Migrated to `docs/testing/scenario-catalog.md`, original file deleted | Phase 1 step 13 |
| `graphclaw/tests/load/README.md` | Content folded into `tests/load/inventory.md`, original deleted | Phase 3 step 22 |
| Any `TESTING.md` fragments in feature directories that contradict the new strategy | Delete or rewrite to point to root `TESTING.md` | Phase 1 |

### 12f. Cleanup verification

After all phases land, run these to confirm cleanup is complete:

```bash
# graphclaw
git ls-files | grep -E '(test_results_final|verify_changes|verify_ws_p45|WS-P45|observations\.md|observations-done)'
# Expected: no output

# cockpit — Puppeteer fully gone
test ! -d graphclaw-cockpit/e2e-puppeteer && test ! -d graphclaw-cockpit/e2e-archive
grep -E '"puppeteer"|"jest":|"ts-jest"' graphclaw-cockpit/package.json
# Expected: no output

# cockpit — committed reports gone
git ls-files | grep -E '(playwright-report|test-results)/'
# Expected: no output

# both — no silent skips
grep -rE '@pytest\.mark\.(skip|xfail)|pytest\.skip\(|it\.skip\(|it\.todo\(|describe\.skip\(|\.only\(' \
  graphclaw/tests graphclaw-cockpit/src graphclaw-cockpit/e2e
# Expected: each match resolved per Phase 4.5 audit doc; ideally zero matches
```

---

## 13. Verification

### Local commands

| Command | Repo | Produces |
|---|---|---|
| `pytest --cov=graphclaw --cov-report=html` | graphclaw | `htmlcov/index.html`, exit 0 if ≥ gate |
| `pytest -m integration --run-integration` | graphclaw | terminal report, sidecars up |
| `pytest tests/contract` | graphclaw | schemathesis output, hypothesis stats |
| `pytest tests/agent_evals -m eval_canary --run-evals` | graphclaw | canary results, transcripts in `reports/` |
| `pytest tests/agent_evals --run-evals` | graphclaw | full evals + judge scores |
| `pytest tests/test_cli` | graphclaw | CliRunner test output |
| `python scripts/api_smoke.py` | graphclaw | printed JSON, no assertions |
| `python scripts/chat_repl.py` | graphclaw | interactive orchestrator chat |
| `python scripts/regen_inventory.py` | graphclaw | rewritten inventory.md, exit 0 |
| `python scripts/check_test_headers.py` | graphclaw | header validation report |
| `npm test -- --coverage` | cockpit | `coverage/index.html`, threshold-enforced |
| `npm run test:contract` | cockpit | MSW vs OpenAPI conformance |
| `npm run test:e2e` | cockpit | Playwright HTML report + traces on failure |
| `node scripts/regen-inventory.mjs` | cockpit | rewritten inventory.md |
| `node scripts/check-test-headers.mjs` | cockpit | header validation report |

### CI dashboard (every PR)

**graphclaw** (in order, all required):
1. `lint` (ruff) → 2. `typecheck` (mypy) → 3. `unit` (pytest) + coverage → 4. `contract` (schemathesis) → 5. `integration` (with Postgres+AGE+Redis+MinIO sidecars) → 6. `cli` (Typer CliRunner) → 7. `agent-eval-canary` (conditional on path filter) → 8. `header-lint` → 9. `inventory-check` → 10. `security` (bandit).

**graphclaw-cockpit** (in order, all required):
1. `lint` (eslint) → 2. `typecheck` (tsc) → 3. `unit` (vitest + coverage) → 4. `contract` → 5. `e2e` (Playwright, `--profile test`) → 6. `header-lint` → 7. `inventory-check`.

### Acceptance for "harness is working"
- Both repos green on `main` with all required checks for two consecutive weeks.
- Every test file in both repos starts with the canonical header block. Header lint = 0 errors.
- `inventory.md` at every test root regenerates in CI with no diff.
- `scenario-catalog.md` has zero `Status: TODO` rows for completed build-plan waves.
- A randomly chosen PRD acceptance criterion can be traced to ≥ 1 test ID via grep against the catalog in under 30 seconds.
- Onboarding test: a new engineer adds a test in the right place, with the right ID and header, by reading only `TESTING.md` + `contributing-tests.md` + the relevant skill.
- Agent eval nightly publishes a summary artifact for each run; canary stays green on PRs that don't touch agent code.
- `e2e-puppeteer/` directory does not exist; `puppeteer` not in `package.json`.

---

## 14. Execution notes for fresh Claude session

- **Read this whole file before starting.** Don't skim.
- **Phase 0 is non-negotiable and must be first.** Cleaning the gitignore + cruft prevents confusion in later phases.
- **Phase 4 (Playwright consolidation) is the longest pole.** Treat it as multiple PRs, one per feature subdir. Don't try to land it in a single change.
- **Skills (Phase 2) come before enforcement (Phase 3) intentionally.** The skills provide the templates that the enforcement scripts validate. Reverse order produces enforcement that fails on every file.
- **Agent evals (Phase 7) requires real LLM credentials in CI.** Confirm `ANTHROPIC_API_KEY` is in repo secrets before wiring nightly workflow. Until then, evals run locally only.
- **Do NOT skip the file headers and inventory.** They are the spine of this whole plan. Every other piece (skills, IDs, traceability, catalog, lint) depends on them.
- **Commit format**: `feat(testing): <phase-N> <description>` for testing work. Don't bundle into feature commits.
- **Run quality gates per repo's CLAUDE.md.** graphclaw: ruff check + format. cockpit: typecheck + lint + test.
- **When in doubt, prefer pytest/Vitest patterns that already exist in the repo.** This plan describes the destination; existing patterns in conftest.py and src/test/utils.tsx are the starting point.

---

_End of build plan. Pick up at Phase 0 and proceed in order._
