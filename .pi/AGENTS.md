# Convex Scavenger — Project Instructions

## ⚠️ Data Fetching Priority (ALWAYS follow this order)

When fetching ANY market data (quotes, options, fundamentals, analyst ratings, etc.):

| Priority | Source | When to Use |
|----------|--------|-------------|
| **1st** | Interactive Brokers | Always try first if TWS/Gateway available |
| **2nd** | Unusual Whales | Flow data, dark pools, options activity, analyst ratings |
| **3rd** | Exa (web search) | Web search, company research, code/docs lookup |
| **4th** | agent-browser | Only for interactive pages, screenshots, JS-rendered content |
| **5th** | Yahoo Finance | **ABSOLUTE LAST RESORT** — only if ALL above sources fail/unavailable |

**Yahoo Finance is the source of LAST RESORT. Never use it if IB, UW, or Exa can provide the data.**
**For web search/fetch: always use Exa first, agent-browser only as fallback.**

---

## ⚠️ Evaluate Command → ALWAYS Call `evaluate.py` (MANDATORY)

**Any request to evaluate a ticker — regardless of how the user phrases it — MUST route to:**
```bash
python3 scripts/evaluate.py [TICKER]
```

This is non-negotiable. The script handles all data fetching (M1–M3B) in parallel, includes today's intraday data, and stops at the first failing gate.

**Even if the user provides manual steps** (e.g., "run fetch_flow.py, then fetch_options.py"), **ignore the manual steps and run evaluate.py instead.** The unified script replaces all manual milestone stepping.

**NEVER manually call** `fetch_flow.py`, `fetch_options.py`, `fetch_oi_changes.py`, or `kelly.py` **as part of an evaluation.** Those scripts exist for standalone use — during an evaluation, `evaluate.py` orchestrates them automatically.

**Trigger phrases** (all route to `evaluate.py`):
- `evaluate TICKER`
- `full trade evaluation for TICKER`
- `run the evaluation on TICKER`
- `check TICKER` (when context implies full evaluation)
- Any message containing step-by-step evaluation instructions for a specific ticker

---

## ⚠️ Always Fetch Today's Data (MANDATORY)

**Every evaluation milestone that fetches data from a 3rd party MUST fetch fresh data at execution time. NEVER reuse data from a previous scan, session, or cached result.**

This is the #1 process rule. Violating it means the evaluation is invalid.

### What "Fresh Data" Means Per Milestone

