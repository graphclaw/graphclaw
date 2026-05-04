# Test Audit — 2026 Q2

> Phase 4.5 hygiene pass. Each flagged item has a status and the resolving commit where applicable.
> Generated: 2026-05-04

---

## 1. Skip / xfail markers

### 1a. Backend (`graphclaw/tests/`)

| File | Line | Marker | Reason | Status | Action |
|---|---|---|---|---|---|
| `tests/integration/test_principals.py` | 141 | `@pytest.mark.skipif(not DATABASE_URL)` | DATABASE_URL not set | **Legitimate** — environment gate, safe in CI where var is always set | No action |
| `tests/test_api/test_chat_history_integration.py` | 161 | `@pytest.mark.skipif(not AUTH_TOKEN)` | TEST_AUTH_TOKEN not set | **Legitimate** — opt-in token test, safe skip when token absent | No action |
| `tests/agent_evals/conftest.py` | 53 | `pytest.skip(ANTHROPIC_API_KEY not set)` | Key absent | **Legitimate** — eval gate, consistent with `--run-evals` design | No action |
| `tests/test_gateway/test_app.py` | 28 | `pytest.mark.skipif(not _HTTPX_AVAILABLE)` | httpx optional | **Vestigial** — httpx is in `dependencies` (`httpx>=0.27.0`), always available. Guard never fires. | Left in place (harmless). Remove when test_gateway is next touched. |
| `tests/test_gateway/test_routes.py` | 32 | `pytest.mark.skipif(not _HTTPX_AVAILABLE)` | httpx optional | Same as above | Same as above |

**Verdict**: No silent regressions. Two vestigial guards are harmless noise; flagged for cleanup on next touch.

---

### 1b. Cockpit E2E (`graphclaw-cockpit/e2e/`)

| File | Tests skipped | Reason | Status | Action |
|---|---|---|---|---|
| `e2e/intelligence/intelligence-hub.spec.ts` | 4 | Rate limited in full suite | **Data-dependent flake** — backend rate limits fire when all E2E tests run sequentially without delay | Add seeded delay between test groups; tracked as known issue below |
| `e2e/settings/settings.spec.ts` | 2 | Rate limited in full suite | Same | Same |
| `e2e/graph/goal-view.spec.ts` | 2 | Rate limited in full suite | Same | Same |
| `e2e/graph/my-tasks.spec.ts` | 3 (2 × bug, 1 × rate limit) | (1) Backend 500 on task create — Pydantic ID validation bug; (2) Rate limited | (1) Known backend bug; (2) rate limit flake | (1) Bug tracked below; (2) rate limit |
| `e2e/graph/project-view.spec.ts` | 4 | Rate limited (3); no goals in DB (1) | (1) Rate limit; (2) seed gap | Seed gap: add goal to seed-all.ts; rate limit: delay |
| `e2e/graph/timeline-view.spec.ts` | 5 | Rate limited (3); no hierarchy in data (1); no gantt rendered (1) | Rate limit + seed gaps | Seed: add hierarchical tasks (parent + children) in seed-all.ts |
| `e2e/global/dashboard.spec.ts` | 4 | Rate limited or auth error in full suite | Rate limit flake | Delay between suites |
| `e2e/admin/admin-panel.spec.ts` | 1 | No features returned from API | Seed gap — no feature flags seeded | Add feature flag rows to seed-all.ts |

**Root cause summary:**

1. **Rate limiting (14 tests)**: Backend enforces 300 req/min/user. With 18 spec files running sequentially and no inter-suite delay, the limit fires on the 3rd–4th test file. Fix: add a 200–500ms delay between each `api.*` call in fixtures, or seed the test user with a higher rate limit override, or add `test.slow()` pacing. Not a code bug — correct behavior being triggered.

2. **Backend 500 on task create (2 tests)**: Known Pydantic ID validation bug in `POST /graph/tasks`. Tracked separately — when fixed, remove the conditional skip.

3. **Seed gaps (4 tests)**: `seed-all.ts` does not create: (a) feature flags, (b) goals with hierarchy (parent task → child tasks), (c) goals in the DB for the project-view test. Fix: extend `seed-all.ts`.

---

## 2. Dead / excluded tests never collected

| File | Issue | Fix | Commit |
|---|---|---|---|
| `tests/test_load/test_thresholds.py` | Real pytest tests excluded by `norecursedirs = [..., "tests/test_load", ...]`. 7 test functions never ran. | Removed `tests/test_load` from `norecursedirs` in `pyproject.toml`. `tests/load` remains excluded (Locust scripts, not pytest). | This commit |

**Note**: `tests/test_load/test_thresholds.py` imports `locust` (via `tests.load.locustfile`). `locust>=2.20.0` is in the `dev` optional group. CI runs `pip install -e ".[dev]"` so this is always available.

