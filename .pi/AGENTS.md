# Radon — Project Instructions

## ⛔ Radon Brand Identity — Mandatory for ALL UI & Asset Work

**Any UI change MUST comply with Radon Brand Identity. Violations are blocking failures.**

**Reference files:** `docs/brand-identity.md`, `brand/radon-brand-system.md`, `brand/radon-design-tokens.json`, `brand/radon-tailwind-theme.ts`, `brand/radon-component-kit.html`, `brand/radon-terminal-mockup.html`. Logo: `brand/radon-app-icon.svg` | Hero: `.github/hero.png`

**Non-negotiable rules:**
- Accent: `signal.core: #05AD98` (teal). Surfaces: canvas `#0a0f14` | panel `#0f1519` | raised `#151c22` | grid `#1e293b`
- Typography: Inter (UI) + IBM Plex Mono (numeric tables) + Sohne (display only)
- 4px max `border-radius` on panels; badges `999px` capsule
- Signal semantics: Baseline > Emerging > Clear > Strong > Dislocated > Extreme
- Panels = instrument modules: hairline borders, matte surfaces, device-label headers
- Grid: 8px base, 4px micro, 16px gutters, 32px section gaps
- Voice: precise, calm, scientific — no hype/emojis
- No glassmorphism, gradients, soft shadows, decorative elements
- Empty states describe measurement conditions, not generic placeholders

---

## ⚠️ Bug Fix Workflow — Mandatory

Red/green TDD: 1) Failing test (RED), 2) Minimal fix, 3) Confirm GREEN, 4) UI bugs: add Playwright E2E test.

## ⚠️ Browser Verification — Mandatory

Visually verify rendered UI before done. Primary: `chrome-cdp`. Fallback: Playwright (`web/`).

## ⚠️ Coverage — 95% target on touched surface. Every change includes tests.

## ⚠️ Options Chain Combo Entry Rules — Mandatory

1. **Never derive BAG `Order.action` from net debit vs credit.** IB combo leg actions define structure. `SELL` envelope reverses legs. Keep entry combos on `BUY` envelope.
2. **Reset manual net price when combo structure changes.** Recompute from normalized combo quote.
3. **Regression coverage required:** Unit (payload semantics) + Browser (rendered net price + submitted payload).
4. **Trace full path before patching:** chain builder > Next route > FastAPI bridge > `scripts/ib_place_order.py`.

## ⚠️ Credit/Debit Sign Convention — Mandatory

**Preserve the sign throughout the entire display pipeline.** Never `Math.abs()` on option prices/values. Credits = negative, debits = positive. Applies to P&L cards, share images, order forms, all price displays.

## ⚠️ Combo Natural Market Bid/Ask — Mandatory

**Always use cross-fields for natural market pricing. Never `sign * bid` and `sign * ask`.**

```
To BUY combo:  pay ASK on BUY legs, receive BID on SELL legs
To SELL combo: receive BID on BUY legs, pay ASK on SELL legs
```

Example (bull call spread BUY $200C bid=4.50/ask=4.70, SELL $210C bid=2.00/ask=2.20):
- netAsk = 4.70 - 2.00 = 2.70 | netBid = 4.50 - 2.20 = 2.30 | mid = 2.50
- WRONG (mid-mid): `sign*bid` = 2.50, `sign*ask` = 2.50 -> bid = ask = mid

Implementations: `computeNetOptionQuote()`, `ComboOrderForm.netPrices`, `resolveOrderPriceData()` for BAG.

## ⚠️ Options Structure Catalog — Reference

**Canonical file:** `docs/options-structures.json` | **Human:** `docs/options-structures.md` — 58 structures, 12 categories.

**Guard decision quick-reference:**
```
Combo (BAG) — checked before BUY early-return:
  Closing (action=SELL)                  -> ALLOW
  sellCallRatio == buyCallRatio          -> ALLOW (vertical/Jade Lizard/Seagull)
  sellCallRatio > buyCallRatio           -> check stock (BLOCK if uncovered)
  Only SELL puts, no SELL calls          -> ALLOW (cash-secured)

Single-leg:
  BUY anything                           -> ALLOW
  SELL put                               -> ALLOW (cash-secured)
  SELL call + stock covers               -> ALLOW (covered call)
  SELL call, naked                       -> BLOCK
  SELL stock, shares >= qty              -> ALLOW
  SELL stock, naked                      -> BLOCK
```

