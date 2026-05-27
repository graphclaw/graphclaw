# GraphClaw Open-Source Launch Plan

**Status:** Active — Wave 0 implementation complete; exit criteria validation pending
**Owner:** Core maintainers
**Scope:** Pre-1.0 public open-source launch of `graphclaw` (backend) and `graphclaw-cockpit` (frontend) under a new GitHub organization, with `graphclaw.ai` serving as the public landing + docs portal.
**Not in scope:** Hosted SaaS runtime. The runtime is self-host-only at launch; the existing ECS deploy pipeline stays gated to maintainers.

> **Distinct from** [`build-plan.md`](./build-plan.md), which tracks engineering phases/waves of the product itself. This document tracks the public-launch readiness, release engineering, and contributor governance.

---

## Goals

1. Ship a coordinated **pre-1.0 (`v0.1.0`) release** of both repos with signed, reproducible artifacts on PyPI + GHCR + GitHub Releases.
2. Stand up `graphclaw.ai` as a GitHub Pages landing site with `/docs` routing to both repos' documentation hubs.
3. Migrate both repositories to a new GitHub organization (canonical slug: **`graphclaw`**).
4. Establish open-source governance: contribution workflow, security disclosure, code of conduct, maintainer roles, and release cadence.
5. Make it possible for a stranger on the internet to: read docs → install → self-host → open an issue → submit a PR.

## Non-Goals

- Hosting the runtime as a SaaS.
- Adding a CLA at launch (DCO sign-off only).
- Helm chart, Homebrew tap, or cockpit npm publishing at launch (revisit in Wave 8).
- Lockstep versioning between the two repos.

---

## Locked Decisions

| Topic | Decision |
| --- | --- |
| License | Apache-2.0 across both repos; remove all "Proprietary" remnants. |
| Org slug | `graphclaw` (canonical; not `graphaclaw`). |
| Versioning | SemVer; independent versions per repo; both launch at `v0.1.0`. |
| Commits | Conventional Commits, enforced by PR title linter. |
| Sign-off | DCO at launch (no CLA). |
| Release automation | `release-please` for both repos. |
| Distribution at launch | PyPI (backend), GHCR multi-arch (both), GitHub Releases (both). Cockpit npm deferred. |
| Artifact security | cosign keyless (GitHub OIDC) + CycloneDX SBOM + SLSA provenance on every release. |
| Branch model | Trunk-based on `main`; squash-merge; short feature branches. |
| Docs hosting | GitHub Pages at `graphclaw.ai`, landing + `/docs`. |
| Docs stack at launch | Static curated links; upgrade to MkDocs Material in Wave 8. |

---

## Open Questions (must answer before Wave 0 completes)

1. **PyPI name reservation.** Confirm `graphclaw` is available on PyPI; reserve under personal account now, transfer to org in Wave 1.
2. **Cockpit multi-arch.** Confirm `linux/amd64` + `linux/arm64` are both desired for v0.1.0 GHCR images.
3. **Cosign mode.** Keyless (OIDC) confirmed? Alternative is a hardware-backed key (more ops burden).
4. **Email aliases.** `conduct@graphclaw.ai` and `security@graphclaw.ai` must exist before `SECURITY.md` and `CODE_OF_CONDUCT.md` are published.

---

## Waves

### Wave 0 — Pre-flight scrub + OSS/release foundation (blocking)

**Goal:** Everything that must be true *before* the repos can be transferred and made public-facing.

**Order:** 0.A first → (0.B ∥ 0.C) → 0.D → 0.E. 0.F is documentation only.

**Progress update (2026-05-27):**
- 0.A release plumbing implemented in both repos; backend manual rerun `26517827855` completed successfully (including `Attach Python artifacts to GitHub Release`).
- 0.B and 0.C metadata/license/credential scrub completed for launch scope.
- 0.D and 0.E governance docs, templates, and automation workflows are now present in both repos.
- Remaining Wave 0 closure work is limited to exit-criteria validation and unresolved open questions.

