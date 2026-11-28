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

## Round 2

Moved the shared Clairic date grammar into `scripts/model_gateway/clairic_rules.py`.
Research still calls the gateway. The native client still calls Cloudflare.
`apply_date` takes the transport as an argument so the research process
cannot inherit the native `fetch_report_date`.

Anti-regression: `test_clairic_date_grammar_has_one_definition`.

Evidence: the check failed while both modules defined the grammar, then
`22 passed` on the grammar and research date tests, `69 passed` on the
model-gateway consumer and specialized suites, `6 passed` on
`cloud/tests/test_model_gateway_package.py`.

## ET calendar day

Branch: `cursor/et-calendar-date-a8d8`. Independent of the other entropy PRs.
It edits the same three jobs as the payload-rows PR, only the date helper.

`market_calendar.et_calendar_date` is the ET calendar day of an instant.
Earnings, catalysts, and informed flow bind it as `_et_today`.

Anti-regression: `test_et_today_helper_is_not_copied`.

Evidence: `89 passed` on the date helper, earnings, catalysts, informed
flow, and the market-open cases that do not import the broker client.
The broker-client cases need `python-dotenv`, which this environment
does not have. CI has it.
