# Backend and operations security remediation status

Scope: the backend/operations tranche assigned to T3 from the HIGH_BUG, BUG, and inherited SECURITY inventories. `fixed-by-root` denotes a shared-worktree patch completed and verified by the root coordinator; it is still complete for this tranche. A duplicate remains a distinct reconciled finding and is covered by the named canonical fix.

## Reconciliation

| Inventory | Assigned | Fixed | Duplicate/canonical fix | Deferred/external | Remaining actionable |
|---|---:|---:|---:|---:|---:|
| HIGH_BUG | 45 | 40 | 5 | 0 | 0 |
| BUG | 34 | 33 | 1 | 0 | 0 |
| SECURITY | 13 | 11 | 2 | 0 | 0 |
| **Total** | **92** | **84** | **8** | **0** | **0** |

## HIGH_BUG rows

| ID | Disposition | Patch evidence | Regression target |
|---|---|---|---|
| HB-001 | fixed | Backup excludes generated FTS/shadow objects and verifies a restorable dump. | `cloud/tests/test_db_backup.py` |
| HB-002 | fixed | Backup holds one source snapshot and keyset-pages inside it. | `cloud/tests/test_db_backup.py` |
| HB-003 | fixed | A paging failure after emission aborts instead of replaying rows. | `cloud/tests/test_db_backup.py` |
| HB-004 | fixed | API migration preflight fails closed on timeout/nonzero exit. | `cloud/tests/test_systemd_services.py` |
| HB-005 | duplicate -> HB-001 | The service uses the corrected verified backup implementation. | `cloud/tests/test_systemd_services.py` |
| HB-006 | duplicate -> HB-002 | The service uses the corrected snapshot-scoped dumper. | `cloud/tests/test_db_backup.py` |
| HB-007 | fixed | Archive, retention, and backup share one exclusive maintenance lock. | `cloud/tests/test_systemd_services.py` |
| HB-008 | fixed | Any required retention-policy failure records error and exits nonzero. | `scripts/tests/test_db_retention_sweep_service.py` |
| HB-009 | duplicate -> HB-007 | Retention catch-up uses the shared maintenance lock. | `cloud/tests/test_systemd_services.py` |
| HB-010 | fixed | Forecast history selects newest N rows, then restores chronological order. | `scripts/tests/test_forecast_writers.py` |
| HB-011 | fixed | Unknown probe state cannot resolve a still-open incident. | `scripts/tests/test_incident_watchdog.py` |
| HB-012 | fixed | Journal knowledge ingestion cursor-pages bounded rows. | `scripts/tests/test_knowledge_sources.py` |
| HB-013 | fixed | Total provider failure preserves last-good LEAP cache and fails health/run. | `scripts/tests/test_leap_scanner.py` |
| HB-014 | fixed | LEAP direct fallback uses pinned Python, bounded phases, and preserved rc. | `scripts/tests/test_refresh_wrapper_exit_status.py` |
| HB-015 | duplicate -> HB-014 | The scheduled service now observes dual-refresh failure. | `cloud/tests/test_systemd_services.py` |
| HB-016 | fixed | Timed-out order handler remains claimed/in-flight and cannot overlap a retry. | `scripts/tests/test_monitor_daemon/test_exit_orders_guard_durability.py` |
| HB-017 | fixed-by-root | Dead browser state is invalidated/recreated with bounded failure escalation. | `web/tests/newsfeed-scraper.test.ts` |
| HB-018 | fixed | OI wrapper preserves failed fetch status. | `scripts/tests/test_refresh_wrapper_exit_status.py` |
| HB-019 | duplicate -> HB-007 | Archive catch-up uses the shared maintenance lock. | `cloud/tests/test_systemd_services.py` |
| HB-020 | fixed | One-minute skew cadence no longer exhausts the unit start burst. | `cloud/tests/test_systemd_services.py` |
| HB-021 | fixed | Skew2d rejects stale parent-session input and requires causal freshness. | `scripts/tests/test_skew2d.py` |
| HB-022 | fixed | VCG wrapper preserves dual-refresh failure status. | `scripts/tests/test_run_vcg_refresh_wrapper.py` |
| HB-023 | fixed | Concurrent watchdog writers lock and atomically replace digest state. | `scripts/tests/test_watchdog/test_notify_escalation.py` |
| HB-024 | fixed | Digest enqueue and selective delivered-item clear are serialized. | `scripts/tests/test_watchdog/test_notify_escalation.py` |
| HB-025 | fixed | CTA launchd cadence gates at runtime in Eastern time after close. | `cloud/tests/test_systemd_services.py` |
| HB-026 | fixed | Data-refresh launchd cadence gates at runtime in Eastern time through close. | `cloud/tests/test_systemd_services.py` |
| HB-027 | fixed | Legacy duplicate exit-order LaunchAgent is removed. | `cloud/tests/test_root_execution_paths.py` |
| HB-028 | fixed | Historical IB calls are bounded and timed-out clients are quarantined. | `scripts/api/tests/test_historical_pool.py` |
| HB-029 | fixed | IBClient exposes a bounded head-timestamp operation. | `scripts/tests/test_ib_client_historical.py` |
| HB-030 | fixed | GEX cooldown/cache identity is validated per ticker. | `scripts/api/tests/test_gex_route.py` |
| HB-031 | fixed | Correction-root identity transactionally supersedes prior lifecycle application. | `tests/test_position_return_capital.py` |
| HB-032 | fixed-by-root | Required UW failure produces degraded state and blocks publish/alert. | `scripts/tests/test_discover.py` |
| HB-033 | fixed | Stable milestone keys make required data failures fail the edge gate. | `scripts/tests/test_evaluate.py` |
| HB-034 | fixed | Empty, malformed, or incomplete probe payloads classify unknown. | `scripts/tests/test_health_service.py` |
| HB-035 | fixed | IB sync fails closed unless one account scopes positions and financial state. | `scripts/tests/test_ib_sync_multi_account.py` |
| HB-036 | fixed | Portfolio price selection rejects zero, nonfinite, and IB sentinels. | `scripts/tests/test_ib_helpers.py` |
| HB-037 | fixed | Derived midpoint remains calculated and follows bid/ask changes. | `scripts/lib/ibTickHandler.test.js` |
| HB-038 | fixed | Destructive mirror sync requires a contained nonsymlink target and sentinel. | `scripts/tests/test_incident_responder.py` |
| HB-039 | fixed | Checkpoint recovery validates reserved metadata and ignores torn tails. | `scripts/tests/test_checkpoint.py` |
| HB-040 | fixed | Relay freshness is tracked per active subscription, not globally. | `scripts/lib/staleDataMachine.test.js` |
| HB-041 | fixed-by-root | Browser cleanup runs on all cycle exits. | `web/tests/newsfeed-scraper.test.ts` |
| HB-042 | fixed-by-root | Newsfeed paths resolve independently of caller cwd. | `web/tests/newsfeed-cycle-ordering.test.ts` |
| HB-043 | fixed-by-root | Invalid persisted newsfeed JSON fails closed. | `web/tests/newsfeed-cycle-ordering.test.ts` |
| HB-044 | fixed-by-root | Newsfeed local persistence uses atomic replacement. | `web/tests/newsfeed-cycle-ordering.test.ts` |
| HB-045 | fixed | Reversal fills split open and close quantities into separate lifecycle events. | `tests/test_position_return_capital.py` |