#### 0.A — Release plumbing dry-run

Set up the full release pipeline on both repos so it's exercised before public eyes arrive.

**Backend (`graphclaw`):**
- Add `release-please-config.json` + `.release-please-manifest.json` at repo root. Component type: `python`. Package: `graphclaw`.
- Add `.github/workflows/release-please.yml` — opens and maintains the release PR from Conventional Commits on every push to `main`.
- Add `.github/workflows/release.yml`, triggered on `v*` tag push:
  - Build wheel + sdist via `python -m build`.
  - Build multi-arch image (`linux/amd64`, `linux/arm64`) with `docker/build-push-action`; push to `ghcr.io/<org>/graphclaw:<tag>` and `:latest`.
  - Generate CycloneDX SBOM via `anchore/sbom-action`.
  - Sign image with `sigstore/cosign-installer` keyless via GitHub OIDC.
  - Attach SBOM + cosign signature + SLSA provenance to GitHub Release assets.
  - **Defer** PyPI trusted-publisher registration until repos live under the final org slug. Workflow YAML can be pre-written.
- Add `.github/workflows/conventional-title.yml` (e.g. `amannn/action-semantic-pull-request`).
- Retarget `.github/workflows/build-push.yml` (ECR) to `workflow_dispatch` only — no auto-trigger.
- Gate `.github/workflows/deploy.yml` to `workflow_dispatch` + new GitHub `production` Environment with required reviewers.

**Cockpit (`graphclaw-cockpit`):**
- Same `release-please` + `release.yml` pattern, component type `node`.
- `release.yml` publishes only the GHCR multi-arch image + the prebuilt `dist/` tarball; no PyPI step; no npm publish (`"private": true` stays).
- Same conventional-title workflow.

**Validate:** Cut `v0.0.1-rc.0` internal tags on both repos → verify all jobs green, signed images pullable from GHCR, SBOMs valid, GitHub Release assets present.

#### 0.B — License + metadata scrub (parallel with 0.A)

- `graphclaw/src/graphclaw/gateway/app.py` (~line 805): flip `license_info` from `"Proprietary"` to `{"name": "Apache-2.0", "identifier": "Apache-2.0"}`.
- Regenerate `graphclaw-cockpit/src/test/openapi.json` from updated backend spec.
- `graphclaw/pyproject.toml`: confirm `license = {text = "Apache-2.0"}`; add PyPI classifiers (`License :: OSI Approved :: Apache Software License`, `Development Status :: 4 - Beta`, supported Python versions); mark Homepage/Repository URLs as TODO for org slug.
- `graphclaw-cockpit/package.json`: add `"license": "Apache-2.0"`, `"homepage"`, `"repository"`, `"bugs"`, `"author"` (org). Keep `"private": true`.
- Verify no `"Proprietary"` string remains anywhere in either repo (grep).

#### 0.C — PII + credential scrub (parallel with 0.A)

- `graphclaw/README.md`: remove personal email (`abhishekgupta86@gmail.com`) and dev credentials (`graphclaw_dev`, `admin/admin`). Replace with placeholders + link to env reference doc.
- Grep both repos for old owner string `abhishekgupta-myrepo`, personal email, and any hardcoded passwords. Old-owner GitHub URLs are fixed in Wave 2; PII and creds are fixed here.
- Verify `.env*` patterns are in `.gitignore`; verify no `.env*` files committed to history (run `git log --all --full-history -- .env*`).

#### 0.D — Governance file set (depends on 0.A)

CONTRIBUTING needs to reference the actual release tooling, hence the dependency.

Both repos get the same set; content tailored where needed.

