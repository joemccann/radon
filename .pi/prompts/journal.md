Log trade decision to trade_log.json:

Entry format:
{
  "date": "{{date}}",
  "ticker": "{{ticker}}",
  "action": "OPEN | CLOSE | SKIP",
  "structure": "description of option structure",
  "cost_per_contract": X,
  "num_contracts": X,
  "total_risk": X,
  "pct_of_bankroll": X,
  "edge_signal": "description of flow signal",
  "p_itm_estimate": X,
  "conditional_value": X,
  "expected_value": X,
  "kelly_optimal": X,
  "convexity_ratio": X,
  "gates_passed": ["CONVEXITY", "EDGE", "RISK_MGMT"],
  "notes": "{{notes}}"
}

Append to trade_log.json. Confirm entry logged.