Implementation: `web/lib/nakedShortGuard.ts` (21 tests in `web/tests/naked-short-guard.test.ts`).

---

## ⚠️ Order Placement Input Validation

`/api/orders/place` rejects: zero/negative quantity or limitPrice, NaN/Infinity, missing required fields -> 400.

## ⚠️ Order Cancel / Modify Failure Propagation

1. **Re-check refreshed open orders** for confirmation, not just original `Trade` object.
2. **Disappearance after cancel = success.**
3. **Extract human-readable error** from subprocess JSON stdout (not raw JSON).
4. **Preserve upstream HTTP status/detail** in Next bridge routes.
5. **Regression: 3 layers** — Python unit (confirmation semantics), Next route (status propagation), Browser (toast/error).

---

## Order System — Unified Components

Shared components in `web/lib/order/`: `OrderPriceStrip`, `OrderLegPills`, `OrderConfirmSummary`, `OrderPriceButtons`, `OrderActionToggle`, `OrderTifSelector`, `OrderQuantityInput`, `OrderPriceInput`. Hooks: `useOrderPrices`, `useOrderValidation`.

**6 entry locations:** `NewOrderForm`, `ComboOrderForm`, `OptionsChainTab > OrderBuilder`, `BookTab > StockOrderForm`, `InstrumentDetailModal`, `ModifyOrderModal`.

**OrderConfirmSummary:** Debit spread: maxLoss=premium, maxGain=width-premium. Credit spread: inverse. Single: maxLoss=premium. Stock: totalCost=qty*price.

---

## ⚠️ Data Fetching Priority (ALWAYS follow this order)

| Priority | Source | When to Use |
|----------|--------|-------------|
| **1st** | Interactive Brokers | Always try first |
| **2nd** | Unusual Whales | Flow, dark pools, options, ratings |
| **3rd** | Exa (web search) | Research, docs |
| **4th** | agent-browser | Interactive/JS-rendered pages only |
| **5th** | Cboe feeds | COR1M historical fallback |
| **6th** | Yahoo Finance | **ABSOLUTE LAST RESORT** |

---

## ⚠️ Evaluate Command -> ALWAYS Call `evaluate.py` (MANDATORY)

```bash
python3.13 scripts/evaluate.py [TICKER]
```

Non-negotiable. Handles all data fetching (M1-M3B + M1D) in parallel, includes today's intraday data, stops at first failing gate.

**NEVER manually call** `fetch_flow.py`, `fetch_options.py`, `fetch_oi_changes.py`, `fetch_news.py`, or `kelly.py` during evaluation. `evaluate.py` orchestrates them.

**Trigger phrases:** `evaluate TICKER`, `full trade evaluation for TICKER`, `run the evaluation on TICKER`, `check TICKER` (when context = full eval).

---

## ⚠️ Always Fetch Today's Data (MANDATORY)

Every evaluation milestone MUST fetch fresh data. Never reuse previous scan/session/cached results.

| Milestone | Freshness Rule |
|-----------|----------------|
| M1 Ticker | Run at eval start |
| M1B Seasonality | OK to cache |
| M1C Analysts | Re-fetch |
| M1D News | Re-fetch |
| M2 Dark Pool | **MUST include today** |
| M3 Options Flow | **MUST be today's data** |
| M3B OI Changes | **MUST be today's OI** |
| M4 Edge | **MUST include today's bar** |
| M5 Structure | **MUST be real-time/today's close** |

**Market hours:** 9:30-16:00 ET Mon-Fri. Utils: `scripts/utils/market_hours.py`. Cache TTL: flow 5min, ratings 15min. Scan data != evaluation data — always re-fetch.

**Data Freshness line MANDATORY in output.** Flag stale data explicitly.

---

## ⚠️ Intraday Dark Pool Interpolation (AUTOMATIC)

`fetch_flow.py` auto-interpolates partial-day DP data during market hours. `aggregate` = interpolated values (used by scanner/evaluate). `aggregate_actual` = raw.

**Algorithm:** Progress = minutes_elapsed / 390. Project today's volume = actual / progress. Blend with prior 5-day avg weighted by progress.

| Progress | Confidence | Interpretation |
|----------|------------|----------------|
| 0-25% | VERY_LOW | Recommend waiting |
| 25-50% | LOW | Heavy prior weighting |
| 50-75% | MEDIUM | Trend emerging |
| 75-100% | HIGH | Today reliable |

