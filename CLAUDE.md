# RADON — CLAUDE.md

## ⛔ Mandatory Rules — Every Session, No Exceptions

These rules apply to ALL work in this project. Violating any rule is a blocking failure.

1. **Be concise.** Get straight to the point. No preamble, no filler, no restating what was asked. Long output only when explicitly requested.

2. **E2E browser verification for ALL UI work.** Any change touching UI code (components, styles, layouts, modals, tooltips, charts) MUST be verified with Playwright end-to-end browser automation before marking complete. Playwright is installed at `@playwright/test` in `web/`. Config: `web/playwright.config.ts`. No UI change is done until visually confirmed in a real browser.

3. **Red/green TDD for ALL code.** Every bug fix, feature, and refactor follows test-driven development: write a failing test first (red), implement the fix (green), then refactor. No exceptions. Unit tests via Vitest, E2E via Playwright.

4. **95% test coverage target.** Always create, update, or delete tests (unit, integration, E2E) to maintain ≥95% coverage. Every PR-worthy change must include corresponding tests.

5. **API keys.** Project API keys live in `.env` files (see Credentials Architecture below). If a key is missing from `.env`, check `~/.zshrc` as a fallback source.

---

## Identity

**Radon** — market structure reconstruction system. Surfaces convex opportunities from noisy datasets: options flow, volatility surfaces, and cross-asset positioning. Detects institutional positioning via dark pool/OTC flow, constructs convex options structures, sizes with fractional Kelly. **No narrative trades. No TA trades. Flow signal or nothing.**

Brand spec: `docs/brand-identity.md`

---

## ⛔ Three Gates — Mandatory, Sequential, No Exceptions

```
GATE 1 — CONVEXITY  : Potential gain ≥ 2× potential loss. Defined-risk only (long options, verticals).
GATE 2 — EDGE       : Specific, data-backed dark pool/OTC signal that hasn't moved price yet.
GATE 3 — RISK MGMT  : Fractional Kelly sizing. Hard cap: 2.5% of bankroll per position.
```

**Any gate fails → stop. No rationalization. No "close enough."**

---

## ⚠️ Data Source Priority — Always Obey

| Priority | Source | Notes |
|----------|--------|-------|
| **1** | Interactive Brokers (TWS/Gateway) | Real-time quotes, options chains |
| **2** | Unusual Whales (`$UW_TOKEN`) | Dark pool flow, sweeps, flow alerts |
| **3** | Yahoo Finance | Fallback only; delayed, rate-limited |
| **4** | Web scrape | Last resort |

**Never skip to Yahoo or web without trying IB → UW first.**

**API Clients:** All scripts use `scripts/clients/` — `IBClient` for IB, `UWClient` for UW. Legacy `scripts/utils/ib_connection.py` and `scripts/utils/uw_api.py` preserved for backward compat but new code should use the clients.

**Credentials — two `.env` files (both gitignored):**

