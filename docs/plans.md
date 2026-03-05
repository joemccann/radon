# Evaluation Plan - Milestone Workflow

## ⚠️ How to Run an Evaluation (MANDATORY — NO EXCEPTIONS)

**ANY evaluation request — regardless of how the user phrases it — routes to:**
```bash
python3 scripts/evaluate.py [TICKER]        # human-readable
python3 scripts/evaluate.py [TICKER] --json  # structured JSON
```

**Even if the user provides manual step-by-step instructions** (e.g., "run fetch_flow.py first, then fetch_options.py"), **ignore the manual steps and run evaluate.py instead.** The script handles milestones M1–M3B in parallel, then M4 (edge) sequentially. If edge passes, the operator designs the structure (M5) and runs Kelly (M6) interactively.

**NEVER manually step through milestones 1–3B.** The script handles all parallel data fetching, includes today's intraday data, and stops at the first failing gate.

## ⚠️ CRITICAL: Fresh Data Rule

**Every milestone that fetches 3rd-party data MUST fetch it live at execution time.**

- Scan results are LEADS, not evidence. Re-fetch everything during evaluation.
- If market is open, data must be from today. Include timestamps in output.
- If any data is stale (from a previous session/day), stop and re-fetch before proceeding.
- Output a **Data Freshness** header at the start of every evaluation:
  ```
  📊 Data as of: 2026-03-05 10:45 AM ET (LIVE)
  ```

---

## Milestone 0: Startup Reconciliation (Automatic + Auto-Log)
**Action**: Pi startup extension runs IB reconciliation asynchronously
**Validation**: Check notification or `data/reconciliation.json`
**Acceptance Criteria**:
- New trades detected and flagged
- New positions identified
- Closed positions identified
- Notification shown if action needed
- **⚠️ If `needs_attention: true` → Auto-log trades immediately**

**Auto-Log Workflow (MANDATORY when new trades detected):**
1. Read `data/reconciliation.json`
2. For each trade in `new_trades`:
   - Assign next ID (max existing ID + 1)
   - Create trade_log entry with full details
   - Set `validation_method: "ib_reconciliation"`
3. Append to `data/trade_log.json`
4. Update `docs/status.md`:
   - Trade Log Summary table
   - Today's Trades section
   - Portfolio State metrics
5. Clear reconciliation:
   - Set `needs_attention: false`
   - Move to `processed_trades`
6. Validate: `python3 -m json.tool data/trade_log.json`

**Do NOT wait for user request — this is automatic on every startup with new trades.**

---

## Milestone 1: Ticker Validation
**Action**: Fetch and verify ticker metadata
**Validation**:
```bash
python3 scripts/fetch_ticker.py [TICKER]
```
**Acceptance Criteria**:
- Company name returned from live source
- Sector/industry identified
- Market cap and avg volume retrieved
- Options availability confirmed
**Stop Condition**: If ticker invalid or no options chain → ABORT

---

## Milestone 1B: Seasonality Analysis
**Action**: Fetch and analyze historical monthly performance
**Validation**:
```bash
# Download seasonality chart
curl -s -o /tmp/{TICKER}_sheet.png "https://charts.equityclock.com/seasonal_charts/{TICKER}_sheet.png"
# Then read the image for analysis
```
**Acceptance Criteria**:
- Current month win rate extracted (% of years positive)
- Current month average return extracted
- Next 1-2 months assessed for hold-through scenarios
- Seasonality rating assigned: FAVORABLE (>60% win rate, >5% avg) / NEUTRAL (50-60%) / UNFAVORABLE (<50%)
**Output**: Seasonality does NOT change score but IS reported in analysis
**Note**: Some tickers may not have data (newer IPOs, small caps). Flag as "NO DATA" and proceed.

---

## Milestone 1C: Analyst Ratings
**Action**: Fetch analyst consensus and recent changes
**⚠️ FRESH DATA**: Re-fetch at evaluation time. Ratings may have changed since last scan.
**Validation**:
```bash
python3 scripts/fetch_analyst_ratings.py [TICKER]
```
**Acceptance Criteria**:
- Buy/Hold/Sell breakdown retrieved
- Price target and upside % calculated
- Recent upgrades/downgrades noted
- **Data timestamp included in output**
**Output**: Analyst data is CONTEXT, not a gate
**Note**: Use to confirm or question flow signals

---

