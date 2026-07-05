# Task: Margin Debt Acceleration Indicator (FINRA)

Decisions (2026-07-03): splice legacy NYSE history back to ~1959 · lives as a new
`/regime/*` tab · net (debit − free credit) and CPI-deflated / %-of-GDP views as
toggles · data-source verdict pending provider research (FRED vs FINRA XLSX;
Exa available for retrieval).

## Phase 0 — Source verdict ✅ (research 2026-07-03, verified by direct fetch)
- [x] Live primary: FINRA XLSX `https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx`
      (200, Last-Modified Jun 16 2026, Jan 1997 → May 2026, $mm; updated in place ~3rd week; next ~Jul 28).
      Fallback 1: FINRA HTML stats page; fallback 2: Alphacast dataset 8207 (free API, ~1mo lag).
      FRED does NOT carry it (only quarterly Z.1 `BOGZ1FL663067003Q` broker-dealer aggregate — cross-check only).
      Quandl/NYXDATA bot-walled + frozen Nov 2017 — do not use.
- [x] Backfill 1959–1996: Wayback-archived NYSE Facts & Figures decade tables
      (`web.archive.org/.../viewer_edition.asp?...category=8`, keys 2313/2316/1374/605/595/278/3153);
      monthly margin debt + free credit columns confirmed present. Parse HTML tables → one-time CSV.
      Methodology break at Jan 1997: NYSE members-only = 96.2% of FINRA all-members ($99,460M vs $103,337M),
      drifting to 92.6% by Nov 2017. Decide: raw concat w/ flagged break vs ratio-adjust pre-1997 ×1.039.
      Archived HTML + XLSX working copies already in session scratchpad.
- [x] XLSX parse needs NO new deps — stdlib zipfile+ElementTree suffices (verified). Drop openpyxl plan.
- [x] CPI + GDP series for normalization toggles: FRED API, CPIAUCSL monthly + GDP quarterly;
      `FRED_API_KEY` added to root `.env` (2026-07-03)
- [x] Splice treatment decided: ratio-adjust pre-1997 ×1.039 for chart display; DB stores RAW values
      + `source` column (`nyse_legacy`/`finra`) so both presentations are recoverable; null the 12
      seam-straddling YoY months

## Phase 1 — Ingestion ✅
- [x] `scripts/clients/finra_client.py` (UWClient shape; conditional GET; typed errors; exported in
      `clients/__init__.py`). ⚠️ UA must stay NON-browser: FINRA Cloudflare 403-challenges browser UAs
      but serves plain clients (verified live 2026-07-03)
- [x] XLSX parse via stdlib zipfile+ElementTree (no new deps; cells mapped by r= column ref)
- [x] `scripts/fetch_margin_debt.py`: stdout = JSON only, progress → stderr; YoY + display splice +
      net + CPI-real + %GDP computed at ingestion; FRED views best-effort w/ availability flag
- [x] Legacy backfill DONE: `scripts/backfill_margin_debt_legacy.py` parsed 5 Wayback decade pages →
      `data/margin_debt_legacy.csv` (492 months, 1959-01..1999-12). Jan-1997 NYSE = 99,460 verified,
      anchoring `NYSE_TO_FINRA_SPLICE_RATIO` against parsed data
- [x] Red/green pytest: 22 tests green (parse, YoY calendar-lookback + gap + seam, splice ratio,
      net, deflate, %GDP, migration, idempotent upsert)

## Phase 2 — Storage ✅
- [x] Migration `0027_margin_debt_history.sql` applied to Turso
- [x] `writer.upsert_margin_debt_rows(...)` + `MARGIN_DEBT_UPSERT_SQL` (ON CONFLICT(date) DO UPDATE);
      dual-write `data/margin_debt.json`
- [x] Verified against Turso: 809 rows, 1959-01..2026-05; 2026-05 level 1,415,557 / YoY +53.7%;
      seam rows null; sources tagged. (Note: full-history upsert ≈ 90s over Turso HTTP — fine for a
      monthly timer, revisit only if it ever runs hot)

## Phase 3 — API ✅ (design simplified: no FastAPI route needed)
- [x] Timer runs `fetch_margin_debt.py` directly (llm-index precedent); full payload mirrored to
      `scan_snapshots` (service `margin-debt`) so reads are Turso-first — no FastAPI hop
- [x] `web/app/api/margin-debt/route.ts`: GET-only `dbFirstRead` (Turso snapshot → `data/margin_debt.json`),
      48h max age, 200 + `{missing:true}` contract; 5 vitest green
- [x] S&P 500 overlay attached at ingestion: Shiller composite 1959-1984 (committed
      `data/spx_monthly_legacy.csv` — Yahoo now caps ^GSPC at ~1985) + Yahoo monthly 1985+; 809/809 coverage

## Phase 4 — Frontend (regime tab) ✅
- [x] `CriHistoryChart.tsx`: per-series `scaleType: "log" | "linear"` (positive-domain guard, geometric
      log ticks) + `xTickFormat` prop; existing call sites byte-identical
- [x] `MarginDebtPanel.tsx`: RegimeStrip header (YoY froth-colored: warn ≥ +50%), range chips
      1Y/5Y/10Y/All, view chips YOY%/LEVEL/NET/%GDP/REAL (CPI+GDP gated on normalization.available),
      3MO MA toggle, BrushMinimap, splice footnote, SpectralLoader/SectionEmptyState
