# Radon Monitor Daemon — Codex Instructions

Applies under `scripts/monitor_daemon/`. Root and `scripts/AGENTS.md` also apply.

## Market-Hours Gate

- `scripts/monitor_daemon/daemon.py:is_market_hours()` uses `datetime.now(ZoneInfo("America/New_York"))`.
- Never reintroduce hardcoded EST/EDT offsets. Missing tzdata logs an error and refuses market-hours admission; ungated handlers continue. Calendar-only failures retain the valid RTH clock fallback (REL-021b / R-046).
- RTH admission honors the calendar source of truth for holidays and early closes (REL-021b / R-030); the equity_ext monitoring fallback explicitly retains the calendar-independent RTH clock (REL-209 / R-625).
- Real-time fill monitor, exit orders, and portfolio sync are market-hours gated.
- Flex-token check and rehydrate-style journal sync run 24/7 where configured; cash-flow sync is not registered.

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

- `cash_flow_sync` is not registered since 2026-09-02. Cash flows come from delivered Activity statements through `flex_delivery_ingest` and `cash_flow_sync --from-file`; do not restore scheduled Flex SendRequest.
- Flex throttle errors require backoff; do not manually retry during throttle because it pushes reset further out.
- `replica_watchdog` is disabled before subprocess or health writes when `data/replica.db` is absent; while the file exists it is event-driven and uses a 24h staleness window.
- `menthorq-session` is cookie-expiry only. `menthorq-login-probe` is the live remint. Session ok + probe error means click OIDC Authorize (`input[name=authorize]`), not stand down on `client_id=aws_cognito_client_id`. Dashboard jar is `data/menthorq_dashboard/`; CTA jar is `data/menthorq_cache/`.

## Journal History Read Bounds

REL-108 / NF-2: journal-sync uses the shared urllib Hrana connection, not native libsql, for history and prior-quantity reads. Recovery, execution coverage and mirror scans use 200-row insertion cursors with a 30-second scan deadline; each HTTP request has the shared transport timeout. A failed page exposes no partial state. Recovery restores effective-time ordering after pagination. Concurrent inserts are included; concurrent update/delete snapshot isolation is not claimed.