| File | Loaded by | Contains |
|------|-----------|----------|
| `.env` (project root) | Python scripts via `python-dotenv` | `MENTHORQ_USER`, `MENTHORQ_PASS` |
| `web/.env` | Next.js built-in | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY` |

**Never commit credentials to source.** All secrets live in `.env` files only.

---

## ⚠️ Market Hours Rule — Always Fetch Fresh Data

```bash
TZ=America/New_York date +"%A %H:%M"   # Check if market open (9:30–16:00 ET, Mon–Fri)
```

- **Market OPEN**: Fetch fresh before ANY analysis. Cache TTL: flow data 5 min, ratings 15 min.
- **Market CLOSED**: Use latest available. Note: `"Market closed — using last available data."`
- **Never analyze stale data without flagging it.**

### CRI / Regime Staleness Rule — Market-Hours Aware

**`/api/regime` only triggers background `cri_scan.py` during market hours.**

| Condition | Stale? | Action |
|-----------|--------|--------|
| `data.date !== today (ET)` | YES | Trigger background scan (new trading day) |
| `market_open === true` + mtime > 60s | YES | Trigger background scan (intraday refresh) |
| `market_open === false` + date = today | **NO** | Serve cached EOD data — launchd handles schedule |

**Rule**: When `cri_scan.py` sets `market_open: false`, the API must treat that data as final and stop triggering re-scans. The launchd CRI service (every 30 min, 4:05 AM–8 PM ET) provides the EOD calculation automatically.

**Implementation**: `web/lib/criStaleness.ts` — `isCriDataStale(data, mtimeMs, todayET)` is the single source of truth for this logic. Tests in `web/tests/regime-cri-staleness.test.ts`. **Do not inline this logic in the route.**

### RegimePanel Market-Closed Value Rules

When `market_open === false`, the component **must**:

| What | Rule |
|------|------|
| `vixVal` / `vvixVal` / `spyVal` | Use `data.vix` / `data.vvix` / `data.spy` only — never WS `last` |
| `intradayCorr` | Return `null` — use `data.avg_sector_correlation` |
| `liveCri` | Return `null` — use `data.cri` (authoritative EOD values) |
| `intradayRvol` | Return `null` — use `data.realized_vol` |
| VIX/VVIX timestamps | Do not update (gate `setVixLastTs`/`setVvixLastTs` on `marketOpen`) |
| Snapshot buffer | Do not append — gate `appendSnapshot` on `marketOpen` |
| SECTOR CORR badge | Must show DAILY, never INTRADAY |

**Tests**: `web/tests/regime-market-closed-values.test.ts` (8 unit), `web/e2e/regime-market-closed-eod.spec.ts` (6 E2E)

---

## Evaluation — 7 Milestones (Stop on Any Failure)

```
1.  Validate Ticker   → python3 scripts/fetch_ticker.py [TICKER]
1B. Seasonality       → curl seasonal chart (context only, not a gate)
1C. Analyst Ratings   → python3 scripts/fetch_analyst_ratings.py [TICKER] (context only)
2.  Dark Pool Flow    → python3 scripts/fetch_flow.py [TICKER]
3.  Options Flow      → python3 scripts/fetch_options.py [TICKER]
4.  Edge Decision     → PASS/FAIL with explicit reasoning (FAIL = stop)
5.  Structure         → Design convex position (R:R < 2:1 = stop)
6.  Kelly Sizing      → Calculate + enforce 2.5% bankroll cap
7.  Log               → Executed trades → trade_log.json | NO_TRADE → docs/status.md
```

---

## Commands

| Command | Action |
|---------|--------|
| `scan` | Watchlist dark pool flow scan |
| `discover` | Market-wide options flow for new candidates |
| `evaluate [TICKER]` | Full 7-milestone evaluation |
| `portfolio` | Positions, exposure, capacity |
| `journal` | Recent trade log |
| `sync` | Pull live portfolio from IB |
| `blotter` | Today's fills + P&L |
| `blotter-history` | Historical trades via Flex Query |
| `leap-scan [TICKERS]` | LEAP IV mispricing opportunities |
| `garch-convergence [TICKERS]` | Cross-asset GARCH vol divergence scan |
| `seasonal [TICKERS]` | Monthly seasonality assessment |
| `x-scan [@ACCOUNT]` | Extract ticker sentiment from X posts |
| `analyst-ratings [TICKERS]` | Ratings, changes, price targets |
| `vcg-scan` | Cross-asset volatility-credit gap divergence signal |
| `cri-scan` | Crash Risk Index — systematic CTA deleveraging detection |
| `menthorq-cta` | Fetch MenthorQ institutional CTA positioning data |
| `menthorq-dashboard [COMMAND]` | Fetch MenthorQ dashboard image (vol, forex, eod, intraday, futures, cryptos_technical, cryptos_options). Supports `--ticker` for eod/intraday/futures/crypto dashboards (16 tickers: spx, vix, ndx, etc.) |
| `menthorq-screener [CATEGORY] [SLUG]` | Fetch MenthorQ screener data (6 categories, 45 sub-screeners). Categories: gamma (5), gamma_levels (5), open_interest (7), volatility (6), volume (6), qscore (16) |
| `menthorq-forex` | Fetch MenthorQ forex gamma levels + blindspot data (14 pairs, 20+ fields per pair) |
| `menthorq-summary [CATEGORY]` | Fetch MenthorQ summary tables (futures: 93 rows, cryptos: 16 rows) |

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/clients/ib_client.py` | **IBClient** — Primary IB API client (connection, orders, quotes, options, fills, flex) |
| `scripts/clients/uw_client.py` | **UWClient** — Primary UW API client (dark pool, flow, chain, ratings, seasonality, 50+ endpoints) |
| `scripts/clients/menthorq_client.py` | **MenthorQClient** — MenthorQ browser automation client. Constants: `DASHBOARD_COMMANDS` (8 commands), `TICKER_TAB_COMMANDS` (5), `DASHBOARD_TICKERS` (16), `SCREENER_SLUGS` (6 categories, 45 slugs), `SUMMARY_CATEGORIES` (2), `FOREX_CARD_SLUGS` (2). Methods: `get_cta()`, `get_eod()`, `get_dashboard_image()`, `get_forex_levels()`, `get_summary()`, `get_screener()`, `get_screener_category()`, `get_all_screener_data()`, `discover_screener_cards()`, `get_futures_list/detail/contracts()`, `get_forex_list/detail()`, `get_crypto_list/detail()`, `get_intraday()`. |
| `scripts/fetch_ticker.py` | Ticker validation |
| `scripts/fetch_flow.py` | Dark pool + options flow |
| `scripts/fetch_options.py` | Options chain + institutional flow |
| `scripts/fetch_analyst_ratings.py` | Ratings, upgrades/downgrades |
| `scripts/scanner.py` | Watchlist batch scan |
| `scripts/discover.py` | Market-wide flow scanner |
| `scripts/kelly.py` | Kelly calculator |
| `scripts/ib_sync.py` | Sync live IB portfolio |
| `scripts/ib_reconcile.py` | Reconcile fills vs trade_log |
| `scripts/blotter.py` | Today's fill P&L |
| `scripts/trade_blotter/flex_query.py` | Historical fills (365d via Flex) |
| `scripts/leap_scanner_uw.py` | LEAP scanner (UW + Yahoo, no IB) |
| `scripts/exit_order_service.py` | Place pending exit orders when IB accepts |
| `scripts/ib_order_manage.py` | Cancel or modify open IB orders |
| `scripts/ib_place_order.py` | JSON-in/JSON-out order placement for web API (client ID 26) |
| `scripts/fetch_x_watchlist.py` | X account tweet sentiment |
| `scripts/vcg_scan.py` | Volatility-Credit Gap divergence scanner |
| `scripts/cri_scan.py` | Crash Risk Index — CTA deleveraging detection |
| `scripts/fetch_menthorq_cta.py` | MenthorQ CTA positioning (S3 image download + Vision extraction) |
| `scripts/fetch_menthorq_dashboard.py` | MenthorQ dashboard charts (S3 download → screenshot fallback + Vision) |
| `scripts/setup_ibc.sh` | **Legacy** — superseded by `local.ibc-gateway` global service (see IB Gateway & IBC section) |
| `scripts/setup_cri_service.sh` | CRI Scan launchd service (every 30 min, 4:05 AM–8 PM ET, Mon-Fri trading days) |
| `scripts/run_cri_scan.sh` | Holiday-aware CRI scan wrapper for launchd |
| `scripts/clients/inspect_dashboard.py` | MenthorQ DOM inspector — finds chart containers, S3 image URLs per command |
| `scripts/clients/map_nav.py` | MenthorQ navigation tree mapper — discovers all sidebar links and pages |
| `scripts/clients/map_screeners.py` | MenthorQ screener slug discovery — maps all sub-slugs and ticker tab clicks |
| `scripts/clients/map_subnav.py` | MenthorQ sub-navigation mapper — checks ticker tabs and selectors per page |