- [x] `lib/marginDebt.ts` + `lib/useMarginDebt.ts`; tab wired in RegimePanel (`margin`) +
      `app/regime/margin/page.tsx`
- [x] Tokens only, no raw hex, existing chip classes, no em dashes
- [x] Tests: 34 vitest (margin-debt-panel) + regime-tab-routes additions; full web suite 3840 green;
      tsc clean. E2E: `web/e2e/margin-debt-tab.spec.ts` 5/5 green (Playwright — chrome-cdp needs
      Tailscale/cloud.sh which was down); live-browser screenshot vs real 809-month payload verified
      (log SPX axis + YoY line + froth-amber +53.7%)

## Phase 5 — Scheduling + deploy ✅
- [x] `scripts/run_margin_debt_refresh.sh` (no holiday gate) + `config/com.radon.margin-debt-refresh.plist`
      (daily 07:10 local)
- [x] Hetzner `radon-margin-debt.{service,timer}` installed + enabled (oneshot venv python, daily
      13:10 UTC, Persistent=true, RANDOMIZED 300s, `RADON_DB_NO_REPLICA=1`); first manual run OK (44s);
      `FRED_API_KEY` added to VPS `/home/radon/radon-cloud/.env`
- [x] service_health `margin-debt` registered in `scripts/watchdog/services.py` (daily bucket, 26h) +
      `web/lib/serviceHealthWindows.ts` (26h uniform); watchdog 141 + windows 96 tests green
- [x] Post-deploy: stray `margin_debt` row deleted; VPS re-run under deployed code wrote
      `margin-debt` ok @ 2026-07-04T06:08Z (304 fast path, snapshot refreshed)

## Notes
- SPX monthly history back to 1959: Yahoo ^GSPC monthly (1950+) via existing fallback path;
  IB/UW have no multi-decade monthly series (documented exception to the source-priority chain)

## Review

Shipped 2026-07-03/04 in two commits (b521924a backend, fd159cbf frontend+scheduling), both
deployed green. /regime/margin renders the Topdown-style overlay: S&P 500 log left axis
1959-2026 vs margin-debt YoY% right axis; current reading May 2026 $1,415.6bn, YoY +53.7%
(froth-amber). Data: FINRA XLSX (plain UA — Cloudflare 403s browser UAs) + Wayback NYSE
legacy splice (raw in DB, ×1.0390 display) + Shiller/Yahoo SPX + FRED CPI/GDP toggles.
Daily VPS timer with 304 fast path; margin-debt service_health registered both registries.
Tests: 25 pytest + 34 panel vitest + 5 Playwright E2E; full suites 3478 pytest /
3840 vitest / 0 fail; live screenshot verified against the real 809-month payload.

Deferred / follow-ups:
- demo.radon.run has no margin-debt snapshot mirror (tab shows empty state there)
- operator-session browser check on app.radon.run (localhost + mocked E2E done; prod is
  Clerk-gated so needs the operator's browser)
- optional chart niceties skipped by design: shaded +50/−20 bands, per-month tooltip parity

## PENDING — Beta-first promotion flow (beta.radon.run → app.radon.run)

Purpose (operator, 2026-07-04): new features deploy to beta first, get tested, then promote to production. Same-commit promotion via the existing gated `Production` GitHub Environment — never a beta branch (commit-to-main convention holds; beta and prod diverge only in time, not code).

### Checklist
- [ ] Prereqs from the beta plan (block the CI work until done):
  - [ ] Build memory safety on the 2-vCPU/0-swap VPS: swap + `MemoryMax`/`CPUQuota` on build, or build off-box — a second concurrent `next build` for beta can OOM-kill prod
  - [ ] DNS: repoint `beta.radon.run` A → <prod-host> (currently Vercel anycast) + detach from Vercel project; add `beta.radon.run {}` Caddy block → :3001
  - [ ] sudoers/polkit coverage for `radon-beta-*` units (deploy + admin panel restarts)
  - [ ] Seed/refresh the separate beta Turso DB (dump→restore from prod; never the prod DB URL)
  - [ ] Commit `radon-beta-*` unit files to radon-cloud once shapes settle (drop the drift-audit `known-untracked` allowlist entries)
- [ ] CI: add `deploy-beta` job to `.github/workflows/ci.yml` — after the test gate, BEFORE the gated `Deploy to VPS`; SSHes to VPS and runs a parameterized `deploy-beta.sh` (RADON_DIR=/home/radon/radon-beta, restarts radon-beta-* only, own post-deploy gate hitting :8331/:3001)
- [ ] Promotion = approve the `Production` gate on the run already validated on beta (UI or `gh api -X POST .../pending_deployments`)
- [ ] Post-beta-deploy smoke: beta health daemon (:8331) + a body-judged `/api/service-health` read on :3001 (mirror the nextjs-db-watchdog probe semantics)
- [ ] Docs: update CLAUDE.md deploy paragraph + docs/cloud-services.md "Day-to-day deploys" to describe push → beta → test → approve-gate → prod

### Notes
- Beta posture per locked decisions: no IB auth (`RADON_BETA_NO_IB_AUTH=1`), no relay, no order path, Clerk satellite domain, separate Turso DB — blast radius of an auto-deploy to beta is near zero, which is what justifies beta deploying WITHOUT approval while prod keeps the gate.
- A newer push cancels a run waiting at the Production gate (concurrency group) — under beta-first flow that means "the beta you were testing got superseded"; the deploy-beta job should stamp the deployed SHA somewhere visible on beta (footer/env) so testers know which commit they're validating.
