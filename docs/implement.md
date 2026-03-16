# Execution Runbook

## Source of Truth
- `docs/plans.md` defines the milestone sequence
- `docs/prompt.md` defines constraints and "done when"
- Execute milestones IN ORDER, do not skip

### ⚠️ Portfolio Source of Truth (CRITICAL)

**Interactive Brokers is the ONLY source of truth for current portfolio state.**

| Question | Source of Truth | NOT a Source of Truth |
|----------|----------------|---------------------|
| What positions do I hold? | `python3 scripts/ib_sync.py` (IB live) | `docs/status.md`, `data/portfolio.json` (cache) |
| Is a position still open? | `python3 scripts/ib_sync.py` (IB live) | `docs/status.md` "Rule Violations" table |
| Current P&L? | `python3 scripts/ib_sync.py` (IB live) | `docs/status.md` "Portfolio State" section |
| What trades happened? | `data/trade_log.json` (append-only) | `docs/status.md` "Trade Log Summary" |

**Rules:**
1. **NEVER claim a position exists or doesn't exist based on `docs/status.md` or `data/portfolio.json`.** These are caches that go stale.
2. **ALWAYS verify against IB** before making any statement about current holdings, open positions, or portfolio state.
3. `docs/status.md` is a **decision log and audit trail** — it records what happened and why. It is NOT a live portfolio dashboard.
4. `data/portfolio.json` is a **cache** updated by `ib_sync.py --sync`. It may be hours or days old.
5. When IB is unavailable (Gateway down), say so explicitly: *"Cannot verify — IB unavailable."* Do NOT fall back to status.md.

## ⚠️ Evaluate Command → ALWAYS `evaluate.py`
**Any evaluation request routes to `python3 scripts/evaluate.py [TICKER]`. No exceptions.**
Even if the user provides manual steps (e.g., "run fetch_flow.py, then fetch_options.py"),
ignore the manual steps and run the unified script. It handles M1–M3B (plus M1D news/catalysts) in parallel.

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

### 5B. Strategy Registry Sync (MANDATORY)
- **`data/strategies.json` MUST stay in sync with `docs/strategies.md`**
- When a new strategy is added to `docs/strategies.md`, IMMEDIATELY add a corresponding entry to `data/strategies.json`
- When a strategy is modified (status, commands, instruments, etc.), update both files
- When a strategy is deprecated/removed, update both files
- **Required fields per strategy**: `id`, `name`, `status`, `description`, `edge`, `instruments`, `hold_period`, `win_rate`, `target_rr`, `risk_type`, `commands`, `doc`
- Optional fields: `manager_override` (only for undefined-risk strategies)
- After any change, validate: `python3 -m json.tool data/strategies.json`
- The `strategies` command reads `data/strategies.json` — if it's stale, users see outdated info

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
| Fetch news & catalysts | `python3 scripts/fetch_news.py [TICKER]` |
| Calculate Kelly | `python3 scripts/kelly.py --prob P --odds O --bankroll B` |

### Scanning Commands
| Action | Command |
|--------|---------|
| **⭐ GARCH Convergence (all presets)** | `python3 scripts/garch_convergence.py --preset all` |
| GARCH Convergence (one preset) | `python3 scripts/garch_convergence.py --preset semis` |
| GARCH Convergence (file preset) | `python3 scripts/garch_convergence.py --preset sp500-semiconductors` |
| GARCH Convergence (ad-hoc) | `python3 scripts/garch_convergence.py NVDA AMD GOOGL META` |
| GARCH Convergence (JSON) | `python3 scripts/garch_convergence.py --preset all --json` |
| **⭐ Risk Reversal** | `python3 scripts/risk_reversal.py IWM` |
| Risk Reversal (bearish) | `python3 scripts/risk_reversal.py SPY --bearish` |
| Risk Reversal (custom) | `python3 scripts/risk_reversal.py QQQ --bankroll 500000 --min-dte 21` |
| LEAP IV scan (UW) | `python3 scripts/leap_scanner_uw.py --preset sectors` |
| LEAP IV scan (IB) | `python3 scripts/leap_iv_scanner.py AAPL --portfolio` |
| Discovery (market-wide) | `python3 scripts/discover.py` |
| Discovery (preset) | `python3 scripts/discover.py ndx100` |
| Discovery (tickers) | `python3 scripts/discover.py AAPL MSFT NVDA` |
| Watchlist scan | `python3 scripts/scanner.py` |
| **⭐ Stress Test (model)** | `python3 scripts/scenario_analysis.py` (update params first, outputs `/tmp/scenario_analysis.json`) |
| **⭐ Stress Test (report)** | `python3 scripts/scenario_report.py` (reads JSON, generates HTML, opens browser) |

