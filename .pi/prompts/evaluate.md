Full trade evaluation for {{ticker}}:

**STEP 1: EDGE CONFIRMATION**
- Run `python scripts/fetch_flow.py {{ticker}}` for current dark pool data
- What is the flow direction and magnitude?
- What happened historically when similar flow occurred?
- Is the flow still active or has price already moved?

**STEP 2: CONVEXITY SCREENING**
- Run `python scripts/fetch_options.py {{ticker}}` for the options chain
- Current IV rank/percentile (low IV = better entry)
- Scan strikes from OTM inward:
  - For each candidate strike: estimate P(ITM), conditional settlement, expected value
  - Calculate EV/cost ratio — need ≥ 2:1
  - Consider vertical spreads if upside is capped
- Select the structure with best convexity profile

**STEP 3: KELLY SIZING**
- Run `python scripts/kelly.py --odds [X] --prob [Y]`
- Inputs: odds ratio from Step 2, your P(ITM) estimate
- If Kelly optimal < 2.5% → marginal, skip
- If Kelly optimal 10-20% → strong, allocate 2.5%
- If Kelly optimal > 20% → check convexity (probably insufficient)
- Check portfolio.json: total exposure vs. avg Kelly → room for new position?

**STEP 4: DECISION**
- PASS all three gates? → Specify exact contracts, quantity, cost
- FAIL any gate? → State which gate failed and why. No trade.
