# Execution Runbook

## Source of Truth
- `docs/plans.md` defines the milestone sequence
- `docs/prompt.md` defines constraints and "done when"
- Execute milestones IN ORDER, do not skip

## ⚠️ Evaluate Command → ALWAYS `evaluate.py`
**Any evaluation request routes to `python3 scripts/evaluate.py [TICKER]`. No exceptions.**
Even if the user provides manual steps (e.g., "run fetch_flow.py, then fetch_options.py"),
ignore the manual steps and run the unified script. It handles M1–M3B in parallel.

## API Client Architecture

All IB and UW access goes through centralized clients in `scripts/clients/`:

| Client | File | Usage |
|--------|------|-------|
| `IBClient` | `scripts/clients/ib_client.py` | `from clients.ib_client import IBClient` |
| `UWClient` | `scripts/clients/uw_client.py` | `from clients.uw_client import UWClient` |

**IBClient** wraps `ib_insync.IB` with connection retries, context manager support, and methods for positions, orders, quotes, options chains, fills, flex queries, and historical data. Exception hierarchy: `IBError` → `IBConnectionError`, `IBOrderError`, `IBTimeoutError`, `IBContractError`. Raw access via `client.ib` property.

**UWClient** wraps all Unusual Whales REST endpoints with session pooling, automatic retry/backoff, and context manager support. Exception hierarchy: `UWAPIError` → `UWAuthError`, `UWRateLimitError`, `UWNotFoundError`, `UWValidationError`, `UWServerError`. 50+ methods covering dark pool, options flow, stock info, GEX, volatility, ratings, seasonality, and more.

**Legacy utils** (`scripts/utils/ib_connection.py`, `scripts/utils/uw_api.py`) are preserved but all scripts have been migrated to the new clients.

---

## Operating Rules

### 1. Validate Before Assuming
- NEVER identify a ticker from memory/training data
- ALWAYS run `fetch_ticker.py` first to get verified company info
- If script fails or returns no data, state "UNVERIFIED" and flag uncertainty

### 2. Always Fetch Fresh Data (CRITICAL)
- **Every evaluation milestone that calls a script or API MUST fetch live data at execution time**
- Scan results are LEADS — when evaluating, re-fetch everything (dark pool, options, OI, analyst ratings)
- If market is open, all data must include today. If a script's output doesn't include today's date, re-run or flag the gap
- Include a `📊 Data as of:` timestamp line at the start of every evaluation
- NEVER carry forward data from a prior scan session as if it were fresh evidence

### 3. Milestone Discipline
- Complete each milestone fully before proceeding
- Run validation command for each milestone
- If validation fails → repair immediately, do not continue
- If stop condition met → halt and report which gate failed

### 3. No Rationalization
- If a gate fails, stop evaluation
- Do not "find reasons" to proceed anyway
- State the failing gate clearly and move on

### 4. Diffs Stay Scoped
- When updating portfolio.json, only modify relevant fields
- When appending to trade_log.json, append only (never overwrite history)
- Keep watchlist.json updates minimal and targeted

### 5. Continuous Documentation
- Update `docs/status.md` after each evaluation
- Log EXECUTED trades only to trade_log.json (with full details)
- Log NO_TRADE decisions to docs/status.md (Recent Evaluations section)
- Include timestamp, ticker, decision, and rationale

### 6. Verification Commands
After any trade decision:
```bash
# Validate JSON integrity
python3 -m json.tool data/portfolio.json
python3 -m json.tool data/trade_log.json
python3 -m json.tool data/watchlist.json
```

### 7. Error Recovery
If a script fails:
1. Check error message
2. Attempt repair if obvious (missing dependency, API issue)
3. If unrecoverable, log the failure and flag for manual review
4. Do not fabricate data

---

## Command Reference

### Evaluation Commands
| Action | Command |
|--------|---------|
| **⭐ Full evaluation** | `python3 scripts/evaluate.py [TICKER]` |
| Full evaluation (JSON) | `python3 scripts/evaluate.py [TICKER] --json` |
| Full evaluation (custom bankroll) | `python3 scripts/evaluate.py [TICKER] --bankroll 1200000` |
| Validate ticker | `python3 scripts/fetch_ticker.py [TICKER]` |
| Fetch dark pool flow | `python3 scripts/fetch_flow.py [TICKER]` |
| Fetch options data | `python3 scripts/fetch_options.py [TICKER]` |
| Fetch options (JSON) | `python3 scripts/fetch_options.py [TICKER] --json` |
| Fetch analyst ratings | `python3 scripts/fetch_analyst_ratings.py [TICKER]` |
| Calculate Kelly | `python3 scripts/kelly.py --prob P --odds O --bankroll B` |

