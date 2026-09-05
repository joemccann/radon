# Radon Monitor Daemon — CLAUDE.md

Real-time fill / order / journal handlers. Loaded automatically when cwd is under `scripts/monitor_daemon/`. Project root + `scripts/CLAUDE.md` rules also apply.

---

## Market-Hours Gate

`scripts/monitor_daemon/daemon.py:is_market_hours()` gates handlers with `requires_market_hours=True`. Uses `datetime.now(ZoneInfo("America/New_York"))` for EST↔EDT auto via tzdata; fail-open UTC-5 fallback. **Never reintroduce hardcoded offsets.** See `feedback_hardcoded_timezone_offsets.md` for the DST bug that motivated this.

Handlers that run 24/7 (flex-token-check, journal-sync via the rehydrate-style importer) set `requires_market_hours=False`. Real-time fill-monitor / exit-orders / portfolio-sync are gated on. `BaseHandler.session_window` picks the window: `"rth"` (default) is 09:30-16:00 ET; `"equity_ext"` is 04:00-20:00 ET (`utils.market_calendar.is_equity_ext_session_et`) so outsideRth stock fills still reach `/orders` after the cash close. Only `fill_monitor` sets `equity_ext`; overnight 20:00-03:50 ET is a different IB venue and is not EXT. FastAPI's 5-min orders-sync tick (`scripts/api/server.py:_orders_sync_tick`) is gated on the same EXT session.

**Post-close grace (opt-in):** `BaseHandler.post_close_grace_minutes` (default 0) lets a market-hours handler keep cycling for a bounded window after the close. The gate answers "was the market open N minutes ago?" via `utils.market_calendar.market_state` (the calendar SoT), so weekends/holidays never gain a window and any calendar error fails closed. Only `journal_sync` opts in (15 min = two more 300s cycles): its per-cycle IB session only covers the current day, so a fill in the session's final seconds was otherwise never journaled (15:59:52 / 15:58:04 incidents). Tests: `scripts/tests/test_monitor_daemon/test_post_close_grace.py`.

---

## Handler Conventions

- **Client ID:** every handler uses `client_id="auto"` via `scripts/clients/ib_client.py:_connect_auto_allocate`. Prior hardcoded values in the 20–49 range hit "client id already in use" after a single CLOSE_WAIT. Don't reintroduce.
- **Heartbeat on every cycle:** even on `nochange` short-circuit, the handler must `record_service_health(<name>, "ok", ...)`. A stale error row latches as the banner state forever if you don't. See `feedback_service_health_heartbeat.md`. `"ok"` is for a successful run or a genuine no-op ONLY — a failed run or a missed tick records `warn`/`error`, never `ok` (31dbb954): heartbeating `ok` over a failure hides the fault from the banner exactly as effectively as not heartbeating at all.
- **Soft failures don't burn the daily slot:** daily handlers must raise on retryable errors so `BaseHandler` doesn't latch `last_run`. Use `record_soft_failure` for short embargo retries (~5 min). See `feedback_dont_latch_last_run_on_soft_failure.md`.

---

## Journal Sync — Action Labelling

`journal_sync.py:_side_to_action` accepts `prior_qty` and labels sells against a prior long as `SELL_OPTION` (close), not `SELL_TO_OPEN` (open short). `prior_net_qty_for_contract` in `scripts/clients/journal_basis.py` does the lookup against the journal table.

Same rule as `journal_rehydrate.py` (commit 4c85847). Real-time handler version shipped in 9833238 + df03565 (test backfill).

Consumer (`web/lib/blotter/fromJournal.ts`) treats the labels very differently — SELL_TO_OPEN sets `isOpen=true` with `net_quantity=-qty` (phantom new short), SELL_OPTION sets `isOpen=false` with `net_quantity=0` (correct close). Lot-matched P&L uses net-qty sign so it's unaffected either way, but the position view + isOpen-keyed stats depend on correct labels.

---

## Fill Monitor — Closing-Trade Risk Discount

Fill monitor processes one fill at a time. For risk reporting, `OrderRiskLeg.coveringLongContracts` must reflect contracts held LONG of the exact same option (strike/right/expiry match). Otherwise SELL-to-close of a long call gets flagged as a naked short.

Full rule in `web/CLAUDE.md` §Combo / BAG Order Guardrails point 7. Python side: keep the field populated; consumer side: short-circuit `maxLoss: 0` when `coveringLongContracts >= effectiveContracts`.

---

## Where Other Daemons Live

- `cash_flow_sync` is NOT registered since 2026-09-02. Cash flows come from the sFTP-delivered Activity statement (`radon-flex-pull.timer` Tue..Sat 07:30 ET -> `flex_delivery_ingest` -> `cash_flow_sync --from-file`), and that ingest writes the `cash-flow-sync` service_health row. The handler's daily no-source run exited `EXIT_FLEX_SEND_DISABLED` and painted "Flex Web Service is file-ingest only" over rows the delivery had already synced. The module stays for its embargo bookkeeping tests (R-104/R-108/R-109); re-registering it means a weekday SendRequest, which is off by policy.

  **Only Flex code 1018 is a rate limit** — IBKR publishes one request per second, ten per minute, per token, and no daily or multi-day cooldown anywhere. The breaker ladder is 90s → 5m → 15m → 1h. It was 24h → 48h → 72h → 168h and also fired on **1001** (transient generation failure) and **1019** (*"statement generation in progress"* — the ordinary not-ready response during polling). A statement seconds from ready therefore bought a 24-hour backoff, and a few transient failures walked to a week: that is the 10-day outage from 2026-08-06. 1001/1009 take the soft lane; 1019 on a poll is not an error at all. See `docs/flex-delivery-architecture.md`.
- `flex_token_check` runs daily, alerts on expiry.
- `menthorq_session_check` is metadata-only (durable `__Secure-authjs.session-token` expiry). `menthorq_login_probe` GETs local FastAPI `/options/exposure/SPX` (90s). Session ok + probe error = unspendable dashboard jar, not a healthy session. Bootstrap must click OIDC `input[name=authorize]` after WordPress login; the page stays on `wp-login.php`. Do not stand down as `client_id=aws_cognito_client_id`. CTA jar ≠ dashboard jar.
- `replica_watchdog` is disabled before subprocess or health writes when `data/replica.db` is absent. While the file exists it is event-driven — only writes `service_health` when it actually heals. Use the 24h staleness window only for that applicable state (event-driven writer windows rule in `feedback_event_driven_writer_windows.md`).