**Volume pace:** actual / (avg_prior * progress). >1.1x = above avg, <0.9x = below avg.

**Edge rules:** Use interpolated values. LOW/VERY_LOW -> recommend re-eval after 2 PM ET. Pace >1.2x + opposing direction = likely reversal.

**Output (MANDATORY when intraday):** Show BOTH actual and interpolated columns for today's flow and aggregate.

---

## Workflow Commands

| Command | Action |
|---------|--------|
| `evaluate [TICKER] [--days N]` | `python3.13 scripts/evaluate.py [TICKER]` — full 7-milestone eval |
| `scan` | Watchlist DP scan + CRI regime overlay -> HTML report |
| `discover` | Market-wide/targeted/preset discovery scanner |
| `portfolio` | `python3.13 scripts/portfolio_report.py` — HTML report |
| `free-trade` | Analyze positions for free trade opportunities |
| `journal` | View recent trade log |
| `sync` | Pull live portfolio from IB |
| `blotter` | Today's fills + P&L with spread grouping |
| `risk-reversal [TICKER]` | `python3.13 scripts/risk_reversal.py [TICKER]` — IV skew analysis |
| `vcg` | Call `vcg_scan` Pi tool (do NOT read strategy docs) |
| `strategies` | List strategies from `data/strategies.json` |
| `stress-test` | Interactive scenario stress test -> HTML report |
| `tweet-it` | Tweet copy + infographic card for sharing trades |
| `commands` | Read `.pi/commands.json` and display table |
| `menthorq-cta` | MenthorQ CTA positioning |
| `cri-scan` | CRI scan with MenthorQ CTA overlay |
| `blotter-history` | Historical trades via Flex Query |
| `leap-scan [TICKERS]` | LEAP IV mispricing scan |
| `garch-convergence [TICKERS]` | `python3.13 scripts/garch_convergence.py` — cross-asset GARCH vol divergence |
| `seasonal [TICKERS]` | Monthly seasonality assessment |
| `x-scan [@ACCOUNT]` | xAI API tweet sentiment (recommended) |
| `x-scan-browser [@ACCOUNT]` | Browser scraping tweet sentiment (faster, lower quality) |
| `analyst-ratings [TICKERS]` | Analyst ratings + targets |

### Commands List (MANDATORY)

When user types `commands`, IMMEDIATELY read `.pi/commands.json` and display as formatted table. No other actions.

### ⚠️ Strategy Registry Sync (MANDATORY)

`data/strategies.json` = machine-readable registry. `docs/strategies.md` = source of truth. When modifying strategies, ALWAYS update both. Validate: `python3.13 -m json.tool data/strategies.json`.

### Stress Test Command

**Trigger phrases:** `stress-test`, `stress test`, `scenario analysis`, `what happens if the market...`

Two-step: 1) Prompt for scenario, 2) Parse into SPX/VIX/sector params -> load portfolio -> run `scripts/scenario_analysis.py` (beta-SPX + sector + VIX crash-beta + BSM IV expansion) -> generate HTML report from `.pi/skills/html-report/stress-test-template.html`.

**Key modeling rules:** Use `BASELINE_IV` dict per-ticker. Defined-risk P&L clamped to [-net_debit, +max_width]. Long options floored at -premium. LEAP IV expansion dampened 50%, medium 75%, short 100%. VIX crash-beta only when scenario VIX > 30.

### Tweet-It Command

**Trigger phrases:** `tweet-it`, `tweet this trade`, `create a tweet`, `X post`, `share this trade`

**6-step workflow:** Generate text -> Generate card HTML -> Screenshot via `agent-browser` -> Base64-encode PNG into data URI (Chrome blocks file:// cross-origin) -> Generate preview HTML with inlined base64 -> Open in browser.

**Output:** `reports/tweet-{TICKER}-{DATE}.html` (self-contained preview with copy buttons). Templates: `.pi/skills/tweet-it/`.

**Voice:** Cashtags, `>` prefix bullets, "Analyzed by Radon" + "radon.run", precise numbers, calm/scientific.

### Scan Command

Run in sequence: 1) `python3.13 scripts/scanner.py` (DP flow), 2) `python3.13 scripts/cri_scan.py --json` (CRI regime), 3) Combine with CRI context, 4) Generate HTML report at `reports/daily-scan-{date}.html`.

**CRI impact:** LOW=normal, ELEVATED=institutions buying dips, HIGH=contrarian+high conviction, CRITICAL=signals unreliable.

