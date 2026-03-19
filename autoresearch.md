# Autoresearch: Evaluate Command Speed Optimization

## Objective
Improve the execution speed of `scripts/evaluate.py` by ≥50%. The evaluation pipeline runs 7 milestones (M1-M3B, then M4-M7) to determine if a ticker has a tradeable edge. Currently ~2.5s for single ticker, ~23s for 5 tickers (sequential).

**Target**: Reduce single-ticker evaluation to <1.25s, multi-ticker (5) to <5.5s.

## Metrics
- **Primary**: `total_ms` (milliseconds, lower is better) — end-to-end time for 5 tickers
- **Secondary**: `single_ms` — single ticker evaluation time

## How to Run
`./autoresearch.sh` — outputs `METRIC total_ms=number` and `METRIC single_ms=number` lines.

## Files in Scope
| File | Purpose |
|------|---------|
| `scripts/evaluate.py` | Main evaluation orchestrator (832 lines) |
| `scripts/fetch_ticker.py` | M1: Ticker validation via UW dark pool API |
| `scripts/fetch_flow.py` | M2: Dark pool flow data |
| `scripts/fetch_options.py` | M3: Options chain + institutional flow |
| `scripts/fetch_oi_changes.py` | M3B: OI change analysis |
| `scripts/fetch_analyst_ratings.py` | M1C: Analyst ratings |
| `scripts/fetch_news.py` | M1D: News & catalysts |
| `scripts/clients/uw_client.py` | UW API client (connection pooling) |
| `scripts/clients/ib_client.py` | IB API client |

## Off Limits
- `data/*.json` — Data files (watchlist, portfolio, trade log)
- `web/` — Next.js frontend (not part of evaluate pipeline)
- `.pi/` — Agent configuration and skills
- `docs/` — Documentation

## Constraints
1. **No feature breakage** — All 34 existing tests must pass
2. **Red/green TDD** — Write failing test before implementing fix
3. **50% minimum improvement** — Target: 23s → <11.5s for 5 tickers
4. **Maintain accuracy** — Evaluation results must be identical

## Architecture Notes

### Current Flow (single ticker)
```
evaluate.py
  ├── fetch_price_history() — IB connection (1.8s connect + 0.7s data)
  └── ThreadPoolExecutor(7 workers)
      ├── M1: fetch_ticker_info() — UW dark pool API (0.5s)
      ├── M1B: fetch_seasonality() — curl EquityClock (0.1s)
      ├── M1C: fetch_analyst_ratings() — UW API (0.1s)
      ├── M1D: fetch_news() — UW API (0.15s)
      ├── M2: fetch_flow() — UW API (0.9s)
      ├── M3: fetch_options() — UW API (0.3s)
      └── M3B: fetch_oi_changes() — UW API (0.1s)
```

### Bottlenecks Identified
1. **IB connection (1.8s/ticker)** — Each ticker opens new connection. Connection pooling could help.
2. **Sequential ticker processing** — 5 tickers run sequentially (5 × 2.5s = 12.5s)
3. **UW client session overhead** — Multiple UWClient instances created per evaluation
4. **IB on main thread** — ib_insync asyncio limitation forces sequential IB calls

### Optimization Ideas
1. **IB connection pooling** — Keep IB connection open, reuse for all tickers
2. **Multi-ticker parallel evaluation** — Process multiple tickers simultaneously
3. **UW batch API** — Check if UW supports batch requests
4. **Skip IB for edge-fail tickers** — If M2 flow fails edge, skip IB price fetch
5. **Async IB alternative** — Use ib_async or raw socket for concurrent price fetches
6. **Caching layer** — Cache price data for same-day evaluations

## What's Been Tried
(Updated as experiments accumulate)

### Experiment 1: Baseline
- Single ticker: ~2450ms
- 5 tickers sequential: ~23,300ms
- IB connect dominates: 1800ms per evaluation