---

## Critical Data Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** executed trade journal |
| `data/watchlist.json` | Tickers under surveillance |
| `data/ticker_cache.json` | Ticker → company name cache |
| `data/reconciliation.json` | IB reconciliation results |
| `data/seasonality_cache/{TICKER}.json` | Cached seasonality (UW + EquityClock Vision fallback) |
| `data/menthorq_cache/cta_{DATE}.json` | Cached MenthorQ CTA positioning (daily, S3 image + Vision) |
| `data/menthorq_cache/{command}_{DATE}.json` | Cached MenthorQ dashboard data (S3/screenshot + Vision) |
| `data/cri_scheduled/cri-{TIMESTAMP}.json` | Scheduled CRI scan readings (intraday time-series) |

---

## Seasonality Fallback: UW → EquityClock Vision → Cache

When UW returns incomplete seasonality data (missing months), the API route falls back to EquityClock chart image extraction via Claude Haiku Vision.

**Flow** (`web/app/api/ticker/seasonality/route.ts`):
1. Check cache: `data/seasonality_cache/{TICKER}.json` — if valid (not expired), return immediately
2. Fetch UW API — if all 12 months have `years > 0`, cache as `source: "uw"`, return
3. Missing months → download `https://charts.equityclock.com/seasonal_charts/{TICKER}_sheet.png`
4. Send image to Claude Haiku Vision (`claude-haiku-4-5-20251001`) for structured extraction
5. Merge: UW data takes priority (years > 0), Vision fills gaps
6. Cache as `source: "uw+equityclock"` — expires 1st of next month UTC
7. If Vision fails → return UW data as-is (partial)