**Report sections (12):** Header, freshness, 6 summary metrics, CRI context, observations, movers, Tier 1/2/3 tables, Monday priorities, market context, methodology.

### Evaluate Command Details

1) Run `python3.13 scripts/evaluate.py [TICKER]`, 2) If NO_TRADE: log to `docs/status.md`, 3) If PENDING: design structure with IB quotes, Kelly, generate trade spec HTML, present for confirmation, 4) If TRADE: execute via `ib_execute.py`, then IMMEDIATELY run Post-Trade Logging.

Options: `--days N` (DP lookback), `--json`, `--bankroll N`.

### Discover Command

```bash
python3.13 scripts/discover.py              # Market-wide (default)
python3.13 scripts/discover.py AAPL MSFT    # Targeted tickers
python3.13 scripts/discover.py ndx100       # Preset
python3.13 scripts/discover.py ndx100 --top 10 --dp-days 5
```

Three modes: market-wide (flow alerts -> aggregate -> validate with DP), targeted (per-ticker flow + DP), preset (resolve to tickers). Scoring 0-100. Does NOT modify watchlist.

### Portfolio Command

`python3.13 scripts/portfolio_report.py` — self-contained: connects IB, fetches positions + prices + 5-day DP flow in parallel, fills HTML template, opens report. Output: `reports/portfolio-{date}.html`.

### Free Trade Command

`python3.13 scripts/free_trade_analyzer.py [--ticker X] [--table] [--summary] [--json]`

Supported: Synthetic Long/Short, Risk Reversal (bullish/bearish), Bull Call Spread, Bear Put Spread. Metrics: effective core cost, progress to free, breakeven close price.

### Risk Reversal Command

`python3.13 scripts/risk_reversal.py [TICKER] [--bearish] [--bankroll N] [--min-dte N] [--max-dte N]`

**⚠️ Manager Override:** Only strategy producing undefined-risk structures. Requires explicit human invocation.

### VCG Command (MANDATORY — NO DOC READS)

**Call `vcg_scan` Pi tool. Do NOT read docs.** The tool returns all data needed.

**Gates (VCG-R):** RO = VIX>28 + VCG>2.5 + sign_ok. EDR = VIX>25 + VCG 2.0-2.5 + sign_ok. BOUNCE = VCG<-3.5 + sign_ok. Panic suppression: VIX>=48 -> VCG adj=0.

**Decision:** RO Tier1 = max hedging. RO Tier2 = standard hedging. EDR = half-Kelly. BOUNCE = close puts. sign_suppressed = no trade.

## Evaluation Milestones

Always in order. Stop if gate fails.

1. **Validate Ticker** -> `fetch_ticker.py`
1B. **Seasonality** (context) | 1C. **Analyst Ratings** (context) | 1D. **News/Catalysts** (context)
2. **Dark Pool Flow** -> `fetch_flow.py`
3. **Options Flow** -> `fetch_options.py`
3B. **OI Changes** -> `fetch_oi_changes.py` (REQUIRED — $95M MSFT LEAPs found here, NOT in flow alerts)
4. **Edge Decision** — PASS/FAIL (stop if FAIL)
5. **Structure** — convex position (R:R < 2:1 = stop)
6. **Kelly Sizing** — enforce 2.5% cap
7. **Log Trade** — MANDATORY Post-Trade Logging (below)

### OI Change Analysis (M3B) — REQUIRED

```bash
python3.13 scripts/fetch_oi_changes.py MSFT                    # Per-ticker
python3.13 scripts/fetch_oi_changes.py --market --min-premium 10000000  # Market-wide
```

Signal: >$10M = MASSIVE, $5-10M = LARGE, $1-5M = SIGNIFICANT. Cross-ref: Large OI + no flow alert = hidden signal.

### Seasonality

Fetch from EquityClock charts. FAVORABLE (>60%), NEUTRAL (50-60%), UNFAVORABLE (<50%). Context only — strong flow overrides weak seasonality.

### Signal Interpretation

**P/C Ratio:** >2.0 BEARISH | 0.8-1.2 NEUTRAL | <0.5 BULLISH
**Flow Side:** Ask-dominant = buying | Bid-dominant = selling
**Analyst Buy%:** >=70% BULL | <30% BEAR
**Seasonality/ratings = context, not gates.**

---

## ⚠️ Portfolio Source of Truth (CRITICAL)