### Portfolio Commands
| Action | Command |
|--------|---------|
| **⭐ Generate portfolio report** | `python3 scripts/portfolio_report.py` (self-contained: IB + DP flow + HTML) |
| Portfolio report (no browser) | `python3 scripts/portfolio_report.py --no-open` |
| Free trade analysis | `python3 scripts/free_trade_analyzer.py --table` |
| Sync IB portfolio | `python3 scripts/ib_sync.py --sync` |
| Run reconciliation | `python3 scripts/ib_reconcile.py` |
| View today's fills | `python3 scripts/blotter.py` |
| Fetch historical trades | `python3 scripts/trade_blotter/flex_query.py --symbol [TICKER]` |
| Start realtime server | `node scripts/ib_realtime_server.js` |
| Validate JSON | `python3 -m json.tool data/[file].json` |

### Context Engineering Commands
| Action | Command |
|--------|---------|
| **View persistent memory** | `python3 scripts/context_constructor.py` |
| View as JSON | `python3 scripts/context_constructor.py --json` |
| View manifest only | `python3 scripts/context_constructor.py --manifest-only` |
| **Save a fact** | `python3 scripts/context_constructor.py --save-fact "key" "value" --confidence 0.95 --source "source"` |
| **Save session episode** | `python3 scripts/context_constructor.py --save-episode "summary" --session-id "id"` |

### Tweet-It Commands
| Action | Command |
|--------|---------|
| **Generate tweet + card** | `tweet-it` (6-step workflow: text → card HTML → screenshot → base64 → preview → open) |

**⚠️ Card PNG must be base64-encoded into preview HTML as a data URI.** Chrome CORS blocks all `file://` image loads. See `.pi/skills/tweet-it/SKILL.md` for the full workflow.

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
| **IBC Gateway status** | `~/ibc/bin/status-secure-ibc-service.sh` |
| **IBC Gateway start** | `~/ibc/bin/start-secure-ibc-service.sh` |
| **IBC Gateway stop** | `~/ibc/bin/stop-secure-ibc-service.sh` |
| **IBC Gateway restart** | `~/ibc/bin/restart-secure-ibc-service.sh` |
| **IBC remote helper** | `./scripts/ibc_remote_control.sh check` |

### IB Gateway Management (IBC)

IB Gateway is managed by a **machine-global secure IBC service** (`local.ibc-gateway`). The active install lives at `~/ibc-install/`, with config and wrappers under `~/ibc/`. Credentials are stored in macOS Keychain, not on disk.

**Service commands:**
```bash
~/ibc/bin/start-secure-ibc-service.sh    # Start Gateway via launchd
~/ibc/bin/stop-secure-ibc-service.sh     # Stop Gateway
~/ibc/bin/restart-secure-ibc-service.sh  # Restart Gateway
~/ibc/bin/status-secure-ibc-service.sh   # Show launchd state
tail -f ~/ibc/logs/ibc-gateway-service.log
```

**Automated lifecycle:**
1. **00:00** — launchd starts Gateway via IBC
2. The secure runner reads credentials from Keychain, writes a temporary `0600` runtime config, and launches Gateway
3. You approve 2FA on IBKR Mobile once
4. **11:58 PM** — IBC restarts Gateway (reuses auth session, no 2FA)
5. **Sunday 07:05** — Cold restart with full re-auth (2FA required)

**Key config settings (`~/ibc/config.secure.ini`):**
| Setting | Value | Purpose |
|---------|-------|---------|
| `ExistingSessionDetectedAction` | `primary` | Gateway reconnects if bumped |
| `AcceptIncomingConnectionAction` | `accept` | No popup for API connections |
| `AutoRestartTime` | `11:58 PM` | Daily restart before IB's forced window |
| `ColdRestartTime` | `07:05` | Sunday re-auth |
| `CommandServerPort` | `7462` | IBC command server for stop/restart |
| `IbLoginId` / `IbPassword` | unset in file | Credentials come from Keychain only |

