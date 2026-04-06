# RADON — CLAUDE.md

## ⛔ Mandatory Rules

1. **Be concise.** No preamble, no filler.
2. **E2E browser verification for ALL UI work.** Primary: `chrome-cdp`. Fallback: Playwright (`web/playwright.config.ts`). No UI change done until visually confirmed.
3. **Red/green TDD for ALL code.** Failing test → fix → green → refactor. Unit: Vitest, E2E: chrome-cdp/Playwright.
4. **95% test coverage target.** Every change includes corresponding tests.
5. **API keys** in `.env` files (see Credentials below). Fallback: `~/.zshrc`.
6. **Options structure reference:** `docs/options-structures.json` + `docs/options-structures.md` — 58 structures, guard decisions, P&L attribution labels.

## Combo / BAG Order Guardrails

1. **Never map combo `Order.action` from debit vs credit.** IB combo leg actions define the structure. A `SELL` BAG envelope reverses legs. For entry/open combos, keep envelope on `BUY` and preserve per-leg actions.
2. **When order-builder structure changes, clear stale manual net pricing.** Single-leg → combo transitions must invalidate previous manual limit price. Recompute from normalized combo quote.
3. **Required regressions:** unit test for combo action/ratio/net-price; browser test for displayed combo net price and submitted payload.
4. **Trace full path before fixing:** chain builder → `/api/orders/place` → FastAPI bridge → `scripts/ib_place_order.py`. Verify whether bug is UI state, payload semantics, or IB combo behavior.

## Identity

**Radon** — market structure reconstruction system. Surfaces convex opportunities from dark pool/OTC flow, vol surfaces, cross-asset positioning. Detects institutional positioning, constructs convex options structures, sizes with fractional Kelly. **Flow signal or nothing.** Brand spec: `docs/brand-identity.md`

## ⛔ Four Gates — Mandatory, Sequential, No Exceptions

```
GATE 1 — CONVEXITY      : Potential gain ≥ 2× potential loss. Defined-risk only.
GATE 2 — EDGE           : Specific, data-backed dark pool/OTC signal that hasn't moved price yet.
GATE 3 — RISK MGMT      : Fractional Kelly sizing. Hard cap: 2.5% of bankroll per position.
GATE 4 — NO NAKED SHORTS: Never naked short stock, calls, futures, or bonds. Every short call fully covered. Violation = immediate cancel.
```

**Any gate fails → stop. No rationalization.**

## Data Source Priority