**IB is the ONLY source of truth.** Never claim position state from `docs/status.md` or `data/portfolio.json` — they go stale. ALWAYS verify via `python3.13 scripts/ib_sync.py`. When IB unavailable, say so explicitly.

## Output Format

Always show: signal -> structure -> Kelly math -> decision. State probabilities, flag uncertainty. Failing gate = immediate stop. Never rationalize bad trades. EXECUTED -> `trade_log.json`. NO_TRADE -> `docs/status.md`.

## Trade Specification Reports ⭐ REQUIRED

**MANDATORY for any eval reaching Structure milestone.**

Template: `.pi/skills/html-report/trade-specification-template.html` | Output: `reports/{ticker}-evaluation-{DATE}.html`

**10 sections:** Header+gates, 6 summary metrics, milestone pass/fail, DP flow, options flow, context (seasonality+ratings+news), structure+Kelly, trade spec, thesis+risk, Four Gates table.

## P&L Reports

Template: `.pi/skills/html-report/pnl-template.html` | Output: `reports/pnl-{TICKER}-{DATE}.html`

Return on Risk = P&L / Capital at Risk (debit=net debit, credit=width-credit, long=premium, stock=cost basis).

## ⚠️ GARCH Convergence -> ALWAYS Call `garch_convergence.py` (MANDATORY)

```bash
python3.13 scripts/garch_convergence.py --preset [PRESET]  # semis, mega-tech, energy, china-etf, all
python3.13 scripts/garch_convergence.py --preset sp500-semiconductors  # file presets
python3.13 scripts/garch_convergence.py NVDA AMD GOOGL META  # ad-hoc pairs
```

Output: `reports/garch-convergence-{preset}-{date}.html`. Strategy spec: `docs/strategy-garch-convergence.md`.

## FastAPI Server Architecture

Next.js routes call FastAPI (`localhost:8321`) via `radonFetch()` (`web/lib/radonApi.ts`).

**Three-Service Dev Stack** (`npm run dev`): Next.js :3000, IB WS relay :8765, FastAPI :8321.

**Auth (Clerk):** JWT middleware on all FastAPI routes (`scripts/api/auth.py`). WS uses ticket-based auth (30s TTL, single-use via `scripts/api/ws_ticket.py`). Localhost bypass for dev. Auth-exempt: `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`. Public share routes exempt.

**Degradation:** FastAPI down -> cached with `is_stale: true`. No spawn fallback.

**IB Gateway:** Cloud mode (default dev) = Hetzner VPS via Tailscale `ib-gateway:4001`, TCP probe only. Docker mode = local `scripts/docker_ib_gateway.sh`, autoheal sidecar.

| FastAPI File | Purpose |
|------|---------|
| `scripts/api/server.py` | 26 endpoints, CORS, Clerk JWT, IB pool, health |
| `scripts/api/auth.py` | Clerk JWT via JWKS, `ALLOWED_USER_IDS` allowlist |
| `scripts/api/ws_ticket.py` | Single-use WS tickets (30s TTL) |
| `scripts/api/ib_pool.py` | Role-based IB pool (sync=3, orders=4, data=5) |
| `scripts/api/ib_gateway.py` | Health check — cloud (TCP), docker (compose), launchd (IBC) |
| `scripts/api/subprocess.py` | Async subprocess helper |

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/evaluate.py` | **⭐ Unified evaluation — all 7 milestones in parallel (ALWAYS USE)** |
| `scripts/ib_execute.py` | **⭐ Place order + monitor + log (ALWAYS USE)** |
| `scripts/garch_convergence.py` | **⭐ GARCH convergence scanner + HTML report** |
| `scripts/risk_reversal.py` | **⭐ Risk reversal scanner + HTML report** |
| `scripts/scenario_analysis.py` | **⭐ Stress test pricing engine** |
| `scripts/scenario_report.py` | **⭐ Stress test HTML report generator** |
| `scripts/context_constructor.py` | **⭐ Context pipeline: persistent memory load/save** |
| `scripts/fetch_oi_changes.py` | **⭐ OI changes — hidden institutional positioning (REQUIRED)** |
| `scripts/scanner.py` | Watchlist batch scan (ThreadPoolExecutor) |
| `scripts/discover.py` | Market-wide/targeted/preset discovery |
| `scripts/fetch_ticker.py` | Validate ticker |
| `scripts/fetch_flow.py` | Dark pool + options flow |
| `scripts/fetch_options.py` | Options chain + institutional flow (IB > UW > Yahoo) |
| `scripts/fetch_analyst_ratings.py` | Analyst ratings + targets |
| `scripts/fetch_news.py` | News headlines, catalyst classification, sentiment |
| `scripts/kelly.py` | Kelly criterion calculator |
| `scripts/ib_sync.py` | Sync live portfolio from IB (atomic writes) |
| `scripts/ib_reconcile.py` | Reconcile fills vs trade_log |
| `scripts/ib_place_order.py` | JSON-in/out order placement (client ID 26) |
| `scripts/ib_order_manage.py` | Cancel/modify open orders (reconnects as original clientId) |
| `scripts/blotter.py` | Trade blotter — fills, P&L, spread grouping |
| `scripts/portfolio_report.py` | HTML portfolio report |
| `scripts/free_trade_analyzer.py` | Free trade opportunity analysis |
| `scripts/leap_scanner_uw.py` | LEAP IV mispricing scanner (UW-based) |
| `scripts/cri_scan.py` | Crash Risk Index |
| `scripts/vcg_scan.py` | Vol-Credit Gap scanner |
| `scripts/ib_realtime_server.js` | WS relay — batched, 100ms flush, ticket auth |
| `scripts/monitor_daemon/run.py` | Extensible monitoring daemon |
| `scripts/utils/presets.py` | Preset loader for 150 ticker presets |

## ⚠️ Order Execution (CRITICAL)

**ALWAYS use `ib_execute.py`.** Auto: places order, monitors fills, logs to `trade_log.json`.

```bash
# Stock
python3.13 scripts/ib_execute.py --type stock --symbol NFLX --qty 4500 --side SELL --limit 98.70 --yes

