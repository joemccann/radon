# Convex Scavenger — Project Instructions

## ⚠️ Data Fetching Priority (ALWAYS follow this order)

When fetching ANY market data (quotes, options, fundamentals, analyst ratings, etc.):

| Priority | Source | When to Use |
|----------|--------|-------------|
| **1st** | Interactive Brokers | Always try first if TWS/Gateway available |
| **2nd** | Unusual Whales | Flow data, dark pools, options activity |
| **3rd** | Yahoo Finance | Only if IB and UW unavailable/don't have the data |
| **4th** | Web Search/Scrape | Last resort only |

**Never skip to Yahoo Finance or web scraping without trying IB/UW first.**

---

## Workflow Commands

| Command | Action |
|---------|--------|
| `scan` | Scan watchlist for dark pool flow signals |
| `discover` | Find new candidates from market-wide options flow |
| `evaluate [TICKER]` | Full 7-milestone evaluation |
| `portfolio` | Current positions, exposure, capacity |
| `journal` | View recent trade log entries |
| `sync` | Pull live portfolio from Interactive Brokers |
| `leap-scan [TICKERS]` | Scan for LEAP IV mispricing opportunities |
| `seasonal [TICKERS]` | Seasonality assessment for one or more tickers |
| `x-scan [@ACCOUNT]` | Fetch latest tweets and extract ticker sentiment |
| `analyst-ratings [TICKERS]` | Fetch analyst ratings, changes, and price targets |

## Evaluation Milestones

Always follow in order. Stop immediately if a gate fails.

1. **Validate Ticker** → `python3 scripts/fetch_ticker.py [TICKER]`
1B. **Seasonality** → Fetch & analyze (does not affect score, but report in analysis)
1C. **Analyst Ratings** → `python3 scripts/fetch_analyst_ratings.py [TICKER]` (context, not a gate)
2. **Dark Pool Flow** → `python3 scripts/fetch_flow.py [TICKER]`
3. **Options Flow** → `python3 scripts/fetch_options.py [TICKER]`
4. **Edge Decision** → PASS/FAIL with reasoning (stop if FAIL)
5. **Structure** → Design convex position (stop if R:R < 2:1)
6. **Kelly Sizing** → Calculate + enforce caps
7. **Log Trade** → Append executed trades only to trade_log.json (NO_TRADE decisions go to status.md)

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

Fetch tweets from X accounts and extract ticker sentiment for watchlist.

```bash
# Scan default account (@aleabitoreddit)
python3 scripts/fetch_x_watchlist.py

# Scan specific account
python3 scripts/fetch_x_watchlist.py --account elonmusk

# Look back 48 hours instead of 24
python3 scripts/fetch_x_watchlist.py --hours 48

# Dry run (don't update watchlist)
python3 scripts/fetch_x_watchlist.py --dry-run
```

**Requires:** `BROWSER_USE_API_KEY` environment variable

**Startup Protocol:**
- Extension checks watchlist for X account subcategories
- If last scan >12 hours ago, notifies agent to run scan
- Agent should run `x-scan` for any flagged accounts

**Output:**
- Extracts tickers mentioned in tweets
- Determines sentiment: BULLISH / BEARISH / NEUTRAL
- Rates confidence: HIGH / MEDIUM / LOW
- Updates watchlist subcategory with new/updated tickers

---

## Analyst Ratings Command

Fetch analyst ratings, recent rating changes, and price targets.

**Data Sources (following standard priority):**
1. Interactive Brokers (`RESC` fundamental data) - requires Reuters subscription
2. Yahoo Finance - fallback if IB unavailable (rate limited)
3. Web scrape - last resort

*Note: Unusual Whales HAS analyst ratings via `/api/screener/analysts` endpoint. See `docs/unusual_whales_api.md` for details.*

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
python3 scripts/fetch_analyst_ratings.py AAPL --source yahoo
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

## Output Format

- Always show: signal → structure → Kelly math → decision
- State probability estimates explicitly, flag uncertainty
- When a trade doesn't meet criteria, say so immediately with the failing gate
- Never rationalize a bad trade
- Log EXECUTED trades to trade_log.json
- Log NO_TRADE decisions to docs/status.md (Recent Evaluations section)

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_ticker.py` | Validate ticker via dark pool activity |
| `scripts/fetch_flow.py` | Fetch dark pool + options flow data |
| `scripts/fetch_options.py` | Options chain data (stub) |
| `scripts/fetch_analyst_ratings.py` | Fetch analyst ratings, changes, and price targets |
| `scripts/scanner.py` | Scan watchlist, rank by signal strength |
| `scripts/discover.py` | Market-wide flow scanner for new candidates |
| `scripts/kelly.py` | Kelly criterion calculator |
| `scripts/ib_sync.py` | Sync live portfolio from Interactive Brokers (periodic) |
| `scripts/ib_realtime_server.js` | Node.js WebSocket server for real-time IB price streaming |
| `scripts/test_ib_realtime.py` | Tests for IB real-time connectivity |
| `scripts/leap_iv_scanner.py` | LEAP IV mispricing scanner (IB connection required) |
| `scripts/leap_scanner_uw.py` | LEAP IV scanner using UW + Yahoo Finance (no IB needed) |
| `scripts/fetch_x_watchlist.py` | Fetch X account tweets and extract ticker sentiment |

## Interactive Brokers Integration

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
python3 scripts/leap_iv_scanner.py AAPL MSFT NVDA EWY

# Use presets
python3 scripts/leap_iv_scanner.py --preset sectors    # State Street sector ETFs
python3 scripts/leap_iv_scanner.py --preset mag7       # Magnificent 7
python3 scripts/leap_iv_scanner.py --preset semis      # Semiconductors
python3 scripts/leap_iv_scanner.py --preset emerging   # Emerging market ETFs

# Custom parameters
python3 scripts/leap_iv_scanner.py --min-gap 20 --years 2027 2028

# Scan portfolio holdings for IV opportunities
python3 scripts/leap_iv_scanner.py --portfolio
```

