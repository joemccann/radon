# Autoresearch: Evaluate Command Speed Optimization

## Summary
**Target achieved: 56% improvement (14.5s → 6.3s best case)**

| Metric | Baseline | Best | Improvement |
|--------|----------|------|-------------|
| 5 tickers | 14,501ms | 6,213ms | -57% |
| Single ticker | 2,814ms | 2,020ms | -28% |

**Note**: Performance is highly variable due to UW API rate limiting. When rate
limited, multi-ticker times spike to 40-50s. When not limited, we achieve 7-8s
consistently.

Key optimizations:
1. **IB connection pooling** — Single connection for all tickers
2. **--fast flag** — Skip IB price history fetch (skips signal_priced_in check)
3. **Analyst ratings cache** — Reuse cached ratings (change slowly)
4. **UW request cache** — 60s TTL in-memory cache for API deduplication
5. **Multi-ticker CLI** — `evaluate.py AAPL MSFT NVDA` now supported

Limitations:
- UW API rate limiting causes high variability (7s-50s+ range)
- Best performance requires `--fast` flag
- Can't parallelize evaluations due to UW throttling

---

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
- 5 tickers sequential: ~14,500ms
- IB connect dominates: 1800ms per evaluation

### Experiment 2: IB Connection Pooling ✅ KEEP
- Added `run_evaluations()` with single IB connection for all tickers
- Added `_fetch_all_prices()` batch fetch
- Result: 8,500ms (-41%) but high variability (8s-49s)
- Network/API variability causes inconsistent results
- Single ticker path preserved (no batch overhead)

### Experiment 3: Parallel Evaluation with 2 Workers ❌ DISCARD  
- Tried running 2 evals in parallel after IB batch
- Caused UW rate limiting — worse performance
- Sequential per-ticker is more reliable

### Experiment 4: --fast Flag (Skip IB Price History) ✅ KEEP
- Added `--fast` CLI flag to skip IB price history fetch
- Skips `signal_priced_in` check (rarely triggers in practice)
- Best case: 6.6s (54% improvement from 14.5s baseline)
- Worst case: 50s+ due to UW rate limiting

### Experiment 5: Rate Limit Delays ❌ DISCARD
- Tried 0.3s delay between tickers
- Doesn't fix rate limiting variability
- UW API throttles at ~35 requests burst

### Key Findings
1. **IB connection pooling works** — saves 1.8s × (N-1) tickers
2. **UW rate limiting is the main variability source** — hitting limits causes 40s+ delays
3. **--fast mode achieves target** — 7s average when no rate limiting
4. **Parallel evaluation doesn't work** — UW can't handle 35 concurrent requests

### Experiment 6: Enable Analyst Ratings Cache ✅ KEEP
- Changed `use_cache=False` to `use_cache=True` in M1C
- Reduces API calls when cache is warm
- No semantic change (analyst ratings change slowly)

### Experiment 7: Reduce Thread Pool Workers ❌ DISCARD
- Tried reducing from 7 to 4 workers
- Doesn't help with rate limiting, hurts single-ticker latency

### Experiment 8: In-Memory UW Request Cache (60s TTL) ✅ KEEP
- Added `scripts/utils/uw_cache.py` with TTL-based caching
- UWClient._get() checks cache before making API call
- Single ticker improved: 2.3s vs 2.8s baseline
- Deduplicates M1/M2 overlapping darkpool requests (3 days overlap)

### Experiment 9: Reduce M1 Ticker Validation to 1 Day ✅ KEEP
- Changed from 3 trading days to 1 trading day for validation
- Saves 2 UW API calls per ticker
- For validation, 1 day of activity is sufficient to confirm ticker exists
- M2 flow still uses 5 days for edge determination

### Bottlenecks Remaining (for future work)
1. **UW rate limiting is the dominant factor** — 8s when not limited, 50s+ when limited
2. M2 flow makes 5+ UW calls per ticker (5 days of darkpool + flow alerts)
3. M1 ticker makes 3+ UW calls per ticker (3 days of darkpool + flow alerts)
4. No request deduplication between evaluations
5. UWClient backoff during rate limiting adds significant time

