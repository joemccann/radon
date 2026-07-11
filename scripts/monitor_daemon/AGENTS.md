# Radon Monitor Daemon — Codex Instructions

Applies under `scripts/monitor_daemon/`. Root and `scripts/AGENTS.md` also apply.

## Market-Hours Gate

- `scripts/monitor_daemon/daemon.py:is_market_hours()` uses `datetime.now(ZoneInfo("America/New_York"))`.
- Never reintroduce hardcoded EST/EDT offsets.
- Real-time fill monitor, exit orders, and portfolio sync are market-hours gated.
- Cash-flow sync, flex-token check, and rehydrate-style journal sync run 24/7 where configured.

## Handler Conventions

- Every handler uses `client_id="auto"`.
- Heartbeat every cycle with `record_service_health(<name>, "ok", ...)`, including no-change short-circuits.
- Retryable daily-handler errors must not burn the daily slot. Raise or record soft failure instead of latching `last_run`.
- State lives in `data/daemon_state.json`; logs in `logs/monitor-daemon.log`.

## Journal / Fill Rules

- `journal_sync.py:_side_to_action` must use prior quantity. Sells against a prior long are `SELL_OPTION`, not `SELL_TO_OPEN`.
- `prior_net_qty_for_contract` in `scripts/clients/journal_basis.py` is the lookup source.
- Fill monitor processes one fill at a time; for risk reporting, populate `OrderRiskLeg.coveringLongContracts` for exact same option long coverage.
- SELL-to-close of a long call must not be flagged as naked exposure.

## Other Daemons

- `cash_flow_sync` runs once per ET trading day at 17:00 ET and uses `IB_FLEX_NAV_QUERY_ID`.
- Flex throttle errors require backoff; do not manually retry during throttle because it pushes reset further out.
- `replica_watchdog` is disabled before subprocess or health writes when `data/replica.db` is absent; while the file exists it is event-driven and uses a 24h staleness window.
