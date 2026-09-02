# Radon Security Audit Playbook

A reusable, extensible process for auditing Radon's security. Re-run it on a
cadence (quarterly + before major releases) and **add to it** as the app grows.

## Assets

| Asset | Purpose |
|---|---|
| `.claude/workflows/security-audit.mjs` | The audit engine. Fans out one finder per dimension, adversarially verifies every finding, runs a completeness + regression critic. Returns structured JSON. |
| `scripts/security/gen_security_report.py` | Deterministic JSON → HTML report renderer. |
| `docs/security-audit-playbook.md` | This file — methodology, dimension catalog, regression invariants, audit log. |
| `docs/security-audit-<date>.html` | One rendered report per run. **This repo is PUBLIC — the report enumerates the attack surface and quotes secret/topology strings, so it is gitignored (`docs/security-audit-*.html`) and MUST be filed in the private `radon-cloud:security-archive`, never committed here.** |

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
10. `data-pii` — share routes (all 5 pairs), demo isolation + demo-mirror scripts, account figures reachable without authz
11. `cloud-archive` — Backblaze B2 portfolio cold-archive: bucket/object ACL, delete-before-verify data loss, S3 credential leakage
12. `regression` — re-checks that every durable invariant below still holds (report ONLY on regression)

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
- **CSP enforced (not Report-Only)** — `web/middleware.ts:buildCspWithNonce` ships a
  per-request nonce with NO `'unsafe-inline'`/`'unsafe-eval'` in `script-src`, and
  `next.config.mjs` emits NO CSP header. Must keep `worker-src 'self' blob:` and the
  Clerk host allowlist (NOT `'strict-dynamic'`, which blocks Clerk's loader).
  (`feedback_csp_strict_dynamic_breaks_clerk_loader`, `web/tests/csp-nonce.test.ts`)
- **Public unauth routes rate-limited** — the 5 `*/share` POST generators + GET
  `/api/share/pnl` call `rateLimit()` (`web/lib/rateLimit.ts`) before any heavy work.
  (`web/tests/rate-limit.test.ts`)
- **Ops admin routes fail CLOSED** — `/api/admin/{stack/restart,ib/restart,
  ib/reset-backoff,services/[unit]/[action]}` call `requireDemoAdmin()` and 403 when
  the allowlist is empty (middleware `isAuthorizedUser` fails OPEN by design; these
  must not inherit that). (`web/tests/api-routes-smoke-admin.test.ts`)
- **CI supply chain** — third-party GitHub Actions SHA-pinned; the gitleaks binary
  download is SHA256-verified; `requirements.txt` pinned to the VPS's running
  versions. (`feedback_pin_requirements_to_vps_not_laptop`)
- **CORS explicit allowlist** — FastAPI CORS uses `allow_origins=[...]` (explicit
  hosts), NOT a `https://.*\.radon\.run` wildcard regex (which a subdomain
  takeover could ride). `allow_credentials` stays False.
  (`scripts/api/tests/test_cors_allowlist.py`)
- **Order idempotency** — `/api/orders/place` dedups concurrent/just-repeated
  identical placements (in-flight + short-TTL content hash, or an explicit
  client `idempotencyKey`) so a double-click never doubles a real-money position.
  (`web/lib/orders/orderIdempotency.ts`, `web/tests/order-place-idempotency-route.test.ts`)
- **Share allowlist filesystem pin** — `PUBLIC_SHARE_API_ROUTES` exactly matches the
  `*/share` + `*/share/content` route files on disk; a new share route can't ship
  unlisted (nor a listed route go missing).
  (`web/tests/middleware-share-allowlist.test.ts`)
- **New read admin routes gated** — `/api/admin/{host-metrics,slo,reliability,
  edge-health}` enforce operator authz and never leak host telemetry to a signed-in
  demo user, same as the mutating admin routes.
- **B2 archive delete-safety** — `scripts/archive_portfolio_snapshots.py` never
  deletes Turso snapshot rows before the B2 partition write is verified, never sets
  a public bucket/object ACL, and never logs `RADON_ARCHIVE_S3_*` credentials.
  (`scripts/tests/test_archive_portfolio_snapshots.py`)
- **Public repo hygiene** — the GitHub `radon` repo is PUBLIC. No secret may be
  committed; git *history* must stay clean (2026-07-05 sweep found creds pending
  rotation — verify purge status each run).
  (`project_public_repo_leak_sweep_2026_07_05`)
- **Stored-credential egress is vendor-pinned** — any validator/probe that sends a
  credential which the server merged from the SecretStore/env (not submitted by the
  caller) must hardcode or allowlist the vendor destination; a caller-supplied URL
  field is an egress decision, never preference (Turso: https + `*.turso.io` only).
  (`scripts/tests/test_credential_validators.py::TestTursoEgressPin`,
  `scripts/api/tests/test_credentials_routes.py::TestValidateEgressPin`)

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
- **Never quote the literal you found.** An audit report describes a credential
  finding by naming the variable and its location, never by reproducing the
  value. Writing `TWS_PASSWORD=<the real value>` into a finding turns the report
  itself into the leak — and reports get committed. This is not hypothetical:
  the 2026-07-18 report did it, the value went into a PUBLIC repo, the secret
  scan flagged it, it was allowlisted as "already public, not a new exposure",
  and a later rebase renumbered that commit, orphaned the allowlist, failed the
  scan and silently skipped the deploy for hours. One quoted string, five hours
  of no deploys and a permanent history entry.

  This is enforced, not just requested: `scripts/security/gen_security_report.py`
  redacts credential literals inside `esc()`, which every rendered field passes
  through, so a finding cannot carry a value into the HTML no matter how it is
  worded or which field it lands in. The variable name and prose survive so the
  finding stays actionable. Verified both directions — the raw findings JSON
  trips gitleaks, the rendered report does not.
  Tests: `scripts/tests/test_security_report_redaction.py`.

  Redaction is a backstop for the renderer, not a licence to put secrets in the
  JSON. If a value is genuinely needed to reproduce a finding, reference where
  it lives (`docker/ib-gateway/.env:4`) and leave it there.

## Audit log

| Date | Dimensions | Raw → Confirmed (exploitable) | FP | Report | Notes |
|---|---|---|---|---|---|
| 2026-06-28 | 10 + critic | 58 → 48 (8) | 12 | `radon-cloud:security-archive/docs/security-audit-2026-06-28.html` | 8 fixes shipped (WS relay bypass, share-content disclosure, ws bump, /docs gate, service-health scrub, path guards, CI hardening, HSTS). Deferred next/@clerk bumps + VPS config. `radon-cloud:security-archive/tasks/security-audit-2026-06-28.md`. |
| 2026-06-29 | 11 + critic | 58 → 26 (**0**) | 34 | `radon-cloud:security-archive/docs/security-audit-2026-06-29.html` | Re-audit after the lower-priority block landed (CSP enforce, rate-limit, Actions SHA-pin, deps pin, media HSTS). **Zero exploitable** (24 info, 2 low). Critic confirmed all prior fixes intact. Closed the 2 lows + stale repo HSTS: gitleaks SHA256 check, admin routes fail-closed, `docker/caddy/Caddyfile` media HSTS. |
| 2026-07-18 | 12 (+`cloud-archive`) + critic | 38 → 31 (**1**) | 8 | `radon-cloud:security-archive/docs/security-audit-2026-07-18.html` (rendered locally; the repo is PUBLIC so the report is gitignored — file it in the private archive) | Post-B2-archive + share-route-proliferation audit. Playbook extended: new `cloud-archive` dimension, PUBLIC-repo framing, 4 new regression invariants (share allowlist FS pin, read-admin gating, B2 delete-safety, public-repo hygiene). The **1 exploitable/high is non-code**: real portfolio figures still resolvable in PUBLIC git history (HEAD-only scrub `772c6493`, no `filter-repo`). **7 fixes shipped** (preset traversal, rate-limit rightmost-XFF, `/admin/services` operator gate, secret-scrub broaden ×2 chokepoints, share-iframe sandbox, IBKR username redact at HEAD, prune docstring footgun). **Deferred to operator** (require VPS/history/rotation): git-history purge + **credential rotation** (VNC/IB/IBKR-username — still in history & closed PR #7 blob), `wsTrust`/relay 0.0.0.0 hardening, `menthorq/cta` Next.js `spawn`, CSP `https:` wildcard, `requirements-api.txt` ceilings, lockfile dedupe. |