**Cache**: `data/seasonality_cache/{TICKER}.json` — auto-expires monthly. Delete file to force refresh.

**API key**: Uses `resolveApiKey()` — checks `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`.

---

## ⭐ Trade Specification HTML Report — MANDATORY

**Required for ANY evaluation reaching Milestone 5 (Structure).**

```
Template : .pi/skills/html-report/trade-specification-template.html
Output   : reports/{ticker}-evaluation-{YYYY-MM-DD}.html
Reference: reports/goog-evaluation-2026-03-04.html
```

**10 required sections:** Header + gate status | 6 Summary Metrics | Milestone pass/fail | Dark Pool Flow | Options Flow | Context (seasonality + ratings) | Structure & Kelly | Trade Spec (exact order) | Thesis & Risk | Three Gates table.

**Workflow:**
1. Complete milestones 1–6
2. Generate HTML report
3. Present for user confirmation
4. On "execute" → place via IB
5. On fill → update `trade_log.json`, `portfolio.json`, `docs/status.md`

---

## P&L Report Template

```
Template : .pi/skills/html-report/pnl-template.html
Output   : reports/pnl-{TICKER}-{YYYY-MM-DD}.html

Return on Risk = Realized P&L / Capital at Risk
  Debit spread  → Net debit paid
  Credit spread → Width − credit received
  Long option   → Premium paid
```

---

## Share PnL Card (Image Generation)

Generates a branded 1200x630 PNG card for sharing trade P&L on social media. Uses `next/og` (Satori) for server-side rendering.

```
API Route : web/app/api/share/pnl/route.tsx
Component : web/components/SharePnlButton.tsx
Fonts     : web/lib/og-fonts.ts (IBM Plex Mono .woff from @fontsource)
Theme     : web/lib/og-theme.ts (Radon brand colors for Satori)
Tests     : web/tests/share-pnl.test.ts (12 unit), web/e2e/share-pnl.spec.ts (6 E2E)
```

**Card layout:** Contract description (e.g. "Long AAOI 2026-04-17 Call $45.00") → Hero P&L $ + % → Fill/Commission/Time details → Radon icon + "Executed with Radon" footer.

**Wired into:** Executed Orders table + Historical Trades (Blotter) table on `/orders` page. Share button appears for non-cancelled fills (executed orders) and closed trades (blotter).

**Clipboard copy:** `navigator.clipboard.write()` with `ClipboardItem({ "image/png": blob })`.

**Font note:** Satori does NOT support `.ttf` parsed by `@vercel/og` in Next.js 16 — use `.woff` format. Font files from `@fontsource/ibm-plex-mono` npm package.

---

## Bug Fix Workflow — Mandatory

**Red/green TDD for every bug fix, no exceptions:**

1. Write a failing test that reproduces the bug (test must be RED before any code change)
2. Implement the minimal fix
3. Confirm the test turns GREEN
4. For UI bugs: add a Playwright E2E test — unit tests alone are not sufficient confirmation

---

## Calculations — Correctness Rules

Financial calculations in the web UI must follow these rules exactly. Bugs here mislead trading decisions.

### Daily Change % (Day Chg column)

```
Day Chg % = Daily P&L / |Yesterday's Close Value| × 100

NEVER divide by entry cost. Entry cost is for total P&L, not daily change.
```

**Why this matters**: A spread bought at $0.52 that's now worth $8.50 has a close value ~16x the entry cost. Dividing a $800 daily move by $2,600 (entry) gives -206%; dividing by $42,500 (close) gives the correct +1.88%.

| Position Type | Daily P&L | Denominator |
|--------------|-----------|-------------|
| Stock | `(last - close) × qty` | `close × qty` |
| Single option | `(last - close) × contracts × 100` | `close × contracts × 100` |
| Spread/combo | `SUM(sign × (last - close) × contracts × 100)` per leg | `SUM(sign × close × contracts × 100)` per leg |

Where `sign = +1` for LONG legs, `-1` for SHORT legs.