# Option
python3.13 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 315 --right C --qty 10 --side BUY --limit MID --yes
```

Limit: `MID`, `BID`, `ASK`, or exact price. Flags: `--yes`, `--dry-run`, `--timeout N`, `--no-log`, `--thesis "..."`, `--notes "..."`.

## ⚠️ Post-Trade Logging (MANDATORY — NO EXCEPTIONS)

**After ANY fill — ALL THREE steps IMMEDIATELY. Do NOT respond "done" until complete.**

### Step 1: trade_log.json
Append with: `id`, `date`, `time`, `ticker`, `company_name`, `contract`, `structure`, `action`, `decision`, `order_id`, `quantity`, `fill_price`, `total_cost`, `max_risk`, `max_gain`, `pct_of_bankroll`, `edge_analysis`, `kelly_calculation`, `gates_passed`, `target_exit`, `stop_loss`, `notes`. Validate: `python3.13 -m json.tool data/trade_log.json`.

### Step 2: docs/status.md (ALWAYS)
Update: Last Updated, Today's Trades table, Trade Log Summary, Logged Position Thesis Check (full thesis block), Rule Violations, Current Portfolio State.

### Step 3: Validate JSON integrity

**Triggers:** `ib_execute.py` fill, inline combo fill, reconciliation `needs_attention: true`, manual fill confirmation.

**If you say "FILLED" without updating status.md, you have failed this process.**

---

## Interactive Brokers Integration

### Gateway Modes (`IB_GATEWAY_MODE`)

| Mode | Behavior |
|------|----------|
| `docker` (default) | Local Docker Compose + autoheal sidecar. `POST /ib/restart` runs `docker compose restart`. |
| `cloud` | Hetzner VPS via Tailscale `ib-gateway:4001`. TCP probe only. No local restart. |
| `launchd` (legacy) | IBC/launchd scripts. Full auto-restart. |

Health: `curl http://localhost:8321/health` -> `ib_gateway`, `ib_pool`, `uw`, `test_mode`.

### Client ID Ranges

| Range | Usage |
|-------|-------|
| 0-2 | Reserved (avoid) |
| 3-5 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10-19 | WS relay (rotates on conflict) |
| 20-49 | Subprocess scripts (`client_id="auto"`) |
| 50-69 | Scanners (CRI/VCG) |
| 70-89 | Daemons (fill=70, exit=71) |
| 90-99 | CLI/standalone |

**Critical: IB clientId scoping.** Cancel/modify scoped by placing clientId. Master (clientId=0) can SEE but CANNOT cancel/modify (Error 10147/103). `ib_order_manage.py` reconnects as original clientId. Never route cancel/modify through pool.

### Auto-Recovery

