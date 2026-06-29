# Security Audit — 2026-06-28

Full report: `docs/security-audit-2026-06-28.html`

## Method
71-agent workflow over 10 dimensions (authn/authz, SQLi, cmd/path/SSRF, secrets,
client/XSS/headers, API validation/IDOR, VPS/infra/deploy, dependencies,
websocket/relay, data/PII). Each finding adversarially verified (refute-first) +
a completeness-critic pass. 58 raw → 48 confirmed (8 exploitable) / 12 false
positives. No SQLi, no command injection, no committed/hardcoded secrets.

## Fixes shipped (all test-backed)
- [x] **WS relay ticket bypass (HIGH, exploitable).** `scripts/lib/wsTrust.js` (new) +
      `scripts/ib_realtime_server.js`. Caddy proxies `/ws*` to loopback, so the old
      `remoteAddress`-only check skipped ticket auth for ALL public connections. Now
      checks forwarding headers (mirrors `is_trusted_local_request`). `WS_BIND_HOST` env added.
- [x] **Share-content file disclosure (MEDIUM, exploitable).** `web/lib/shareReportPath.ts`
      (new) + 5 `*/share/content` routes. Were serving any file under `reports/` to anon
      callers (portfolio/eval reports). Now allowlisted to `tweet-<type>-<date>` cards, file
      must sit directly in `reports/`.
- [x] **ws 8.19.0 → ^8.21.0** (DoS advisory, reachable via relay). root + web.
- [x] **FastAPI /docs + /openapi.json** removed from `AUTH_EXEMPT_PATHS` (was public via
      Caddy `/api/ib/*`); now trusted-local only. Pins updated.
- [x] **/api/service-health** error strings routed through `scrubSecrets()`.
- [x] **ticker/seasonality, ticker/info, futures/chain** symbol regex guard (path traversal DiD).
- [x] **CI**: `permissions: contents: read` + `appleboy/ssh-action` pinned to v1.2.5 SHA.
- [x] **Caddyfile (in-repo)**: HSTS added.

## Deferred — ALL CLOSED by 2026-06-29 (see `tasks/security-audit-2026-06-29.md`)
- [x] **next 16.1.6 → >=16.2.6** — bumped to 16.2.9 (commit 72cb6230); CI green incl. build.
- [x] **@clerk/nextjs 7.0.7 → >=7.5.9** — bumped; fixed `baseTheme`→`theme` rename.
- [x] **Relay loopback bind on VPS** — `WS_BIND_HOST=127.0.0.1` in radon-cloud relay env.
- [x] **Sync HSTS + headers to live VPS Caddy** — app + media blocks (radon-cloud 779405e, ac106e6).
- [x] **CSP Report-Only → enforced** — per-request nonce; ThemeBootstrap nonce'd. (9133edf7)
- [x] **Edge rate-limiting** on public `/api/share/pnl` + `*/share` generators. (9133edf7)
- [x] **Pin remaining Actions to SHA** + `requirements.txt` (to VPS versions). (9133edf7)
- [ ] **Pre-existing flaky web vitest** (~17 order-dependent failures; pass in isolation) — not
      security, but erodes CI signal. CI already excludes the worst offenders
      (`data-reader`, `kelly`, `runner`); full shared-state isolation still open.

## Durable invariants (do not regress)
1. The WS relay must NOT trust `socket.remoteAddress` alone — Caddy makes every public client
   look like 127.0.0.1. Trust = loopback AND no forwarding header. Logic: `scripts/lib/wsTrust.js`.
2. Public `*/share/content` routes serve ONLY `tweet-<type>-<date>[-card-N].html` from directly
   inside `reports/`. Never widen to arbitrary `reports/` paths. Validator: `web/lib/shareReportPath.ts`.
3. Anything in `AUTH_EXEMPT_PATHS` is world-reachable via Caddy `/api/ib/*` — additions need review
   + the two pinned tests (`test_health_payload.py`, `test_route_authz_matrix.py`).
