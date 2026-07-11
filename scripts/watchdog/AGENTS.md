# Radon Watchdog — Codex Instructions

Applies under `scripts/watchdog/`. Mirrors `scripts/watchdog/CLAUDE.md`.

## Buckets

Watchdog monitors scheduled services from `web/lib/serviceHealthWindows.ts` and writes always-on `service_health` rows.

- `intraday`: `vcg-scan`, `cri-scan`, `orders-sync`, `portfolio-sync`.
- `continuous`: `newsfeed-scraper`, `fill-monitor`, `exit-orders`, `journal-sync`, plus `replica-watchdog` only while `data/replica.db` exists.
- `daily`: `cash-flow-sync`, `flex-token-check`, `cta-sync`.
- `error`: every scheduled service except `watchdog-alerts` itself.

## Anti-Flood

- Require 2 consecutive failures before alerting.
- Use 1h cooldown per `(service, severity)` in `watchdog_cooldowns`.
- Manual mute: `python -m scripts.watchdog ack <service>` for 4h.
- Missing Pushover env degrades gracefully; still write rows.

## State Semantics

- Stale `scheduled` services produce red banners.
- Stale `on-demand` services become `state="dormant"` with amber chip.
- Applicable event-driven writers use 24h windows; a missing replica file disables `replica-watchdog` evaluation before historical health rows are read.
- `service_health` row state reflects the writer's health, not the content or severity of the last event it dispatched.
- Do not mirror dispatched alert severity into the writer's own row.

## IB Outage Grouping

- Each service window declares `requires_ib`.
- When IB is down, group all IB-required failures into a single "IB Gateway awaiting 2FA / unreachable" message.
- UW-only, Flex-only, and Playwright-only writers are not IB-required just because they share the dashboard.
- On IB transition from `awaiting_2fa` to `authenticated`, clear stale service-health rows for IB-required services.