## Milestone 2: Dark Pool Flow Analysis
**Action**: Fetch 5-day dark pool / OTC data **including today**
**⚠️ FRESH DATA**: MUST run `fetch_flow.py` live. Today's flow may confirm or reverse the signal from prior days. NEVER rely on scan results — re-fetch.
**Validation**:
```bash
python3 scripts/fetch_flow.py [TICKER]
```
**Acceptance Criteria**:
- Aggregate buy ratio calculated
- Daily breakdown available **including today's date**
- Flow direction determined (ACCUMULATION/DISTRIBUTION/NEUTRAL)
- Flow strength quantified (0-100)
- Minimum 20 prints for statistical significance
- **Today's flow explicitly shown and analyzed** (if market open/recently closed)
- **Data timestamp included in output**
**Stop Condition**: If NEUTRAL or <20 prints → FLAG insufficient edge signal
**Stale Data Check**: If output does not include today's date → re-fetch or note gap

---

## Milestone 3: Options Flow Analysis
**Action**: Fetch options chain activity and institutional flow alerts **for today**
**⚠️ FRESH DATA**: MUST run `fetch_options.py` live at evaluation time. Chain premium, volume, and flow alerts change throughout the day. NEVER reuse scan data.
**Validation**:
```bash
python3 scripts/fetch_options.py [TICKER]
```
**Data Sources**: IBClient (spot price) → UWClient (chain + flow) → Yahoo (absolute last resort only)

**Acceptance Criteria**:
- Call/put premium ratio calculated
- Chain bias determined (BULLISH/LEAN_BULLISH/NEUTRAL/LEAN_BEARISH/BEARISH)
- Flow alerts analyzed (if available)
- Flow bias and strength quantified (0-100)
- Combined bias synthesized with confidence rating
- Chain liquidity assessed (bid-ask spreads, OI)
- **Data timestamp included in output**

**Key Metrics**:
| Metric | Source | Purpose |
|--------|--------|---------|
| Put/Call Ratio | UW chain | Directional sentiment |
| Bid/Ask Volume | UW chain | Buyer vs seller pressure |
| Flow Alerts | UW flow | Institutional activity |
| Sweep Premium | UW flow | Urgency signal |
| Combined Bias | Calculated | Final options signal |

**Interpretation**:
- P/C ratio >2.0x = BEARISH, <0.5x = BULLISH
- Bid-side dominant = selling pressure
- Ask-side dominant = buying pressure
- Sweeps = urgency, often predictive

**Stop Condition**: If illiquid (spreads >10%, OI <100) → FLAG structure risk
**Conflict Flag**: If chain bias contradicts flow bias → reduce confidence, note in analysis

---

## Milestone 3B: OI Change Analysis (REQUIRED)
**Action**: Fetch and analyze Open Interest changes to identify institutional positioning
**When to Use**: **EVERY evaluation** — this is mandatory, not optional
**⚠️ FRESH DATA**: MUST run `fetch_oi_changes.py` live. OI snapshots change daily — yesterday's OI data is stale.

**Validation**:
```bash
# Per-ticker OI changes (ALWAYS run this)
python3 scripts/fetch_oi_changes.py [TICKER]

# Filter for significant positions only
python3 scripts/fetch_oi_changes.py [TICKER] --min-premium 1000000

# Market-wide scan (for discover command)
python3 scripts/fetch_oi_changes.py --market --min-premium 10000000

# Verify specific external claims
python3 scripts/verify_options_oi.py [TICKER] --expiry [DATE] --verify "strike1:size1,strike2:size2"
```

**Why This Matters**:
UW has TWO separate data sources:
1. **Flow Alerts** (`/api/option-trades/flow-alerts`) — Filtered for "unusual" activity
2. **OI Changes** (`/api/stock/{ticker}/oi-change`) — Raw positioning data

Flow alerts may miss large institutional trades that don't trigger their filters.
**OI changes show ALL significant positioning regardless of whether it's "unusual".**

**Example:** The $95M MSFT LEAP call purchase did NOT appear in flow alerts but showed up clearly in OI changes:
```
MSFT OI Changes:
Symbol                    OI Change        Premium   Signal
MSFT270115C00625000      +100,458    $50,974,889   MASSIVE
MSFT270115C00575000       +50,443    $44,800,215   MASSIVE
MSFT270115C00675000       +50,148    $15,068,081   MASSIVE
```

**Signal Strength Classification**:
| Premium | Signal |
|---------|--------|
| > $10M | 🚨 MASSIVE |
| $5-10M | LARGE |
| $1-5M | SIGNIFICANT |
| < $1M | MODERATE |

