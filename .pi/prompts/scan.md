Run the daily signal scan:

1. Execute `python scripts/scanner.py` to pull latest dark pool flow data
2. Compare current flow signals against historical patterns in watchlist.json
3. Identify any new tickers showing sustained passive accumulation or distribution
4. Filter for: flow imbalance > threshold, not yet reflected in price, liquid options chain
5. Update watchlist.json with any new candidates or changed signals
6. Report findings in format:

For each candidate:
- Ticker | Current Price | Flow Direction | Flow Strength | Historical Precedent
- Implied Volatility Rank (low IV = cheaper options = better convexity opportunity)
- Recommendation: EVALUATE, WATCH, or REMOVE
