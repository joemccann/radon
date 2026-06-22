# Fincept -> Radon: 14-feature implementation

Branch: `feat/fincept-adoption` (NOT pushed; main auto-deploys to prod, so review before shipping).
Method: sequential TDD per feature (red -> green), full suite must pass. Workflow-orchestrated.

House rules (every feature): TDD red/green; mock all UW/IB/Turso (never hit live); no raw hex in UI
(brand tokens, 4px panel radius); no em dashes in user-facing copy; Python tests in `scripts/tests/`,
web tests in `web/tests/` (`cd web && npm run test`); next migration index continues from 0017.

## Build order (dependency-sequenced)

### Foundations
- [ ] F1 Shared transaction-cost + slippage model (`scripts/costs.py` + `web/lib/order/costs.ts`) [S]
- [ ] F2 Provider-agnostic LLM layer (`web/lib/llm/provider.ts`) [M]

### Data-surface features (reuse existing UW client methods)
- [ ] F3 Earnings/FDA/economic catalyst feed (`scripts/fetch_catalysts.py` + `/catalysts`) [S]
- [ ] F4 Congress + insider informed-flow surface (`scripts/fetch_informed_flow.py` + `/informed-flow/{t}`) [S]
- [ ] F5 Polymarket event-odds overlay (`scripts/clients/polymarket_client.py` + `/event-odds/{t}`) [S]

### Engines & guards
- [ ] F6 User-configurable signal alert rules engine (`scripts/alerts/` + `alert_rules` table + `/api/alerts`) [M]
- [ ] F7 Agentic tool-calling assistant loop (`web/lib/assistant/tools.ts` + rewrite assistant route) [M]
- [ ] F8 Portfolio correlation-aware risk-budget guard (`scripts/portfolio_risk.py`) [M]
- [ ] F9 SABR/SVI vol-surface fit (`scripts/vol_surface.py`) [M]
- [ ] F10 Toxic-flow / order-book-imbalance microstructure (`scripts/microstructure.py`, imbalance+microprice; skip VPIN) [M]
- [ ] F11 Forecast-driven scan scoring: wire Chronos-2 quantiles into ranking [M]

### Heavy lifts (tested first increment)
- [ ] F12 Strategy backtester + walk-forward harness (`scripts/backtest/`) [L]
- [ ] F13 Paper-trading / shadow-fill engine (`scripts/paper/`) [L]
- [ ] F14 Visual flow-pipeline node-graph composer (`scripts/workflow/` + `web/app/workflow/`) [XL]

## Verification
- [ ] Each feature: targeted tests green before moving on
- [ ] Full pytest suite green
- [ ] Full vitest suite green
- [ ] Scoped per-feature commits (do NOT sweep in-flight ib_watchdog / forecast changes)

## Review
(filled in after the run)
