# Radon Scripts — Codex Instructions

Applies under `scripts/`. Mirrors `scripts/CLAUDE.md`; prefer the Claude file if newer or more specific.

## Data Source Priority

Yahoo Finance is **ABSOLUTE LAST RESORT**. Never make Yahoo the scheduled, primary, or only source for a series IB or UW can serve. Try IB every cycle (skip the socket only when `/health` `auth_state` is set and not `authenticated`), then UW, then Robinhood (`scripts/clients/robinhood_client.py` — official trading MCP, READ-ONLY, skipped cleanly when no credentials (access or refresh token) are configured; access tokens expire ~3 days, refreshed automatically via the 0600 token file `ROBINHOOD_MCP_TOKEN_FILE`), then Yahoo. Full order: IB > UW > Cboe > Robinhood > Yahoo. 2FA / unattended timers / "historical needs a gateway" do not skip IB or UW. Execution stays on IB — never call a place_*/cancel_* Robinhood tool.

## Python / IB Basics

- Use `python3.13` for project commands unless an existing script explicitly requires another interpreter.
- In subprocess code, use `sys.executable` instead of hardcoded `python3`.
- Client ID ranges:
  - `0-9`: FastAPI IBPool (`sync=3`, `orders=4`, `data=5`)
  - `10-19`: WS relay
  - `20-49`: subprocess scripts and monitor daemon handlers, always `client_id="auto"`
  - `50-69`: scanners
  - `90-99`: CLI
- Never hardcode IDs in `20-49`; use `scripts/clients/ib_client.py:_connect_auto_allocate`.
- Scripts importing `ib_insync` directly must wrap every IB await in `asyncio.wait_for(..., timeout=15)` and pre-check FastAPI `/health` for `auth_state == "authenticated"` before constructing `IB()`.
- Use `requests` for Unusual Whales API calls. `urllib.request` has returned 403.

## Evaluation Pipeline

- Evaluation commands must call `python3.13 scripts/evaluate.py [TICKER]`.
- Do not manually call `fetch_flow.py`, `fetch_options.py`, `fetch_oi_changes.py`, `fetch_news.py`, or `kelly.py` during an evaluation; `evaluate.py` orchestrates them.
- Milestones: ticker, seasonality/analyst/news context, dark pool flow, options flow, OI changes, edge decision, structure, Kelly, log.
- M3B OI changes are required. Large OI with no flow alert can be the signal.
- Every milestone must fetch fresh data and include today's data. Print a Data Freshness line and flag stale data.
- Intraday dark-pool interpolation is automatic in `fetch_flow.py`; use interpolated aggregate for edge while showing actual and interpolated values with confidence.
- Reports are mandatory at structure milestone; see `docs/reports.md`.

## Order Placement Contract

- Prefer `scripts/ib_execute.py` for real execution; it places, monitors, and logs.
- `ib_place_order.py` must never disconnect while `trade.order.permId == 0`.
- Wait up to 12s for combo orders and 6s for single-leg orders before disconnecting.
- If still `PendingSubmit`, `ApiPending`, or `Unknown`, return `status:"error"` with an operator-readable hint.
- stdout is reserved for result JSON. Every progress/debug print goes to stderr.
- IB may silently drop some combo structures, especially bearish risk reversals. Verify with counterpart/spread; workaround is split single-leg orders.
- Live probes can fill. Use qty 1 max, far-away limit, immediate cancel, and verify open orders.
- Heavy connect/disconnect subprocess load can trigger IB Gateway 2FA renewal; reuse one `IBClient` for multi-case probes.

## Cancel / Modify

- Cancel/modify must reconnect as the original placing `clientId`.
- Master client can see all orders but cannot cancel/modify them.
- Confirm against refreshed open-order snapshot; disappearance after cancel = success.
- Clear VOL fields before modify: `volatility=1.7976931348623157e+308`, `volatilityType=2147483647`.
- Preserve upstream error detail; do not collapse to 500.

## Portfolio / Basis

- IB is the only current portfolio source. Verify with `python3.13 scripts/ib_sync.py`.
- `ib_insync.positions()` is cached; `IBClient.get_positions()` calls `reqPositions()` then waits for `positionEndEvent` and finite `avgCost` (1s cap) before reading.
- `scripts/clients/journal_basis.py` computes lot-matched open basis from raw journal rows and overrides IB's drifting VWAP in `ib_sync.py`.
- `PortfolioLeg.avg_cost` convention is per-contract for options and per-share for stocks; web display divides option cost by 100 when needed.
- Entry-date fallback in `ib_sync.py` must match web docs: blotter per-contract -> trade_log ticker/structure -> IB fills -> previous portfolio ticker/structure/expiry -> today.

## High-Throughput / State

- Use `scripts/utils/atomic_io.py` for atomic state writes.
- Scanner/discover use parallel workers and skip tickers on `UWRateLimitError`.
- WS relay batches last-write-wins updates with 100ms flush.
- Stale tick detection restarts Gateway only through the documented recovery path.
- Performance page uses Phase A IB+cache and Phase B parallel UW/Yahoo fallback; preserve SWR behavior.