**Implementation**: `getOptionDailyChg()` in `WorkspaceSections.tsx`. Tests in `lib/tools/__tests__/daily-chg.test.ts`.

### Spread Net Mid (Last Price for BAG orders)

```
Spread Mid = SUM(sign × (bid + ask) / 2) per leg
```

Resolve each leg's WS bid/ask via `legPriceKey()`, compute per-leg mid, combine sign-aware. Do NOT use the underlying stock price for spread orders.

**Implementation**: `resolveOrderLastPrice()` in `WorkspaceSections.tsx`.

### Total P&L % (P&L column)

```
P&L % = (Market Value - Entry Cost) / |Entry Cost| × 100
```

This correctly uses entry cost as the denominator because it measures return on capital deployed over the life of the position.

### Per-Leg P&L (expanded combo rows)

```
Leg P&L = sign × (|Market Value| − |Entry Cost|)

Where sign = +1 for LONG legs, −1 for SHORT legs
```

| Leg Direction | Interpretation |
|---------------|----------------|
| LONG | Profit when option appreciates: MV − EC |
| SHORT | Profit when option decays: EC − MV |

Sum of per-leg P&L equals the position-level P&L. Uses RT WS price when available, falls back to IB sync `market_value`.

**Implementation**: `LegRow` in `PositionTable.tsx`.

### Return on Risk (trade log)

```
Return on Risk = Realized P&L / Capital at Risk

Capital at Risk:
  Debit spread  → Net debit paid
  Credit spread → Width − credit received
  Long option   → Premium paid
```

### Price Resolution Priority

| Context | Price Source |
|---------|-------------|
| Stock position | `prices[ticker].last` |
| Single-leg option | `prices[optionKey(...)].last` (option contract, NOT underlying) |
| Multi-leg spread | Compute net from each leg's `prices[legPriceKey(...)]` |
| BAG order last price | `resolveOrderLastPrice()` — net mid from portfolio legs |
| BAG modify modal BID/MID/ASK | `resolveOrderPriceData()` in `ModifyOrderModal.tsx` — synthetic `PriceData` from per-leg WS bid/ask |
| Order form BID/MID/ASK | Same resolved price data as PriceBar (option-level for options) |
| PriceBar in modal | `resolvePriceBar()` — option-level for single-leg, underlying for multi-leg |

**Rule**: Never show the underlying stock price where the user expects an option or spread price. If option prices aren't available, show "---" rather than a misleading underlying price.

### Data Normalization

**Ticker key**: Always use `"ticker"` as the key name in JSON data files. Never use `"symbol"` — that's for IB contract objects only.

| File | Key | Example |
|------|-----|---------|
| `watchlist.json` entries | `ticker` | `{"ticker": "AAPL", ...}` |
| `portfolio.json` positions | `ticker` | `{"ticker": "GOOG", ...}` |
| `trade_log.json` entries | `ticker` | `{"ticker": "AMD", ...}` |
| IB order contracts | `symbol` | `{"symbol": "AAPL", "secType": "STK"}` |
| Discover candidates | `ticker` | `{"ticker": "NET", ...}` |

Scripts that write to data files must use `"ticker"`. Scripts that read must handle both defensively (`t.get("ticker") or t.get("symbol")`).

---

## Unusual Whales API Quick Reference

```
Base URL : https://api.unusualwhales.com
Auth     : Authorization: Bearer $UW_TOKEN
```

| Endpoint | Use |
|----------|-----|
| `GET /api/darkpool/{ticker}` | Dark pool flow (primary edge) |
| `GET /api/option-trades/flow-alerts` | Sweeps, blocks, unusual activity |
| `GET /api/stock/{ticker}/info` | Ticker validation |
| `GET /api/stock/{ticker}/option-contracts` | Options chain |
| `GET /api/stock/{ticker}/greek-exposure` | GEX |
| `GET /api/screener/analysts` | Analyst ratings |
| `GET /api/seasonality/{ticker}/monthly` | Monthly seasonality |
| `GET /api/shorts/{ticker}/interest-float/v2` | Short interest |

Full spec: `docs/unusual_whales_api.md` | `docs/unusual_whales_api_spec.yaml`

---

## Signal Interpretation

**Put/Call Ratio → Bias:**
`>2.0` BEARISH | `1.2–2.0` LEAN_BEARISH | `0.8–1.2` NEUTRAL | `0.5–0.8` LEAN_BULLISH | `<0.5` BULLISH