**Available Presets:** `sectors`, `mag7`, `semis`, `financials`, `energy`, `china`, `emerging`

**Output:** HTML report at `reports/leap-iv-scan.html`

See `docs/strategies.md` for full methodology.

## Data Files

| File | Purpose |
|------|---------|
| `data/watchlist.json` | Tickers under surveillance with flow signals |
| `data/portfolio.json` | Open positions, entry prices, Kelly sizes, expiry dates |
| `data/trade_log.json` | Executed trades only (append-only) |
| `data/ticker_cache.json` | Local cache of ticker → company name mappings |
| `data/analyst_ratings_cache.json` | Cached analyst ratings data |

## Documentation

| File | Purpose |
|------|---------|
| `docs/prompt.md` | Spec, constraints, deliverables |
| `docs/plans.md` | Milestone workflow with validation steps |
| `docs/implement.md` | Execution runbook |
| `docs/status.md` | Current state, recent decisions, audit log |
| `docs/strategies.md` | Trading strategies (Dark Pool Flow, LEAP IV Mispricing) |
| `docs/unusual_whales_api.md` | **Unusual Whales API quick reference** |
| `docs/unusual_whales_api_spec.yaml` | **Full OpenAPI spec for UW API** |

## Data Source Priority (Detailed)

**ALWAYS use sources in this order. Never skip ahead.**

| Priority | Source | Use Case | Notes |
|----------|--------|----------|-------|
| **1** | Interactive Brokers | Real-time quotes, options chains, analyst ratings, fundamentals | Requires TWS/Gateway running |
| **2** | Unusual Whales | Dark pool flow, options activity, institutional flow | API key in UW_TOKEN env var |
| **3** | Yahoo Finance | Quotes, analyst ratings when IB unavailable | Rate limited, can be delayed |
| **4** | Web Search/Scrape | Only when no API has the data | Use `agent-browser` skill |

**What each source provides:**

| Data Type | IB | UW | Yahoo | Web |
|-----------|----|----|-------|-----|
| Real-time quotes | ✅ | ❌ | ⚠️ delayed | ❌ |
| Options chains | ✅ | ✅ | ✅ | ❌ |
| Dark pool flow | ❌ | ✅ | ❌ | ❌ |
| Options flow/sweeps | ❌ | ✅ | ❌ | ❌ |
| Analyst ratings | ✅ (subscription) | ✅ | ✅ | ✅ |
| Fundamentals | ✅ (subscription) | ❌ | ✅ | ✅ |
| News/Events | ❌ | ✅ | ❌ | ✅ |
| Seasonality | ❌ | ✅ | ❌ | ✅ EquityClock |
| Greek exposure (GEX) | ❌ | ✅ | ❌ | ❌ |
| Institutional ownership | ❌ | ✅ | ✅ | ✅ |
| Short interest | ❌ | ✅ | ✅ | ❌ |
| Congress trades | ❌ | ✅ | ❌ | ❌ |
| Insider trades | ❌ | ✅ | ❌ | ✅ |

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
- `agent-browser` — Web browsing and scraping (see web-fetch skill)

## Skills

Skills are loaded on-demand when tasks match their descriptions.

| Skill | Location | Purpose |
|-------|----------|---------|
| `options-analysis` | `.pi/skills/options-analysis/SKILL.md` | Options pricing and structure analysis |
| `web-fetch` | `.pi/skills/web-fetch/SKILL.md` | Fetch and extract content from websites |
| `browser-use-cloud` | `.pi/skills/browser-use-cloud/SKILL.md` | AI browser agent for autonomous web tasks |
| `html-report` | `.pi/skills/html-report/SKILL.md` | Generate styled HTML reports (Terminal theme) |
| `context-engineering` | `.pi/skills/context-engineering/SKILL.md` | Persistent memory, context pipelines, token budget management |

### Web Fetch Quick Reference
```bash
# Open and snapshot a page
agent-browser open "https://example.com"
agent-browser snapshot -i -c

# Extract text from element (use @refs from snapshot)
agent-browser get text @e5

# Screenshot
agent-browser screenshot page.png

# Interactive: fill form and click
agent-browser fill @e3 "value"
agent-browser click @e5
```

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
