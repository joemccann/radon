# Autoresearch: Python Test Suite Speed

## Objective
Optimize the Python test suite (`scripts/tests/` + `scripts/trade_blotter/test_*.py`) for wall-clock execution time. The suite has 1327 non-integration tests across ~60 files. Current baseline is ~22s.

## Metrics
- **Primary**: `total_s` (seconds, lower is better) — total pytest wall-clock time
- **Secondary**: `tests_passed` — must stay at 1327 (no tests dropped), `tests_failed` — must stay at 0

## How to Run
`./autoresearch.sh` — runs pytest with `--ignore` for integration tests, outputs `METRIC` lines.

## Files in Scope
- `scripts/tests/test_*.py` — all unit/integration test files
- `scripts/tests/test_monitor_daemon/*.py` — daemon test files
- `scripts/trade_blotter/test_blotter.py` — blotter tests
- `scripts/trade_blotter/test_integration.py` — trade blotter integration tests
- `scripts/tests/conftest.py` — shared fixtures
- `scripts/clients/uw_client.py` — UW client (retry/backoff config)
- `scripts/clients/ib_client.py` — IB client (connect retry logic)
- `scripts/api/subprocess.py` — subprocess runner
- `pyproject.toml` — pytest config

## Off Limits
- **DO NOT** delete or skip any tests
- **DO NOT** change test assertions or expected behavior
- **DO NOT** change production code behavior (only test infrastructure and timing)
- `scripts/tests/test_menthorq_integration.py` — always excluded (requires browser, hangs without it)
- Web/frontend tests (`web/tests/`)

## Constraints
- All 1327 tests must pass
- 0 test failures
- No functionality changes in production code
- No reduction in code coverage

## What's Been Tried

### Baseline → 2.2s (90% reduction)

**Wins (in order of impact):**

1. **Patch time.sleep in retry tests** (-15s, 68%)
   - `test_uw_client::test_500_raises_server_error`: UW client retried 500 with real backoff (1+2+4=7s). Added `patch('time.sleep')`.
   - `test_ib_client::test_connect_exhausts_retries` and `test_connect_retries_on_transient_error`: IB connect retry with real sleep (3+1=4s). Added `@patch('time.sleep')`.
   - `test_client_id_allocation::test_explicit_id_no_retry_on_conflict`: Real sleep in retry (1s). Added `@patch('time.sleep')`.

2. **pytest-xdist parallel execution** (-4s, 18%)
   - `-n 4` is the sweet spot (benchmarked n=2..12). Fewer workers under-utilize; more workers add spawn overhead.
   - Default `load` distribution is most consistent (vs loadscope/loadfile/worksteal).

3. **Replace slow subprocess test** (-2s, 9%)
   - `test_module_error_falls_back` was running `trade_blotter.flex_query` (5s timeout). Replaced with `json.tool --no-such-arg` (0.07s).

4. **Reduce async test sleep times** (-0.5s, 2%)
   - batched_relay: flush_interval 50→10ms, sleep 100→30ms
   - performance_lock: sleep 100→10ms
   - timeout_kills_process: 500→100ms

5. **Fix xdist flake** (stability, not speed)
   - `asyncio.get_event_loop().run_until_complete()` → `asyncio.run()` in 13 subprocess tests.
   - Python 3.13 deprecates `get_event_loop()`, causing intermittent RuntimeError in xdist workers.

### Dead ends / diminishing returns
- `-p no:warnings`, `-p no:cacheprovider` — negligible impact
- `--import-mode=importlib` — no measurable gain
- Worker counts >4 or <4 — slower due to spawn overhead or under-utilization
- `loadscope`/`loadfile`/`worksteal` distribution — all worse or noisier than default `load`
- pyproject.toml `addopts` — conflicts with script CLI flags
- Reducing ThreadPoolExecutor overhead in evaluate.py — would require production code changes