**Acceptance Criteria**:
- Identify all OI changes > $1M premium
- Flag MASSIVE positions (> $10M)
- Cross-reference with flow alerts
- Note any large OI change NOT in flow alerts → hidden signal

**Cross-Reference with Flow Alerts**:
| Scenario | Interpretation |
|----------|----------------|
| Large OI change + Flow alert | ✅ Confirmed signal |
| Large OI change + NO flow alert | ⚠️ **Hidden signal — investigate** |
| Flow alert + Small OI change | Day trade, not positioning |

**Position Age Check**:
- If today's volume << OI → Position opened earlier, still held
- If today's volume ≈ OI → Position opened today
- Check `oi_diff_plain` for exact OI change from previous day

**Reference**: `docs/options-flow-verification.md`

---

## Milestone 4: Edge Determination
**Action**: Synthesize flow data into edge verdict
**⚠️ FRESH DATA**: Use ONLY data fetched in milestones 2, 3, 3B above (which must be today's data). Also fetch today's price action via IB to confirm signal is not yet priced in.
**Criteria for PASS**:
- Sustained direction (3+ consecutive days same direction) **including today**
- Flow strength >50 on aggregate OR >70 on recent days
- Options flow confirms (or at least doesn't contradict)
- Signal NOT yet reflected in price (check **today's** price action via IB)
**Output**: EDGE_CONFIRMED or EDGE_REJECTED with specific reasoning
**Stop Condition**: If EDGE_REJECTED → NO TRADE (stop here)

---

## Milestone 5: Structure Proposal
**Action**: Design convex options structure
**⚠️ FRESH DATA**: Fetch **live option quotes** (bid/ask/mid) from IB at structure time. Do NOT use option prices from an earlier session — IV and prices move intraday.
**Options**:
- ATM/OTM calls (bullish edge)
- ATM/OTM puts (bearish edge)
- Vertical spreads (defined risk, reduced cost)
**Validation**: Structure must have R:R ≥ 2:1 based on **live quotes**
**Stop Condition**: If R:R < 2:1 → restructure or ABORT

**⭐ REQUIRED: Generate Trade Specification Report**
After structure is designed, ALWAYS generate HTML report:
- Template: `.pi/skills/html-report/trade-specification-template.html`
- Output: `reports/{ticker}-evaluation-{date}.html`
- Reference: `reports/goog-evaluation-2026-03-04.html`

---

## Milestone 6: Kelly Sizing
**Action**: Calculate optimal position size
**Validation**:
```bash
python3 scripts/kelly.py --prob [P] --odds [ODDS] --bankroll [B]
```
**Acceptance Criteria**:
- Kelly optimal % calculated
- Fractional Kelly (0.25x) applied
- Hard cap 2.5% enforced
- Position contracts/cost computed
**Stop Condition**: If Kelly >20% → insufficient convexity, restructure

---

## Milestone 7: Final Decision & Log
**Action**: Log executed trades to trade_log.json; log rejections to docs/status.md

**If TRADE (executed)**:
Log to `data/trade_log.json` with fields:
- id (auto-increment)
- date, time
- ticker, company_name (VERIFIED)
- action: "TRADE", decision: "EXECUTED"
- contract, structure, fill_price, total_cost, contracts
- pct_of_bankroll, max_risk
- edge_analysis, kelly_calculation
- gates_passed, thesis, target_exit, stop_loss, notes

**If CLOSED (realized P&L)**:
Update existing entry or add new entry with:
- close_date, close_time
- exit_fills (price, shares, commission per fill)
- realized_pnl, return_on_risk
- outcome description

**If NO_TRADE (rejected)**:
Log to `docs/status.md` under "Recent Evaluations" with:
- ticker, date, failing_gate, reason

**Validation**: JSON schema valid for trade_log.json

---

## Portfolio Review Workflow

### Daily Startup
1. Check reconciliation notification
2. Review positions expiring <7 DTE
3. Check thesis alignment for logged positions
4. Flag positions below -50% stop

### Position Thesis Check
For each logged position:
1. Fetch current dark pool flow
2. Compare to entry flow
3. If flow reversed → flag for review
4. If flow unchanged → thesis intact

### P&L Reconciliation
When position closes:
1. Fetch fills from IB (today) or Flex Query (historical)
2. Calculate realized P&L with commissions
3. Update trade_log.json with close data
4. Generate P&L report if significant