| File | Content summary |
| --- | --- |
| `CONTRIBUTING.md` | DCO sign-off requirement; Conventional Commits with examples; branch model; local dev pointer; link to GOVERNANCE for release cadence. |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1 verbatim; `conduct@graphclaw.ai` as contact. |
| `SECURITY.md` | Private GitHub Security Advisory flow; supported versions = latest minor + previous minor; 5 business day acknowledgment SLA; 90-day disclosure window; optional PGP key. |
| `SUPPORT.md` | Discussions for Q&A; Issues for bugs/features; Security Advisories for vulns. |
| `GOVERNANCE.md` | Roles: Triager → Reviewer → Maintainer → Admin. Trust escalation criteria (e.g. 5 merged PRs → Triager invite). Lazy-consensus decision model. Release cadence: patch as-needed, minor ~4 weeks. |
| `.github/CODEOWNERS` | Per-path ownership (e.g. `/src/graphclaw/agent/ @<org>/agent-maintainers`). |
| `.github/ISSUE_TEMPLATE/bug.yml` | Structured bug report. |
| `.github/ISSUE_TEMPLATE/feature.yml` | Feature request. |
| `.github/ISSUE_TEMPLATE/question.yml` | Redirected to Discussions in `config.yml`. |
| `.github/ISSUE_TEMPLATE/config.yml` | `blank_issues_enabled: false`; security link. |
| `.github/pull_request_template.md` | Sections: Summary, Linked issue, Type of change, Testing, Breaking change, Checklist. |
| `.github/FUNDING.yml` | Optional; can defer to Wave 8. |

#### 0.E — PR governance automation (depends on 0.D)

- `.github/workflows/dco.yml` — e.g. `tim-actions/dco`.
- `.github/workflows/gitleaks.yml` — secret scan on every PR.
- `.github/workflows/stale.yml` — 60-day warn, 90-day close, friendly reopen message.
- `.github/workflows/welcome.yml` — first-time contributor welcome comment.
- `.github/labels.yml` + sync workflow via `crazy-max/ghaction-github-labeler`. Taxonomy:
  - `good first issue`, `help wanted`
  - `type:bug`, `type:feat`, `type:docs`, `type:chore`
  - `area:agent`, `area:gateway`, `area:cockpit`, `area:infra`, `area:docs`
  - `breaking`, `needs-triage`, `blocked`, `stale`
  - `security`, `dependencies`

#### 0.F — Branch protection prep (documentation only)

Document rules in this file under Wave 2; cannot apply until repos exist under the new org.

**Rules to apply to `main` on both repos in Wave 2:**
- Require pull request before merging.
- Require 1 CODEOWNERS approval (2 for any PR labeled `breaking`).
- Required status checks: `ci`, `pr-checks`, `dco`, `conventional-title`, `gitleaks`.
- No force pushes.
- Linear history required.
- Conversation resolution required before merge.
- Restrict who can push to matching branches: maintainers only.

#### Wave 0 exit criteria

- [ ] `gh api repos/<repo>/community/profile` returns 100% on both repos.
- [ ] `v0.0.1-rc.0` dry-run release succeeds end-to-end on both repos with signed GHCR images + SBOMs.
- [ ] `grep -ri "Proprietary"` returns no hits in either repo.
- [ ] `grep -ri "abhishekgupta86\|admin/admin\|graphclaw_dev"` returns only intentional placeholders.
- [x] Test PR with bad title + missing sign-off fails `conventional-title` + `dco` checks; clean PR passes.
- [ ] All four Open Questions above answered and resolved.

---

### Wave 1 — Org + repo migration (blocking)

- Confirm org slug `graphclaw` is available on GitHub; create the org.
- Transfer both repositories into the org. GitHub auto-creates HTTP redirects from old URLs.
- Freeze merges during transfer window to avoid broken links mid-migration.
- Configure org-level secrets: `GHCR_TOKEN` (if not using built-in `GITHUB_TOKEN`), PyPI trusted publisher for `graphclaw` package (PyPI side: link to `<org>/graphclaw` repo + `release.yml` workflow + `production` environment).
- Transfer PyPI package ownership to org account.
- Create org teams: `core-maintainers`, `agent-maintainers`, `gateway-maintainers`, `cockpit-maintainers`, `infra-maintainers`, `triagers`.
- Set up `conduct@` and `security@` email aliases at registrar / Google Workspace.

