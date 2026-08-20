# Monorepo legibility plan

Owner file for a no-break cleanup of the public `joemccann/radon` tree.
Do not execute Phase 2 or 3 without the grep gates in each section.
Visual: [`show-me-monorepo-legibility.html`](archive/show-me/show-me-monorepo-legibility.html).

Audited: 2026-08-20 against `main` working tree. Runtime code paths were
not moved. Dirty WIP in `scripts/`, `web/`, `cloud/` is out of scope.

## Verdict

Do not reshape `web/`, `scripts/`, `cloud/`, `site/`, `docker/ib-gateway/`,
`tests/`, or `lib/tools/` into an `apps/` / `packages/` monorepo. That is
the fastest way to take production down. Fifty systemd units, sixteen
launchd plists, `pyproject.toml` pythonpath, `.github/workflows/ci.yml`
collection roots, and `deploy.sh` all hardcode those paths. Push-to-`main`
auto-deploys.

The illegibility is not "Python files in the wrong package." It is:

1. Root archaeology (session notes next to live weekend-audit contracts).
2. Stale nested READMEs that still teach `npm`.
3. A root README that is an env-var encyclopedia instead of GitHub's
   five-question front door.
4. Missing GitHub security files on a **public** proprietary repo.
5. Tracked runtime caches that `.gitignore` already wants ignored.

**Confidence: high** on the layout diagnosis (`git ls-files` counts, CI
paths, `TestThinIndex`, weekend-skill contracts). **Moderate** on whether
`deploy/beta/` is still used and whether `web/package-lock.json` is a
zombie. **Low** on whether `docker/caddy/` has any remaining laptop caller
beyond `phase0_capture.sh`.

## What GitHub actually requires

