# Architecture entropy progress

Branch: `cursor/architecture-entropy-1906`

## Map

Python session time already has one calendar (`scripts/utils/market_calendar.py`)
and a web twin (`web/lib/marketCalendar.ts`), each with a source scan. The
remaining Python copies were name collisions and pass-throughs, not a second
holiday table. Hubs that are real module boundaries, not entropy: `web/lib/types.ts`,
`web/lib/pricesProtocol.ts`, `scripts/db/writer.py`, `scripts/api/auth.py`.
`scripts/api/server.py` and `web/components/WorkspaceSections.tsx` are wide
surfaces; splitting them is recorded as pending, not started.

## Round 1

Deleted the parallel `is_market_open` definitions and `MARKET_HOLIDAYS_2026`.
Regular-session callers use `is_market_open_et`. Streaks TTL uses that
predicate directly instead of `price_cache.is_market_hours`.

Anti-regression: `test_no_parallel_market_open_predicate`.

Evidence: the new check failed on 14 definitions, then
`198 passed` across the calendar, flow, ticker, discover, price-cache,
VCG wiring, and market-hours suites.