**Exit criteria:** Old URLs redirect; clone with new URLs works; org teams populated; PyPI trusted publisher configured.

---

### Wave 2 — Post-migration link + metadata sweep (parallel across repos)

- Replace `abhishekgupta-myrepo` with new org slug in:
  - `graphclaw/README.md` (lines ~7, 74)
  - `graphclaw/pyproject.toml` (Homepage, Repository)
  - `graphclaw/docs/release-notes/CHANGELOG.md` (compare URLs ~lines 248–250)
  - `graphclaw/docs/cockpit-backend-api-prd.md` (line 4)
  - `graphclaw-cockpit/README.md` (line 11)
  - `graphclaw-cockpit/package.json` (homepage, repository, bugs)
- Verify `.github/CODEOWNERS` uses new org team handles.
- Regenerate `graphclaw-cockpit/src/test/openapi.json` from the migrated backend spec.
- **Apply branch protection rules** documented in 0.F to both repos via GitHub UI or `gh api`.
- Verify labels synced via the labels sync workflow.

**Exit criteria:** No `abhishekgupta-myrepo` references remain; branch protection enforced on both repos; CODEOWNERS reviewers auto-assigned on test PR.

---

### Wave 3 — Documentation IA cleanup (parallel with Wave 2)

- Normalize entry points: `README.md` → `docs/README.md` (hub) → canonical docs index for each repo.
- Fix relative links and old-owner references across `docs/`.
- Add a "Self-host deployment guide" prominent link in both READMEs, pinned to `v0.1.0` once released.
- Update `docs/redirects.md` for any path moves.
- Add `docs/how-to/release.md` (release process for maintainers).
- Add `docs/explanation/versioning.md` (SemVer policy + support window).
- Add `docs/explanation/deprecations.md` (deprecation policy: 1 full MINOR window of warnings).

---

### Wave 4 — `graphclaw.ai` website architecture (blocking before DNS cutover)

- Create new repo `<org>/graphclaw.ai` for GitHub Pages source.
- Build a minimal landing page at `/`:
  - What GraphClaw is (1-paragraph elevator pitch).
  - Self-host CTA (quickstart link).
  - Links to backend repo, cockpit repo.
  - Status badges: build, release, license.
- Build `/docs` as a curated index that links to:
  - Backend docs hub (`https://github.com/<org>/graphclaw/tree/main/docs`).
  - Cockpit docs hub (`https://github.com/<org>/graphclaw-cockpit/tree/main/docs`).
  - Quickstart, architecture, API reference.
- Add `robots.txt`, `sitemap.xml`, Open Graph / Twitter card metadata, favicon.
- **Scope guard:** site documents and routes; it does not host the runtime.

---

### Wave 5 — Domain + DNS setup for GitHub Pages (depends on Wave 4)

- Configure custom domain `graphclaw.ai` in Pages site settings (creates `CNAME` file).
- Add DNS records at registrar:
  - `A` records → GitHub Pages IPs for apex.
  - `AAAA` records → GitHub Pages IPv6.
  - `CNAME` for `www` → `<org>.github.io`.
- Enforce HTTPS.
- Decide and enforce apex vs www canonical (recommend apex; redirect `www` → apex).
- Verify `https://graphclaw.ai` and `https://graphclaw.ai/docs` serve correctly with valid TLS.

---

### Wave 6 — Pre-1.0 release dry run (depends on Waves 0–2)

Exercise the full release workflow under the final org slug before public launch.