---

## 3. Duplicate tests

**Conclusion: none found.**

- `e2e-puppeteer/` does not exist in the repo (was never committed, or was removed before this audit).
- No pairs of Playwright + Puppeteer tests asserting the same scenario.
- No pairs within the Playwright suite (`e2e/`) asserting identical scenarios (checked by spec file review).
- No pairs within the backend pytest suite.

---

## 4. Dead helpers / fixtures

| Location | Status |
|---|---|
| `graphclaw-cockpit/src/test/` — handlers.ts, server.ts, setup.ts, utils.tsx | All referenced by unit tests. Not dead. |
| `graphclaw-cockpit/e2e/helpers/` — db.ts, minio.ts, api.ts, browser.ts | All imported by `e2e/fixtures/test.ts`. Not dead. |
| `graphclaw-cockpit/e2e/fixtures/` — auth.fixture.ts, test.ts | auth.fixture.ts used by existing legacy specs; test.ts is the new merged fixture. Not dead. |
| `graphclaw/tests/fixtures/` | Does not exist as a unified dir yet (formalization is Phase 3+). Fakes live in test_api/conftest.py. |

**Conclusion: no dead helpers or fixtures.**

---

## 5. Stale snapshot files

**Conclusion: none.** No `__snapshots__/` directories exist in either repo.

---

## 6. Wave coverage gaps

Waves are tracked in `graphclaw-cockpit/build-plan.md`. Analysis of completed waves vs. test file wave references:

| Wave | Status | Test coverage |
|---|---|---|
| Wave 1 — Scaffold | Complete | Vitest unit tests exist (47 `.test.tsx` files cover components from W1–W12) |
| Wave 2 — API Client + Auth | Complete | MSW handlers + contract test cover auth + API layer |
| Wave 3 — App Shell | Complete | E2E `global/dashboard.spec.ts`, `graph/goal-view.spec.ts` cover navigation |
| Wave 4 — Graph Views | Complete | E2E `graph/my-tasks.spec.ts`, `graph/project-view.spec.ts`, `graph/timeline-view.spec.ts` |
| Wave 4b — Timeline Gantt | Complete | E2E `graph/timeline-view.spec.ts` (partially skipped — seed gap) |
| Wave 5 — Agent Monitor (superseded) | Superseded by Wave M | — |
| Wave M — Agent Monitor v2 | Partial (M-E-2, M-E-3, M-F-2 blocked/pending) | E2E `agent/agent-monitor.spec.ts` exists |
| Wave 6 — Settings | Complete | E2E `settings/settings.spec.ts` |
| Wave 7 — Marketplace | Complete | E2E `marketplace/skill-marketplace.spec.ts` |
| Wave 8 — Canvas | Complete | E2E `canvas/canvas-editor.spec.ts` |
| Wave 9 — Intelligence Hub | Complete | E2E `intelligence/intelligence-hub.spec.ts` |
| Wave 10 — Chat | Complete | E2E `chat/chat-interface.spec.ts` |
| Wave 11 — Admin | Complete | E2E `admin/admin-panel.spec.ts` |
| Wave 12 — Polish / E2E | In progress | — |

**Gap**: Wave M items M-E-2 (trigger snooze/resume), M-E-3 (run history), M-F-2 (recent jobs table) are pending implementation — no tests expected until those tasks land.

---

## 7. `.only()` debug leaks

**Conclusion: none.** Grep across `graphclaw-cockpit/src/**/*.{ts,tsx}` and `e2e/**/*.spec.ts` found zero `.only(` occurrences.

---

## 8. Tests importing deleted symbols

Not systematically checked (requires running mypy/tsc on every test import). Proxy check: `npm run typecheck` and `npm run test` both pass, so no missing import errors in the TypeScript/Vitest layer. Backend: `pytest --collect-only` would surface import errors; CI is green.

---

## 9. Summary of actions taken

| Action | File | Phase |
|---|---|---|
| Remove `tests/test_load` from `norecursedirs` — enables 7 threshold unit tests | `pyproject.toml` | Phase 4.5 (this commit) |

## 10. Open items (not fixed in this pass)

| # | Item | Owner | Priority |
|---|---|---|---|
| 1 | Rate limit flakes (14 E2E skips) — add inter-suite pacing or rate-limit override for test user | frontend-team | Medium |
| 2 | Backend 500 on `POST /graph/tasks` (Pydantic ID validation) — fix in backend, then remove `test.skip` | backend-team | High |
| 3 | Seed gaps — add feature flags, hierarchical tasks, goals to `e2e/seed/seed-all.ts` | frontend-team | Medium |
| 4 | Remove vestigial `skipif(not _HTTPX_AVAILABLE)` guards from test_gateway when next touched | backend-team | Low |
