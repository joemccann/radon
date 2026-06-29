# Security Audit — 2026-06-29 (re-audit + remediation)

Full report: `docs/security-audit-2026-06-29.html` · Playbook: `docs/security-audit-playbook.md`
Prior round: `tasks/security-audit-2026-06-28.md`

## Method
Re-ran the reusable engine (`.claude/workflows/security-audit.mjs`) — 11 dimensions
(adds a `regression` dimension to the original 10), each finding adversarially
verified (refute-first) + completeness/regression critic. 73 agents.
**58 raw → 26 confirmed → 0 exploitable** (24 info, 2 low), 34 false positives.
The regression critic confirmed every prior fix intact (Clerk webhook fail-closed,
LLM tool loop never auto-trades, CSRF / `/pi` allowlist / SQLi all clean).

## Lower-priority block (carried over from 06-28) — DONE + deployed
- [x] **CSP Report-Only → ENFORCED** — per-request nonce in middleware; dropped
      `unsafe-inline`/`unsafe-eval`; Clerk by host allowlist + `worker-src 'self' blob:`
      (NOT strict-dynamic — it blocks Clerk's unnonced loader → auth white-screen).
      Browser-verified on a prod build (0 violations). `feedback_csp_strict_dynamic_breaks_clerk_loader`.
- [x] **Rate-limit** the 6 public unauth routes (`web/lib/rateLimit.ts`).
- [x] **SHA-pin** third-party GitHub Actions (checkout/setup-bun/setup-python).
- [x] **Pin `requirements.txt`** to the VPS's running versions (deploy install =
      no-op; avoided a cryptography 46→48 major + scipy downgrade).
      `feedback_pin_requirements_to_vps_not_laptop`.
- [x] **HSTS on `media.radon.run`** (live Caddy + in-repo reference).

## Re-audit findings closed (the 2 lows + a stale repo file)
- [x] **Admin ops routes fail CLOSED** (RA-1, low) — `/api/admin/{stack/restart,
      ib/restart,ib/reset-backoff,services/[unit]/[action]}` now call `requireDemoAdmin()`
      and 403 when `ALLOWED_USER_IDS` is empty (middleware `isAuthorizedUser` fails
      OPEN by design for dev/demo; these must not inherit it). Regression test added.
- [x] **Gitleaks CI binary SHA256-verified** (RA, low) — supply-chain on the
      secret-scan step that guards every deploy.
- [x] **CORS explicit allowlist** (RA-2, low — operator-requested) — replaced the
      `https://.*\.radon\.run` wildcard regex with `allow_origins=[...]`
      (`RADON_CORS_EXTRA_ORIGINS` extends). Closes the subdomain-takeover bypass.
      Live-verified: `app.radon.run` gets ACAO, `evil.radon.run` does not.
- [x] **Order idempotency** (RA-3, info — operator-requested) — `/api/orders/place`
      dedups concurrent/just-repeated identical placements (in-flight + short-TTL
      content hash, or explicit client `idempotencyKey`) so a double-click can't
      double a real-money position. Lives in the Next route (single chokepoint, all
      UI order surfaces flow through it) — no per-component changes, money-path
      Python untouched. Deduped responses carry `deduplicated:true`.

## Accepted as info — not fixed (rationale)
- [ ] **CSP `connect-src`/`img-src` breadth** (`'self' wss: https:` / any HTTPS).
      Defense-in-depth only — no XSS surface exists, `script-src` (the injection
      gate) is strict. Tightening risks self-inflicted outages from an incomplete
      allowlist (Turso / UW / Clerk / WSS). Low-priority hardening if ever wanted.
- [ ] **`*.radon.run` CORS regex** → **DONE above (RA-2).**
- [ ] **No order idempotency key** → **DONE above (RA-3).**

## Commits
- `9133edf7` — CSP enforce, rate-limit, Actions SHA-pin, deps pin
- `d78bc050` — admin fail-closed, gitleaks checksum, media HSTS
- `915986aa` — CORS allowlist, order idempotency
(media HSTS live Caddy: radon-cloud `ac106e6`)

## Verification
All CI runs green incl. **Deploy to VPS**. New/changed tests: csp-nonce (8),
rate-limit (11), admin fail-closed (4), CORS (3), idempotency (13); 138 existing
order-place tests + 71-route authz matrix still pass. CSP + CORS live-verified on
app.radon.run.