**Cloud mode:** No restart. Subprocess errors set 15s cooldown. TCP probe only. **Docker/launchd:** Detect error -> verify port -> restart if down -> reconnect pool -> retry once.

**Recovery layers:** Pool retry (3x, 2s backoff) > IBClient reconnect (5x, exponential) > WS relay reconnect (5s, client ID rotation) > Stale tick detection (45s) > Cached fallback (`is_stale: true`).

### Portfolio Sync

```bash
python3.13 scripts/ib_sync.py          # Display
python3.13 scripts/ib_sync.py --sync   # Save to portfolio.json
```

**Structure detection:** Covered calls, verticals, synthetics, risk reversals, straddles, all-long combos. All-long combos (no shorts, no stock) = `defined` risk. Unrecognized = `complex` -> Undefined Risk table.

### Startup Protocol

Extension `.pi/extensions/startup-protocol.ts` runs 6+ checks with numbered progress:

| # | Process | Description |
|---|---------|-------------|
| 1 | market | Market hours check (9:30-16:00 ET) |
| 2 | docs | Load project docs + skills + memory (NF/NE/NH) |
| 3 | ib | IB trade reconciliation |
| 4 | free_trade | Free trade scan (waits for IB) |
| 5 | daemon | Monitor daemon status |
| 6+ | x_{account} | X account scans (parallel) |

### ⚠️ Auto-Reconciliation Rule (MANDATORY)

