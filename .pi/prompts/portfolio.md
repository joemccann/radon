Portfolio status report:

1. Read portfolio.json
2. For each open position, fetch current price via scripts
3. Report:
   - Position | Entry Cost | Current Value | P&L | Days to Expiry
   - Original thesis still intact? (check current flow vs. entry flow)
   - Kelly optimal at entry vs. current
4. Portfolio summary:
   - Total positions | Total % deployed | Avg Kelly optimal
   - Remaining capacity for new positions
   - Portfolio drawdown from peak
5. Flag any positions approaching expiry needing thesis review