- Cut `v0.1.0-rc.1` tag on each repo via the release-please PR flow.
- Verify:
  - PyPI publishes `graphclaw==0.1.0rc1` via trusted publisher (no manual token).
  - `pip install graphclaw==0.1.0rc1` works from a clean venv.
  - GHCR images pull: `docker pull ghcr.io/<org>/graphclaw:0.1.0-rc.1` and `:graphclaw-cockpit:0.1.0-rc.1`.
  - `cosign verify` succeeds against published images.
  - SBOM downloadable from release assets; CycloneDX valid.
  - SLSA provenance attached.
  - Auto-generated CHANGELOG is sensible.
  - Compose quickstart in README runs end-to-end against `:0.1.0-rc.1` tags.
- Fix any pipeline issues; re-tag `-rc.2` if needed.

---

### Wave 7 — Coordinated `v0.1.0` launch (depends on Waves 3–6)

- Run final validation: link-check on all READMEs + landing page; verify all migration redirects; verify docs nav from `graphclaw.ai`.
- Merge release-please PRs on both repos → tags pushed → release workflows publish PyPI + GHCR + GitHub Releases.
- Verify from a clean machine:
  - `pip install graphclaw` resolves to `0.1.0`.
  - `docker pull ghcr.io/<org>/graphclaw:0.1.0` works.
  - `docker pull ghcr.io/<org>/graphclaw-cockpit:0.1.0` works.
  - Compose quickstart pinned to `v0.1.0` brings up a working local stack.
- Publish launch announcement (blog post on `graphclaw.ai`, social posts) with:
  - Self-host quickstart pinned to `v0.1.0`.
  - Clear support boundaries (Discussions / Issues / Security).
  - Contribution onramp (CONTRIBUTING, good-first-issue label).

---

### Wave 8 — Post-launch hardening (parallelizable)

- Move `/docs` to MkDocs Material for versioned search + better navigation.
- Add docs CI: link-check on changed files in both repos.
- Establish public roadmap via org-level GitHub Projects (Now / Next / Later).
- Document deprecation policy formally.
- Evaluate Helm chart in a separate `<org>/charts` repo based on adoption signal.
- Evaluate Homebrew tap for the CLI based on adoption signal.
- Revisit cockpit npm publishing (`@<org>/graphclaw-cockpit`) for embedders.
- Set up `actions/dependency-review` for supply chain.
- Set up Renovate or Dependabot for automated dependency updates.

---

## Release Engineering Reference

### Versioning

- SemVer `MAJOR.MINOR.PATCH`. Pre-1.0 means breaking changes are allowed in MINOR.
- Pre-releases: `-alpha.N`, `-beta.N`, `-rc.N`.
- Repos version independently; a compatibility matrix on `graphclaw.ai` shows which cockpit versions work with which backend versions.

### Cadence

- **Patch:** as needed (bugfix / security).
- **Minor:** approximately every 4 weeks once signal stabilizes — a release train, so contributors can plan.
- **Major:** deliberate, with migration guide and `breaking` changelog section.

### Support window

- Latest minor + previous minor receive patches.
- Older minors get security fixes only if trivially backportable.

### Release artifacts (per repo, per tag)

**Backend `graphclaw`:**
- Git tag `vX.Y.Z` + GitHub Release with auto-generated notes.
- Python wheel + sdist on PyPI as `graphclaw`.
- Multi-arch Docker images on GHCR: `ghcr.io/<org>/graphclaw:X.Y.Z` and `:latest`.
- CycloneDX SBOM attached to the release.
- Cosign signature (keyless via GitHub OIDC).
- SLSA provenance attestation.

**Cockpit `graphclaw-cockpit`:**
- Git tag + GitHub Release.
- Multi-arch Docker image on GHCR: `ghcr.io/<org>/graphclaw-cockpit:X.Y.Z`.
- Prebuilt static `dist/` tarball attached for users serving via their own web server.
- SBOM + cosign signature.

### Security releases

