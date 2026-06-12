# Radon Watchdog — CLAUDE.md

Service-health monitor + alert dispatcher. Loaded when cwd is under `scripts/watchdog/`.

---

## Four Buckets

Monitors every `scheduled` service in `web/lib/serviceHealthWindows.ts`, notifies via Pushover (P1 only) + always-on `service_health` row.

- **`intraday`**: `vcg-scan`, `cri-scan`, `orders-sync`, `portfolio-sync` — 5 min cadence, Mon–Fri 13:00–21:00 UTC.
- **`continuous`**: `newsfeed-scraper`, `replica-watchdog`, `fill-monitor`, `exit-orders`, `journal-sync`, `ib-watchdog` — 5 min, 24/7.
- **`daily`**: `cash-flow-sync`, `flex-token-check`, `cta-sync`, `llm-token-index`, `leap-scan`, `garch-scan` — hourly, 24/7.
- **`error`**: every scheduled service except `watchdog-alerts` itself (recursive-alert prevention) — 5 min, 24/7.

---

## Systemd Unit Alarm (`units.py`)

Rides the continuous bucket only (`__main__._cmd_bucket`, NOT in `SCHEDULED_SERVICES` — the CI contract test locks that dict to the TS file). Probes `systemctl show 'radon-*' -p Id,ActiveState,SubState,Result,NRestarts` (read-only) and alerts on: `ActiveState=failed` (P1; `Result=start-limit-hit` never auto-recovers — the DUR-02 StartLimit brake in radon-cloud unit files depends on this alarm), 2-consecutive-cycle `auto-restart` flap (P1), per-cycle NRestarts delta (P3). Last-cycle state in `data/watchdog_units_state.json` (gitignored). **ALERT-ONLY** — never restarts anything (`feedback_ib_auto_recovery_conservative`). No own `service_health` row (matches siblings; `watchdog-alerts` dispatcher row covers it). No-op on hosts without systemctl.

---

## Anti-Flood

- 2-consecutive-failure hysteresis before alerting.
- 1h per-(service, severity) cooldown in `watchdog_cooldowns` table.
- Manual mute: `python -m scripts.watchdog ack <service>` for 4h.

Env: `PUSHOVER_USER`, `PUSHOVER_TOKEN` (absent → degrade gracefully, just write rows).

---

## Service Categorisation

Services tagged `scheduled` or `on-demand`:
- Stale `scheduled` → red banner.
- Stale `on-demand` → `state="dormant"`, amber chip.

**Event-driven writers** (`replica-watchdog`, `watchdog-alerts`) use 24h windows. Tight windows treat quiet healthy periods as stale — see `feedback_event_driven_writer_windows.md`.

---

## Writer-State vs Event-Content

`service_health` row state reflects THIS writer's health, never the content of the last event it dispatched. `watchdog-alerts` previously mirrored dispatched-alert severity into its own row, causing stale "in error state: Failed to connect to IB" banner attribution. Fix in commit af1491a + one-shot migration.

See `feedback_service_health_writer_state_not_event_content.md`.

---

## IB-Outage Grouping

Each service window declares `requires_ib`. When IB is down, the watchdog groups all `requires_ib: true` failures into a single "IB Gateway awaiting 2FA / unreachable" message instead of N independent alerts.

Verified against each writer's source code in `test_services.py`. UW-only / Flex-only / Playwright-only writers are FALSE even if they live on the same dashboard as IB-backed services.

---

## Auto-Heal on Recovery

When IB transitions from `awaiting_2fa` → `authenticated`, the handler clears stale `service_health` rows for `requires_ib=true` services. Watchdog-alerts is intentionally NOT in scope (separate writer-semantics fix). Commit 5aea4ec.
