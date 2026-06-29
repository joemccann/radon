# Radon Security Audit Playbook

A reusable, extensible process for auditing Radon's security. Re-run it on a
cadence (quarterly + before major releases) and **add to it** as the app grows.

## Assets

| Asset | Purpose |
|---|---|
| `.claude/workflows/security-audit.mjs` | The audit engine. Fans out one finder per dimension, adversarially verifies every finding, runs a completeness + regression critic. Returns structured JSON. |
| `scripts/security/gen_security_report.py` | Deterministic JSON → HTML report renderer. |
| `docs/security-audit-playbook.md` | This file — methodology, dimension catalog, regression invariants, audit log. |
| `docs/security-audit-<date>.html` | One rendered report per audit run (kept in git as the record). |

## How to run

1. **Audit** (in Claude Code): `Workflow({ name: "security-audit" })`. Subset:
   `args: { focus: ["authn-authz","sqli"] }`. Extend on the fly:
   `args: { extraDimensions: [{ key, label, scope }] }`.
2. **Capture** the workflow's returned JSON (the Workflow tool writes it to the
   task-output file).
3. **Render**:
   `python3 scripts/security/gen_security_report.py <task-output.json> docs/security-audit-<date>.html --date <date> [--fixes fixes.json]`
   (`--fixes` is an optional list of `{id,sev,title,finding,fix,files[],tests}`
   documenting patches you shipped for that run.)
4. **Triage & patch** — see policy below. **Re-verify** every patch with red/green tests.
5. **Record** the run in the Audit Log table + update memories/lessons.

## Dimension catalog

Edit the `DIMENSIONS` array in the workflow to extend. Keep each scope concrete:
name the files to read and the exact weaknesses to hunt.

1. `authn-authz` — Clerk/JWT, middleware default-deny, trusted-local bypass, ALLOWED_USER_IDS, IDOR, admin routes
2. `sqli` — every db.execute / hrana_execute / cursor.execute call site; parameterization
3. `cmd-path-ssrf` — subprocess/PI allowlists, spawn, path traversal in file routes, SSRF in server fetch
4. `secrets` — leakage to client/logs/errors, committed secrets, git history
5. `client-xss` — dangerouslySetInnerHTML, CSP, security headers, CORS, open redirect
6. `api-idor-validation` — input validation, mass assignment, IDOR, rate limiting on mutating routes
7. `vps-infra-deploy` — GitHub Actions, Docker, Caddy, SSH/deploy, exposed ports, SHA-pinning
8. `dependencies` — npm/pip audit, runtime-reachability triage, lockfile integrity
9. `websocket-relay` — ticket auth, forwarding-header trust, origin check, subscription limits, maxPayload
10. `data-pii` — share routes, demo isolation, account figures reachable without authz
11. `regression` — re-checks that every durable invariant below still holds (report ONLY on regression)

## Durable regression invariants (re-checked every run)

Each fixed issue becomes an invariant the `regression` dimension verifies. **Add a
line here whenever you ship a security fix.**

- **WS relay trust** — `scripts/lib/wsTrust.js:shouldSkipTicketValidation` must
  return false when a forwarding header is present (Caddy proxies `/ws*` to
  loopback, so peer address alone is untrustworthy). The relay must call it.
  (`feedback_ws_relay_trust_forwarding_headers`)
- **Share-content allowlist** — the 5 `*/share/content` routes serve ONLY
  `tweet-<type>-<date>[-card-N].html` from directly inside `reports/`, scoped per
  type. Never widen to arbitrary `reports/` paths.
  (`feedback_share_content_allowlist_not_arbitrary_reports`, `web/lib/shareReportPath.ts`)
- **AUTH_EXEMPT_PATHS** — must NOT contain `/docs` or `/openapi.json` (trusted-local
  only); pinned by `test_health_payload.py` + `test_route_authz_matrix.py`.
  (`feedback_auth_exempt_paths_double_pin`)
- **Middleware perimeter** — default-deny (`if(!isPublicRoute)`), ALLOWED_USER_IDS
  operator gate, the `/api` matcher always running, `PUBLIC_SHARE_API_ROUTES` a
  FIXED explicit list (no `/api/**/share` regex).
  (`feedback_middleware_is_the_perimeter`, `project_prod_perimeter_operator_allowlist`)
- **Trust-scoped surfaces** — `/health` and `/api/service-health` must never emit
  account IDs, IB topology, or secret-shaped error strings to anonymous callers
  (service-health runs `scrubSecrets`).
  (`feedback_health_endpoint_public_leak_and_trust_chokepoint`)
- **No Next.js spawn with user args**; subprocess + PI exec allowlists intact.

## Triage & patch policy

- Severity = verifier-revised, real-world impact for a **single-operator** app, not
  raw scanner output. Adversarial verification routinely downgrades scanner
  "criticals" that don't apply to this app's default-deny architecture — trust it,
  but confirm the reasoning.
- Patch order: **exploitable** first, then defense-in-depth. Patch the safe,
  high-confidence ones with red/green tests; **defer** anything needing an
  environment-specific verification step (production build, VPS config in the
  separate `radon-cloud` repo) and document it.
- **Do NOT auto-push.** `git push origin main` IS a production deploy (CI SSHes to
  Hetzner). Land perimeter/relay changes only on the operator's explicit go; after
  deploy, verify the relay + a real authed page render.
- Don't blind-bump a framework (next/@clerk) just because a scanner flags it —
  verify the exploit applies, then gate the bump on a full build + perimeter-smoke.

## Audit log

| Date | Dimensions | Raw → Confirmed (exploitable) | FP | Report | Notes |
|---|---|---|---|---|---|
| 2026-06-28 | 10 + critic | 58 → 48 (8) | 12 | `docs/security-audit-2026-06-28.html` | 8 fixes shipped (WS relay bypass, share-content disclosure, ws bump, /docs gate, service-health scrub, path guards, CI hardening, HSTS). Deferred next/@clerk bumps + VPS config. `tasks/security-audit-2026-06-28.md`. |