- Triaged out-of-band via private GitHub Security Advisory.
- Fix prepared in a private fork.
- Coordinated CVE disclosure via the Advisory.
- Patch released on all supported lines simultaneously.
- `SECURITY.md` publishes timeline after disclosure.

### Deprecation policy

One full MINOR window of warnings before removal. Deprecations documented in `docs/explanation/deprecations.md` with replacement guidance.

---

## Contribution Workflow Reference

### Intake

- Issue first for non-trivial work.
- PR-only welcome for typo / docs / small fix.
- Templates: bug, feature, question (→ Discussions), security (→ Advisory).

### Author requirements (enforced by CI)

- **DCO sign-off** on every commit (`git commit -s`).
- **Conventional Commits** PR title — release-please depends on this.
- PR template filled in: what / why, linked issue, test plan, screenshots for UI, breaking-change callout.

### Required CI gates (branch protection on `main`)

**Backend:**
- `ruff check` + `ruff format --check`
- `pytest` (unit + integration)
- `bandit` (security)
- Header / inventory checks
- Link-check on changed docs

**Cockpit:**
- `eslint`
- `tsc --noEmit`
- `vitest` with coverage threshold
- Contract test
- Header / inventory checks
- Playwright E2E on labeled or push
- Link-check on changed docs

**Both:**
- DCO check
- Conventional Commit title check
- CODEOWNERS review (1 approval; 2 for `breaking`)
- gitleaks (no secrets)

### Review process

- CODEOWNERS auto-assigns reviewers per path.
- First-time contributor: welcome bot comment; maintainer responds within 3 business days (publicly committed SLA).
- Squash merge only; PR title becomes commit message; auto-delete branch on merge.

### Trust escalation

| Role | How earned | Powers |
| --- | --- | --- |
| Triager | 3 issues triaged + invite | Apply labels, close obvious dupes, request info |
| Reviewer | 5 merged PRs + invite | Review and approve PRs in their area |
| Maintainer | Sustained reviewer activity + supermajority maintainer vote | Merge, manage releases, edit repo settings |
| Admin | Founding maintainers + appointed | Org settings, billing, branch protection |

Documented in `GOVERNANCE.md`.

### External contributor protections

- Fork PRs do not have access to repo secrets (GitHub default).
- Manual `workflow_dispatch` approval for any secret-using job.
- `deploy.yml` gated to maintainers only via `production` Environment with required reviewers.

### Stale management

- `actions/stale`: 60-day inactivity warning, 90-day auto-close with re-open instructions.
- Triage rotation among maintainers (documented in GOVERNANCE).

### Public roadmap

Org-level GitHub Projects with columns Now / Next / Later, linked from both READMEs and `graphclaw.ai`.

---

## Relevant Files

