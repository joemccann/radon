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
**Action**: Fetch options flow alerts and premium data
**Validation**:
```bash
python3 scripts/fetch_options.py [TICKER]
```
**Acceptance Criteria**:
- Call/put premium ratio calculated
- Bias determined
- Chain liquidity assessed (bid-ask spreads, OI)
**Stop Condition**: If illiquid (spreads >10%, OI <100) → FLAG structure risk

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
**Action**: Document decision in trade_log.json
**Required Fields**:
- timestamp
- ticker
- company_name (VERIFIED)
- decision (TRADE / NO_TRADE)
- failing_gate (if NO_TRADE)
- edge_summary
- structure (if TRADE)
- kelly_math (if TRADE)
- position_details (if TRADE)
**Validation**: JSON schema valid, appended to log
