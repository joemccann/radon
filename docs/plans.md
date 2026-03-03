# Evaluation Plan - Milestone Workflow

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

## Milestone 2: Dark Pool Flow Analysis
**Action**: Fetch 5-day dark pool / OTC data
**Validation**:
```bash
python3 scripts/fetch_flow.py [TICKER]
```
**Acceptance Criteria**:
- Aggregate buy ratio calculated
- Daily breakdown available
- Flow direction determined (ACCUMULATION/DISTRIBUTION/NEUTRAL)
- Flow strength quantified
- Minimum 20 prints for statistical significance
**Stop Condition**: If NEUTRAL or <20 prints → FLAG insufficient edge signal

---

## Milestone 3: Options Flow Analysis
**Action**: Fetch options chain activity and institutional flow alerts
**Validation**:
```bash
python3 scripts/fetch_options.py [TICKER]
```
**Data Sources**: IB (spot price) → UW (chain + flow) → Yahoo (fallback)

**Acceptance Criteria**:
- Call/put premium ratio calculated
- Chain bias determined (BULLISH/LEAN_BULLISH/NEUTRAL/LEAN_BEARISH/BEARISH)
- Flow alerts analyzed (if available)
- Flow bias and strength quantified (0-100)
- Combined bias synthesized with confidence rating
- Chain liquidity assessed (bid-ask spreads, OI)

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

## Milestone 4: Edge Determination
**Action**: Synthesize flow data into edge verdict
**Criteria for PASS**:
- Sustained direction (3+ consecutive days same direction)
- Flow strength >50 on aggregate OR >70 on recent days
- Options flow confirms (or at least doesn't contradict)
- Signal NOT yet reflected in price (check recent price action)
**Output**: EDGE_CONFIRMED or EDGE_REJECTED with specific reasoning
**Stop Condition**: If EDGE_REJECTED → NO TRADE (stop here)

---

## Milestone 5: Structure Proposal
**Action**: Design convex options structure
**Options**:
- ATM/OTM calls (bullish edge)
- ATM/OTM puts (bearish edge)
- Vertical spreads (defined risk, reduced cost)
**Validation**: Structure must have R:R ≥ 2:1
**Stop Condition**: If R:R < 2:1 → restructure or ABORT

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

**If NO_TRADE (rejected)**:
Log to `docs/status.md` under "Recent Evaluations" with:
- ticker, date, failing_gate, reason

**Validation**: JSON schema valid for trade_log.json
