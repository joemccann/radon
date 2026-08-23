---
name: indicator
description: Parallel TDD swarm that ships a new market indicator end to end. Usage - /indicator <data-source> <indicator-name>. Researches the source, writes a spec plus failing tests, fans out three worktree implementer subagents (ingestion, API, chart tab), merges, runs the full suite, screenshots the live tab with Playwright, then commits, pushes, waits for CI green, and verifies production.
---

# /indicator — parallel TDD swarm for a new market indicator

Arguments: `<data-source>` (URL or short description) and `<indicator-name>`.
Prerequisite: read `.claude/skills/new-indicator/SKILL.md` first — it defines the
target pattern, the lockstep pins, and the CI gates. Every step below produces
verifiable evidence before the next step starts.

Derive up front and state them: `slug` (route segment, short), `service` (kebab-case,
used for `scan_snapshots.service`, `service_health`, and systemd unit names), `Name`
(PascalCase for components), tab label (UPPERCASE, no em dashes), and the next free
migration number (`ls scripts/db/migrations | sort | tail -1`).

## Step 1 — Research subagent (blocking)

Spawn one research subagent (general-purpose; give it WebFetch/curl) to confirm, with
evidence pasted back:

- **Source**: exact URL(s), transport (XML/JSON/CSV/XLSX), auth (must be none or an
  existing repo credential), and a captured sample saved to
  `scripts/tests/fixtures/<name>_sample.<ext>` (this becomes the pytest fixture).
- **Schema**: field names, units, date keying, history depth, how far back a backfill
  can go, and any splice/seam issues between historical regimes.
- **Update cadence**: when the source actually publishes (day of week/month, time,
  timezone) — this dictates the timer OnCalendar, `MAX_AGE_MS`, staleness windows,
  and what freshness copy is honest.
- **Licensing**: US-government/public-domain data is fine; otherwise confirm terms
  permit storage + display. Record verdict in the spec. Stop and ask the user if
  licensing is unclear.
- **Data-source priority**: note why IB/UW do not already serve this series (repo rule:
  IB → UW → Yahoo → scrape).

Reject the indicator here if the source needs browser impersonation, scraping behind
auth, or has hostile terms.

## Step 2 — Spec + failing tests (red)

Write `docs/indicators/<slug>.md`: signal definition and thresholds, source facts from
Step 1, payload shape (`scan_time`, `source_last_modified`, `current`, `series[]`),
migration DDL, API contract (missing object, cache headers, MAX_AGE reasoning), UI spec
(strip cells, chart series/axes, presets, copy strings including tooltip), timer
cadence, and the exact file checklist per the pattern skill.

Then write the failing tests against the spec (implementation files do not exist yet):

- `scripts/tests/test_<name>.py` — parse the captured fixture, transforms, payload
  contract, migration schema pin, conditional-GET/heartbeat behavior, writer arity.
- `web/tests/<name>-api.test.ts` — dbFirstRead behavior + missing contract.
- `web/tests/<name>-panel.test.tsx` — loader/empty/strip/chart/chips + NaN guard.

Run all three suites and **record the red output**. Failing on import/missing-module is
the expected red for greenfield files.

## Step 3 — Three worktrees, parallel implementers

Partition by ownership so merges are near-disjoint:

| Worktree branch | Owns | Its tests |
|---|---|---|
| `ind/<slug>-ingestion` | `scripts/fetch_<name>.py`, client, migration, `scripts/db/writer.py` additions, `scripts/watchdog/services.py`, `cloud/services/radon-<name>.{service,timer}`, `setup-vps.sh` array, `cloud/tests/test_systemd_services.py` | `pytest scripts/tests/test_<name>.py` + `pytest cloud/tests -q` |
| `ind/<slug>-api` | `web/app/api/<name>/route.ts`, `web/lib/<name>.ts`, `web/lib/use<Name>.ts`, `web/lib/serviceHealthWindows.ts` entry + its pin test update | `bunx vitest run --config vitest.config.ts web/tests/<name>-api.test.ts web/tests/service-health-windows.test.ts` |
| `ind/<slug>-ui` | `web/components/<Name>Panel.tsx`, `RegimePanel.tsx` registration (all four places), `web/app/regime/<slug>/page.tsx`, `web/tests/regime-tab-routes.test.tsx` update, `web/e2e/<name>-tab.spec.ts` | `bunx vitest run --config vitest.config.ts web/tests/<name>-panel.test.tsx web/tests/regime-tab-routes.test.tsx` |

