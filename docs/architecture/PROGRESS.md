# Architecture entropy progress

Branch: `cursor/architecture-entropy-a8d8`

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

## Round 3

Deleted the unused frontier selectors, picker label, and second catalog
store from `scripts/refresh_model_catalog.py`. The app job imports
`load_previous`, `persist_result`, and `_record_cycle_failure` from
`scripts/model_gateway/_catalog_backend.py`. Discovery still goes through
the gateway socket. The app seed now keeps NVIDIA and Cerebras rows, which
the old three-provider filter dropped before carry-forward.

Anti-regression: `test_catalog_frontier_rules_have_one_definition` and
`test_app_previous_seed_keeps_every_catalog_provider`.

Evidence: the definition check failed while both modules defined the four
functions, then `66 passed` on the catalog suites and `6 passed` on
`cloud/tests/test_model_gateway_package.py`.

## Round 4

Moved the knowledge query decision into `scripts/model_gateway/embed_rules.py`:
embedding text, backend choice, dual-write, the v2 coverage cache, and
`resolve_query_vector`. The app module and the native embedding client each
bind their own `embed_query`. HTTP and fastembed stay in the native client.
The app still calls the gateway.

Anti-regression: `test_embed_query_policy_has_one_definition` and
`test_embed_query_policy_markers_have_one_home`.

Evidence: both checks failed while the two modules still defined the policy,
then `95 passed` across the embed-rules, v2, pipeline, ingest-budget, and
write-bounds suites, and `6 passed` on
`cloud/tests/test_model_gateway_package.py`.