**Architecture:**
- LaunchAgent: `~/Library/LaunchAgents/local.ibc-gateway.plist`
- Runner: `~/ibc/bin/run-secure-ibc-gateway.sh`
- Logs: `~/ibc/logs/ibc-gateway-service.log` plus IBC diagnostics under `~/ibc/logs/`
- `KeepAlive=false` — IBC/Gateway manage their own lifecycle via `AutoRestartTime`

**Phase 1 remote access dependencies:**
- `Tailscale.app` on the Mac
- Tailscale on the iPhone, connected to the same tailnet
- macOS `Remote Login` enabled so SSH listens on port `22`
- iPhone SSH client such as Termius, Blink Shell, or Prompt
- Optional: dedicated SSH public key in `~/.ssh/authorized_keys`

**Phase 1 remote access usage:**
```bash
# Direct secure service commands over SSH
ssh joemccann@macbook-pro '~/ibc/bin/status-secure-ibc-service.sh'
ssh joemccann@macbook-pro '~/ibc/bin/restart-secure-ibc-service.sh'

# Optional repo helper
ssh joemccann@macbook-pro 'cd /Users/joemccann/dev/apps/finance/convex-scavenger && ./scripts/ibc_remote_control.sh ibc-status'
```

**Reference:** `docs/ibc-remote-access.md`

**Troubleshooting:**
- Gateway not running after a scheduled start → approve 2FA on IBKR Mobile, or run `~/ibc/bin/start-secure-ibc-service.sh`
- `ExistingSessionDetectedAction=primary` means this Gateway always wins session conflicts
- IBC command server (port 7462) allows `STOP`, `RESTART`, `RECONNECT` commands via `echo "STOP" | nc localhost 7462`
- Legacy `scripts/setup_ibc.sh` is retained for historical reference only and is not the active service path

### IB Connection Ports
| Port | Environment |
|------|-------------|
| 7496 | TWS Live |
| 7497 | TWS Paper |
| 4001 | IB Gateway Live |
| 4002 | IB Gateway Paper |
| 7462 | IBC Command Server (stop/restart Gateway) |

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

## Scenario Stress Test

**Interactive two-step command (`stress-test`):**

1. Agent asks: *"What is the change in the overall market?"*
2. User describes scenario → Agent parses, models, generates report

```bash
# Template
.pi/skills/html-report/stress-test-template.html

# Output
reports/stress-test-{date}.html

# Pricing engine (update parameters per scenario, then run)
python3 scripts/scenario_analysis.py

# Reference report generator (reads /tmp/scenario_analysis.json)
python3 scripts/scenario_report.py
```

**Model pipeline:**
1. Parse user scenario into: SPX move, VIX level, sector shocks (oil, crypto, etc.)
2. Update `scenario_analysis.py` parameters: `SCENARIO_SPX_MOVE`, `SCENARIO_VIX`, `SCENARIO_OIL_MOVE`, etc.
3. Run `scenario_analysis.py` → outputs `/tmp/scenario_analysis.json`
4. Write per-position narratives (oil, SPX beta, VIX stress, options structure)
5. Generate HTML from template with all 10 sections + expandable ▶ detail rows
6. Open in browser

**Key modeling constraints:**
- Single per-ticker IV (never per-leg)
- Defined risk P&L clamped: `[-debit, +max_width]`
- LEAP IV dampening: >180 DTE 50%, 60-180 DTE 75%, <60 DTE 100%
- VIX crash-beta only when scenario VIX > 30

**Reference:** `reports/scenario-stress-test-2026-03-08.html`

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
| `context/memory/fact/` | Persistent facts (trading lessons, API quirks, portfolio state) |
| `context/memory/episodic/` | Session summaries |
| `context/human/` | Human annotations (overrides model output) |
| `context/history/_transactions.jsonl` | All context read/write operations |
| `context/metadata.json` | Governance policies + token budget |
