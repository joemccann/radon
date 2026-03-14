# Autoresearch: IB Sync Latency Optimization

## Objective
Minimize end-to-end wall-clock time for `python3 scripts/ib_sync.py --sync --client-id 98` — the full portfolio sync from Interactive Brokers. Currently ~22.7s. This is the critical path for the web dashboard's portfolio refresh.

## Profiling Baseline (22.7s total)

| Phase | Time (ms) | % of Total | Notes |
|-------|-----------|------------|-------|
| **PnL Single requests** | **13,029** | **57%** | `get_pnl_single()` does `sleep(0.5)` per position × 26 = 13s |
| Sleep for PnL data | 3,001 | 13% | Additional 3s sleep after all PnL requests |
| Sleep for MktData | 3,001 | 13% | Fixed 3s sleep waiting for market data |
| PnL poll | 2,001 | 9% | `get_pnl()` does 2s initial sleep + up to 8×1s polling |
| Qualify contracts | 1,013 | 4% | Single batch call — already batched |
| Connect | 604 | 3% | IB API connection handshake |
| Account summary | 208 | 1% | Fast |

## Root Cause: Serial 0.5s Sleeps in PnL Single
`fetch_position_daily_pnl()` calls `client.get_pnl_single()` in a loop, which does `client.sleep(0.5)` PER position. 26 positions × 0.5s = 13s of pure sleeping. The fix is to request all PnL subscriptions upfront (no per-request sleep), then do ONE combined sleep.

## Secondary: Overlapping Sleeps for MktData + PnL
Market data and PnL Single both need time for IB to stream data back. Currently these are sequential (3s + 3s). They can overlap if we request both simultaneously.

## Metrics
- **Primary**: `total_s` (seconds, lower is better) — wall-clock `ib_sync.py --sync --client-id 98`
- **Secondary**: `positions` — number of positions fetched (must stay same, ~20)

## How to Run
`./autoresearch.sh` — runs ib_sync.py with timing, outputs `METRIC name=number` lines.

## Files in Scope
- `scripts/ib_sync.py` — Main sync script (845 lines). Orchestrates connect → fetch → collapse → save.
- `scripts/clients/ib_client.py` — IBClient wrapper (821 lines). `get_pnl_single()` has 0.5s sleep.
- `scripts/utils/incremental_sync.py` — Diff-based skip logic (126 lines).
- `scripts/utils/atomic_io.py` — Atomic JSON writes (97 lines).

## Off Limits
- `scripts/tests/` — Do NOT modify existing tests. Add new ones only.
- `web/` — Do not touch the web layer.
- `lib/` — Do not touch the TypeScript tools layer.
- IB API protocol — We can only optimize our side (sleep durations, concurrency, batching).

## Constraints
1. All 40 existing tests must pass (`pytest scripts/tests/test_incremental_sync.py scripts/tests/test_atomic_io.py scripts/tests/test_covered_call_detection.py scripts/tests/test_all_long_combo.py`).
2. Output portfolio.json must have the same schema and data quality (positions, market values, PnL).
3. Must correctly fetch market prices and daily PnL for all positions.
4. Must work with IB Gateway on port 4001. Use `--client-id 98` to avoid conflicts.
5. The `collapse_positions()` logic and structure detection must not change behavior.

## What's Been Tried

### Wins (kept)
1. **Batch PnL Single requests** (23.3s → 9.7s, −58%): Bypass IBClient's `get_pnl_single()` which sleeps 0.5s per position. Call `ib.reqPnLSingle()` directly for all positions, then one combined sleep.
2. **Overlap MktData + PnL + account PnL** (9.7s → 5.2s, −37%): Restructure main() so all subscriptions are requested concurrently, then ONE combined sleep replaces 3 sequential sleeps.
3. **Reduce combined sleep to 2s** (5.2s → 5.2s, kept with conditional PnL poll): 2s main sleep + conditional 1s poll for account PnL if not arrived.
4. **Skip qualifyContracts** (5.2s → 3.7s, −28%): Set `exchange='SMART'` manually for ALL contracts (not just empty ones — stocks have AMEX/BATS which fail with reqMktData type 4). Saves 1.1s round-trip.
5. **Request PnL BEFORE market data** (improves reliability): PnL Single takes slightly longer to arrive, so requesting first gives it more lead time.
6. **Direct ib.reqMktData/cancelMktData** (minor cleanup): Skip subscription tracking in IBClient.get_quote.
7. **accountValues() instead of accountSummary()** (3.26s → 3.19s, −2%): `ib.accountValues()` reads from ib_insync's internal cache (0ms) vs `ib.accountSummary()` which makes a blocking round-trip (200-700ms). Same data, same tags.
8. **Eliminate Phase 6 fallback entirely** (pending clean benchmark): Account PnL arrives during Phase 4's 2.7s sleep without any fallback. Confirmed in 3 runs (daily_pnl present, 0 none_pnl). Clean timing benchmark blocked by gateway degraded state (frozen option data unavailable after prolonged 2FA loop — only stocks return data Friday evening). Needs market open to validate timing.

### Dead ends (discarded)
- **1.5s combined sleep**: Lost 3 PnL + 2 market values. Too aggressive.
- **1.75s combined sleep**: Lost 1 PnL. Still too aggressive.
- **Adaptive polling (0.1s intervals)**: Per-iteration `client.sleep()` overhead dominates savings. 7.3s (worse than fixed 2s).
- **Adaptive polling (0.25s intervals)**: 6.35s, still slower than fixed 2s.
- **2s sleep with completeness retry**: IB throttles rapid-fire connections, causing cascading failures on consecutive runs.
- **2.25s sleep**: 4/5 runs perfect but 1 run lost 4 market values. Not reliable.
- **Move account summary AFTER subscriptions**: Account summary takes 2.4s (vs 200ms) because IB event loop is clogged with incoming data. Must fetch account BEFORE starting subscriptions.
- **Snapshot market data** (`snapshot=True` in reqMktData): Only 8/26 positions get prices with delayed-frozen data (type 4). Works during market hours with live data (~24/26 in 1.5s) but catastrophically fails after-hours. Not robust.
- **Import optimization**: ib_insync import takes 121ms — unavoidable direct dependency.
- **Post-IB processing**: collapse_positions <1ms, atomic_save ~1.4ms — negligible.

### Architecture insights
- **2.7s is the minimum reliable sleep for 20+ positions** when using accountValues() (which eliminates the implicit ~200ms delay from accountSummary). Below this, delayed/frozen data for thin options may not arrive.
- **Stock contracts need exchange='SMART'**: Stocks from `get_positions()` have exchange-specific values (AMEX, BATS, NASDAQ) that work for trading but fail with `reqMktData` type 4. Always set to SMART.
- **IB event loop processing**: Calling any ib_insync method that polls for responses (like `accountSummary()`) when there's heavy incoming data causes massive slowdown. Always do blocking calls BEFORE starting streaming subscriptions.
- **IB throttles rapid connections**: Running >5 connect/disconnect cycles in quick succession causes CLOSE_WAIT accumulation and eventual connection refusal. Need 10-15s cooldown between runs.
- **Client ID conflicts**: Each connection needs a unique client ID. Stale connections hold IDs for 30-60s after disconnect.
- **Snapshot mode + type 4 = broken**: The delayed-frozen data source does not reliably serve snapshot requests. Only streaming mode works consistently across market conditions.