1. Interactive Brokers (TWS/Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Yahoo Finance — fallback only
4. Web scrape — last resort

**Never skip to Yahoo/web without trying IB → UW first.**

**Clients:** `scripts/clients/` — `IBClient`, `UWClient`, `MenthorQClient`.

**Credentials:**

| File | Loader | Contains |
|------|--------|----------|
| `.env` (root) | `python-dotenv` | `MENTHORQ_USER`, `MENTHORQ_PASS`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `ALLOWED_USER_IDS` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` |

## Market Hours

```bash
TZ=America/New_York date +"%A %H:%M"   # 9:30–16:00 ET, Mon–Fri
```

**Open**: Fetch fresh. Cache TTL: flow 5min, ratings 15min. **Closed**: Use latest. Flag stale data.

### CRI/Regime Staleness

`/api/regime` triggers `cri_scan.py` during market hours only. Logic: `web/lib/criStaleness.ts`. Tests: `web/tests/regime-cri-staleness.test.ts`.

| Condition | Stale? | Action |
|-----------|--------|--------|
| `data.date !== today (ET)` | YES | Background scan |
| `market_open + mtime > 60s` | YES | Background scan |
| `market_open === false + date = today` | NO | Serve cached EOD |

### VCG (Volatility-Credit Gap) Tab

Tabbed into `/regime` page. Detects divergence between vol complex (VIX/VVIX) and credit markets (HYG).

| Component | File |
|-----------|------|
| Hook | `web/lib/useVcg.ts` |
| Staleness | `web/lib/vcgStaleness.ts` |
| API route | `web/app/api/vcg/route.ts` |
| Panel | `web/components/VcgPanel.tsx` |
| Scanner | `scripts/vcg_scan.py` (20-session history) |
| Share | `scripts/generate_vcg_share.py` |
| FastAPI | `POST /vcg/scan` (60s cooldown), `POST /vcg/share` |
| Cache | `data/vcg.json` |

**VCG-R thresholds:** RO = VIX > 28 + VCG > 2.5 + sign_ok. EDR = VIX > 25 + VCG 2.0–2.5. BOUNCE = VCG < -3.5. VVIX is severity amplifier (Tier 1/2/3), not a gate.

### RegimePanel Market-Closed Rules

When `market_open === false`:
- Use `data.vix`/`data.vvix`/`data.spy` only (never WS `last`)
- `activeCorr` = `data.cor1m` (not rebuilt from sector ETFs)
- `liveCri` / `intradayRvol` = `null` (use `data.cri` / `data.realized_vol`)
- Don't update VIX/VVIX timestamps; COR1M badge = DAILY

Tests: `regime-market-closed-values.test.ts`, `regime-market-closed-eod.spec.ts`, `regime-cor1m.spec.ts`

### RegimePanel Day Change Indicators

During market hours, the regime strip shows day change:

| Metric | Source | Display |
|--------|--------|---------|
| VIX/VVIX/SPY | WS `last` vs WS `close` | `+1.50 (+6.25%) ↑` |
| RVOL | `intradayRvol - data.realized_vol` | `-0.01% intraday ↓` |
| COR1M | strip: WS `last` or `data.cor1m`; change: `data.cor1m_5d_change` | `37.25` + `-0.50 pts 5d chg ↓` |

**Arrow placement**: Always **right** of change text. `display: flex` + `gap: 4px` in `.regime-strip-day-chg`.
**Tests**: `regime-day-change.test.ts` (12), `regime-day-change.spec.ts` (3)

### Regime History Charts

Two D3 charts, 20 sessions. Left: VIX (`#05AD98`) + VVIX (`#8B5CF6`), dual Y. Right: RVOL (`#F5A623`) + COR1M (`#D946A8`), dual Y. Height 440px. Component: `CriHistoryChart.tsx`.

### Options Chain Sticky Header

`OptionsChainTab.tsx` — three required CSS rules:
1. `background: var(--bg-panel-raised)` on `.chain-header` + `.chain-side-label`
2. `position: sticky; top: 0` / `top: 24px`
3. `.chain-grid thead { position: relative; z-index: 10 }`

All three required or overlap bug returns. Tests: `chain-sticky-header.test.ts` (8).

## Exposure Delta Sign Rule

`rawDelta = sign * lp.delta` where `sign = -1` for SHORT. LONG Call → +, SHORT Call → −, LONG Put → −, SHORT Put → +. Impl: `web/lib/exposureBreakdown.ts`. Tests: `exposure-breakdown.test.ts` (3).

## FastAPI Server Architecture

Next.js routes call FastAPI (`localhost:8321`) via `radonFetch()` (`web/lib/radonApi.ts`). No `spawn()`.

### Three-Service Dev Stack (`npm run dev`)

| Service | Port |
|---------|------|
| Next.js | 3000 |
| IB WS relay | 8765 |
| FastAPI | 8321 |

### FastAPI Files (`scripts/api/`)

| File | Purpose |
|------|---------|
| `server.py` | 26 endpoints, CORS, Clerk JWT auth, IB pool, health, auto-restart |
| `auth.py` | Clerk JWT — JWKS validation, single-tenant allowlist, graceful bypass |
| `ws_ticket.py` | Short-lived single-use WS tickets (30s TTL) |
| `ib_pool.py` | Role-based IB pool (sync/orders/data), auto-reconnect |
| `ib_gateway.py` | Health check + auto-restart, CLOSE_WAIT detection |
| `subprocess.py` | Async `run_script()`, `run_module()` — uses `sys.executable` |

### Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| FastAPI + IB up | Normal |
| FastAPI up, IB down | Auto-restart Gateway, retry once, else 503 + cached |
| FastAPI up, IB CLOSE_WAIT | Auto-restart + kill lingering processes |
| FastAPI down | Cached from disk, `is_stale: true` |

### Authentication (Clerk)

All FastAPI routes protected by Clerk JWT middleware. Next.js by Clerk middleware (`web/middleware.ts`). WebSocket via ticket-based flow (`scripts/api/ws_ticket.py`).

Key files: `scripts/api/auth.py` (FastAPI), `web/middleware.ts` (Next.js), `web/lib/wsTicket.ts` (browser), `web/lib/radonApi.ts` (Bearer token attachment).

**Auth-exempt:** `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`.
**Localhost bypass:** Auth skipped for `127.0.0.1`/`::1` (local dev).
**Public share routes:** `/api/regime/share`, `/api/vcg/share`, `/api/internals/share`, `/api/menthorq/cta/share`.
**Tests:** `test_auth.py`, `auth-integration.test.ts`, `ws-ticket-local.test.ts`.

### IB Gateway Auto-Recovery

Startup: check port 4001 + CLOSE_WAIT detection, restart if needed, poll 45s. Runtime: subprocess errors trigger health check FIRST — only restart if port not listening or CLOSE_WAIT. Client ID collisions, VOL errors, transient timeouts do NOT trigger restart. Restart snapshots pre-existing PIDs, only force-kills those that survived SIGTERM. Manual: `POST /ib/restart`. Health: `GET /health`.

## Cancel / Modify Failure Propagation

1. **Cancel/modify MUST use subprocess with original clientId.** Master client (0) can SEE all orders but CANNOT cancel/modify (Error 10147/103). `ib_order_manage.py` detects original clientId and reconnects as that client.
2. **Clear VOL fields before modify.** Reset `volatility`/`volatilityType` to IB sentinels (`1.7976931348623157e+308` / `2147483647`) to avoid Error 321.
3. **Confirm against refreshed open-order snapshot**, not stale `Trade` object.
4. **Treat disappearance after cancel as success.**
5. **Preserve upstream error detail end to end.** Subprocess JSON → FastAPI `detail` → Next.js route. Never collapse to generic 500.
6. **Required regressions:** unit for refreshed confirmation, route for status propagation, browser for toast/error.

## Naked Short Protection (Gate 4)

**Hard rule — no exceptions.**

| Scenario | Action |
|----------|--------|
| SELL stock, no long shares | BLOCK |
| SELL call, no covering long calls/shares | BLOCK |
| SELL call, long calls same expiry | ALLOW (vertical) |
| SELL N calls, shares < N×100 | BLOCK |
| SELL put (cash-secured) | ALLOW |
| Vertical spread (BUY C + SELL C) | ALLOW |
| Short risk reversal (SELL C + BUY P) | BLOCK (put doesn't cover call) |
| 1x2 ratio (BUY 1C + SELL 2C) | BLOCK (unless stock covers) |
| Jade Lizard (BUY C + SELL C + SELL P) | ALLOW |
| Combo closing (action=SELL) | ALLOW |
| BUY anything | ALLOW |

**Enforcement:** UI (`checkNakedShortRisk()` in `OrderTab.tsx`) → API (403 in `orders/place/route.ts`) → Post-sync audit (`naked_short_audit.py`).
**Combo check:** BAG orders use `action=BUY` envelope. Guard inspects leg-level `right`/`action`. `sellCallRatio - buyCallRatio` = uncovered shorts.
**Impl:** `web/lib/nakedShortGuard.ts`, `scripts/naked_short_audit.py`. Tests: `naked-short-guard.test.ts` (21), `test_naked_short_audit.py`.

---

## High-Throughput Architecture

500+ symbols, <500ms signal-to-order.

**Parallel scanning:** `scanner.py` (15 workers), `discover.py` (10 workers). `UWRateLimitError` skips ticker, doesn't crash.
**Atomic state:** `scripts/utils/atomic_io.py` — `atomic_save()` (temp + `os.replace()` + SHA-256), `verified_load()`.
**Batched WS relay:** `ib_realtime_server.js` — per-client last-write-wins, 100ms flush. 5000 msg/s → 10 batched/s.
**Stale tick detection:** 30s check, 45s no-ticks → auto-restart Gateway (120s cooldown).

### WebSocket Connection State Machine (`usePrices.ts`)

`idle → connecting → open → closed`. `connStateRef` for idempotent connect, `socketGenRef` ignores stale events, diff-based sub/unsub, callback refs, exponential backoff (1s–30s, max 10). Tests: `use-prices-ws-stability.test.ts` (25), `ws-connection-stability.spec.ts` (4).

**Vectorized math:** `kelly_size_batch()` (NumPy), `portfolio_greeks_vectorized()`. Cross-validated to 10⁻¹².
**Resilient IBClient** (`scripts/clients/ib_client.py`): Subscription tracking, disconnect recovery (5 attempts, 2ⁿs capped 30s), pacing violations (162/366: 10s backoff), invalid contracts (200/354: no retry, `_failed_contracts`).
**Incremental sync:** `scripts/utils/incremental_sync.py` — diff by `(ticker, expiry)` + contract count.

### Performance Page

`scripts/portfolio_performance.py` — Phase A (sequential): IB + cache. Phase B (ThreadPool): UW/Yahoo fallbacks. `PERF_FETCH_WORKERS` env (default 8). Disk cache: `data/price_history_cache/`, TTL 15min/24h. SWR via `POST /performance/background`. Tests: 211 (160 Python + 51 TS).

## Evaluation — 7 Milestones (Stop on Failure)

1. Validate ticker → `scripts/fetch_ticker.py`
1B. Seasonality | 1C. Analyst ratings | 1D. News/catalysts (all context)
2. Dark pool flow → `scripts/fetch_flow.py` (with intraday interpolation)
3. Options flow → `scripts/fetch_options.py`
3B. OI changes → `scripts/fetch_oi_changes.py` (REQUIRED)
4. Edge decision — PASS/FAIL (FAIL = stop)
5. Structure — convex position (R:R < 2:1 = stop)
6. Kelly sizing — enforce 2.5% cap
7. Log → `trade_log.json` or `docs/status.md`

## Intraday Dark Pool Interpolation

When evaluating during market hours, today's partial data is volume-weighted interpolated. **Always output BOTH actual and interpolated values.**

**Calculation:** Progress = minutes since 9:30 ET / 390. Project today's volume = actual / progress. Blend: `(projected_ratio × progress) + (prior_5d_avg × (1 - progress))`. Volume pace = actual / (avg_prior × progress).

| Progress | Confidence | Prior Weight |
|----------|------------|--------------|
| 0-25% | VERY_LOW | 75%+ |
| 25-50% | LOW | 50-75% |
| 50-75% | MEDIUM | 25-50% |
| 75-100% | HIGH | <25% |

**Output (mandatory when `is_interpolated: true`):** Show ACTUAL and INTERPOLATED columns for today's flow and 5-day aggregate.

**Edge assessment:** Use interpolated values. LOW/VERY_LOW → re-evaluate after 2 PM ET. Pace >1.2x → signal real. Actual opposite prior → likely reversal.

## Commands

| Command | Action |
|---------|--------|
| `scan` | Watchlist dark pool scan |
| `discover` | Market-wide flow scanner |
| `evaluate [TICKER]` | Full 7-milestone eval |
| `portfolio` | Positions, exposure, capacity |
| `sync` | Pull live portfolio from IB |
| `blotter` / `blotter-history` | Today's fills / Historical trades |
| `leap-scan` / `garch-convergence` / `seasonal` | IV mispricing / GARCH divergence / Seasonality |
| `analyst-ratings [TICKERS]` | Ratings + targets |
| `vcg-scan` / `cri-scan` | Vol-credit gap / Crash Risk Index |
| `menthorq-cta` / `menthorq-dashboard` / `menthorq-screener` / `menthorq-forex` / `menthorq-summary` / `menthorq-quin` | MenthorQ tools |

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/cloud.sh` | Default dev: local services + VPS IB Gateway via Tailscale |
| `scripts/local.sh` | Fully local with Docker gateway |
| `scripts/api/server.py` | FastAPI — 26 endpoints, IB pool, auto-restart |
| `scripts/api/ib_pool.py` | Role-based IB pool (sync=3, orders=4, data=5) |
| `scripts/api/ib_gateway.py` | IB Gateway health + auto-restart |
| `scripts/clients/ib_client.py` | IBClient — orders, quotes, options, fills, flex, reconnect |
| `scripts/clients/uw_client.py` | UWClient — dark pool, flow, chain, ratings, 50+ endpoints |
| `scripts/clients/menthorq_client.py` | MenthorQClient — browser automation, dashboards, screeners |
| `scripts/ib_sync.py` | Sync IB portfolio (atomic writes, structure detection) |
| `scripts/ib_place_order.py` | JSON-in/out order placement (client ID 26) |
| `scripts/ib_order_manage.py` | Cancel/modify open orders |
| `scripts/ib_realtime_server.js` | WS relay — batched, 100ms flush, ticket auth |
| `scripts/utils/atomic_io.py` | Atomic JSON save/load + SHA-256 |
| `scripts/monitor_daemon/run.py` | Monitor daemon — fills, exit orders, rebalance |

## Critical Data Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** trade journal |
| `docs/options-structures.json` | 58 structures, guard decisions, bias, risk profile |
| `data/watchlist.json` | Surveillance tickers |
| `data/vcg.json` | VCG scan cache |
| `data/price_history_cache/` | Stock + option price histories (auto-pruned at 500) |

## Seasonality Fallback

UW → EquityClock Vision → Cache. Route: `web/app/api/ticker/seasonality/route.ts`. Cache: `data/seasonality_cache/{TICKER}.json`. Missing months → EquityClock chart → Claude Haiku Vision → merge (UW priority). API key: `resolveApiKey()` checks `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`.

## ⭐ Trade Specification Report — MANDATORY

Required for any eval reaching Milestone 5.

```
Template : .pi/skills/html-report/trade-specification-template.html
Output   : reports/{ticker}-evaluation-{YYYY-MM-DD}.html
Reference: reports/goog-evaluation-2026-03-04.html
```

**10 sections:** Header + gates | Summary Metrics | Milestone pass/fail | Dark Pool | Options Flow | Context | Structure & Kelly | Trade Spec | Thesis & Risk | Four Gates table.

## P&L Report

```
Template: .pi/skills/html-report/pnl-template.html
Output:   reports/pnl-{TICKER}-{YYYY-MM-DD}.html
Return on Risk = P&L / Capital at Risk
```

## Share PnL Card

1200x630 PNG via `next/og` (Satori). Route: `web/app/api/share/pnl/route.tsx`. Fonts: IBM Plex Mono `.woff`. Theme: `web/lib/og-theme.ts`. Position grouping in `WorkspaceSections.tsx`. Tests: `share-pnl.test.ts` (24), `share-pnl.spec.ts` (6).

## Calculations — Correctness Rules

### Credit/Debit Sign Convention

**Preserve sign throughout entire display pipeline.** Never `Math.abs()` on option prices without approval. Credits = negative, debits = positive.

### Daily Change %

```
Day Chg % = Daily P&L / |Yesterday's Close Value| × 100   (NEVER entry cost)
```

Per-leg: `sign × (last - close) × contracts × 100`. Impl: `getOptionDailyChg()` in `WorkspaceSections.tsx`.

### Combo Natural Market Bid/Ask

**CRITICAL:** Use cross-fields for natural market.

```
BUY combo:  pay ASK on BUY legs, receive BID on SELL legs
SELL combo: receive BID on BUY legs, pay ASK on SELL legs
```

**Implementations:** `computeNetOptionQuote()` in `optionsChainUtils.ts`, `ComboOrderForm.netPrices` in `OrderTab.tsx`, `resolveOrderPriceData()` in `ModifyOrderModal.tsx`.

### Total P&L %

`P&L % = (Market Value - Entry Cost) / |Entry Cost| × 100`

### Per-Leg P&L

`Leg P&L = sign × (|MV| − |EC|)` — LONG: MV−EC, SHORT: EC−MV. Sum = position P&L. Impl: `LegRow` in `PositionTable.tsx`.

### Price Resolution Priority

| Context | Source |
|---------|--------|
| Stock | `prices[ticker].last` |
| Single-leg option | `prices[optionKey(...)].last` |
| Multi-leg spread | Net from each leg's `prices[legPriceKey(...)]` |
| BAG order | `resolveOrderLastPrice()` / `resolveOrderPriceData()` |
| PriceBar | `resolvePriceBar()` — option-level for single-leg, underlying for multi-leg |

**Never show underlying price where user expects option/spread price. Show "---" if unavailable.**

### Position Structure Classification (`ib_sync.py`)

`detect_structure_type()`: Stock→equity, Long Call/Put→defined, Short Call/Put→undefined, Spreads→defined, Synthetic/Risk Reversal→undefined, Straddle (both long)→defined, Covered Call→defined, All-long combo→defined, Unrecognized→complex (→Undefined Risk table).

### IB Combo (BAG) Order Leg Convention

**ComboLeg.action = spread structure, NOT trade direction.** `Order.action` controls open/close; IB reverses legs when SELL. Always `LONG → BUY`, `SHORT → SELL` in ComboLeg.action. Never flip — causes double-reversal → IB error 201.

### Data Normalization

JSON files: `"ticker"`. IB contracts: `"symbol"`. Read defensively: `t.get("ticker") or t.get("symbol")`.

## UW API Quick Reference

```
Base: https://api.unusualwhales.com | Auth: Bearer $UW_TOKEN
```

Key endpoints: `/api/darkpool/{ticker}`, `/api/option-trades/flow-alerts`, `/api/stock/{ticker}/info`, `/api/stock/{ticker}/option-contracts`, `/api/stock/{ticker}/greek-exposure`, `/api/screener/analysts`, `/api/seasonality/{ticker}/monthly`, `/api/shorts/{ticker}/interest-float/v2`. Full spec: `docs/unusual_whales_api.md`

## Signal Interpretation

**P/C Ratio:** >2.0 BEARISH | 1.2–2.0 LEAN_BEAR | 0.8–1.2 NEUTRAL | 0.5–0.8 LEAN_BULL | <0.5 BULLISH
**Flow Side:** Ask-dominant = buying | Bid-dominant = selling
**Analyst Buy%:** ≥70% BULL | 50–69% LEAN_BULL | 30–49% LEAN_BEAR | <30% BEAR
**Seasonality:** >60% FAVORABLE | 50–60% NEUTRAL | <50% UNFAVORABLE

> Seasonality/ratings = context, not gates. Strong flow overrides weak seasonality.

## IB Gateway

Three modes via `IB_GATEWAY_MODE` env (default: `docker`):

### Docker Mode (Primary)

Image: `ghcr.io/gnzsnz/ib-gateway`. Config: `docker/ib-gateway/`. Commands: `scripts/docker_ib_gateway.sh start|stop|restart|status` or `npm run ib:start`. Docker handles reliability via `restart: unless-stopped` + healthcheck. Password via Docker secrets (`docker/ib-gateway/secrets/ib_password.txt`, chmod 600).

### Cloud Mode (Tailscale) — Default for Dev

Gateway on Hetzner VM at `ib-gateway:4001` via Tailscale MagicDNS. Set `IB_GATEWAY_HOST=ib-gateway`, `IB_GATEWAY_MODE=cloud` in root `.env`. All scripts import `DEFAULT_HOST` from `ib_client`.

**VPS:** Port `0.0.0.0:4001 → container:4003` (socat). "Allow connections from localhost only" must be **unchecked** in VNC settings.
**Cloud behavior:** TCP probe only. No local restart/CLOSE_WAIT. `POST /ib/restart` returns 503.
**VPS commands:** `ibstart`, `ibstop`, `ibrestart`, `ibstatus`, `iblogs`, `ibhealth` (via `/usr/local/bin/ibgw`).

### LaunchD Mode (Legacy)

Global service: `local.ibc-gateway`. Scripts in `~/ibc/bin/`. Mon-Fri auto-lifecycle with 2FA on cold start.

### Gateway Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `IB_GATEWAY_MODE` | `docker` | `docker`, `cloud`, or `launchd` |
| `IB_GATEWAY_HOST` | `127.0.0.1` | Gateway host |
| `IB_GATEWAY_PORT` | `4001` | Gateway port |

### Ports

3000 (Next.js), 8321 (FastAPI), 8765 (WS relay), 4001/4002 (IB Gateway Live/Paper), 7496/7497 (TWS), 7462 (IBC).

### Client ID Ranges

| Range | Usage |
|-------|-------|
| 0-9 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10-19 | WS relay |
| 20-49 | Subprocess scripts (`client_id="auto"`) |
| 50-69 | Scanners |
| 70-89 | Daemons (fill=70, exit=71) |
| 90-99 | CLI/standalone |

**Rule:** On-demand scripts MUST use `client_id="auto"` (20-49). Never hardcode.

### Log Rotation

Python: `RotatingFileHandler` (10MB, 2 backups). System: `newsyslog` via `/etc/newsyslog.d/radon.conf` (10MB, 2 bzip2).

## Cloud Deployment Notes

- **FastAPI auth**: Clerk JWT on external requests. Localhost bypass for Next.js → FastAPI.
- **Clerk middleware**: API routes excluded from `protect()`. Auth handled by FastAPI.
- **`NEXT_PUBLIC_*`**: Baked at build time. Rebuild after `.env` changes.
- **Root `node_modules`**: Has `@sinclair/typebox` (pinned `0.34.48`) used by `lib/tools/`.
- **CI/CD**: Push to `main` → GitHub Actions → SSH → `deploy.sh` on VPS. Auto-rollback on health failure. Secrets: `VPS_HOST`, `VPS_SSH_KEY`.

### Historical Data API

`POST /contract/qualify`, `POST /historical/head-timestamp`, `POST /historical/bars`. Auth: `X-API-Key` header vs `MDW_API_KEY` env. Endpoints: `scripts/api/routes/historical.py`.

## Output Rules

- Always: `signal → structure → Kelly math → decision`
- State probabilities; flag uncertainty
- Failing gate = immediate stop, name the gate
- **Never rationalize a bad trade**
- Executed → `trade_log.json` | NO_TRADE → `docs/status.md`

## Startup Checklist

- [ ] `scripts/cloud.sh` (default) or `scripts/local.sh`
- [ ] `curl http://localhost:8321/health` — verify `ib_gateway.port_listening: true`
- [ ] Reconciliation, exit orders, CRI scan auto-running
- [ ] Check market hours

## ⛔ Brand Identity — Mandatory for UI Work

Full spec: `docs/brand-identity.md` + `brand/radon-brand-system.md`. Tokens: `brand/radon-design-tokens.json`. Tailwind: `brand/radon-tailwind-theme.ts`. Kit: `/kit` route.

**Typography:** Inter (UI) + IBM Plex Mono (numeric/telemetry) + Söhne (display only).

**Radon Spectrum:**

| Token | Hex | Meaning |
|-------|-----|---------|
| `signal.core` | `#05AD98` | Core accent |
| `signal.strong` | `#0FCFB5` | High-confidence |
| `signal.deep` | `#048A7A` | Deep data / selected |
| `warn` | `#F5A623` | Caution |
| `fault` | `#E85D6C` | Feed fault |
| `violet.extreme` | `#8B5CF6` | Extreme dislocation |
| `magenta.dislocation` | `#D946A8` | Structural dislocation |
| `neutral` | `#94a3b8` | Neutral |

**Surfaces (dark):** canvas `#0a0f14` | panel `#0f1519` | raised `#151c22` | grid `#1e293b`
**Surfaces (light):** canvas `#FFFFFF` | panel `#FFFFFF` | raised `#F1F5F9` | grid `#BBBFBF`

**CSS variables:** `--bg-base`, `--bg-panel`, `--bg-panel-raised`, `--bg-hover`, `--border-dim`, `--line-grid`, `--signal-core`, `--signal-strong`, `--signal-deep`, `--dislocation`, `--extreme`, `--fault`, `--neutral`, `--text-secondary`.

**Non-negotiable:**
- 4px max border-radius on panels (badges: 999px capsule)
- All colors via tokens — no raw hex
- Mono for machine, sans for product — never reversed
- Voice: precise, calm, scientific — no hype/emojis
- Grid: 8px base, 4px micro, 16px gutters, 32px section gaps
- No decorative elements; panels = instrument modules (hairline borders, matte)
- Signal semantics: Baseline → Emerging → Clear → Strong → Dislocated → Extreme
