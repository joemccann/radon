# Radon Watchdog — Codex Instructions

Applies under `scripts/watchdog/`. Mirrors `scripts/watchdog/CLAUDE.md`.

## Buckets

Watchdog monitors scheduled services from `web/lib/serviceHealthWindows.ts` and writes always-on `service_health` rows.

- `intraday`: `vcg-scan`, `cri-scan`, `orders-sync`, `portfolio-sync`.
- `continuous`: `newsfeed-scraper`, `fill-monitor`, `journal-sync`.
- `daily`: `cash-flow-sync`, `flex-token-check`, `cta-sync`.
- `error`: every scheduled service except `watchdog-alerts` itself.

## Anti-Flood

- Require 2 consecutive failures before alerting.
- Use 1h cooldown per `(service, severity)` in `watchdog_cooldowns`.
- Manual mute: `python -m scripts.watchdog ack <service>` for 4h.
- Missing Pushover env degrades gracefully; still write rows.
- A delivered P1 writes `watchdog_pages` for the VPS Grok responder
  (`docs/grok-page-responder.md`). Laptop launchd is off. Enqueue is best-effort and must never
  block or unmute the page. Follow-up and deploy-live pages are priority 0.

## State Semantics

- Stale `scheduled` services produce red banners.
- Stale `on-demand` services become `state="dormant"` with amber chip.
- `event-odds` is request-driven by `GET /event-odds/{ticker}`; no VPS timer owns it. Keep it out of scheduled watchdog buckets.
- Event-driven writers use 24h windows; retired writers (`dropbox-research`, `replica-watchdog`) are disabled before historical health rows are read.
- `service_health` row state reflects the writer's health, not the content or severity of the last event it dispatched.
- Do not mirror dispatched alert severity into the writer's own row.

## IB Outage Grouping

- Each service window declares `requires_ib`.
- When IB is down, group IB-required failures whose last_error / check reason is IB-shaped (connection, 2FA, gateway, timeout). A latched writer-integrity error (execution fact conflict) falls through per-service. `awaiting_2fa` copy is approve-on-phone / reset-backoff; `unreachable` copy is `radon restart`, not a 2FA prompt.
- IBKR operator hold (`/health` `ib_gateway.operator_hold.held`): absorb every IB-caused failure with no page. The Gateway is down on purpose; the broker `ib-watchdog` pages HELD once per hold.
- UW-only, Flex-only, and Playwright-only writers are not IB-required just because they share the dashboard.
- On IB transition from `awaiting_2fa` to `authenticated`, clear stale service-health rows for IB-required services.