### Portfolio Commands
| Action | Command |
|--------|---------|
| **Generate portfolio report** | `python3 scripts/portfolio_report.py` |
| Portfolio report (sync first) | `python3 scripts/portfolio_report.py --sync` |
| Sync IB portfolio | `python3 scripts/ib_sync.py --sync` |
| Run reconciliation | `python3 scripts/ib_reconcile.py` |
| View today's fills | `python3 scripts/blotter.py` |
| Fetch historical trades | `python3 scripts/trade_blotter/flex_query.py --symbol [TICKER]` |
| Start realtime server | `node scripts/ib_realtime_server.js` |
| Validate JSON | `python3 -m json.tool data/[file].json` |

### Order Execution Commands

**⚠️ ALWAYS use `ib_execute.py` — it monitors and logs automatically.**

| Action | Command |
|--------|---------|
| **Sell stock** | `python3 scripts/ib_execute.py --type stock --symbol X --qty N --side SELL --limit N --yes` |
| **Buy stock** | `python3 scripts/ib_execute.py --type stock --symbol X --qty N --side BUY --limit N --yes` |
| **Buy option** | `python3 scripts/ib_execute.py --type option --symbol X --expiry YYYYMMDD --strike N --right C/P --qty N --side BUY --limit MID --yes` |
| **Sell option** | `python3 scripts/ib_execute.py --type option --symbol X --expiry YYYYMMDD --strike N --right C/P --qty N --side SELL --limit N --yes` |
| Check pending exits | `python3 scripts/exit_order_service.py --status` |
| Run exit order check | `python3 scripts/exit_order_service.py` |
| Exit service daemon | `python3 scripts/exit_order_service.py --daemon` |
| Install exit service | `./scripts/setup_exit_order_service.sh install` |
| Exit service status | `./scripts/setup_exit_order_service.sh status` |

### IB Connection Ports
| Port | Environment |
|------|-------------|
| 7496 | TWS Live |
| 7497 | TWS Paper |
| 4001 | IB Gateway Live |
| 4002 | IB Gateway Paper |

---

## Trade Specification Reports

**ALWAYS generate a Trade Specification HTML report when recommending a trade.**

```bash
# Template
.pi/skills/html-report/trade-specification-template.html

# Output
reports/{ticker}-evaluation-{date}.html
```

**Workflow:**
1. Complete evaluation milestones 1-6
2. Generate HTML report using template
3. Present to user for confirmation
4. On "execute" → use `ib_execute.py` (auto-monitors and logs)
5. Place exit orders (stop loss + target)

**Reference:** `reports/goog-evaluation-2026-03-04.html`

---

## Order Execution Workflow

**⚠️ ALWAYS use `ib_execute.py` for all orders. It automatically:**
- Places the order
- Monitors for fills (real-time updates)
- Logs filled trades to `trade_log.json`

### Single-Leg Orders

**Stock:**
```bash
# Sell stock at bid
python3 scripts/ib_execute.py --type stock --symbol NFLX --qty 4500 --side SELL --limit BID --yes

# Buy stock at limit
python3 scripts/ib_execute.py --type stock --symbol AAPL --qty 100 --side BUY --limit 175.50 --yes
```

**Option:**
```bash
# Buy call at mid
python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 315 --right C --qty 44 --side BUY --limit MID --yes

# Sell put at limit
python3 scripts/ib_execute.py --type option --symbol GOOG --expiry 20260417 --strike 290 --right P --qty 10 --side SELL --limit 3.50 --yes
```

**Multi-leg spread:** Use inline Python with `ib_insync` (see `ib-order-execution` skill)

### Exit Orders

After entry fill, place exit orders:
1. **Stop Loss** — Stop-limit order at stop price
2. **Target Profit** — Limit sell order at target

**Note:** IB rejects limit orders >40% from current price. Use exit order service.

---

