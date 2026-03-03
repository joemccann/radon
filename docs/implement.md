# Execution Runbook

## Source of Truth
- `docs/plans.md` defines the milestone sequence
- `docs/prompt.md` defines constraints and "done when"
- Execute milestones IN ORDER, do not skip

## Operating Rules

### 1. Validate Before Assuming
- NEVER identify a ticker from memory/training data
- ALWAYS run `fetch_ticker.py` first to get verified company info
- If script fails or returns no data, state "UNVERIFIED" and flag uncertainty

### 2. Milestone Discipline
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

## Command Reference
| Action | Command |
|--------|---------|
| Validate ticker | `python3 scripts/fetch_ticker.py [TICKER]` |
| Fetch dark pool flow | `python3 scripts/fetch_flow.py [TICKER]` |
| Fetch options data | `python3 scripts/fetch_options.py [TICKER]` |
| Fetch options (JSON) | `python3 scripts/fetch_options.py [TICKER] --json` |
| Fetch options (force UW) | `python3 scripts/fetch_options.py [TICKER] --source uw` |
| Fetch analyst ratings | `python3 scripts/fetch_analyst_ratings.py [TICKER]` |
| Calculate Kelly | `python3 scripts/kelly.py --prob P --odds O --bankroll B` |
| Sync IB portfolio | `python3 scripts/ib_sync.py --sync` |
| Validate JSON | `python3 -m json.tool data/[file].json` |

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
python3 scripts/fetch_options.py AAPL --source yahoo # Yahoo Finance
```

**Output includes:**
- Chain: Premium, volume, OI, bid/ask volume, P/C ratio, bias
- Flow: Institutional alerts, sweeps, bid/ask side premium, flow strength
- Combined: Synthesized bias with conflict detection and confidence rating
