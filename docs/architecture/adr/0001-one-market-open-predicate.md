# 0001. One regular-session predicate

## Status

Accepted. 2026-11-28.

## Context

`utils.market_calendar.is_market_open_et` is the holiday- and half-day-aware
answer to "is the US cash session open". Several modules still defined
`is_market_open`. Three of them (`fetch_flow`, `fetch_ticker`, `discover`)
implemented a different predicate under that name: date-only trading-day
membership, with no clock. The others were one-line pass-throughs. A
year-stamped `MARKET_HOLIDAYS_2026` constant existed only so tests could
import it.

## Decision

Callers use `is_market_open_et` for the regular session and
`is_trading_day_et` for date membership. Report labels stay in
`utils.market_hours` (`get_market_status`, `get_last_market_close`). The
FastAPI seam `_is_market_open_now_et` stays: orders-sync tests patch that
process-local clock, and the extended-session helper falls back to it.

`scripts/tests/test_market_open_single_source.py` rejects any new
`def is_market_open`, `def _is_market_open`, or `MARKET_HOLIDAYS_<year>`
under `scripts/` outside tests.

## Consequences

A trading-day check can no longer be imported under a name that means the
cash session is open. Half-day interpolation in `fetch_flow.get_trading_day_progress`
is a separate clock and is not changed here.