**Flow Side:**
- Ask-side dominant → buying pressure (opening longs)
- Bid-side dominant → selling pressure (closing longs or opening shorts)

**Analyst Buy %:**
`≥70%` BULLISH | `50–69%` LEAN_BULLISH | `30–49%` LEAN_BEARISH | `<30%` BEARISH

**Discovery Score (0–100):**
`60–100` Strong (full eval) | `40–59` Monitor | `20–39` Weak | `<20` No signal

**Seasonality Rating:**
`FAVORABLE` = win rate >60% | `NEUTRAL` = 50–60% | `UNFAVORABLE` = win rate <50%

> Seasonality and analyst ratings are **context, not gates.** Strong flow can override weak seasonality. Weak flow + weak seasonality = pass entirely.

---

## IB Gateway & IBC

IB Gateway is managed by a **machine-global secure IBC service** (`local.ibc-gateway`), shared with [market-data-warehouse](https://github.com/joemccann/market-data-warehouse). IBC install lives at `~/ibc-install/`, config and wrappers at `~/ibc/`.

**Credentials:** Stored in macOS Keychain (not on disk). The runner reads credentials at launch, writes a temporary `0600` runtime config, and removes it after IBC exits.

**Service commands:**

| Command | Action |
|---------|--------|
| `~/ibc/bin/start-secure-ibc-service.sh` | Start Gateway via launchd |
| `~/ibc/bin/stop-secure-ibc-service.sh` | Stop Gateway |
| `~/ibc/bin/restart-secure-ibc-service.sh` | Restart Gateway |
| `~/ibc/bin/status-secure-ibc-service.sh` | Check service status |
| `~/ibc-install/stop.sh` | Clean shutdown (while running) |
| `~/ibc-install/reconnectdata.sh` | Reconnect market data |
| `~/ibc-install/reconnectaccount.sh` | Reconnect to IB login server |

**LaunchAgent:** `~/Library/LaunchAgents/local.ibc-gateway.plist`

**Lifecycle (automated via launchd):**
- **Mon-Fri 00:00** — launchd starts Gateway via IBC, reads Keychain credentials
- **2FA** — approve once on IBKR Mobile; IBC retries if missed (`TWOFA_TIMEOUT_ACTION=restart`)
- **11:58 PM daily** — IBC auto-restarts Gateway (reuses auth session, no 2FA needed)
- **Sunday 07:05** — Cold restart: full shutdown + fresh login (weekly re-auth, 2FA required)

**Key config (`~/ibc/config.secure.ini`):**
- `ExistingSessionDetectedAction=primary` — Gateway reconnects if bumped by another session
- `AcceptIncomingConnectionAction=accept` — no popup for API connections
- `CommandServerPort=7462` — IBC command server for stop/restart
- No `IbLoginId`/`IbPassword` — credentials are in Keychain only

**Ports:**

| Port | Connection |
|------|-----------|
| 7496 | TWS Live |
| 7497 | TWS Paper (default) |
| 4001 | IB Gateway Live |
| 4002 | IB Gateway Paper |
| 7462 | IBC Command Server (stop/restart Gateway) |

**Phase 1 remote access (working path):**
- Canonical remote control surface is the secure wrapper set in `~/ibc/bin/`
- Transport is **standard macOS SSH over Tailscale**, not Tailscale SSH server mode
- Dependencies:
  - `Tailscale.app` on the Mac
  - Tailscale on the iPhone, connected to the same tailnet
  - macOS `Remote Login` enabled
  - iPhone SSH client such as Termius, Blink Shell, or Prompt
  - Optional: dedicated public key in `~/.ssh/authorized_keys` for key-based login
- Example direct remote commands:
  - `ssh joemccann@macbook-pro '~/ibc/bin/status-secure-ibc-service.sh'`
  - `ssh joemccann@macbook-pro '~/ibc/bin/restart-secure-ibc-service.sh'`
- Optional repo helper: `scripts/ibc_remote_control.sh`
- Detailed runbook: `docs/ibc-remote-access.md`

**Legacy:** The old `com.convex-scavenger.ibc-gateway` LaunchAgent and `scripts/setup_ibc.sh` are superseded by the global `local.ibc-gateway` service. The legacy plist was migrated automatically by the market-data-warehouse installer.

IB error `10358` = Reuters Fundamentals subscription inactive → auto-fallback to next source.

---

## Output Rules

- Always show: `signal → structure → Kelly math → decision`
- State probability estimates explicitly; flag uncertainty
- Failing gate = immediate stop with the failing gate named
- **Never rationalize a bad trade**
- Executed trades → `trade_log.json`
- NO_TRADE decisions → `docs/status.md` (Recent Evaluations)

---

## Startup Checklist

- [ ] IB Gateway running (`~/ibc/bin/status-secure-ibc-service.sh`) — if not, approve 2FA on IBKR Mobile
- [ ] IB reconciliation auto-runs (`scripts/ib_reconcile.py`) — check `data/reconciliation.json`
- [ ] Exit order service auto-runs — checks `PENDING_MANUAL` positions
- [ ] CRI scan service running (`./scripts/setup_cri_service.sh status`) — 30-min intervals, premarket-close
- [ ] X account scan: if last scan >12h ago, run `x-scan` for flagged accounts
- [ ] Check market hours before any analysis

---

## ⛔ Radon Brand Identity — Mandatory for ALL UI Work

**Full specification:** `docs/brand-identity.md` (reference) + `brand/radon-brand-system.md` (complete spec).
**Design tokens:** `brand/radon-design-tokens.json` | **Tailwind theme:** `brand/radon-tailwind-theme.ts`
**Component reference:** `brand/radon-component-kit.html` | **Live kit:** `/kit` route (`web/components/kit/`) | **Terminal mockup:** `brand/radon-terminal-mockup.html`
**Logo assets:** `brand/radon-app-icon.svg`, `radon-monogram.svg`, `radon-wordmark.svg`, `radon-lockup-horizontal.svg` | **Hero:** `.github/hero.png`

Any change touching UI code (components, styles, layouts, modals, charts, empty states, system messages) MUST comply with the Radon Brand Identity. Violations are blocking failures equivalent to a broken test.

### Quick Reference (see `docs/brand-identity.md` for full details)

**System name:** Radon (not "Convex Scavenger" in UI). Hierarchy: Radon Terminal, Flow, Signals, Exposure, Surface, Structure, Sets.

**Typography:** Inter (UI, titles, labels, metrics) + IBM Plex Mono (dense numeric tables, status/meta telemetry) + Söhne (display/wordmark only).

**Radon Spectrum (color — clarity scale, not P&L):**
| Token | Hex | Meaning |
|-------|-----|---------|
| `signal.core` | **`#05AD98`** | **Core Radon discovery layer (flagship accent)** |
| `signal.strong` | `#0FCFB5` | High-confidence signal |
| `signal.deep` | `#048A7A` | Deep data / selected states |
| `warn` | `#F5A623` | Quality / caution |
| `fault` | `#E85D6C` | Feed fault / integrity problem |
| `violet.extreme` | `#8B5CF6` | Extreme dislocation / rare state |
| `magenta.dislocation` | `#D946A8` | Structural dislocation |
| `neutral` | `#94a3b8` | Neutral comparative states |

**Surfaces (dark):** `bg.canvas: #0a0f14` | `bg.panel: #0f1519` | `bg.panelRaised: #151c22` | `line.grid: #1e293b`
**Surfaces (light):** `bg.canvas: #FFFFFF` | `bg.panel: #FFFFFF` | `bg.panelRaised: #F1F5F9` | `line.grid: #BBBFBF`

**CSS signal variables:** `--signal-core`, `--signal-strong`, `--signal-deep`, `--dislocation`, `--extreme`, `--fault`, `--neutral`, `--text-secondary` — all auto-adapt to dark/light theme via `globals.css`.

**Non-negotiable rules:**
- **4px max** `border-radius` on panels (badges use `999px` capsule) — no soft consumer rounding
- All colors reference design tokens — no raw hex in components
- Mono for machine (numbers, telemetry), sans for product (titles, labels) — never reversed
- Empty states describe the measurement condition, not generic placeholders
- Brand voice: precise, calm, scientific, unsensational — no hype, no emojis, no emotional punctuation
- Grid: 8px base unit, 4px micro unit, 16px gutters, 32px section gaps
- No decorative elements: no glassmorphism, heavy gradients, soft consumer shadows, or icons-as-decoration
- Panels feel like mountable instrument modules with hairline borders, matte surfaces, device-label headers
- Signal semantics use clarity scale (Baseline → Emerging → Clear → Strong → Dislocated → Extreme)

**Contributor acceptance (verify before any UI PR):** See `docs/brand-identity.md` Section 9.