## Exit Order Service

Automatically places pending target orders when IB will accept them.

**Check status:**
```bash
python3 scripts/exit_order_service.py --status
```

**Run single check:**
```bash
python3 scripts/exit_order_service.py
```

**Run as daemon (every 5 min during market hours):**
```bash
python3 scripts/exit_order_service.py --daemon
```

**Install as launchd service:**
```bash
./scripts/setup_exit_order_service.sh install
./scripts/setup_exit_order_service.sh status
./scripts/setup_exit_order_service.sh logs
```

**Logs:** `logs/exit-order-service.out.log`

---

## Options Flow Analysis

The `fetch_options.py` script provides comprehensive options analysis:

```bash
# Full analysis with formatted report
python3 scripts/fetch_options.py AAPL

# JSON output for programmatic use
python3 scripts/fetch_options.py AAPL --json

# Force specific data source
python3 scripts/fetch_options.py AAPL --source uw   # Unusual Whales
python3 scripts/fetch_options.py AAPL --source ib   # Interactive Brokers
python3 scripts/fetch_options.py AAPL --source yahoo # LAST RESORT ONLY
```

**Output includes:**
- Chain: Premium, volume, OI, bid/ask volume, P/C ratio, bias
- Flow: Institutional alerts, sweeps, bid/ask side premium, flow strength
- Combined: Synthesized bias with conflict detection and confidence rating

---

## Trade Blotter & P&L

### Today's Fills
```bash
python3 scripts/blotter.py
```

Shows:
- All executions grouped by contract
- Spread detection (put spreads, call spreads, risk reversals)
- Combined P&L for multi-leg positions
- Commission totals

### Historical Trades (Flex Query)
```bash
# All trades
python3 scripts/trade_blotter/flex_query.py

# Filter by symbol
python3 scripts/trade_blotter/flex_query.py --symbol EWY
```

Requires `IB_FLEX_TOKEN` and `IB_FLEX_QUERY_ID` environment variables.

---

## P&L Reports

When generating P&L reports, use the template:
```
.pi/skills/html-report/pnl-template.html
```

**Required sections:**
1. Header with CLOSED/OPEN status pill
2. 4 metrics: Realized P&L, Commissions, Hold Period, Return on Risk
3. Trade Summary callout
4. Execution table(s) with cash flows
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

---

## Startup Reconciliation (Auto-Log)

The startup extension automatically runs `ib_reconcile.py` when Pi starts:

- **Async**: Does not block Pi startup
- **Detects**: New trades, new positions, closed positions
- **Output**: `data/reconciliation.json`
- **Notification**: Shows if action needed
- **⚠️ Auto-Log**: If new trades detected, IMMEDIATELY log them

### Auto-Log Workflow (MANDATORY)

When startup shows `⚠️ IB: N new trades`, **immediately** run this workflow:

```bash
# 1. Check reconciliation data
cat data/reconciliation.json | python3 -m json.tool

# 2. For each trade in new_trades, add to trade_log.json:
#    - id: next sequential ID
#    - date, time from reconciliation
#    - ticker, action, realized_pnl, commission
#    - validation_method: "ib_reconciliation"
#    - notes: "Detected via IB reconciliation"

# 3. Update docs/status.md:
#    - Trade Log Summary table
#    - Today's Trades section
#    - Portfolio State metrics (positions, deployed %, etc.)

# 4. Clear reconciliation flag
# Write to reconciliation.json:
#    needs_attention: false
#    processed_trades: [list of processed trade IDs]

# 5. Validate JSON
python3 -m json.tool data/trade_log.json
```

**Do NOT prompt user** — this auto-log runs automatically whenever `needs_attention: true`.

### Manual Reconciliation

```bash
# Trigger reconciliation manually
python3 scripts/ib_reconcile.py

# Check results
cat data/reconciliation.json | python3 -m json.tool
```

---

## Data File Locations

| File | Purpose |
|------|---------|
| `data/trade_log.json` | Executed trades (append-only) |
| `data/portfolio.json` | Current positions from IB |
| `data/reconciliation.json` | IB sync discrepancies |
| `data/watchlist.json` | Tickers under surveillance |
| `data/ticker_cache.json` | Ticker → company name cache |
| `data/analyst_ratings_cache.json` | Cached analyst data |