Source: [About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
and [community health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file).

A README must answer, in order:

1. What the project does.
2. Why it is useful.
3. How to get started.
4. Where to get help.
5. Who maintains it.

GitHub auto-generates a heading outline. A hand-written Contents section
is optional. Relative links, not absolute GitHub URLs. README locations
are `.github/` then repo root then `docs/`. **Never add `.github/README.md`.**
It would replace the root README on the repo home page.

Community files GitHub looks for: `LICENSE` (have), `CONTRIBUTING.md`
(have), `SECURITY.md` (missing), issue/PR templates (missing),
`CODE_OF_CONDUCT.md` (skip: single-operator proprietary), `FUNDING.yml`
(skip), `SUPPORT.md` (add: this is not a third-party product).

Live GitHub state on 2026-08-20:

- Public, homepage `https://radon.run`, license SPDX `NOASSERTION`.
- Topics: `dark-pool`, `interactive-brokers`, `options`, `trading`.
- Community health **57%**. Wiki **enabled**. Secret scanning **disabled**.
  Dependabot security updates **disabled**.
- Description repeats the same sentence twice.

## Inventory (tracked)

| Tree | Tracked files | Role |
|---|---|---|
| `web/` | 1499 | Next.js terminal. Production. |
| `scripts/` | 907 | Python + JS daemons. 163 top-level files, 114 of them `.py`. |
| `cloud/` | 160 | systemd, Caddy, deploy, IB Gateway compose for the VPS. |
| `site/` | 142 | Marketing site. Separate Vercel project. Still npm. |
| `docs/` | 100 | Specs plus 9 HTML dumps (6 of them `show-me-*`). |
| `tasks/` | 43 | Session plans, completed checklists, HTML reports. |
| `.pi/` | 40 | Pi commands, prompts, extensions. Load-bearing. |
| `lib/` | 29 | Pi tools. Vitest + CI include them. |
| `context/` | 18 | Agent memory. March 2026 facts. Constructor still reads it. |
| `tests/` | 17 | TWR money-math. CI collects this directory by name. |
| `config/` | 16 | Laptop launchd plists. |
| Root `*.md` | 12 | Mix of live contracts and dead session notes. |

Do not use "710 Python files at `scripts/` top level." Nested `scripts/**/*.py`
is hundreds of files. Top-level `scripts/*.py` is **114**.

## Do not touch

These look messy and are load-bearing.

| Path | Why |
|---|---|
| `web/`, `scripts/`, `cloud/`, `site/` as roots | CI, deploy, systemd, imports. |
| `docker/ib-gateway/` | Laptop IB Gateway. `scripts/docker_ib_gateway.sh` and `scripts/api/ib_gateway.py` default here. Distinct from `cloud/docker-compose.yml`. |
| `tests/` at repo root | `ci.yml` pytest invocation names it. `pyproject.toml` comments T-051. |
| `lib/tools/` | Root `vitest.config.ts` include + coverage. |
| `config/*.plist` | Laptop schedulers. |
| `CLAUDE.md` / `AGENTS.md` dual tree | Intentional. `DEVELOPMENT.md` says both agents have their own discovery convention. |
| `RELIABILITY_AUDIT.md`, `RELIABILITY_LOG.md` | Weekend skill contract. Finding IDs `R-###` / `REL-###`. `git log` on these filenames is the ledger fallback. |
| `TEST_AUDIT.md`, `TEST_LOG.md`, `REMEDIATION_LOG.md` | Testing-weekend skill contract. `T-###` IDs. |
| `## Now true` in README | `scripts/tests/test_docs_contract.py` `TestThinIndex.test_readme_has_now_true_not_recent_additions` requires the heading, 1-5 bullets, and three specific doc links. Deleting it without rewriting the test fails CI and blocks auto-deploy. |
| `docs/owners.json` + `test_docs_contract.py` | Thin-index contract. Mapped path changes need an owner doc in the same commit. |
| `data/strategies.json`, `data/margin_debt_legacy.csv`, `data/spx_monthly_legacy.csv`, `data/backtest/cri.json` | Seed data, not caches. |

## Target shape (root)

```
radon/
├─ README.md                 # GitHub front door (slim, current)
├─ LICENSE                   # proprietary, keep
├─ SECURITY.md               # add
├─ SUPPORT.md                # add
├─ CONTRIBUTING.md           # keep, operator-only
├─ DEVELOPMENT.md            # toolchain map, keep
├─ CLAUDE.md / AGENTS.md     # agent runbooks, keep dual
├─ VERSION
├─ RELIABILITY_AUDIT.md      # stay at root (weekend contract)
├─ RELIABILITY_LOG.md
├─ TEST_AUDIT.md
├─ TEST_LOG.md
├─ REMEDIATION_LOG.md
├─ web/  site/  scripts/  cloud/  docker/ib-gateway/
├─ lib/tools/  tests/  config/  brand/  docs/
├─ .github/CODEOWNERS
├─ .github/ISSUE_TEMPLATE/
└─ docs/
    ├─ README.md             # documentation index (new)
    ├─ archive/              # completed plans, show-me dumps, PROGRESS.md
    └─ monorepo-legibility-plan.md  # this file
```

No `apps/`. No `packages/`. No merging `docker/ib-gateway` into `cloud/`.

## README rewrite

Current README is 17,393 bytes / ~255 lines. GitHub truncates at 500 KiB.
Length is not the defect. Content type is. The External services tables
and the 20-row What's where index belong in `docs/`, not on the repo
home page. `CONTRIBUTING.md` already forbids a changelog in README.
`Now true` is the allowed durable-facts slot (max 5 bullets).

Keep:

- Hero, CI badge, version from `VERSION` (0.7.0), proprietary license badge.
- One-paragraph what / why.
- Three gates table.
- Quick start (Python 3.13, bun, two `.env.example` files, `cloud.sh` / `local.sh`).
- Architecture ASCII + port list.
- Project layout (complete: add `docker/`, `lib/tools/`, `tests/`, `site/`).
- `## Now true` with 1-5 bullets (test-pinned).
- Tests commands (bun, not npm).
- Short data-source priority (4 lines).
- Short deploy paragraph that points at `cloud/CLAUDE.md`.

Move out:

- External services encyclopedia -> `docs/operations.md` (already exists)
  or a new `docs/external-services.md` owned by operations.
- What's where table -> `docs/README.md`.
- Glossary -> `docs/README.md` or a stub `docs/glossary.md`.

Add (GitHub's missing questions):

- Maintainers: Joe McCann, single operator.
- Help: not a supported product; security reports via `SECURITY.md`;
  operator issues use the factory template.

Nested README fixes, same wave:

| File | Defect | Fix |
|---|---|---|
| `web/README.md` | `npm install` / `npm run dev`. Root README and CI use bun. | bun. Point at `scripts/cloud.sh`. |
| `site/README.md` | `npm install` is probably still true (tracked `site/package-lock.json`, no `site/bun.lock`). | Keep npm **if** still true. Do not pretend bun. |
| `cloud/README.md` | Speaks as a standalone repo. Mentions Vercel Phase 2 and CPX11. `PLAN.md` is already marked archived. | Speak as `cloud/` in this monorepo. Drop stale hardware/Vercel claims. |

## Community files and GitHub settings

Add:

- `SECURITY.md` at repo root. Private disclosure only. No public issues
  for vulns on a live broker stack. GitHub then shows "Report a
  vulnerability" on the Security tab.
  ([GitHub docs](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository))
- `SUPPORT.md`. This software is proprietary and unsupported for clones.
- `.github/CODEOWNERS` with `* @joemccann`.
- `.github/ISSUE_TEMPLATE/` with a `factory` template (label `factory`)
  and a blank `config.yml` that disables empty issues if desired.
- Optional `.github/pull_request_template.md`: TDD, no `git add -A`,
  wait for in-flight deploy.

Do not add:

- `CODE_OF_CONDUCT.md`. Health-percentage theater for a one-person repo.
- `FUNDING.yml`.
- `CITATION.cff`.
- A wiki. Disable the existing wiki so `docs/` stays the only corpus.

GitHub settings (operator, not a file):

```bash
gh repo edit joemccann/radon --enable-wiki=false
```

Enable secret scanning and push protection on the public repo. They are
currently **disabled**. That is a worse gap than a missing CODEOWNERS.

Tighten the About description to one sentence. Drop the repeated
"Detects hidden positioning..." clause.

## Phase 0 — docs and GitHub only (`depends_on: []`)

No path moves. No lockfile deletes. Cannot break runtime.

- [x] L0a `SECURITY.md`, `SUPPORT.md`, `.github/CODEOWNERS`, factory issue template
- [x] L0b `docs/README.md` as the documentation index (absorb What's where)
- [x] L0c Slim root README; keep `## Now true` with 1-5 bullets; TestThinIndex extended
- [x] L0d Fix `web/README.md` (bun) and `cloud/README.md` (monorepo voice)
- [x] L0e Confirmed `site/` uses npm (`site/package-lock.json`, no `site/bun.lock`); documented
- [x] L0f `.gitignore`: `CLAUDE-SECURITY-*/`
- [x] L0g Untracked `data/menthorq_cache/cta_2026-03-06.json` and seasonality JSON
- [x] L0h Untracked `data/flex_token_config.json`; committed sanitized example; tests fall back
- [x] L0i Wiki disabled. Secret scanning + push protection enabled. About description de-duplicated.
- [x] L0j Expand `docs/owners.json` (`repo-front-door`)

Gate: `python3.13 -m pytest scripts/tests/test_docs_contract.py -q` green.
`python3.13 -m pytest tests/test_no_public_account_assets.py tests/test_no_tracked_account_figures.py -q` green.

## Phase 1 — archive dead trees (`depends_on: [L0]`)

Move, do not delete. Git history keeps the blobs.

Gate before each move: `git grep -n <filename>` across `*.md *.py *.yml *.sh
*.json *.ts` plus `.claude/skills` and `.pi/`. Zero runtime hits, or leave
a stub that points at the new path.

- [x] L1a `docs/archive/{plans,show-me,sessions}`
- [x] L1b `plans/` -> `docs/archive/plans/animation-2026-07-15/`
- [x] L1c `PROGRESS.md` -> `docs/archive/sessions/`
- [x] L1d `cloud/PLAN.md` -> `docs/archive/sessions/cloud-phase1-plan.md`
- [x] L1e Tracked `docs/show-me-*.html` plus demo-setup / stop-orders HTML -> `docs/archive/show-me/`
- [x] L1f `specs/gex-levels.spec.md` -> `docs/archive/` (not a regime-tab spec)
- [x] L1g `scripts/README.md` map. No Python moves.
- [x] L1h `tasks/archive/README.md` rotation policy. Did not rewrite dirty `tasks/todo.md` WIP.
- [x] L1i Untracked `context/memory/fact/*.json` except `portfolio-source-of-truth.json`. Files remain on disk.

Do **not** archive `RELIABILITY_*` or `TEST_*` or `REMEDIATION_LOG.md`.

## Phase 2 — lockfiles (`depends_on: [L1]`)

Investigate, then maybe delete. Do not guess.

- [x] L2a Beta sunset. `deploy/beta/` and `cloud/config/sudoers.d/radon-beta` removed. Operator confirmed the stack was never finished.
- [x] L2b Root `package-lock.json` removed. CI uses `bun.lock`.
- [x] L2c `web/package-lock.json` removed. CI uses `web/bun.lock`.
- [x] L2d `site/` keeps npm. Documented in `site/README.md` and `DEVELOPMENT.md`.

Gate: CI `web-tests` job still `bun install --frozen-lockfile` at root and
in `web/`. Site Vercel build unchanged.

## Phase 3 — optional later, shims required (`depends_on: [L2]`)

Only if top-level `scripts/*.py` is still unreadable after L1g.

- [x] L3a Skipped file grouping. `scripts/README.md` states new scripts stay top-level until a shim plan exists.
- [x] L3b No shims. No `ExecStart` path changes.
- [x] L3c Left `docker/caddy/` in place. `scripts/phase0_capture.sh` still dumps that path. Not proven unused.

## Verification (every phase)

From repo root, after the phase commit is staged (never `git add -A`):

```bash
python3.13 -m pytest scripts/tests/test_docs_contract.py tests/test_no_public_account_assets.py tests/test_no_tracked_account_figures.py -q
python3.13 scripts/run_pytest_affected.py --files README.md docs/monorepo-legibility-plan.md -- -q
bunx vitest run --config vitest.config.ts --exclude '**/web/tests/integration.test.ts' --exclude '**/lib/tools/__tests__/kelly.test.ts' --exclude '**/lib/tools/__tests__/runner.test.ts'
```

Full suites before the push that lands on `main`. Wait for the previous
deploy to finish.

## Non-goals

- Turning this into a Turborepo / pnpm-workspace / Nx repo.
- Merging `CLAUDE.md` and `AGENTS.md`.
- Deleting `video/` or `marketing-mockups/` from disk (already gitignored).
- Rewriting `docs/prompt.md` / `docs/implement.md` / `docs/plans.md` in
  this wave (stale evaluation-era docs; separate docs rewrite, already
  sketched in `docs/archive` once L1e runs). Gate 4 text in `docs/prompt.md`
  is wrong as of 2026-04-30; fix that as its own commit, not a drive-by.
- Raising GitHub community-health percentage as a goal.
- History rewrite (`git filter-repo`) for the untracked-then-still-in-history
  account figures. Treat past blobs as disclosed. Do not force-push `main`.
