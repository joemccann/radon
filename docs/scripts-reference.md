# Scripts & CLI Reference

The CLI surface area lives behind shell aliases registered in `.pi/`. Run `commands` from the repo root to see the live registry.

## Scanning

| Command | Description |
|---------|-------------|
| `scan` | Watchlist dark pool flow scan with CRI regime overlay and HTML report |
| `discover` | Market-wide or targeted discovery scan for new candidates |
| `leap-scan [TICKERS]` | Find LEAP IV mispricing opportunities (scheduled default: largecaps = NDX+SPX) |
| `garch-convergence [TICKERS]` | Cross-asset implied-versus-realized volatility divergence scan (scheduled default: largecaps = NDX+SPX curated pairs) |
| `seasonal [TICKERS]` | Monthly seasonality analysis from EquityClock |
| `analyst-ratings [TICKERS]` | Ratings, price targets, and recent changes |
| `vcg` | VCG-R scan with VIX/VVIX/HYG regression and severity tiers |
| `gex-scan` | Gamma exposure scan with GEX flip, max magnet, put/call wall, bias |
| `cri-scan` | Crash Risk Index with CTA exposure model |

## Evaluation & Risk

| Command | Description |
|---------|-------------|
| `evaluate [TICKER]` | Full seven-milestone trade evaluation (see CLAUDE.md "Evaluation — 7 Milestones") |
| `stress-test` | Interactive bear/base/bull scenario report for the current portfolio |
| `risk-reversal [TICKER]` | IV-skew analysis for directional risk-reversal structures |

## Portfolio & Operations

| Command | Description |
|---------|-------------|
| `portfolio` | Live portfolio report with dark pool thesis checks |
| `free-trade` | Analyze multi-leg positions for free-trade progression |
| `journal` | View recent trade log entries |
| `sync` | Pull live portfolio data from Interactive Brokers |
| `blotter` | Today's fills, grouped spreads, and commission totals |
| `blotter-history` | Historical trades via IB Flex Query |

## Research & System

| Command | Description |
|---------|-------------|
| `strategies` | Show the strategy registry |
| `menthorq-cta` | Fetch or backfill institutional CTA positioning data manually |
| `x-scan [@ACCOUNT]` | Fetch X sentiment through xAI |
| `x-scan-browser [@ACCOUNT]` | Fetch X sentiment through browser scraping |
| `commands` | Display the full command registry |

Tickets place `STP` and `STP LMT` through `/api/orders/place`. There is no extra CLI alias. Scheduled LEAP and GARCH default to the virtual `largecaps` preset (NDX+SPX). The wider `indexes` union (adds Russell 2000, ~2500 tickers at 3 Unusual Whales requests each) is opt-in per run — three GARCH runs on it spend three quarters of the 40k daily UW cap. Knowledge retrieval is the `radon-kb` MCP (`kb_search`, `kb_incidents`, `kb_recent`), not a shell alias.

## Test Runners

```bash
# Affected-file Python tests (fast, scoped)
python3.13 scripts/run_pytest_affected.py
python3.13 scripts/run_pytest_affected.py --files scripts/ib_sync.py -- -q

# Full Python suite
python -m pytest scripts/tests/ -v

# Frontend (repo root, bun / bunx only)
bunx vitest run --config vitest.config.ts
cd web && bun test

# E2E
cd web && npx playwright test
```

`run_pytest_affected.py` resolves changed Python files to matching tests under `scripts/tests/` and `scripts/trade_blotter/`, skipping pytest entirely when no Python changed. Prefer it for scoped work.

## Order-route integration harness

`web/tests/order-e2e.test.ts` boots an isolated test-mode FastAPI instance through `web/tests/fastapiHarness.ts`. The harness sets `RADON_API_TEST_MODE=1`, points `RADON_API_URL` at the isolated server, and refuses to reuse the live broker-backed `localhost:8321` process unless it explicitly reports `test_mode: true`. Test mode disables IB Gateway / pool startup and stubs all order endpoints, so Vitest never touches an active IBC or IB session.