Mechanics:

```bash
git worktree add .claude/worktrees/<slug>-ingestion -b ind/<slug>-ingestion HEAD
# same for -api and -ui
```

Copy the spec + that worktree's failing tests into each worktree, then launch three
implementer subagents **in parallel**, one per worktree. Each prompt must include: the
worktree path (work ONLY there), the spec file, the reference implementation to copy
(`fetch_margin_debt.py` / `margin-debt/route.ts` / `MarginDebtPanel.tsx`), its
ownership list (touch nothing else), the exact test command, and the loop contract:
run tests → fix → rerun **until its suite passes**, then commit on its branch (scoped
`git add` of named files only). The UI implementer mocks the hook in unit tests, so it
does not need the API worktree's files to go green; TypeScript integration is checked
after merge.

## Step 4 — Merge + full suite (integration green)

```bash
git worktree add .claude/worktrees/<slug>-merge -b ind/<slug> HEAD
cd .claude/worktrees/<slug>-merge
git merge --no-ff ind/<slug>-ingestion ind/<slug>-api ind/<slug>-ui   # or sequentially
```

Resolve any conflicts (should be none if ownership held), add the spec file, then run
the FULL gates exactly as CI does, from the merge worktree root:

```bash
python -m pytest scripts/tests scripts/api/tests scripts/trade_blotter -q
python -m pytest cloud/tests -q
bun install --frozen-lockfile && (cd web && bun install --frozen-lockfile)
bunx vitest run --config vitest.config.ts
(cd web && npm run typecheck)
```

Fix integration failures here yourself (type mismatches across worktree seams, missed
lockstep pins, coverage ratchet). Do not loosen a pin test to get green.

## Step 5 — Live browser verification (honest freshness)

1. Run the real ingestion once against Turso (laptop writes direct-to-cloud) so the
   tab has production data: `python scripts/fetch_<name>.py --json | head`. Verify the
   rows in Turso (not the JSON file).
2. Start the dev server from the merge worktree (`cd web && npx next dev -p 3100` or
   let Playwright's webServer do it with `PLAYWRIGHT_PORT`). Never kill 3000/8321/8765.
3. Run the Playwright spec, plus a screenshot pass:
   `page.goto("/regime/<slug>")` against the live API (no route mocks) →
   `page.screenshot({ path: "docs/indicators/<slug>-tab.png", fullPage: false })`.
4. Assert on the live page: the chart `<svg>` has stroked paths (real data, not the
   empty state); the header clock/`SOURCE UPDATED` cell shows the actual ingest/source
   timestamps; and **no copy claims a cadence the backend does not meet** — grep the
   new UI strings for `Refresh|Updated|hourly|daily|5m` and check each against the
   real timer OnCalendar. Screenshot both themes if the change touches theme tokens.

## Step 6 — Ship and verify production

1. Bring the verified branch onto main: from the main worktree, `git merge ind/<slug>`
   (or cherry-pick the squashed commits). Stage nothing extra — operator WIP stays
   untouched; `git status` before committing. Commit style:
   `feat(regime): <NAME> indicator - ingestion, API, chart tab` (+ the Claude Code
   trailer). Do not commit while a deploy is in flight.
2. Push once. `gh run watch` (or poll `gh run list --workflow=ci.yml --limit 1`) to
   green — the deploy job runs in the same workflow.
3. Verify production: migration applied (`schema_migrations` has the new version),
   Turso has the snapshot + history rows, prod API returns the payload (expect the
   auth perimeter as anon), and load `https://app.radon.run/regime/<slug>` in a real
   browser session for a final screenshot.
4. Confirm the deploy installed and enabled the timer (`systemctl list-timers radon-<name>.timer`;
   the deploy log prints `install-units: installed=2` when the manifest carried both
   hashes), then trigger one run (`systemctl start radon-<name>.service` as root) and
   check the `service_health` row.
5. Clean up: `git worktree remove` the four worktrees, delete the `ind/*` branches,
   update `tasks/todo.md` review section.

Deliverables to show the user: red test output (Step 2), per-worktree green (Step 3),
full-suite green (Step 4), the tab screenshot with real data (Step 5), CI run URL +
production evidence (Step 6).
