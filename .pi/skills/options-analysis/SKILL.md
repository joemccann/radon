---
description: On-demand options pricing and structure analysis for the Convex Scavenger trading agent
---

# Options Analysis Skill

## Description
On-demand options pricing and structure analysis for the Convex Scavenger trading agent.

## Capabilities
- Fetch and parse options chains for a given ticker
- Calculate implied volatility rank and percentile
- Evaluate convexity profile of candidate structures (calls, puts, vertical spreads)
- Estimate P(ITM), conditional settlement value, and expected value for each strike
- Compare structures: naked options vs. spreads for optimal convexity

## Usage
Invoke when evaluating a specific ticker's options chain as part of the /evaluate workflow.

## Data Source Priority

When fetching live options pricing, use sources in this order:

| Priority | Source | Command/Method |
|----------|--------|----------------|
| **1** | Interactive Brokers | `python3 scripts/ib_sync.py` (requires TWS/Gateway) |
| **2** | Unusual Whales | See API reference below |
| **3** | Yahoo Finance | `agent-browser` to scrape options chain (fallback, rate limited) |

**Note:** IB provides the most accurate real-time bid/ask spreads. Yahoo Finance should only be used as a last resort due to rate limiting and potential data delays.

## Unusual Whales API for Options

**API Reference:** `docs/unusual_whales_api.md`
**Full Spec:** `docs/unusual_whales_api_spec.yaml`

### Key Options Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/stock/{ticker}/option-contracts` | All option contracts with greeks, IV, OI |
| `GET /api/stock/{ticker}/expiry-breakdown` | Available expirations with volume/OI summary |
| `GET /api/stock/{ticker}/greeks?expiry=YYYY-MM-DD` | Greeks for each strike at an expiry |
| `GET /api/stock/{ticker}/flow-per-strike` | Flow aggregated by strike |
| `GET /api/stock/{ticker}/flow-per-expiry` | Flow aggregated by expiration |
| `GET /api/stock/{ticker}/greek-exposure` | GEX data for gamma analysis |
| `GET /api/stock/{ticker}/volatility/realized` | IV vs realized volatility |
| `GET /api/stock/{ticker}/iv-rank` | IV rank percentile |
| `GET /api/option-contract/{symbol}/historic` | Historical data for specific contract |

### Example: Fetch Option Chain
```bash
curl -H "Authorization: Bearer $UW_TOKEN" \
  "https://api.unusualwhales.com/api/stock/AAPL/option-contracts?expiry=2026-04-17&option_type=call&maybe_otm_only=true"
```

### Response includes:
- `strike`, `expiry`, `option_type`
- `bid`, `ask`, `mid_price`
- `volume`, `open_interest`, `volume_oi_ratio`
- `implied_volatility`, `delta`, `gamma`, `theta`, `vega`
- `underlying_price`

## Dependencies
- scripts/fetch_options.py — data retrieval
- scripts/kelly.py — position sizing
- data/portfolio.json — current exposure context
