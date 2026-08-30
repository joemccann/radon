# Task: Diagnose HEADLINES FEED DOWN (2026-08-30, ~17:56–18:12+ UTC)

## Objective

- Identify why the LIVE MARKET FEED headlines tape showed "HEADLINES FEED DOWN. LAST PRINT 16M AGO" on Sunday 2026-08-30 while the rest of the dashboard stayed live.
- Ship only the hardening the evidence supports; hand off the ops action it cannot fix from the repo.

## Dependency graph

- T1 depends_on: [] - Map the pipeline (mktnews hub, radon-mktnews.service, /ws-headlines edge, useHeadlines) and the exact banner trigger
- T2 depends_on: [T1] - Localize the stall with external probes (edge health, hub handshake, vendor WS tap, CI/deploy timeline)
- T3 depends_on: [T2] - Red tests: flash REST poll lane while the upstream WS is down; upstream-down status at client admit
- T4 depends_on: [T3] - Implement hub changes green
- T5 depends_on: [T2] - Manifest-pin radon-mktnews.service; retire the stale not-installed drift ack
- T6 depends_on: [T4, T5] - Full JS suite + cloud pytest; commit; PR with diagnosis and the ops action

## Checklist

- [x] T1 Pipeline mapped (hub.js / client.js / Caddy :8766 / useHeadlines status machine)
- [x] T2 Stall localized to the VPS→api.mktnews.net WS dial (vendor healthy globally, hub process alive, no deploy in the window)
- [x] T3 Red tests written (3 red / 2 guard-green pre-fix)
- [x] T4 Hub poll fallback + admit-time status frame green (68/68 mktnews)
- [x] T5 installed-units.sha256 pin + drift-allowlist retirement (cloud tests green)
- [x] T6 8307 vitest passed, 1503 cloud pytest passed; PR opened

## Review

- Root cause is VPS-side upstream WS dial failure to the Cloudflare-fronted api.mktnews.net (ops: journalctl -u radon-mktnews shows the close/reconnect codes; egress/IP remediation if 403/1015).
- Code hardening: hub now feeds the tape from the flash REST lane during a WS outage (health row deliberately stays error) and tells mid-outage dashboards the true state at admit.
- Config hardening: the hub's unit is no longer excluded from every automated install path (R-092 trapdoor).
