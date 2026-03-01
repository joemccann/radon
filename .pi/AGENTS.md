# Convex Scavenger — Project Instructions

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

## Evaluation Milestones

Always follow in order. Stop immediately if a gate fails.

1. **Validate Ticker** → `python3 scripts/fetch_ticker.py [TICKER]`
2. **Dark Pool Flow** → `python3 scripts/fetch_flow.py [TICKER]`
3. **Options Flow** → `python3 scripts/fetch_options.py [TICKER]`
4. **Edge Decision** → PASS/FAIL with reasoning (stop if FAIL)
5. **Structure** → Design convex position (stop if R:R < 2:1)
6. **Kelly Sizing** → Calculate + enforce caps
7. **Log Decision** → Append to trade_log.json

## Output Format

- Always show: signal → structure → Kelly math → decision
- State probability estimates explicitly, flag uncertainty
- When a trade doesn't meet criteria, say so immediately with the failing gate
- Never rationalize a bad trade
- Log ALL decisions (TRADE and NO_TRADE)

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_ticker.py` | Validate ticker via dark pool activity |
| `scripts/fetch_flow.py` | Fetch dark pool + options flow data |
| `scripts/fetch_options.py` | Options chain data (stub) |
| `scripts/scanner.py` | Scan watchlist, rank by signal strength |
| `scripts/discover.py` | Market-wide flow scanner for new candidates |
| `scripts/kelly.py` | Kelly criterion calculator |
| `scripts/ib_sync.py` | Sync live portfolio from Interactive Brokers |
| `scripts/leap_iv_scanner.py` | LEAP IV mispricing scanner (IB connection required) |
| `scripts/leap_scanner_uw.py` | LEAP IV scanner using UW + Yahoo Finance (no IB needed) |

## Interactive Brokers Integration

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

**Setup:**
1. Install: `pip install ib_insync`
2. In TWS: Configure → API → Settings → Enable "ActiveX and Socket Clients"
3. Ensure "Read-Only API" is unchecked if you want order capability later

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
| `data/trade_log.json` | Append-only decision journal (TRADE + NO_TRADE) |
| `data/ticker_cache.json` | Local cache of ticker → company name mappings |

## Documentation

| File | Purpose |
|------|---------|
| `docs/prompt.md` | Spec, constraints, deliverables |
| `docs/plans.md` | Milestone workflow with validation steps |
| `docs/implement.md` | Execution runbook |
| `docs/status.md` | Current state, recent decisions, audit log |
| `docs/strategies.md` | Trading strategies (Dark Pool Flow, LEAP IV Mispricing) |

## Tools Available

- `bash` — Run Python scripts in ./scripts/
- `read`/`write`/`edit` — Manage data and documentation files
- `kelly_calc` — Built-in fractional Kelly calculator

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