## BUG rows

| ID | Disposition | Patch evidence | Regression target |
|---|---|---|---|
| BUG-004 | fixed | CTA retry/backoff envelope fits one unit budget. | `cloud/tests/test_systemd_services.py` |
| BUG-005 | fixed | CTA calendar expressions explicitly declare UTC. | `cloud/tests/test_systemd_services.py` |
| BUG-006 | duplicate -> BUG-021 | Demo mirror service now receives a failing child status. | `cloud/tests/test_systemd_services.py` |
| BUG-007 | fixed | Drift audit waits for network and fails when final health publication fails. | `cloud/tests/test_drift_audit.py` |
| BUG-008 | fixed | Complete required forecast-pipeline failure exits nonzero. | `scripts/tests/test_nightly_forecast.py` |
| BUG-009 | fixed | GARCH wrapper preserves API/direct failure status. | `scripts/tests/test_refresh_wrapper_exit_status.py` |
| BUG-010 | fixed | LEAP timer schedules 10:00 Eastern across DST. | `cloud/tests/test_systemd_services.py` |
| BUG-012 | fixed | Portfolio wrapper gates on the authoritative exchange session. | `scripts/tests/test_portfolio_refresh_wrapper.py` |
| BUG-013 | fixed | An accepted signals request timeout cannot launch a duplicate direct scan. | `scripts/tests/test_run_signals_refresh_wrapper.py` |
| BUG-014 | fixed | Signals timer has an explicit timezone. | `cloud/tests/test_systemd_services.py` |
| BUG-015 | fixed | VCG refresh gates on the current exchange session. | `scripts/tests/test_run_vcg_refresh_wrapper.py` |
| BUG-016 | fixed | CRI LaunchAgent uses periodic runtime Eastern-time gating. | `cloud/tests/test_root_execution_paths.py` |
| BUG-017 | fixed | VCG LaunchAgent uses periodic runtime Eastern-time gating. | `cloud/tests/test_root_execution_paths.py` |
| BUG-018 | fixed-by-root | Kelly domains are constrained at schema, wrapper, and CLI boundaries. | `scripts/tests/test_kelly_domain_guards.py` |
| BUG-019 | fixed-by-root | Scanner/VCG count controls require bounded positive integers. | `lib/tools/__tests__/schemas.test.ts` |
| BUG-020 | fixed-by-root | VCG proxy is allowlisted and noncanonical runs cannot publish shared state. | `scripts/tests/test_vcg_input_guards.py` |
| BUG-021 | fixed | Required demo-mirror source/destination failures are accumulated and fatal. | `scripts/tests/test_demo_seed_guard.py` |
| BUG-022 | fixed | Successful demo mirror batches prune destination latest/history windows. | `scripts/tests/test_demo_seed_guard.py` |
| BUG-023 | fixed | Pending structure/Kelly gates are failed/incomplete and exit distinctly. | `scripts/tests/test_evaluate.py` |
| BUG-024 | fixed | Gateway unit failure is classified as dependency degradation. | `scripts/tests/test_health_service.py` |
| BUG-025 | fixed | Stale cached unit evidence cannot override live probe state. | `scripts/tests/test_health_service.py` |
| BUG-026 | fixed | NAV history update is locked and atomically replaced. | `scripts/tests/test_nav_history.py` |
| BUG-027 | fixed | Knowledge batches preserve whole-document chunk sets before pruning. | `scripts/tests/test_knowledge_store.py` |
| BUG-028 | fixed | Knowledge multi-statement writes explicitly rollback on failure. | `scripts/tests/test_knowledge_store.py` |
| BUG-029 | fixed | Checkpoints reject forged reserved metadata and recompute trusted hashes. | `scripts/tests/test_checkpoint.py` |
| BUG-030 | fixed | Dropped incremental entries stay dirty until the admitted batch settles. | `scripts/lib/sendBackpressure.test.js` |
| BUG-031 | fixed-by-root | Newsfeed DB-dirty delivery state retries and blocks healthy heartbeat. | `web/tests/newsfeed-cycle-ordering.test.ts` |
| BUG-032 | fixed-by-root | Media-dirty delivery state retries without a new content change. | `web/tests/newsfeed-cycle-ordering.test.ts` |
| BUG-033 | fixed-by-root | Relative-image migration writes through atomic validated replacement. | `web/tests/newsfeed-migrate-relative-image-urls.test.ts` |
| BUG-034 | fixed-by-root | Attribution scrub writes through atomic validated replacement. | `web/tests/newsfeed-scrub-generic-image-attributions.test.ts` |
| BUG-035 | fixed-by-root | Taxonomy update uses cross-process locking and atomic replacement. | `web/tests/newsfeed-taxonomy.test.ts` |
| BUG-036 | fixed | Production scanner fetches options and scores the provider's real bias field. | `scripts/tests/test_scanner.py` |
| BUG-037 | fixed | Invalid cache envelopes are quarantined and treated as misses. | `scripts/tests/test_price_cache.py` |
| BUG-115 | fixed | Signed negative BAG credit-combo limits are accepted for IB what-if validation. | `scripts/tests/test_ib_whatif_margin.py` |

