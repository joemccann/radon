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

## Deferred — operator action required (need env-specific verification)
- [ ] **next 16.1.6 → >=16.2.6** — advisory HIGH but NOT exploitable here (api matcher always
      runs; pages are thin shells). Needs `bun run build` on `--experimental-build-mode=compile`
      + perimeter-smoke before deploy. `cd web && bun add next@latest`.
- [ ] **@clerk/nextjs 7.0.7 → >=7.5.9** — advisory CRITICAL but NOT exploitable (default-deny
      pattern is the safe one per the advisory). `cd web && bun add @clerk/nextjs@latest`; verify build.
- [ ] **Relay loopback bind on VPS** — set `WS_BIND_HOST=127.0.0.1` in radon-cloud relay env.
- [ ] **Sync HSTS + headers to live VPS Caddy** (radon-cloud repo — not this tree).
- [ ] **CSP Report-Only → enforced** — needs ThemeBootstrap inline-script nonce first.
- [ ] **Edge rate-limiting** on public `/api/share/pnl` + `*/share` generators.
- [ ] **Pin remaining Actions to SHA** (`oven-sh/setup-bun@v2`, `actions/*`); pin `requirements.txt`.
- [ ] **Pre-existing flaky web vitest** (~17 order-dependent failures; pass in isolation) — not
      security, but erodes CI signal. Isolate shared WS/DB state.

## Durable invariants (do not regress)
1. The WS relay must NOT trust `socket.remoteAddress` alone — Caddy makes every public client
   look like 127.0.0.1. Trust = loopback AND no forwarding header. Logic: `scripts/lib/wsTrust.js`.
2. Public `*/share/content` routes serve ONLY `tweet-<type>-<date>[-card-N].html` from directly
   inside `reports/`. Never widen to arbitrary `reports/` paths. Validator: `web/lib/shareReportPath.ts`.
3. Anything in `AUTH_EXEMPT_PATHS` is world-reachable via Caddy `/api/ib/*` — additions need review
   + the two pinned tests (`test_health_payload.py`, `test_route_authz_matrix.py`).