When IB sync detects new trades (`needs_attention: true`), IMMEDIATELY:
1. Read `data/reconciliation.json`
2. Log each trade to `data/trade_log.json` with `validation_method: "ib_reconciliation"`
3. Update `docs/status.md` (Trade Log Summary, Today's Trades, Portfolio State, violations)
4. Clear: set `needs_attention: false`, move to `processed_trades`
5. Validate JSON

**Automatic — do NOT wait for user request.**

### Exit Order Service

Monitors `PENDING_MANUAL` exit orders, places when spread is within 40% of target. Runs at startup. Manual: `python3.13 scripts/exit_order_service.py [--status|--dry-run|--daemon]`.

### Real-Time Price Streaming

WS relay `scripts/ib_realtime_server.js` on :8765. Protocol: subscribe/unsubscribe/snapshot. React hook: `usePrices()`. API: `POST /api/prices` (snapshot only, GET deprecated).

---

## LEAP IV Mispricing Scanner

```bash
python3.13 scripts/leap_scanner_uw.py AAPL MSFT NVDA          # Ad-hoc
python3.13 scripts/leap_scanner_uw.py --preset sectors         # Built-in
python3.13 scripts/leap_scanner_uw.py --preset sp500-semiconductors  # File preset
python3.13 scripts/leap_scanner_uw.py --list-presets           # List all 150
```

**Built-in presets:** sectors (11), mag7 (7), semis (9), emerging (8), china (9), row (45, with sub-regions), metals (23), energy (24).

**File presets (`data/presets/`):** 150 files across 3 indices, 2,446 unique tickers. SP500: 503 tickers, 111 presets (99 sub-industry + 11 sector + master). NDX100: 101 tickers, 22 presets (21 thematic + master). R2K: 1,929 tickers, 17 presets (11 sector + 5 tier + master). Use `--list-presets` for full list.

**Preset loader:** `from utils.presets import load_preset` -> `.tickers`, `.pairs`, `.vol_driver`, `.group_tickers()`, `.group_pairs()`.

---

## Monitor Daemon

Handlers: `fill_monitor` (60s, market hours), `exit_orders` (300s, market hours), `preset_rebalance` (weekly), `flex_token_check` (daily). State: `data/daemon_state.json`. Logs: `logs/monitor-daemon.log`. Launchd: `scripts/setup_monitor_daemon.sh install`.

## Flex Query (Historical Trades)

Real-time API = today's fills only. Flex Query = up to 365 days. Requires `IB_FLEX_TOKEN` and `IB_FLEX_QUERY_ID` env vars. Setup: IB Account Management > Reports > Flex Queries. Usage: `python3.13 scripts/trade_blotter/flex_query.py [--symbol X] [--json]`.

## X Account Scan

**xAI API** (recommended): `python3.13 scripts/fetch_x_xai.py --account USERNAME [--days 7] [--dry-run]`. Requires `XAI_API_KEY`. High quality, slow (2-3 min).

**Browser** (fallback): `python3.13 scripts/fetch_x_watchlist.py [--account USERNAME]`. Faster, lower quality.

Output: tickers, sentiment (BULLISH/BEARISH/NEUTRAL), confidence (HIGH/MEDIUM/LOW).

---

## Context Engineering (Persistent Memory)

File-system context repo (`context/`). `scripts/context_constructor.py` runs at startup, assembles token-budgeted payload (8000 tokens default).

| Directory | Type | Lifecycle |
|-----------|------|-----------|
| `context/memory/fact/` | Atomic facts | Permanent, deduplicated |
| `context/memory/episodic/` | Session summaries | 1 year retention |
| `context/memory/experiential/` | Action->outcome | Permanent |
| `context/human/` | Human overrides | Permanent, highest priority |
| `context/history/` | Transaction log | Append-only |

Save facts after: evaluation lessons, API quirks, portfolio changes, pattern recognition. Priority: Human > Facts > Episodic > Experiential.

---

## Data Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions (cache from IB) |
| `data/trade_log.json` | Executed trades (append-only) |
| `data/watchlist.json` | Tickers under surveillance |
| `data/strategies.json` | Strategy registry (sync with `docs/strategies.md`) |
| `data/ticker_cache.json` | Ticker -> company name |
| `data/presets/` | 150 strategy-agnostic ticker presets (SP500/NDX100/R2K) |
| `data/menthorq_cache/` | MenthorQ CTA cache (daily) |
| `context/memory/` | Persistent memory (facts, episodic, experiential) |
| `context/human/` | Human annotations (highest priority) |

## Documentation

| File | Purpose |
|------|---------|
| `docs/prompt.md` | Spec, constraints, deliverables |
| `docs/plans.md` | Milestone workflow |
| `docs/implement.md` | Execution runbook |
| `docs/status.md` | Current state, decisions, audit log |
| `docs/strategies.md` | Source of truth for all 6 strategies |
| `docs/options-structures.json` | 58 structures, guard decisions, P&L attribution |
| `docs/options-flow-verification.md` | OI verification methodology |
| `docs/unusual_whales_api.md` | UW API reference |
| `docs/unusual_whales_api_spec.yaml` | Full UW OpenAPI spec |

## UW API Reference

Base: `https://api.unusualwhales.com` | Auth: `Bearer $UW_TOKEN`

| Endpoint | Purpose |
|----------|---------|
| `GET /api/darkpool/{ticker}` | Dark pool (primary edge) |
| `GET /api/option-trades/flow-alerts` | Sweeps, blocks |
| `GET /api/stock/{ticker}/info` | Validation |
| `GET /api/stock/{ticker}/option-contracts` | Chain |
| `GET /api/stock/{ticker}/greek-exposure` | GEX |
| `GET /api/screener/analysts` | Ratings |
| `GET /api/seasonality/{ticker}/monthly` | Seasonality |
| `GET /api/shorts/{ticker}/interest-float/v2` | Short interest |

Full docs: `docs/unusual_whales_api.md`

## Discovery Scoring (0-100 Scale)

| Component | Weight | Measure |
|-----------|--------|---------|
| DP Strength | 30% | Flow imbalance (0-100) |
| DP Sustained | 20% | Consecutive days same direction |
| Confluence | 20% | Options + DP alignment |
| Vol/OI Ratio | 15% | Unusual volume indicator |
| Sweeps | 15% | Urgency signal |

60-100 = Strong (evaluate), 40-59 = Moderate (monitor), 20-39 = Weak, 0-19 = No signal.

**OI Change Discovery:** `python3.13 scripts/fetch_oi_changes.py --market --min-premium 10000000` — surfaces positions not in flow alerts.

## Tools Available

- `bash` — Run Python scripts in ./scripts/
- `read`/`write`/`edit` — Manage data and docs
- `kelly_calc` — Built-in fractional Kelly calculator
- `exa` — Web search (primary)
- `agent-browser` — Browser automation (fallback)

## Skills

| Skill | Purpose |
|-------|---------|
| `options-analysis` | Options pricing and structure analysis |
| `web-fetch` | Exa (primary) + browser automation (fallback) |
| `browser-use-cloud` | AI browser agent |
| `html-report` | Styled HTML reports (Terminal theme) |
| `context-engineering` | Persistent memory, token budget |
| `tweet-it` | Tweet copy + infographic card |
| `fast-regex-search` | Sparse n-gram indexed regex (auto-loaded) |