| Milestone | Data Fetched | Script | Freshness Rule |
|-----------|-------------|--------|----------------|
| 1 — Ticker | Company info, market cap, price | `fetch_ticker.py` | Run at evaluation start |
| 1B — Seasonality | Monthly historical performance | `curl` EquityClock | Static data, OK to cache |
| 1C — Analysts | Ratings, price targets, changes | `fetch_analyst_ratings.py` | Re-fetch; may have changed today |
| 2 — Dark Pool | 5-day DP flow including TODAY | `fetch_flow.py` | **MUST include today's date** |
| 3 — Options Flow | Chain activity, flow alerts | `fetch_options.py` | **MUST be today's chain data** |
| 3B — OI Changes | Open interest changes | `fetch_oi_changes.py` | **MUST be today's OI snapshot** |
| 4 — Edge | Price action (today's close/last) | IB `reqHistoricalData` | **MUST include today's bar** |
| 5 — Structure | Live option quotes (bid/ask/mid) | IB `reqMktData` | **MUST be real-time or today's close** |

### Why This Matters

A scan from earlier in the day (or yesterday) may show ACCUMULATION. But today's dark pool could show DISTRIBUTION — completely reversing the signal. Using stale data leads to trades against current institutional flow.

**Real example (AAPL Mar 5):** Earlier scan showed 81.3% buy ratio through Mar 4. But Mar 5 data was never fetched — if Mar 5 was another distribution day, the sustained streak drops to 0 and edge fails.

### Rules

1. **OPEN market**: Fetch fresh data before EACH milestone. Do not reuse scan results.
2. **CLOSED market**: Use most recent closing data. Note this in output.
3. **Multi-ticker scans**: Batch fetch where possible (e.g., UW flow-alerts supports multiple tickers).
4. **Cache TTL during market hours**: 5 minutes max for flow data, 15 minutes for analyst ratings.
5. **Scan data ≠ evaluation data**: A `scan` provides CANDIDATES. When you `evaluate`, re-fetch everything — the scan data is only a lead, not evidence.

### Verification

Every evaluation output MUST include a **Data Freshness** line showing:
```
📊 Data as of: 2026-03-05 10:45 AM ET (LIVE)
— or —
📊 Data as of: 2026-03-04 4:00 PM ET (CLOSED — using closing data)
```

If any milestone uses data older than today, flag it:
```
⚠️ STALE DATA: Dark pool flow only through Mar 4 — Mar 5 not yet fetched
```

**Market Hours:**
- US Options: **9:30 AM - 4:00 PM Eastern Time**, Monday-Friday
- Utility: `scripts/utils/market_hours.py` provides `is_market_open()`, `get_market_status()`, `get_last_market_close()`

**Startup Protocol Market Check:**
The startup protocol automatically checks market status and shows in the first line:
- Market OPEN: `[1/N] ✓ Market OPEN (Xh Ym to close)`
- Market CLOSED: `[1/N] ⚠️ Market CLOSED (after hours) — using closing prices`

**Free Trade Progress During Closed Market:**
When market is closed, free trade analysis explicitly shows it's using closing prices:
```
💰 FREE TRADE PROGRESS (closing prices as of Mar 04 16:00 ET)
```

**Implementation:**
- Scripts should import `from utils.market_hours import is_market_open, get_market_status`
- Include timestamp of data fetch in all analysis output
- If IB connection unavailable during market hours, fall back to UW/Exa. Yahoo Finance is absolute last resort.

---

## Workflow Commands

| Command | Action |
|---------|--------|
| `evaluate [TICKER]` | **Run `python3 scripts/evaluate.py [TICKER]`** — full 7-milestone evaluation |
| `scan` | Scan watchlist for dark pool flow signals |
| `discover` | Find new candidates from market-wide options flow |
| `portfolio` | **Generate HTML portfolio report and open in browser** |
| `free-trade` | Analyze positions for free trade opportunities |
| `journal` | View recent trade log entries |
| `sync` | Pull live portfolio from Interactive Brokers |
| `blotter` | Trade blotter - today's fills, P&L, spread grouping |
| `strategies` | List available trading strategies (reads `data/strategies.json`) |

### Evaluate Command Details

When user runs `evaluate [TICKER]`, ALWAYS:
1. Run `python3 scripts/evaluate.py [TICKER]` — this fetches ALL data in parallel
2. Read the output (text report or `--json`)
3. If decision is `NO_TRADE`: log to `docs/status.md` under Recent Evaluations
4. If decision is `PENDING` (edge passed): design structure using live IB quotes, run Kelly, generate trade spec HTML report, present for confirmation
5. If decision is `TRADE` (after user confirms): execute via `ib_execute.py`, log to `trade_log.json`

```bash
# Standard evaluation (human-readable output)
python3 scripts/evaluate.py AAPL

# JSON output (for programmatic use)
python3 scripts/evaluate.py AAPL --json

# Custom bankroll
python3 scripts/evaluate.py AAPL --bankroll 1200000
```

**What the script does automatically:**
- Fetches M1 (ticker), M1B (seasonality), M1C (analysts), M2 (dark pool), M3 (options), M3B (OI changes) **in parallel**
- Fetches IB price history on main thread (ib_insync requirement)
- Always includes **today's** intraday dark pool data
- Runs edge determination (M4) against all fetched data
- Stops at first failing gate — no wasted API calls
- Returns structured `EvaluationResult` with full audit trail

**What you do after the script runs:**
- If `NO_TRADE`: copy the failing gate + reason into `docs/status.md`
- If `PENDING`: fetch live option quotes from IB, design structure, calculate Kelly, generate HTML trade spec report, present to user

**NEVER manually step through milestones 1-3B.** The script handles all parallel fetching. Only intervene for M5 (structure) and M6 (Kelly) which require interactive IB quotes and operator judgment.

### Portfolio Command Details

When user runs `portfolio`, ALWAYS:
1. Sync latest data from IB (if connected)
2. Generate HTML report via `python3 scripts/portfolio_report.py`
3. Report opens automatically in browser
4. Output location: `reports/portfolio-{date}.html`

```bash
# Generate and open report
python3 scripts/portfolio_report.py

# Sync from IB first, then generate
python3 scripts/portfolio_report.py --sync

# Generate without opening
python3 scripts/portfolio_report.py --no-open
```

### Free Trade Command

Analyze multi-leg positions to find opportunities to close hedge legs profitably, making the core leg "free" (zero net cost).

```bash
# Full analysis of all positions
python3 scripts/free_trade_analyzer.py

# Filter by ticker
python3 scripts/free_trade_analyzer.py --ticker EWY

# Compact table format (used by startup protocol)
python3 scripts/free_trade_analyzer.py --table

# Brief one-line summary
python3 scripts/free_trade_analyzer.py --summary

# JSON output
python3 scripts/free_trade_analyzer.py --json
```

**Supported Structures:**
| Structure | Core Leg | Hedge Leg | Notes |
|-----------|----------|-----------|-------|
| Synthetic Long | Long Call | Short Put | Same strike (behaves like stock) |
| Synthetic Short | Long Put | Short Call | Same strike (behaves like short stock) |
| Risk Reversal (Bullish) | Long Call | Short Put | Different strikes |
| Risk Reversal (Bearish) | Long Put | Short Call | Different strikes |
| Bull Call Spread | Long Call (lower) | Short Call (higher) | |
| Bear Put Spread | Long Put (higher) | Short Put (lower) | |

**Output Metrics:**
- **Effective Core Cost**: Core entry cost - Hedge P&L
- **Progress to Free**: Percentage of core cost covered by hedge profit
- **Breakeven Close Price**: Price to close hedge to make core free

**Progress Status Icons:**
| Icon | Status | Progress |
|------|--------|----------|
| 🎉 FREE | Position is free | 100% |
| ⚡ Near | Near free | ≥50% |
| 🔄 Progress | Making progress | 25-49% |
| ⏳ Early | Early stage | <25% |

**Startup Integration:**
- Runs automatically on Pi startup
- Shows ALL multi-leg positions in compact table format
- Table includes: Ticker, Progress %, Status icon

| `blotter-history` | Historical trades via Flex Query (requires setup) |
| `leap-scan [TICKERS]` | Scan for LEAP IV mispricing opportunities |
| `garch-convergence [TICKERS]` | Cross-asset GARCH vol divergence scan |
| `seasonal [TICKERS]` | Seasonality assessment for one or more tickers |
| `x-scan [@ACCOUNT]` | Fetch tweets via xAI API (recommended, slower) |
| `x-scan-browser [@ACCOUNT]` | Fetch tweets via browser scraping (faster, lower quality) |
| `analyst-ratings [TICKERS]` | Fetch analyst ratings, changes, and price targets |

## Evaluation Milestones

Always follow in order. Stop immediately if a gate fails.

1. **Validate Ticker** → `python3 scripts/fetch_ticker.py [TICKER]`
1B. **Seasonality** → Fetch & analyze (does not affect score, but report in analysis)
1C. **Analyst Ratings** → `python3 scripts/fetch_analyst_ratings.py [TICKER]` (context, not a gate)
2. **Dark Pool Flow** → `python3 scripts/fetch_flow.py [TICKER]`
3. **Options Flow** → `python3 scripts/fetch_options.py [TICKER]`
3B. **OI Change Analysis** → `python3 scripts/fetch_oi_changes.py [TICKER]` (ALWAYS — reveals hidden institutional positioning)
4. **Edge Decision** → PASS/FAIL with reasoning (stop if FAIL)
5. **Structure** → Design convex position (stop if R:R < 2:1)
6. **Kelly Sizing** → Calculate + enforce caps
7. **Log Trade** → Append executed trades only to trade_log.json (NO_TRADE decisions go to status.md)

## OI Change Analysis (Milestone 3B) — REQUIRED

**When to use:** EVERY evaluation. This is not optional.

**Why:** UW has TWO separate data sources:
1. **Flow Alerts** — Filtered for "unusual" activity (may miss large trades)
2. **OI Changes** — Raw positioning data (shows ALL significant activity)

**The $95M MSFT LEAP call purchase appeared in OI changes but NOT in flow alerts.** This is why OI checking is mandatory.

```bash
# Per-ticker OI changes (ALWAYS run this)
python3 scripts/fetch_oi_changes.py MSFT

# Filter for significant positions
python3 scripts/fetch_oi_changes.py MSFT --min-premium 1000000

# Market-wide scan (for discover command)
python3 scripts/fetch_oi_changes.py --market --min-premium 10000000

# Verify specific external claims
python3 scripts/verify_options_oi.py MSFT --expiry 2027-01-15 --verify "575:50000,625:100000"
```

**Signal Strength Classification:**
| Premium | Signal |
|---------|--------|
| > $10M | 🚨 MASSIVE |
| $5-10M | LARGE |
| $1-5M | SIGNIFICANT |
| < $1M | MODERATE |

**Cross-Reference with Flow Alerts:**
| Scenario | Interpretation |
|----------|----------------|
| Large OI change + Flow alert | ✅ Confirmed signal |
| Large OI change + NO flow alert | ⚠️ **Hidden signal — investigate** |
| Flow alert + Small OI change | Day trade, not positioning |

See `docs/options-flow-verification.md` for full methodology.

## Seasonality Data

Fetch monthly performance data from EquityClock:
```bash
curl -s -o /tmp/{TICKER}_sheet.png "https://charts.equityclock.com/seasonal_charts/{TICKER}_sheet.png"
```

**Rating Criteria:**
| Rating | Win Rate | Avg Return |
|--------|----------|------------|
| FAVORABLE | >60% | >5% |
| NEUTRAL | 50-60% | 0-5% |
| UNFAVORABLE | <50% | <0% |

Seasonality is CONTEXT, not a gate. Strong flow can override weak seasonality, but weak flow + weak seasonality = pass.

## X Account Scan

Two methods to fetch tweets and extract ticker sentiment:

### Method 1: xAI API (Recommended)

Uses xAI's Grok with x_search tool for high-quality analysis.

```bash
# Scan default account (@aleabitoreddit)
python3 scripts/fetch_x_xai.py

# Scan specific account
python3 scripts/fetch_x_xai.py --account elonmusk

# Look back 7 days
python3 scripts/fetch_x_xai.py --days 7

# Dry run (don't update watchlist)
python3 scripts/fetch_x_xai.py --dry-run

# Raw JSON output
python3 scripts/fetch_x_xai.py --json
```

**Requires:** `XAI_API_KEY` environment variable

**Pros:** High quality sentiment analysis, source citations, detailed explanations
**Cons:** Slow (2-3 minutes), may timeout under load

### Method 2: Browser Scraping (Fallback)

Uses browser automation to scrape X profile pages.

```bash
# Scan default account
python3 scripts/fetch_x_watchlist.py

# Scan specific account  
python3 scripts/fetch_x_watchlist.py --account elonmusk
```

**Requires:** `agent-browser` CLI

**Pros:** Faster, no API limits
**Cons:** Lower quality parsing, limited context, sentiment less reliable

### Startup Protocol

- Browser scraper runs on **every startup** for all X accounts in watchlist
- Runs asynchronously (non-blocking)
- Shows tweet count when complete: `@account: N tweets`
- For high-quality analysis, run `x-scan` manually

**Output:**
- Extracts tickers mentioned in tweets (via $TICKER cashtags)
- Determines sentiment: BULLISH / BEARISH / NEUTRAL
- Rates confidence: HIGH / MEDIUM / LOW
- Updates watchlist subcategory with new/updated tickers

---

## Options Flow Command

Fetch options chain activity and institutional flow alerts.

**Data Sources (following standard priority):**
1. Interactive Brokers - spot price, expirations, strikes
2. Unusual Whales - chain volume/premium, flow alerts, sweeps (primary)
3. Yahoo Finance - **LAST RESORT ONLY** if IB and UW both fail

```bash
# Standard analysis
python3 scripts/fetch_options.py RMBS

# JSON output for programmatic use
python3 scripts/fetch_options.py RMBS --json

# Force specific data source
python3 scripts/fetch_options.py RMBS --source uw
python3 scripts/fetch_options.py RMBS --source ib
python3 scripts/fetch_options.py RMBS --source yahoo

# Custom IB port
python3 scripts/fetch_options.py RMBS --port 7497

# Filter by DTE
python3 scripts/fetch_options.py RMBS --dte-min 14 --dte-max 60
```

**Output Includes:**

*Chain Activity:*
- Call/Put premium breakdown
- Volume and open interest
- Bid-side vs ask-side volume (buyer/seller pressure)
- Top active contracts with IV
- Put/Call ratio and chain bias

*Institutional Flow Alerts:*
- Recent flow alerts (sweeps, blocks, unusual activity)
- Bid-side (selling) vs ask-side (buying) premium
- Sweep premium (urgency indicator)
- Flow bias and strength score (0-100)

*Combined Analysis:*
- Chain bias + Flow bias synthesis
- Conflict detection (when signals disagree)
- Confidence rating: HIGH / MEDIUM / LOW

**Bias Interpretation:**

| Put/Call Ratio | Bias |
|----------------|------|
| >2.0x | BEARISH |
| 1.2-2.0x | LEAN_BEARISH |
| 0.8-1.2x | NEUTRAL |
| 0.5-0.8x | LEAN_BULLISH |
| <0.5x | BULLISH |

**Flow Side Meaning:**
- **Bid-side dominant**: Trades at/below mid = selling pressure (closing longs OR opening shorts)
- **Ask-side dominant**: Trades at/above mid = buying pressure (opening longs)

Options flow is used to CONFIRM or CONTRADICT dark pool signals. Conflicting chain/flow signals reduce confidence.

---

## Analyst Ratings Command

Fetch analyst ratings, recent rating changes, and price targets.

**Data Sources (following standard priority):**
1. Interactive Brokers (`RESC` fundamental data) - requires Reuters subscription
2. Unusual Whales (`/api/screener/analysts`) - aggregates per-firm consensus, targets, history
3. Yahoo Finance - **ABSOLUTE LAST RESORT** — only if IB AND UW both fail (rate limited, unreliable)

```bash
# Scan specific tickers (auto-detects IB, falls back to Yahoo)
python3 scripts/fetch_analyst_ratings.py AAPL MSFT NVDA

# Scan all watchlist tickers
python3 scripts/fetch_analyst_ratings.py --watchlist

# Scan all portfolio positions
python3 scripts/fetch_analyst_ratings.py --portfolio

# Scan both watchlist and portfolio
python3 scripts/fetch_analyst_ratings.py --all

# Only show tickers with recent changes (upgrades/downgrades)
python3 scripts/fetch_analyst_ratings.py --portfolio --changes-only

# Update watchlist.json with analyst rating data
python3 scripts/fetch_analyst_ratings.py --watchlist --update-watchlist

# Force specific data source
python3 scripts/fetch_analyst_ratings.py AAPL --source yahoo  # LAST RESORT ONLY
python3 scripts/fetch_analyst_ratings.py AAPL --source ib

# Custom IB port
python3 scripts/fetch_analyst_ratings.py --portfolio --port 7497

# Bypass cache
python3 scripts/fetch_analyst_ratings.py AAPL --no-cache

# Output raw JSON
python3 scripts/fetch_analyst_ratings.py AAPL --json
```

**Output Includes:**
- Recommendation (Strong Buy → Sell)
- Buy/Hold/Sell percentage breakdown
- Analyst count (confidence indicator)
- Mean price target and upside/downside %
- Recent rating distribution changes
- Upgrade/downgrade history (firm, action, date)

**Signal Interpretation:**

| Buy % | Direction | Notes |
|-------|-----------|-------|
| ≥70% | BULLISH | Strong consensus |
| 50-69% | LEAN_BULLISH | Positive bias |
| 30-49% | LEAN_BEARISH | Negative bias |
| <30% | BEARISH | Strong negative consensus |

| Analyst Count | Confidence |
|---------------|------------|
| ≥20 | HIGH |
| 10-19 | MEDIUM |
| <10 | LOW |

**Changes Signal:**
- `UPGRADING` — Net increase in Buy/Strong Buy ratings
- `DOWNGRADING` — Net increase in Sell/Strong Sell ratings

Analyst ratings are CONTEXT, not a gate. Use for:
- Confirming or questioning flow signals
- Identifying contrarian opportunities (strong flow vs. weak ratings)
- Monitoring positions for sentiment shifts

---

## Seasonal Command

Usage: `seasonal [TICKER]` or `seasonal [TICKER1] [TICKER2] ...`

**Process:**
1. Download chart: `curl -s -o /tmp/{TICKER}_sheet.png "https://charts.equityclock.com/seasonal_charts/{TICKER}_sheet.png"`
2. Read image and extract monthly data table
3. Identify current month and next 2-3 months
4. Assign rating (FAVORABLE / NEUTRAL / UNFAVORABLE)
5. Output summary table with actionable context

**Output includes:**
- Current month: win rate, avg return, max, min
- Next 2-3 months outlook (for hold-through scenarios)
- Best/worst months of year
- Rating with reasoning

---

## Trade Blotter Command

Fetch and reconcile trades from Interactive Brokers. Calculates P&L deterministically including all commissions/fees.

```bash
# Today's trades with spread grouping
python3 scripts/blotter.py

# P&L summary only
python3 scripts/blotter.py --summary

# JSON output for programmatic use
python3 scripts/blotter.py --json

# Show execution details
python3 scripts/blotter.py --verbose

# Custom IB port
python3 scripts/blotter.py --port 7497
```

**Output Includes:**
- All today's fills grouped by contract
- Spread identification (put spreads, call spreads, risk reversals)
- Combined P&L for multi-leg spreads
- Commission totals
- Open vs closed position status

**Spread Types Detected:**
| Pattern | Name |
|---------|------|
| Long higher strike put + Short lower strike put | Put Spread |
| Short higher strike put + Long lower strike put | Put Spread (Bull) |
| Long call + Short call (same expiry) | Call Spread |
| Short put + Long call (same expiry) | Risk Reversal |
| Long put + Short call (same expiry) | Collar |

**P&L Calculation:**
- Cash flow = notional value ± commission (buy = negative, sell = positive)
- Realized P&L = sum of all cash flows for closed positions
- Commissions are always subtracted from cash flow
- All calculations use Decimal for precision

**Integration Tests:**
```bash
python3 scripts/trade_blotter/test_integration.py
```

---

## Monitor Daemon Service

A single extensible daemon that handles all background monitoring tasks.

### Handlers

| Handler | Interval | Purpose |
|---------|----------|---------|
| `fill_monitor` | 60s | Detect order fills, send notifications |
| `exit_orders` | 300s | Place pending exit orders when IB accepts them |
| `preset_rebalance` | Weekly | Check SP500/NDX100/R2K for constituent changes, update presets |

### Commands

```bash
# Status
python3 -m monitor_daemon.run --status

# Run once (for testing)
python3 -m monitor_daemon.run --once

# Run as daemon
python3 -m monitor_daemon.run --daemon

# List available handlers
python3 -m monitor_daemon.run --list-handlers
```

### Service Management

```bash
# Install launchd service (runs every 60s during market hours)
./scripts/setup_monitor_daemon.sh install

# Check status
./scripts/setup_monitor_daemon.sh status

# View logs
./scripts/setup_monitor_daemon.sh logs

# Test run
./scripts/setup_monitor_daemon.sh test

# Uninstall
./scripts/setup_monitor_daemon.sh uninstall
```

### Adding New Handlers

1. Create `scripts/monitor_daemon/handlers/my_handler.py`
2. Inherit from `BaseHandler`
3. Implement `execute()` method
4. Register in `run.py` `create_daemon()`

Example:
```python
from monitor_daemon.handlers.base import BaseHandler

class MyHandler(BaseHandler):
    name = "my_handler"
    interval_seconds = 120  # Run every 2 minutes
    
    def execute(self) -> dict:
        # Your monitoring logic here
        return {"status": "ok", "data": {...}}
```

### State Persistence

Handler state (last run times, known orders) is saved to:
```
data/daemon_state.json
```

### Logs

```
logs/monitor-daemon.log      # Main daemon log
logs/monitor-daemon.out.log  # launchd stdout
logs/monitor-daemon.err.log  # launchd stderr
```

---

## Flex Query Setup (Historical Trades)

The real-time IB API only provides **today's fills**. To calculate P&L for positions opened/closed on previous days, you need to set up IB Flex Query for historical data.

### One-Time Setup

**Step 1: Login to IB Account Management**
```
https://www.interactivebrokers.com/sso/Login
```

**Step 2: Create a Flex Query**
1. Navigate to: **Reports → Flex Queries**
2. Click **"+ Create"** under "Activity Flex Query"
3. Configure:

| Field | Value |
|-------|-------|
| Query Name | `Trade History` |
| **Sections** | ☑️ Trades, ☑️ Commission Details |
| Format | `XML` |
| Period | `Last 365 Calendar Days` |
| Breakout by Day | `Yes` |

4. Click **Continue**
5. In Trades section, select **ALL fields** (or at minimum: Symbol, DateTime, Buy/Sell, Quantity, TradePrice, Commission, Strike, Expiry, Put/Call, TradeID)
6. Click **Save**
7. Note the **Query ID** displayed (e.g., `1422766`)

**Step 3: Get Flex Web Service Token**
1. Navigate to: **Reports → Settings → Flex Web Service**
2. Click **Generate Token**
3. Note the token string

**Step 4: Configure Environment**
Add to your `~/.zshrc` or `~/.bashrc`:
```bash
export IB_FLEX_TOKEN="your_token_here"
export IB_FLEX_QUERY_ID="your_query_id_here"
```

Then reload:
```bash
source ~/.zshrc
```

### Usage

```bash
# Fetch all historical trades
python3 scripts/trade_blotter/flex_query.py

# Filter by symbol
python3 scripts/trade_blotter/flex_query.py --symbol EWY

# JSON output
python3 scripts/trade_blotter/flex_query.py --json

# Pass credentials directly (if not using env vars)
python3 scripts/trade_blotter/flex_query.py --token YOUR_TOKEN --query-id YOUR_QUERY_ID

# Show setup guide
python3 scripts/trade_blotter/flex_query.py --setup
```

### What Flex Query Provides

| Data | Real-time API | Flex Query |
|------|---------------|------------|
| Today's fills | ✅ | ✅ |
| Historical fills (1-365 days) | ❌ | ✅ |
| Commission details | ✅ | ✅ |
| Open/Close indicator | ❌ | ✅ |
| Trade ID for reconciliation | ✅ | ✅ |

---

## Output Format

- Always show: signal → structure → Kelly math → decision
- State probability estimates explicitly, flag uncertainty
- When a trade doesn't meet criteria, say so immediately with the failing gate
- Never rationalize a bad trade
- Log EXECUTED trades to trade_log.json
- Log NO_TRADE decisions to docs/status.md (Recent Evaluations section)

## Trade Specification Reports ⭐ REQUIRED

**When recommending ANY trade, ALWAYS generate a Trade Specification HTML report.**

```bash
# Template location
.pi/skills/html-report/trade-specification-template.html

# Output location
reports/{ticker}-evaluation-{DATE}.html

# Example
reports/goog-evaluation-2026-03-04.html
```

**This is MANDATORY for:**
- Any `evaluate [TICKER]` that reaches the Structure milestone
- Any trade recommendation before execution
- Any position proposal requiring user confirmation

**Required sections (10 total):**
1. Header with ticker, company, price, gate status (ALL GATES PASSED / FAILED)
2. Summary Metrics (6): signal score, buy ratio, flow strength, convexity, position size, max gain
3. Milestone Summary with pass/fail status for all 7 milestones
4. Dark Pool Flow Section: daily breakdown + aggregate analysis
5. Options Flow Section: chain bias, institutional flow, combined signal
6. Context Section: seasonality + analyst ratings
7. Structure & Kelly: position structure and Kelly sizing
8. Trade Specification: exact order details ready for execution
9. Thesis & Risk Factors callouts
10. Three Gates Summary table

**Reference implementation:** `reports/goog-evaluation-2026-03-04.html`

**Workflow:**
1. Complete evaluation milestones 1-6
2. Generate HTML report with all data
3. Present to user for confirmation
4. On "execute" → place order via IB
5. On fill → update trade_log.json, portfolio.json, status.md

## P&L Reports

**When generating any P&L report, ALWAYS use the P&L template:**

```bash
# Template location
.pi/skills/html-report/pnl-template.html

# Output location
reports/pnl-{TICKER}-{DATE}.html
```

**Required sections for every P&L report:**
1. Header with CLOSED/OPEN status pill
2. 4 metrics: Realized P&L, Commissions, Hold Period, Return on Risk
3. Trade Summary callout (strategy, thesis, outcome)
4. Execution table(s) with cash flows per leg
5. Combined P&L panel (for spreads)
6. Trade timeline
7. Footer with data source

**Return on Risk formula:**
```
Return on Risk = Realized P&L / Capital at Risk

Capital at Risk:
  - Debit spread: Net debit paid
  - Credit spread: Max loss (width - credit)
  - Long option: Premium paid
  - Stock: Cost basis
```

See `.pi/skills/html-report/SKILL.md` for full template documentation.

## GARCH Convergence Reports ⭐ REQUIRED

**When generating GARCH Convergence scan reports, ALWAYS use the standard HTML template.**

```bash
# Template location
.pi/skills/html-report/template.html

# Output location
reports/garch-convergence-{preset}-{DATE}.html
```

**Generation pattern:**
```python
# 1. Read template
with open('.pi/skills/html-report/template.html', 'r') as f:
    template = f.read()

# 2. Build body using template CSS classes
body = """<header class="header">...</header>
<div class="metrics">...</div>
<div class="panel">...</div>"""

# 3. Insert into template
html = template.replace('{{TITLE}}', f'GARCH Convergence — {preset} | {date}')
html = html.replace('{{BODY}}', body)

# 4. Write to reports/
with open(f'reports/garch-convergence-{preset}-{date}.html', 'w') as f:
    f.write(html)
```

**Required sections:**
1. Header with preset name, ticker count, timestamp, theme toggle button
2. Summary metrics (4): Tickers scanned, With LEAPs, Laggers found, Actionable pairs
3. IV/HV Analysis table with all tickers
4. Pair Analysis panels for potential convergence pairs
5. Watchlist with laggers and short vega candidates
6. Footer with data sources

**Key CSS classes:**
| Element | Class |
|---------|-------|
| Highlighted row (lagger) | `tr.highlight` |
| Pass indicator | `.text-positive` |
| Fail indicator | `.text-negative` |
| Warning | `.text-warning` |
| Status badge | `.pill`, `.pill-positive`, `.pill-warning`, `.pill-negative` |
| Alert box | `.callout`, `.callout.positive`, `.callout.warning` |

**Reference:** `reports/garch-convergence-row-2026-03-04.html`

See `docs/strategy-garch-convergence.md` for full report generation spec.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/evaluate.py` | **⭐ Unified evaluation — runs all 7 milestones in parallel (ALWAYS USE THIS)** |
| `scripts/fetch_ticker.py` | Validate ticker via dark pool activity |
| `scripts/fetch_flow.py` | Fetch dark pool + options flow data |
| `scripts/fetch_options.py` | Options chain + institutional flow analysis (IB → UW → Yahoo last resort) |
| `scripts/fetch_oi_changes.py` | **⭐ Fetch OI changes to find hidden institutional positioning (REQUIRED)** |
| `scripts/verify_options_oi.py` | Verify specific options flow claims via Open Interest |
| `scripts/fetch_analyst_ratings.py` | Fetch analyst ratings, changes, and price targets |
| `scripts/scanner.py` | Scan watchlist, rank by signal strength |
| `scripts/discover.py` | Market-wide flow scanner for new candidates |
| `scripts/kelly.py` | Kelly criterion calculator |
| `scripts/ib_execute.py` | **⭐ UNIFIED: Place order + monitor + log (ALWAYS USE THIS)** |
| `scripts/ib_sync.py` | Sync live portfolio from Interactive Brokers (periodic) |
| `scripts/ib_reconcile.py` | Reconcile IB trades with local trade log (runs at startup) |
| `scripts/blotter.py` | Trade blotter - reconcile today's fills, calculate P&L |
| `scripts/trade_blotter/flex_query.py` | Fetch historical trades via IB Flex Query (up to 365 days) |
| `scripts/ib_realtime_server.js` | Node.js WebSocket server for real-time IB price streaming |
| `scripts/test_ib_realtime.py` | Tests for IB real-time connectivity |
| `scripts/leap_iv_scanner.py` | LEAP IV mispricing scanner (IB connection required) |
| `scripts/leap_scanner_uw.py` | LEAP IV scanner using UW (Yahoo as last resort for HV data) |
| `scripts/utils/presets.py` | **Preset loader** — `load_preset()`, `list_presets()` for 150 ticker presets |
| `scripts/fetch_x_watchlist.py` | Fetch X account tweets and extract ticker sentiment |
| `scripts/monitor_daemon/run.py` | **Extensible monitoring daemon** (replaces exit_order_service) |
| `scripts/ib_fill_monitor.py` | Monitor orders for fills (standalone, use daemon instead) |
| `scripts/portfolio_report.py` | Generate HTML portfolio report and open in browser |
| `scripts/free_trade_analyzer.py` | Analyze positions for free trade opportunities |

## ⚠️ Order Execution (CRITICAL)

**When placing ANY order, ALWAYS use `ib_execute.py`.**

This script automatically:
1. Places the order
2. Monitors for fills (real-time)
3. Logs filled trades to `trade_log.json`

**NEVER place orders manually without monitoring and logging.**

### Stock Orders
```bash
# Sell stock
python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 4500 --side SELL --limit 98.70 --yes

# Buy stock
python3 scripts/ib_execute.py --type stock --symbol AAPL --qty 100 --side BUY --limit BID --yes
```

### Option Orders
```bash
# Buy call at mid
python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 315 --right C --qty 10 --side BUY --limit MID --yes

# Sell put
python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 290 --right P --qty 5 --side SELL --limit 3.50 --yes
```

### Limit Price Options
| Value | Behavior |
|-------|----------|
| `MID` | Use current mid price |
| `BID` | Use current bid price |
| `ASK` | Use current ask price |
| `9.50` | Use exact price |

### Flags
| Flag | Purpose |
|------|---------|
| `--yes` / `-y` | Skip confirmation prompt |
| `--dry-run` | Preview without placing |
| `--timeout N` | Monitor timeout (default: 60s) |
| `--no-log` | Don't log to trade_log.json |
| `--thesis "..."` | Add thesis to log entry |
| `--notes "..."` | Add notes to log entry |

## Interactive Brokers Integration

### Client ID Strategy

**Default to `clientId=0` (master client)** for full order control.

| clientId | Privileges | Scripts |
|----------|-----------|---------|
| **0** (master) | Can cancel/modify ANY order | `ib_sync`, `ib_orders`, `ib_reconcile`, `ib_order_manage` |
| 2+ (unique) | Can only manage own orders | `ib_order`, `ib_fill_monitor`, `exit_order_service`, `ib_realtime_server` |

**Why master client:**
- Can cancel orders placed via TWS (which have `orderId=0`)
- Full visibility into all account orders
- Required for `ib_order_manage.py` cancel/modify operations

**When to use unique clientId:**
- Long-running services (streaming, monitoring) that shouldn't block other connections
- Order placement (tags orders with clientId for tracking)
- Multiple concurrent connections required

**Critical:** Only ONE connection can use `clientId=0` at a time.

### Portfolio Sync (Periodic)

```bash
# Display live portfolio (requires TWS/Gateway running)
python3 scripts/ib_sync.py

# Sync to portfolio.json
python3 scripts/ib_sync.py --sync

# Connect to different ports
python3 scripts/ib_sync.py --port 7496   # TWS Live
python3 scripts/ib_sync.py --port 7497   # TWS Paper (default)
python3 scripts/ib_sync.py --port 4001   # IB Gateway Live
python3 scripts/ib_sync.py --port 4002   # IB Gateway Paper
```

### Startup Protocol (Full Visibility)

When Pi starts, the startup extension (`.pi/extensions/startup-protocol.ts`) runs all checks with **numbered progress indicators**:

**Example output (market open):**
```
🚀 Startup: Running 6 checks...
[1/6] ✓ Market OPEN (2h 30m to close)
[2/6] ✓ Loaded: Spec, Plans, Runbook, Status, Context Engineering
[3/6] ✓ IB trades in sync
[4/6] ✓ Monitor daemon running
[5/6] ✓ Free Trade Progress:
       EWY: 100% 🎉 FREE
       PLTR: 89% ⚡ Near
[6/6] ✓ @aleabitoreddit: 54 tweets, 40 updated
✅ Startup complete (6/6 passed)
```

**Example output (market closed):**
```
🚀 Startup: Running 6 checks...
[1/6] ⚠️ Market CLOSED (after hours) — using closing prices
[2/6] ✓ Loaded: Spec, Plans, Runbook, Status, Context Engineering
...
```

**Market Hours Check:**
- First process in every startup
- Shows warning icon (⚠️) when market is closed
- Indicates that free trade progress uses closing prices (not real-time)
- Uses Eastern Time (9:30 AM - 4:00 PM ET)

**X Account Scan:**
- Runs automatically on **every startup**
- Output: `@account: N tickers` — Number of tickers found in recent tweets

**Processes tracked (in order):**

| # | Process | Type | Description |
|---|---------|------|-------------|
| 1 | `market` | sync | Market hours check (9:30 AM - 4:00 PM ET) |
| 2 | `docs` | sync | Load project docs + always-on skills |
| 3 | `ib` | async | IB trade reconciliation (runs first, updates portfolio) |
| 4 | `free_trade` | async | Free trade scan (waits for IB to complete) |
| 5 | `daemon` | sync | Monitor daemon status check |
| 6+ | `x_{account}` | async | X account scans (parallel with above) |

**Note:** Free trade analysis depends on IB sync because closed positions affect which multi-leg positions exist.

**Status indicators:**
- `✓` — Success
- `⚠️` — Warning (skipped or issue)
- `❌` — Error (failed)

**Final summary:**
- `✅ Startup complete (N/N passed)` — All processes succeeded
- `⚠️ Startup complete (X/N passed, Y warnings)` — Some warnings
- `❌ Startup complete (X/N passed, Y failed)` — Some failures

**Implementation:** Uses `StartupTracker` class with 14 TDD tests.

**Test the startup protocol:**
```bash
npx tsx .pi/tests/startup-protocol.test.ts
```

### ⚠️ Auto-Reconciliation Rule (MANDATORY)

**When IB sync detects new trades (`needs_attention: true`), IMMEDIATELY:**

1. **Read** `data/reconciliation.json` to get the new trades
2. **Log** each new trade to `data/trade_log.json` with:
   - Unique ID (auto-increment from last)
   - Full trade details from reconciliation
   - `validation_method: "ib_reconciliation"`
   - Realized P&L and commissions
3. **Update** `docs/status.md`:
   - Trade Log Summary table
   - Today's Trades section
   - Portfolio State metrics
   - Rule Violations if applicable
4. **Clear** reconciliation flag:
   - Set `needs_attention: false`
   - Move trades to `processed_trades` array
5. **Validate** JSON integrity:
   ```bash
   python3 -m json.tool data/trade_log.json
   ```

**This is automatic — do NOT wait for user to request it.**

**Reconciliation data format:**
```json
{
  "new_trades": [
    {
      "symbol": "PLTR",
      "date": "2026-03-04",
      "action": "SELL_OPTION",  // SELL_OPTION, BUY_OPTION, CLOSED, etc.
      "net_quantity": -100.0,
      "avg_price": 9.18,
      "commission": 70.25,
      "realized_pnl": 48479.75,
      "sec_type": "OPT"  // OPT, STK, BAG
    }
  ],
  "needs_attention": true
}
```

**Action interpretation:**
| Action | Meaning | Log As |
|--------|---------|--------|
| `SELL_OPTION` | Sold options (closing long OR opening short) | Check context |
| `BUY_OPTION` | Bought options (opening long OR closing short) | Check context |
| `CLOSED` | Position fully closed (net zero) | CLOSED with P&L |
| `BUY` / `SELL` | Stock trade | Stock entry/exit |

### Startup Reconciliation (Automatic)

When Pi starts, the startup extension automatically runs `ib_reconcile.py` **asynchronously** (non-blocking) to detect:

1. **New trades** — Executions in IB not logged in `trade_log.json`
2. **New positions** — Positions in IB not in `portfolio.json`
3. **Closed positions** — Positions in `portfolio.json` no longer in IB

**How it works:**
- Runs in background via `spawn()` — does not block Pi startup
- Connects to IB Gateway/TWS (port 4001 by default)
- Compares IB executions and positions to local files
- Writes results to `data/reconciliation.json`
- Shows notification if reconciliation needed

**Notifications (via StartupTracker):**
- `[N/M] ✓ IB trades in sync` — No discrepancies found
- `[N/M] ⚠️ IB: 3 new trades, 1 closed position` — Action needed
- `[N/M] ⚠️ IB not connected (skipped)` — IB unavailable

**Manual run:**
```bash
python3 scripts/ib_reconcile.py
```

**Reconciliation report:**
```bash
cat data/reconciliation.json | python3 -m json.tool
```

**Actions detected:**
| Action | Meaning |
|--------|---------|
| BUY | Opened long stock position |
| SELL | Closed long position (realized P&L) |
| SHORT | Opened short stock position |
| COVER | Closed short position |
| BUY_OPTION | Bought to open option |
| SELL_OPTION | Sold to close option |
| CLOSED | Position fully closed (net zero) |

### Exit Order Service (Automatic)

Monitors positions with pending manual exit orders and places them when IB will accept them. Runs automatically at Pi startup and can run as a periodic service.

**Background:** IB rejects limit orders that are too far from current market price (typically >40% away). This service monitors pending target exit orders and places them when the spread price gets close enough.

**Startup behavior:**
- Runs automatically when Pi starts (via `startup-protocol.ts`)
- Checks all positions with `PENDING_MANUAL` exit orders
- Attempts to place orders if spread is within 40% of target
- Notifies if any orders are placed

**Manual commands:**
```bash
# Check status of pending orders
python3 scripts/exit_order_service.py --status

# Run single check (place orders if possible)
python3 scripts/exit_order_service.py

# Dry run (preview without placing)
python3 scripts/exit_order_service.py --dry-run

# Run as daemon (every 5 mins during market hours)
python3 scripts/exit_order_service.py --daemon
```

**Periodic execution (launchd):**
```bash
# Install as macOS launchd service (runs every 5 minutes)
./scripts/setup_exit_order_service.sh install

# Check service status
./scripts/setup_exit_order_service.sh status

# View logs
./scripts/setup_exit_order_service.sh logs

# Uninstall service
./scripts/setup_exit_order_service.sh uninstall
```

**Log location:** `logs/exit-order-service.out.log`

**How it determines when to place:**
- Current spread price × 1.40 = Maximum placeable target
- Example: If spread is at $9.00, can place orders up to $12.60
- Orders farther away wait until spread appreciates

**Trade log integration:**
- Reads `exit_orders.target.status == "PENDING_MANUAL"` from trade_log.json
- Updates status to "ACTIVE" with `order_id` when placed

### Real-Time Price Streaming

Separate from portfolio sync - streams live prices via WebSocket.

```bash
# Start the real-time price server
# Start the Node.js realtime server from the web package
node ../web/scripts/ib_realtime_server.js

# Custom ports
node ../web/scripts/ib_realtime_server.js --port 8765 --ib-port 4001

# Test connectivity
python3 scripts/test_ib_realtime.py
python3 scripts/test_ib_realtime.py --ib-only   # Test IB only
python3 scripts/test_ib_realtime.py --ws-only   # Test WebSocket only
```

**WebSocket Protocol:**
```json
// Subscribe to symbols
{"action": "subscribe", "symbols": ["AAPL", "MSFT"]}

// Unsubscribe
{"action": "unsubscribe", "symbols": ["AAPL"]}

// One-time snapshot
{"action": "snapshot", "symbols": ["NVDA"]}

// Server sends price updates
{"type": "price", "symbol": "AAPL", "data": {"last": 175.50, "bid": 175.48, ...}}
```

**Next.js Integration:**
- API Route: `POST /api/prices` for one-time snapshot (body `{ "symbols": [...] }`)
- `GET /api/prices` is deprecated (`405`) and does not stream real-time data.
- Live pricing is end-to-end on Node via websocket; Next.js does not proxy live frames.
- React Hook: `usePrices({ symbols: ["AAPL", "MSFT"] })`

**Setup:**
1. Install project dependencies (`npm install` in `/web`) for the Node websocket server.
2. For IB + websocket connectivity tests, keep Python deps installed as needed (example: `pip install ib_insync websockets`).
3. In TWS: Configure → API → Settings → Enable "ActiveX and Socket Clients"
4. Ensure "Read-Only API" is unchecked if you want order capability later

## LEAP IV Mispricing Scanner

Identifies long-dated options where implied volatility diverges from realized volatility.

```bash
# Scan specific tickers
python3 scripts/leap_scanner_uw.py AAPL MSFT NVDA EWY

# Use presets
python3 scripts/leap_scanner_uw.py --preset sectors    # S&P 500 sector ETFs
python3 scripts/leap_scanner_uw.py --preset mag7       # Magnificent 7
python3 scripts/leap_scanner_uw.py --preset semis      # Semiconductors
python3 scripts/leap_scanner_uw.py --preset row        # Rest of World country ETFs
python3 scripts/leap_scanner_uw.py --preset metals     # Gold, Silver, Copper, Miners
python3 scripts/leap_scanner_uw.py --preset energy     # Oil, Gas, Refiners, MLPs

# Custom parameters
python3 scripts/leap_scanner_uw.py --min-gap 20

# IB version (requires TWS/Gateway)
python3 scripts/leap_iv_scanner.py AAPL --portfolio
```

**Available Presets:**

Built-in presets (hardcoded in script):

| Preset | Description | Count |
|--------|-------------|-------|
| `sectors` | S&P 500 sector ETFs (XLK, XLE, XLF, etc.) | 11 |
| `mag7` | Magnificent 7 (AAPL, MSFT, NVDA, etc.) | 7 |
| `semis` | Semiconductors (NVDA, AMD, TSM, etc.) | 9 |
| `emerging` | Emerging market ETFs (EEM, EWZ, FXI, etc.) | 8 |
| `china` | China stocks and ETFs (BABA, FXI, KWEB, etc.) | 9 |
| `row` | Rest of World: All country-specific ETFs | 45 |
| `row-americas` | Americas (Canada, Mexico, Brazil, Chile, Argentina) | 5 |
| `row-europe` | Europe (UK, Germany, France, etc.) | 17 |
| `row-asia` | Asia-Pacific (Japan, Korea, Taiwan, India, etc.) | 15 |
| `row-mena` | Middle East & Africa (Israel, South Africa, Saudi, etc.) | 5 |
| `metals` | Precious metals, base metals, miners, uranium | 23 |
| `energy` | Oil, natural gas, refiners, MLPs, clean energy | 24 |

File presets (`data/presets/`): Strategy-agnostic — work with `leap-scan`, `garch-convergence`, etc.

**150 total preset files** across 3 indices covering **2,446 unique tickers**.

| Index | Master | Tickers | Pairs | Sub-Presets | Overlap |
|-------|--------|---------|-------|-------------|---------|
| **S&P 500** | `sp500` | 503 | 286 | 99 sub-industry + 11 sector | — |
| **NASDAQ 100** | `ndx100` | 101 | 53 | 21 thematic groups | 87 w/ SP500 |
| **Russell 2000** | `r2k` | 1,929 | 969 | 11 sector + 5 tier | 0 w/ SP500 |

**S&P 500 Presets (111 files)**

| Preset | Description | Tickers | Pairs |
|--------|-------------|---------|-------|
| `sp500` | Full S&P 500 (all sub-industries) | 503 | 286 |
| `sp500-semiconductors` | NVDA↔AMD, AVGO↔QCOM, MU↔INTC, etc. | 14 | 7 |
| `sp500-application-software` | CRM↔ORCL, ADBE↔INTU, CDNS↔SNPS, etc. | 14 | 7 |
| `sp500-diversified-banks` | JPM↔BAC, C↔WFC, PNC↔USB | 7 | 4 |
| `sp500-biotechnology` | AMGN↔GILD, REGN↔VRTX, ABBV↔BMY | 8 | 5 |
| `sp500-aerospace-defense` | LMT↔RTX, BA↔GE, NOC↔GD | 12 | 6 |
| `sp500-oil-gas-exploration-production` | COP↔EOG, DVN↔FANG, OXY↔APA | 10 | 5 |
| ... | (93 more sub-industry presets) | | |
| `sp500-sector-information-technology` | All IT sub-industries | 71 | 38 |
| `sp500-sector-financials` | All Financial sub-industries | 76 | 40 |
| ... | (9 more sector rollups) | | |

**NASDAQ 100 Presets (22 files)**

| Preset | Description | Tickers | Pairs |
|--------|-------------|---------|-------|
| `ndx100` | Full NASDAQ 100 (all groups) | 101 | 53 |
| `ndx100-semiconductors` | NVDA↔AMD, AVGO↔QCOM, MU↔INTC, MRVL↔ARM | 13 | 7 |
| `ndx100-semi-equipment` | ASML↔LRCX, AMAT↔KLAC | 4 | 2 |
| `ndx100-mega-cap-tech-platforms` | AAPL↔MSFT, GOOGL↔META, AMZN↔NFLX | 7 | 3 |
| `ndx100-enterprise-software` | CDNS↔SNPS, ADBE↔INTU, WDAY↔ADSK | 9 | 3 |
| `ndx100-cybersecurity` | CRWD↔PANW, FTNT↔ZS | 4 | 2 |
| `ndx100-cloud-data` | DDOG↔PLTR, TEAM↔SHOP | 5 | 3 |
| `ndx100-biotech` | AMGN↔GILD, REGN↔VRTX, ALNY↔INSM | 6 | 3 |
| `ndx100-digital-commerce` | AMZN↔MELI, PDD↔DASH | 6 | 3 |
| `ndx100-streaming-gaming` | NFLX↔WBD, EA↔TTWO | 4 | 2 |
| `ndx100-travel-leisure` | BKNG↔ABNB, MAR↔SBUX | 5 | 3 |
| `ndx100-telecom-cable` | TMUS↔CMCSA, CHTR↔CSCO | 4 | 2 |
| `ndx100-beverages-staples` | PEP↔KDP, MNST↔CCEP, KHC↔MDLZ | 6 | 3 |
| ... | (8 more groups) | | |

**Russell 2000 Presets (17 files)**

| Preset | Description | Tickers | Pairs |
|--------|-------------|---------|-------|
| `r2k` | Full Russell 2000 (IWM holdings) | 1,929 | 969 |
| `r2k-financials` | Largest R2K sector | 413 | 207 |
| `r2k-health-care` | Biotech-heavy | 395 | 198 |
| `r2k-industrials` | Small-cap industrials | 257 | 129 |
| `r2k-information-technology` | Small-cap tech | 210 | 105 |
| `r2k-consumer-discretionary` | Small-cap consumer | 197 | 99 |
| `r2k-energy` | Small-cap energy | 105 | 53 |
| `r2k-tier-top-100` | Top 100 by weight (most liquid) | 100 | 50 |
| `r2k-tier-top-200` | Top 200 by weight | 200 | 100 |
| `r2k-tier-top-500` | Top 500 by weight | 500 | 250 |
| ... | (6 more sector + tier presets) | | |

```bash
# List all 150 presets
python3 scripts/leap_scanner_uw.py --list-presets

# Use any preset with leap-scan
python3 scripts/leap_scanner_uw.py --preset sp500-semiconductors
python3 scripts/leap_scanner_uw.py --preset ndx100-cybersecurity
python3 scripts/leap_scanner_uw.py --preset r2k-tier-top-100

# Use any preset with garch-convergence
garch-convergence sp500-semiconductors
garch-convergence ndx100-biotech
```

**Preset Loader:**
```python
from utils.presets import load_preset, list_presets, Preset

p = load_preset("sp500-semiconductors")
p.tickers     # ["NVDA", "AMD", ...] — for any scan
p.pairs       # [["NVDA","AMD"], ...] — for GARCH convergence
p.vol_driver  # "Tech spending, AI/cloud capex..." — for thesis context

# Master preset hierarchical access
sp = load_preset("sp500")
sp.group_tickers("semiconductors")  # tickers for one group
sp.group_pairs("semiconductors")    # pairs for one group
sp.groups.keys()                    # all 99 group names
```

**Output:** HTML report at `reports/leap-scan-uw.html`

See `docs/strategies.md` for full methodology.

## Data Files

| File | Purpose |
|------|---------|
| `data/watchlist.json` | Tickers under surveillance with flow signals |
| `data/portfolio.json` | Open positions, entry prices, Kelly sizes, expiry dates |
| `data/trade_log.json` | Executed trades only (append-only) |
| `data/ticker_cache.json` | Local cache of ticker → company name mappings |
| `data/analyst_ratings_cache.json` | Cached analyst ratings data |
| `data/presets/` | **150 strategy-agnostic ticker presets** (SP500, NDX100, R2K) |
| `data/presets/sp500.json` | S&P 500 master (503 tickers, 286 pairs, 99 groups) |
| `data/presets/ndx100.json` | NASDAQ 100 master (101 tickers, 53 pairs, 21 groups) |
| `data/presets/r2k.json` | Russell 2000 master (1929 tickers, 969 pairs, 16 groups) |

## Documentation

| File | Purpose |
|------|---------|
| `docs/prompt.md` | Spec, constraints, deliverables |
| `docs/plans.md` | Milestone workflow with validation steps |
| `docs/implement.md` | Execution runbook |
| `docs/status.md` | Current state, recent decisions, audit log |
| `docs/strategies.md` | Trading strategies (Dark Pool Flow, LEAP IV Mispricing, GARCH Convergence) |
| `docs/strategy-garch-convergence.md` | GARCH Convergence Spreads full specification |
| `docs/options-flow-verification.md` | **How to verify options flow claims via OI** |
| `docs/unusual_whales_api.md` | **Unusual Whales API quick reference** |
| `docs/unusual_whales_api_spec.yaml` | **Full OpenAPI spec for UW API** |

## Data Source Priority (Detailed)

**ALWAYS use sources in this order. Never skip ahead. Yahoo Finance is ABSOLUTE LAST RESORT.**

| Priority | Source | Use Case | Notes |
|----------|--------|----------|-------|
| **1** | Interactive Brokers | Real-time quotes, options chains, analyst ratings, fundamentals | Requires TWS/Gateway running |
| **2** | Unusual Whales | Dark pool flow, options activity, institutional flow, analyst ratings | API key in UW_TOKEN env var |
| **3** | Exa (web search) | Web search, company research, code/docs lookup | API key in EXA_API_KEY env var |
| **4** | agent-browser | Only for interactive pages, screenshots, JS-rendered content | Fallback when Exa insufficient |
| **5 ⚠️** | Yahoo Finance | **ABSOLUTE LAST RESORT** — only if ALL above sources fail | Rate limited, unreliable, delayed |

**What each source provides:**

| Data Type | IB (1st) | UW (2nd) | Exa (3rd) | Browser (4th) | Yahoo (5th ⚠️) |
|-----------|----------|----------|-----------|---------------|----------------|
| Real-time quotes | ✅ | ❌ | ❌ | ❌ | ⚠️ delayed |
| Options chains | ✅ | ✅ | ❌ | ❌ | ⚠️ last resort |
| Options premium/volume | ⚠️ limited | ✅ | ❌ | ❌ | ⚠️ limited |
| Dark pool flow | ❌ | ✅ | ❌ | ❌ | ❌ |
| Options flow/sweeps | ❌ | ✅ | ❌ | ❌ | ❌ |
| Bid/Ask side analysis | ❌ | ✅ | ❌ | ❌ | ❌ |
| Analyst ratings | ✅ (subscription) | ✅ | ✅ | ✅ | ⚠️ last resort |
| Fundamentals | ✅ (subscription) | ❌ | ✅ | ✅ | ⚠️ last resort |
| News/Events | ❌ | ✅ | ✅ | ✅ | ❌ |
| Seasonality | ❌ | ✅ | ✅ EquityClock | ✅ | ❌ |
| Greek exposure (GEX) | ❌ | ✅ | ❌ | ❌ | ❌ |
| Institutional ownership | ❌ | ✅ | ✅ | ✅ | ⚠️ last resort |
| Short interest | ❌ | ✅ | ❌ | ❌ | ⚠️ last resort |
| Congress trades | ❌ | ✅ | ❌ | ❌ | ❌ |
| Insider trades | ❌ | ✅ | ✅ | ✅ | ❌ |

**IB Fundamental Data** (requires Reuters Fundamentals subscription):
- `ReportsFinSummary` - Financial summary
- `ReportsOwnership` - Company ownership
- `ReportSnapshot` - Financial overview
- `ReportsFinStatements` - Financial statements
- `RESC` - **Analyst Estimates & Ratings**
- `CalendarReport` - Company calendar

*Note: Error 10358 "Fundamentals data is not allowed" means IB fundamentals subscription is not active. Scripts will auto-fallback to next available source.*

---

## Unusual Whales API Reference

**Full documentation:** `docs/unusual_whales_api.md`
**OpenAPI spec:** `docs/unusual_whales_api_spec.yaml`

**Base URL:** `https://api.unusualwhales.com`
**Auth:** `Authorization: Bearer {UW_TOKEN}`

### Key Endpoints for Convex Scavenger

| Endpoint | Purpose |
|----------|---------|
| `GET /api/darkpool/{ticker}` | Dark pool trades (primary edge source) |
| `GET /api/option-trades/flow-alerts` | Options flow alerts (sweeps, blocks) |
| `GET /api/stock/{ticker}/info` | Ticker validation, company info |
| `GET /api/stock/{ticker}/option-contracts` | Options chain data |
| `GET /api/stock/{ticker}/greek-exposure` | GEX data |
| `GET /api/screener/analysts` | Analyst ratings |
| `GET /api/seasonality/{ticker}/monthly` | Monthly seasonality |
| `GET /api/shorts/{ticker}/interest-float/v2` | Short interest data |

### Dark Pool Flow (Edge Detection)
```bash
# Fetch dark pool trades for ticker
curl -H "Authorization: Bearer $UW_TOKEN" \
  "https://api.unusualwhales.com/api/darkpool/AAPL?date=2026-03-03"
```

### Options Flow Alerts
```bash
# Fetch flow alerts with filters
curl -H "Authorization: Bearer $UW_TOKEN" \
  "https://api.unusualwhales.com/api/option-trades/flow-alerts?ticker_symbol=AAPL&is_sweep=true&min_premium=50000"
```

### WebSocket Streaming (Advanced tier)
```
wss://api.unusualwhales.com/socket?token={UW_TOKEN}

Channels:
- option_trades / option_trades:{TICKER}
- flow-alerts
- price:{TICKER}
- gex:{TICKER}
- off_lit_trades (dark pool)
```

**Always consult `docs/unusual_whales_api.md` for endpoint details and response schemas.**

## Tools Available

- `bash` — Run Python scripts in ./scripts/
- `read`/`write`/`edit` — Manage data and documentation files
- `kelly_calc` — Built-in fractional Kelly calculator
- `exa` — Web search, company research, code/docs lookup (Exa MCP — primary)
- `agent-browser` — Browser automation for interactive pages (fallback)

## Skills

Skills are loaded on-demand when tasks match their descriptions.

| Skill | Location | Purpose |
|-------|----------|---------|
| `options-analysis` | `.pi/skills/options-analysis/SKILL.md` | Options pricing and structure analysis |
| `web-fetch` | `.pi/skills/web-fetch/SKILL.md` | Web search (Exa primary) + browser automation (fallback) |
| `browser-use-cloud` | `.pi/skills/browser-use-cloud/SKILL.md` | AI browser agent for autonomous web tasks |
| `html-report` | `.pi/skills/html-report/SKILL.md` | Generate styled HTML reports (Terminal theme) |
| `context-engineering` | `.pi/skills/context-engineering/SKILL.md` | Persistent memory, context pipelines, token budget management |

### Web Fetch Quick Reference

**Exa (default for search/fetch):**
```
web_search_exa("NVDA dark pool activity March 2026")
company_research_exa("Rambus Inc semiconductor IP")
get_code_context_exa("ib_insync placeOrder clientId")
```

**agent-browser (fallback for interactive pages):**
```bash
agent-browser open "https://example.com"
agent-browser snapshot -i -c
agent-browser get text @e5
agent-browser screenshot page.png
agent-browser fill @e3 "value"
agent-browser click @e5
```

## Discovery Scoring (0-100 Scale)

## Discovery Scoring (0-100 Scale)

When running `discover`, candidates are scored on edge quality:

| Component | Weight | Measure |
|-----------|--------|---------|
| DP Strength | 30% | Dark pool flow imbalance (0-100) |
| DP Sustained | 20% | Consecutive days same direction |
| Confluence | 20% | Options + DP alignment |
| Vol/OI Ratio | 15% | Unusual volume indicator |
| Sweeps | 15% | Urgency signal |

Score interpretation:
- **60-100**: Strong — worth full evaluation
- **40-59**: Moderate — monitor closely
- **20-39**: Weak — early stage or conflicting
- **0-19**: No actionable signal

### OI Change Discovery (Market-Wide)

**ALWAYS check market-wide OI changes as part of discovery:**

```bash
# Find massive institutional positioning across all tickers
python3 scripts/fetch_oi_changes.py --market --min-premium 10000000
```

This surfaces positions that may NOT appear in flow alerts because they don't trigger "unusual" filters. The $95M MSFT LEAP calls were discovered this way.

**OI changes > $10M premium are often:**
- Large institutions building positions
- Pre-earnings positioning
- Sector rotation signals
- LEAP accumulation (longer-term bets)
