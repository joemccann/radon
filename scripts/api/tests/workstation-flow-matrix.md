# Workstation backend flow contracts

Inventory: 101 decorated HTTP routes in `scripts/api/server.py` and
`scripts/api/routes/*.py` at base commit `8afaaa61`. This is a route inventory,
not a claim that every route has functional end-to-end coverage. Browser
navigation and TesterArmy SDK integration belong to the web and site suites.

| Journey / boundary | Representative routes | Existing backend evidence |
| --- | --- | --- |
| Authenticated workstation entry | Default route protection, loopback trust, shared routes | `test_route_authz_matrix.py`, `test_loopback_browser_bypass.py`, `test_auth_fail_closed.py`, `test_cors_allowlist.py` |
| Health and broker connection state | `/health`, `/health/lite`, `/ib/restart`, operator hold | `test_health_payload.py`, `test_ib_health_event_loop.py`, `test_ib_restart_cloud_delegate.py`, `test_ib_restart_2fa_lock.py` |
| Portfolio and working-order refresh | `/portfolio/sync`, `/portfolio/background-sync`, `/orders/refresh` | `test_db_source_truth_routes.py`, `test_ib_sync_coordinator.py` |
| Order preview and execution safety | `/orders/whatif`, place, cancel, modify, replace | `test_orders_whatif_route.py`, `test_orders_place_safety_contract.py`, `test_orders_place_timeout_indeterminate.py`, `test_order_failure_details.py`, `test_order_replace_state_machine.py`, `test_modify_snapshot_contract_shape.py` |
| Trading controls | `/trading/status`, halt, resume, kill, cancel-all | `test_trading_halt_routes.py`, `test_order_limits_routes.py`, `test_order_audit_trail.py` |
| Options inspection | Chain, expirations, IB quotes, exposure, futures/index chain | `test_option_secdef.py`, `test_equity_options_chain.py`, `test_index_options_chain.py`, `test_futures_chain.py`, `test_options_exposure.py`, `test_chain_fetch_bounded.py` |
| Scanner refresh and regime modules | Scan, discover, regime, breadth, VCG, GEX, strategy scans | `test_demo_scan_guards.py`, `test_scan_gate.py`, `test_flow_tab_cooldown.py`, strategy route suites |
| Ticker research overlays | Earnings, ratings, informed flow, event odds | `test_earnings_route.py`, `test_ticker_ratings_and_pi.py`, **new `test_workstation_flow_contracts.py`** |
| Analytics form submission | `/forecast/chronos`, `/flow-surprise` | **New `test_workstation_flow_contracts.py`** |
| Account configuration | Credentials and preferences | `test_credentials_routes.py`, `test_no_secret_leakage.py`, `test_preferences_routes.py` |
| Research retrieval and assistant context | Research files/evidence, knowledge search, assistant market | `test_research_files.py`, `test_knowledge_routes.py`, `test_assistant_market_routes.py`, `test_assistant_catalog.py` |
| Reports, accounting, shadow execution | Journal reconciliation, performance, cash flows, paper placement, backtests | `test_journal_reconcile_startup_gate.py`, `test_performance_background_cooldown.py`, `test_flex_p2_routes.py`; `test_remaining_workstation_http_flows.py` adds HTTP backtest/cache/disconnect and cash-flow persistence/fallback cases; `scripts/tests/test_cash_flows_route_last_synced.py` covers GET sync health; `scripts/tests/test_paper_place_route.py` covers the paper subprocess contract |
| Operations and script dispatch | Admin services, `/pi/exec`, UW usage | `test_services.py`, `test_services_host_control.py`, `test_ticker_ratings_and_pi.py`, `test_route_abuse_controls.py`, `test_uw_usage_route.py` |

## Added contracts and repair

`test_workstation_flow_contracts.py` adds 58 HTTP-level cases. The overlay
matrix exercises synthetic successful upstream payloads, cache fallback during upstream failure,
legitimate empty payloads (`200` + `missing`), uncached upstream failure (`502`),
symbol normalization, and invalid-symbol rejection before subprocess dispatch.

The analytics matrix exercises valid defaults, numeric-string compatibility,
subprocess argument/timeout contracts, upstream errors, demo guards, malformed
JSON, non-object JSON, invalid integer fields, and oversized decimal strings.
Guarding JSON bodies and positive
integer fields in `server.py` returns a `400` before dispatch for malformed input.
Current-base red/green verification is recorded below.
These tests patch all relevant subprocess calls, use temporary overlay paths,
and do not enter FastAPI lifespan. No broker mutation, external script
execution, credential request, or live database write is part of this matrix.

## Verification and limits

Verification on current base `8afaaa61` in the isolated requirements-matched
Python 3.13.12 runtime, with an empty inherited environment except PATH/HOME:

- Current-base red proof (temporarily restored only production server/distill files): `.venv/bin/python -m pytest scripts/api/tests/test_workstation_flow_contracts.py scripts/tests/test_knowledge_enrichment_budget.py -q --tb=short`; **41 failed, 24 passed**. Failures comprise 40 malformed-input cases and one redundant group-signal permission regression.
- Restored fixes, new contracts plus worker tests: `.venv/bin/python -m pytest scripts/api/tests/test_workstation_flow_contracts.py scripts/api/tests/test_remaining_workstation_http_flows.py scripts/tests/test_knowledge_enrichment_budget.py -q --tb=short`; **82 passed**.
- Changed executable lines measured with pytest-cov against `scripts.api.server` and `knowledge.distill`: **28/28 covered (100%)**; **65 focused tests passed**.
- Full API: `.venv/bin/python -m pytest scripts/api/tests -q -n auto --dist loadfile --tb=short`; **1179 passed, 0 failed**. The first batch had one existing services-poll timing failure (8.6s against an 8s limit); its focused repeat passed and the full rerun passed in 39.99s. The timing contract and production implementation are unchanged.
- Full scripts/root suite: `.venv/bin/python -m pytest scripts/tests tests -q -n auto --dist loadfile --tb=short`; **13143 passed, 2 skipped, 23 subtests passed, 1 unrelated failure** in 632.48s. Untouched `test_subscription_tokens.py::test_the_real_login_runner_kills_a_login_nobody_approved` expected a prompt within its 1s synthetic CLI window but received none under full-suite load. Focused repeat passed (**1 passed in 1.50s**). No threshold or production login behavior was changed.

Prior saved-snapshot totals are omitted because they describe a superseded base.

- All new HTTP tests patch subprocess, broker/provider transports and database calls, use temporary cache paths, and omit FastAPI lifespan. No live provider or database integration is exercised.
- Backend contracts do not prove live market data freshness, execution, private cloud configuration, browser rendering, or TesterArmy SDK behavior.
- Backtest coverage includes registry GET, cache-miss success/error, disconnect cancellation and the existing refresh POST contract; strategy simulation is outside this matrix.
- Short availability covers IB priority/success/error/timeout, UW fresh/stale/error fallback and absent measurements; provider transports remain mocked.
- Cash-flow HTTP contracts cover query/date/type filtering, exact summaries, JSON fallback, missing snapshots and Interest/Fee net totals. Ingestion is covered by separate script suites; live database persistence remains unverified.