## Inherited SECURITY rows

| ID | Disposition | Patch evidence | Regression target |
|---|---|---|---|
| SEC-005 | fixed | Root recurring unit uses `StateDirectory`; recursive writable-tree chown is removed. | `cloud/tests/test_root_execution_paths.py` |
| SEC-036 | fixed | Backup unit and writer apply restrictive creation modes. | `cloud/tests/test_systemd_services.py` |
| SEC-037 | fixed-by-root | Root drift reads descriptor-traverse the repository with no-follow checks. | `cloud/tests/test_drift_audit.py` |
| SEC-038 | fixed | Health unit has task/memory bounds and serves a cached background probe sweep. | `scripts/tests/test_health_service.py` |
| SEC-039 | fixed | External knowledge input recursively scrubs secret keys, headers, URLs, and prose. | `scripts/tests/test_knowledge_pipeline.py` |
| SEC-040 | fixed | Monitor service applies `UMask=0077`. | `cloud/tests/test_systemd_services.py` |
| SEC-041 | fixed | Newsfeed service applies `UMask=0077`. | `cloud/tests/test_systemd_services.py` |
| SEC-043 | fixed | Data refresh no longer writes diagnostics to predictable shared `/tmp` names. | `cloud/tests/test_root_execution_paths.py` |
| SEC-044 | fixed | Margin-debt errors redact URL credentials and launchd files are private. | `scripts/tests/test_forecast_writers.py` |
| SEC-045 | fixed | Historical contract qualification is capped at 50 contracts. | `scripts/api/tests/test_historical_pool.py` |
| SEC-046 | fixed | Account-derived flow is excluded and purged on every demo mirror run. | `scripts/tests/test_demo_seed_guard.py` |
| SEC-047 | duplicate -> SEC-038 | Per-request probe executors are replaced by the shared cached sweep. | `scripts/tests/test_health_service.py` |
| SEC-053 | duplicate -> SEC-041, fixed-by-root | Browser storage state is atomically persisted with mode 0600. | `web/tests/newsfeed-scraper.test.ts` |

## Verification

- Backend affected Python tranche: `475 passed`; targeted demo-mirror regressions: `7 passed`; targeted cloud/root/service/drift regressions: `383 passed`.
- Relay protocol/backpressure/freshness regressions: `82 passed` with Vitest.
- Delegated root tranches: tools/Kelly/VCG `33 Vitest + 68 pytest`; newsfeed durability `75 Vitest`; drift audit `31 pytest`; discover `27 pytest`.
- No backend-owned finding remains deferred, external, or actionable.