### Backend `graphclaw`
- `src/graphclaw/gateway/app.py` — flip `license_info` to Apache-2.0 (Wave 0.B).
- `pyproject.toml` — update Homepage/Repository post-migration; PyPI classifiers (Wave 0.B, 2).
- `README.md` — PII scrub now (0.C); rewrite as OSS landing in Wave 3.
- `docs/README.md` — docs hub entrypoint for `graphclaw.ai/docs` (Wave 3).
- `docs/redirects.md` — track path moves.
- `docs/release-notes/CHANGELOG.md` — managed by release-please post-launch; fix compare URLs in Wave 2.
- `.github/workflows/ci.yml` — add DCO, conventional-title, gitleaks, link-check (Wave 0.E).
- `.github/workflows/release.yml` — **NEW** (Wave 0.A): tag-triggered build → PyPI + GHCR + cosign + SBOM + GitHub Release.
- `.github/workflows/release-please.yml` — **NEW** (Wave 0.A): maintain release PRs from Conventional Commits.
- `.github/workflows/deploy.yml` — gate to `workflow_dispatch` + `production` environment (Wave 0.A).
- `.github/workflows/build-push.yml` — re-target to GHCR for public path; ECR gated to dispatch (Wave 0.A).
- `.github/CODEOWNERS`, `.github/FUNDING.yml`, `.github/ISSUE_TEMPLATE/*.yml`, `.github/pull_request_template.md` — **NEW** (Wave 0.D).
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SUPPORT.md`, `GOVERNANCE.md` — **NEW** (Wave 0.D).

### Cockpit `graphclaw-cockpit`
- `package.json` — add homepage/repository/bugs/license; keep `private: true` at v0.1.0 (Wave 0.B).
- `README.md` — update backend org links and self-host references (Wave 2, 3).
- `src/test/openapi.json` — regenerate after backend license fix (Wave 0.B / 2).
- `docs/README.md` — docs hub entrypoint (Wave 3).
- `docs/redirects.md` — path migration map.
- `.github/workflows/ci.yml` — add DCO + conventional-title + gitleaks + link-check (Wave 0.E).
- `.github/workflows/release.yml` — **NEW** (Wave 0.A): tag-triggered GHCR multi-arch + cosign + SBOM + dist tarball.
- `.github/workflows/release-please.yml` — **NEW** (Wave 0.A).
- `.github/CODEOWNERS`, `.github/FUNDING.yml`, `.github/ISSUE_TEMPLATE/*.yml`, `.github/pull_request_template.md` — **NEW** (Wave 0.D).
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SUPPORT.md`, `GOVERNANCE.md` — **NEW** (Wave 0.D).

### New site repo `<org>/graphclaw.ai`
- `index.html` or `index.md` — landing page (Wave 4).
- `docs/index.md` — curated docs index (Wave 4).
- `CNAME` — custom domain (Wave 5).
- `robots.txt`, `sitemap.xml` — discoverability (Wave 4).

---

## Verification Checklist

### Wave 0
- [ ] Community profile 100% on both repos.
- [ ] `/openapi.json` returns `Apache-2.0`; no "Proprietary" anywhere.
- [ ] No PII or dev creds in any README.
- [ ] DCO + conventional-title + gitleaks fail a bad test PR, pass a clean one.
- [ ] `v0.0.1-rc.0` dry-run release succeeds end-to-end on both repos.

### Wave 1–2
- [ ] Old URLs redirect to new org URLs.
- [ ] `pyproject.toml` + `package.json` repository fields resolve.
- [ ] Branch protection enforced.

### Wave 4–5
- [ ] `graphclaw.ai` serves with valid TLS.
- [ ] `/docs` route loads and links to both docs hubs.

### Wave 6
- [ ] `pip install graphclaw==0.1.0rc1` works from clean venv.
- [ ] `docker pull ghcr.io/<org>/graphclaw:0.1.0-rc.1` works.
- [ ] `docker pull ghcr.io/<org>/graphclaw-cockpit:0.1.0-rc.1` works.
- [ ] `cosign verify` succeeds; SBOM downloadable.
- [ ] Compose quickstart against `:0.1.0-rc.1` works.

### Wave 7
- [ ] `v0.1.0` tags published on both repos with release notes.
- [ ] Announcement links resolve.
- [ ] Quickstart pinned to `v0.1.0` reproduces on a clean machine.

### Wave 8 (ongoing)
- [ ] Docs link-check in CI.
- [ ] Public roadmap visible.
- [ ] Deprecation policy documented.

---

## Further Considerations

1. **Cockpit npm publishing** — keep `"private": true` at v0.1.0 (Docker-only distribution); revisit after adoption signal.
2. **Changelog tooling** — release-please for both repos (uniform). Changesets considered for cockpit but rejected to avoid tooling sprawl.
3. **Docs stack** — static curated links at launch; MkDocs Material upgrade in Wave 8.
4. **CLA vs DCO** — DCO at launch. Adding a CLA later is straightforward; removing one is not.
5. **Lockstep vs independent versioning** — independent versions with a compatibility matrix; lockstep adds release friction without proportional benefit for a 2-repo project.
