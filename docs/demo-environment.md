# demo.radon.run — Demo Environment Plan

A public, Clerk-gated demo of Radon that real users can self-serve try for **max 3 trading days**, fully isolated from the real IB Gateway / brokerage account / production Turso. Scoped 2026-06-25. **Planning doc — build is phased; nothing is live until the infra below is provisioned.**

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Clerk | **Same instance**, demo users gated by `publicMetadata.demoRole='trial'`. MFA is scoped to the operator account (Clerk policy "optional" + operator TOTP enrolled), NOT required instance-wide, so demo signups stay frictionless (no second-factor enrollment). |
| 2 | Host | **New Hetzner VM** (hard physical isolation from prod) |
| 3 | Demo data | **Separate Turso DB** (`radon-demo-*`) |
| 4 | Rate limiting | **Upstash** Redis + `@upstash/ratelimit` |
| 5 | Quote feed | **Realtime** (real entitled relay feed) |
| 6 | AI keys | **Reuse the existing prod Anthropic/Cerebras/Exa keys** (revised 2026-06-27 — per-user `demo_ai_usage` quota + Upstash tier-D are the only spend guard; no separate provider cap) |
| 7 | Signup gating | **Email-verify** required before a trial starts |

## Why a separate deployment (not a demo-mode flag)

FastAPI short-circuits auth on `is_trusted_local_request` (`scripts/api/auth.py:110`), returning `{"sub":"localhost","local":True}` before reading the Bearer token — so the backend **cannot** tell a demo user from the operator on server-to-server calls. Any in-prod demo flag would depend on flawless Next.js-layer discipline against a backend wired to the real IB pool + real Turso. A separate VM makes the blast radius **structurally zero**.

## Architecture

- **New Hetzner VM** (own public IP) running its own docker-compose: `radon-nextjs-demo:3000`, `radon-api-demo:8321`, `ib-realtime-relay-demo:8765`, Caddy terminating TLS for `demo.radon.run`.
- **Cloudflare** (orange-cloud) in front: WAF + volumetric DDoS + IP rate-limiting before origin.
- FastAPI runs **`RADON_API_TEST_MODE=1`** → every IB path stubbed at source (`server.py:1760` returns synthetic permIds without calling `ib_place_order.py`). **No IB Gateway container, no Tailscale route to `ib-gateway:4001`** — IB is physically unreachable.
- **Realtime quote feed (decision 5):** the demo relay connects to the entitled market-data feed for realism. It is READ-ONLY market data; it can never trade. (Risk: shares IB data-farm entitlement capacity — monitor; fall back to a delayed feed if it pressures prod.)
- Same git repo deploys both; a **CI isolation guard** (`scripts/ci/check_demo_isolation.py`, run by the `py-coverage` job in `.github/workflows/ci.yml` — skipped with a workflow warning annotation until the `TURSO_DEMO_DB_URL` / `TURSO_DEMO_APP_DB_URL` secrets are provisioned, TEST_AUDIT T-130) rejects any demo deploy whose env carries a prod `TURSO_DB_URL` or a reachable IB host. It checks BOTH `TURSO_DEMO_DB_URL` and `TURSO_DB_URL` against the prod marker: every account route reads through `dbExecute` → `getDb()` → `TURSO_DB_URL`, so that variable is the isolation boundary (`getDemoDb()` serves only `/api/admin/demo-users`). The two being equal on the demo VM is the desired state. Until 2026-08-23 nothing invoked the guard and it never inspected `TURSO_DB_URL` at all (RELIABILITY_AUDIT R-156).

## Isolation model (three independent guarantees)

1. **No IB** — `RADON_API_TEST_MODE=1` + no IB container/route on the VM. `ib_place_order.py` is never invoked; there is no IB endpoint to reach even on a code bug.
2. **Separate Turso** — reads go Next.js→Turso directly (`web/app/api/portfolio/route.ts`), and `portfolio_snapshots`/`journal`/`orders` are **global tables with no `user_id`**. A same-DB `is_demo` filter is one forgotten WHERE-clause from leaking the operator's real positions — so demo gets its own DB, where the operator's data is physically absent. Demo writes (simulated orders) go to the existing `paper_fills` table.
3. **Separate Clerk context** — demo's `ALLOWED_USER_IDS` never contains the operator's prod user id, so a stolen demo session can't impersonate the operator.

**Synthetic demo dataset:** `scripts/db/demo_seed.py` seeds the demo Turso once with a fabricated-but-consistent `portfolio_snapshots` row (~500K net liq, 3-4 synthetic SPY/QQQ/TSLA positions), matching journal + open-orders rows, modeled on `marketing-mockups/portfolio-recreation.html`. Demo users' simulated orders land in `paper_fills` (`account='PAPER'`), never mutating the seed.

## Guardrails (enforcement points)

- **No real orders** — (backend) VM `RADON_API_TEST_MODE=1`; (UX) `web/app/api/orders/place/route.ts` detects `demoRole` via `auth()` and routes to `/paper/place` → `paper_fills`. Both present; neither load-bearing alone.
- **AI quotas** — new `demo_ai_usage` table (PK user_id+endpoint+day_et); guard at the top of the 3 Next.js LLM routes (`assistant` 5/day, `ticker/seasonality` 10/day, `ticker/info` 20/day) where Clerk identity exists; 429 on exceed; reset 00:00 ET. Backstop (revised 2026-06-27): demo **reuses the prod AI keys**, so the per-user quota + the Upstash tier-D limiter (5/day) are the only AI-spend guard — no separate provider cap.
- **Rate-limit / DOS** — Cloudflare edge (IP limits, Bot Fight, OWASP WAF) + a **greenfield** app-layer tiered sliding-window limiter via `@upstash/ratelimit` keyed by Clerk userId (Tier A reads ~100/hr, B expensive ~10/hr, C mutations 5/day, D AI 5/day). No rate-limiting exists today.
- **Write spam** — per-trial caps in demo DB (journal ≤1000 + 10KB/note + 1/5s; alerts/watchlist bounded).

## Trial: 3 trading days

Counted via the existing market calendar (`scripts/utils/market_calendar.py:get_last_n_trading_days` — weekends + US holidays + IBKR closures excluded), expiring at **16:00 ET of the 3rd trading day** from signup. Marked at signup on the Clerk `user.created` webhook → `publicMetadata.{demoRole,demoTrialStartedAt,demoTrialExpiresAt}` (expiry computed via a small FastAPI helper since the calendar logic is Python-only) + mirrored to a demo Turso `demo_users` row. Enforced in `web/middleware.ts` (block expired) + a scheduled sweep. **Email-verify (decision 7)** required before the trial clock starts (deters bot signups burning fresh quotas).

## Operator runbook

- **Self-serve signup:** `demo.radon.run` landing → "Start 3-day demo" → standard Clerk `/sign-up` (email-verify) → `user.created` webhook sets metadata + inserts `demo_users`. No operator action to start a trial.
- **Management:** new **`/admin/demo-users`** tab in the existing operator panel (gated by `ALLOWED_USER_IDS` like other `/api/admin/*`): LIST (email, started, expiry countdown, status, today's AI burn), INSPECT (paper fills + per-endpoint quota), REVOKE / EXTEND (write Clerk metadata + `demo_users`).

## Environment variables (the demo's runtime dependency)

The demo reads the vars below. They live in **two places**: `web/.env` (local dev) and the **`radon-demo` Vercel project** env (encrypted, all targets). **Prod must NOT carry `TURSO_DEMO_*` / `UPSTASH_*`** — their absence is what guarantees prod can never reach the demo DB (`web/lib/db.ts:getDemoDb()` throws when unset). Read paths: `getDemoDb()` (Turso), `web/lib/demo/demoGate.ts:demoRateLimit` (Upstash), the 3 LLM routes (AI keys).

| Var(s) | Source | Status |
|---|---|---|
| `TURSO_DEMO_DB_URL` · `TURSO_DEMO_AUTH_TOKEN` | demo Turso DB `radon-demo` (aws-us-west-2, isolated from `radon-joemccann`) | ✅ provisioned + staged |
| `UPSTASH_REDIS_REST_URL` · `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis `radon-demo-ratelimit` (us-east-1, co-located with Vercel `iad1`) | ✅ provisioned + staged |
| `ANTHROPIC_API_KEY` · `CEREBRAS_API_KEY` · `EXA_API_KEY` | **reused from prod `web/.env`** (decision 6 revised) | ✅ staged to Vercel |
| `CLERK_WEBHOOK_SECRET` | Clerk `user.created` webhook signing secret (`whsec_…`) | ⏳ pending (Clerk step) |
| `RADON_API_URL` | demo VM FastAPI over TLS (e.g. `demo-api.radon.run`) | ⏳ pending (VM) |
| Clerk publishable / secret keys | same instance (decision 1) | ⏳ to stage |

**Topology revised from the original plan (2026-06-27):** the **frontend runs on Vercel** (`demo.radon.run`, project `radon-demo`, root `web/`) behind **Vercel WAF + rate-limiting** (100 req/min/IP challenge, 1000 req/min/IP deny) — replacing the Cloudflare layer described above. Only **FastAPI + the relay** run on the Hetzner VM; the Vercel frontend calls the VM backend over the public internet via `RADON_API_URL`, so the backend authenticates the Clerk JWT (no localhost-trust bypass). The managed OWASP WAF ruleset is Enterprise-only and not enabled on Pro.

**Ignored Build Step:** `web/vercel.json` → `scripts/vercel-ignore-build.mjs` skips the `radon-demo` build unless `web/` or `lib/tools` changed (the `@tools` webpack alias). Docs/tasks/site-only pushes cancel with "Canceled by Ignored Build Step", matching the marketing `site/` project. Defaults to continuing the build if the diff cannot be determined.

## Build phases

**Operator-provisioned infra (you; I provide configs/checklists):**
- **VM** — new Hetzner VM + docker-compose (demo stack, no IB container/route), Caddy for `demo.radon.run`.
- **DNS + Cloudflare** — `demo.radon.run` A record; Cloudflare zone with WAF + IP rate-limit + Bot Fight.
- **Turso** — `radon-demo-*` DB + read-mostly auth token.
- **Upstash** — Redis instance + token.
- **AI keys** — demo-scoped Anthropic/Cerebras/Exa keys with hard caps.
- **Clerk** — enable email-verify; configure the `user.created` webhook endpoint; confirm same-instance `demoRole` metadata. MFA is scoped to the operator account (Clerk policy "optional" + operator has TOTP enrolled), NOT required instance-wide: Clerk challenges only users who have an enrolled factor, so the operator is MFA-gated while demo signups stay frictionless. Keep the Clerk MFA policy on "optional"; flipping it to "required for all users" would force every demo signup through second-factor enrollment.

- **Vercel→VM auth** — the demo frontend (Vercel) reaches the demo VM FastAPI over the public proxy, where the loopback/tailnet trust bypass does not apply. It authenticates as a trusted service with the shared `RADON_SERVICE_TOKEN` (header `X-Radon-Service-Token`, set in `radonFetch`, verified by `is_trusted_service_request` in `scripts/api/auth.py`). The token is set on the Vercel `radon-demo` project and in the VM `.env`; it is unset on prod, so prod stays loopback/JWT-gated. Per-user identity/gating (trial expiry, quotas) stays at the Next.js middleware layer; the VM trusts the frontend.

**Code (I build, in-repo, testable ahead of infra):**
0. ✅ Isolation skeleton + the **CI isolation guard** (reject demo deploy with prod Turso / reachable IB).
1. ✅ Demo DB migrations (`demo_users`, `demo_ai_usage`) + `demo_seed.py` (synthetic dataset).
2. ✅ Clerk `user.created` webhook route + the trial-expiry helper + middleware expiry gate.
3. ✅ Order blockade (demo `orders/place` → `paper/place`).
4. ✅ AI quota guard module + wire into the 3 LLM routes.
5. ✅ App-layer Upstash tiered rate-limiter (middleware gate). Per-table write-spam caps deferred.
6. ✅ `/admin/demo-users` panel + the list/revoke/extend admin API.

### Build status (Phases 2–6 shipped — code only, infra-gated)

All inert on the prod app: every demo path no-ops for non-demo users
(`resolveDemoContext → null`) and the demo DB is constructed only after a demo
identity is confirmed, so prod (no `TURSO_DEMO_DB_URL`) never touches it.

- **Phase 2** — `POST /demo/trial-expiry` (FastAPI, auth-exempt); `getDemoDb()`;
  `lib/demo/{demoUsers,svixVerify,trialExpiry,provisionTrial}.ts`;
  `app/api/webhooks/clerk/route.ts` (svix-verified, allowlisted + filesystem-pinned);
  middleware expiry gate (`lib/demo/demoGate.ts`).
- **Phase 3** — `lib/demo/orderBlockade.ts` + `orders/place` redirect to `/paper/place`.
- **Phase 4** — `lib/demo/enforceAiQuota.ts` wired into `assistant`, `ticker/seasonality`, `ticker/info` (charged on cache-miss only).
- **Phase 5** — `lib/demo/rateTier.ts` + middleware Upstash tiered limiter (`/api/*` only; no-ops without Upstash env).
- **Phase 6** — `lib/demo/{adminAuth,adminActions,demoUsersView}.ts`, `app/api/admin/demo-users/route.ts` (404s cleanly when demo unconfigured), `components/admin/DemoUsersTable.tsx`.

Tests: 55 new Vitest (10 files) + 2 extended perimeter pins + 4 new pytest, all green; `tsc --noEmit` clean.

**Env this introduces** (set on the demo deployment only): `TURSO_DEMO_DB_URL`,
`TURSO_DEMO_AUTH_TOKEN`, `CLERK_WEBHOOK_SECRET`, `UPSTASH_REDIS_REST_URL`,
`UPSTASH_REDIS_REST_TOKEN`, `ALLOWED_USER_IDS`. The demo sign-up page must set
`unsafeMetadata.demo = true` so the webhook provisions a trial.

## Risks + mitigations

- **Single-codebase drift** → CI isolation guard + demo-targeted env checks.
- **Edge-runtime middleware trap** → keep the trial-expiry gate free of `node:*` imports (Edge runtime; a prior prod bug — `feedback_middleware_edge_runtime`).
- **FastAPI per-user blindness** → never rely on FastAPI to gate per-user; VM `TEST_MODE` + Next.js `auth()` are the two guarantees.
- **Clerk webhook failure** → a user could exist without `demoRole`/expiry → unlimited access. Add a reconciliation sweep + default-deny (no `demoRole` = no demo access).
- **Realtime feed entitlement pressure** (decision 5) → monitor IB data-farm capacity; fall back to delayed feed if it pressures prod.
- **Prod AI-budget burn** (revised 2026-06-27) → demo reuses the prod Anthropic/Cerebras/Exa keys, so a demo quota bug spends the *prod* AI budget. The only guard is the `demo_ai_usage` per-user quota + Upstash tier-D (5/day); add a provider spend cap if abuse appears.
- **Cost/ops** → second VM + Turso + Upstash + Cloudflare zone. No separate IB paper account needed (TEST_MODE).

---

## Outage 2026-08-13 → 2026-09-03: no new user could get a trial

**Symptom.** Signups completed at Clerk and then every page and `/api/*` call
returned `403 "Demo access is not active."` with no explanation. The last
provisioned trial was `2026-08-13T17:30:04Z`; ~60 accounts created after that
carry empty `publicMetadata`.

**Cause.** Commit `4eaaf5e9` shipped two changes together:

1. A webhook replay ledger — `claimDemoWebhookEvent` INSERTs into
   `demo_webhook_events` *before* `provisionDemoTrial` runs
   (`web/app/api/webhooks/clerk/route.ts`). The table was never created in the
   demo Turso, so every `user.created` delivery threw and provisioned nothing.
2. A default-deny in `web/lib/demo/demoGate.ts` — any signed-in user with no
   `demoRole` is refused on the demo deployment. Correct on its own; combined
   with (1) it turned a silent provisioning failure into a total wall.

**Why the migration never ran.** The demo Turso is a full prod-shaped clone and
shared ONE `schema_migrations` table with the main series (versions 1..69).
`demo_migrations/0003` declared version 3, which the main series claimed on
2026-06-29, so `apply_demo_migrations` read it as already applied and skipped it
on every run. Every future demo migration numbered ≤ 69 was pre-swallowed the
same way. This is a different failure from 2026-07-03 ("nobody ran the tool");
here the tool ran and reported success.

**Fixes.**

| Change | Where |
|---|---|
| Demo series gets its own NAME-keyed ledger `demo_schema_migrations`; the shared table is never read or written | `scripts/db/demo_seed.py`, `scripts/db/demo_migrations/*.sql` |
| `assert_not_prod` also requires the positive `radon-demo` marker | `scripts/db/demo_seed.py` |
| A missing idempotency STORE degrades to at-least-once provisioning; every other claim failure still throws | `web/lib/demo/webhookLedger.ts` |
| Provisioning failures page the operator (counts and reason only, never a user id or email) | `web/lib/notify/pushover.ts` |
| Unprovisioned page requests redirect to a public `/demo-pending` leaf that bounds its own token-refresh retry; `/api/*` keeps the hard 403 | `web/app/demo-pending/`, `web/lib/demo/demoGate.ts`, `web/middleware.ts` |
| A dead Upstash denies instead of throwing out of middleware as an opaque 500 | `web/lib/demo/rateLimit.ts` |
| Seed dates anchor to the run date (`RADON_DEMO_SEED_TODAY` overrides) so the demo book never shows expired options | `scripts/db/demo_seed.py` |
| Marketing CTA deep-links to `/sign-up`; the bare demo origin is a gated route that 404s a signed-out visitor | `site/lib/editorial-content.ts` |

**Stranded users are NOT backfilled.** `clerk.radon.run` is the shared prod
instance and "created after 2026-08-13 without `demoRole`" is not the set of
demo signups — OAuth signups never carry the `unsafe_metadata.demo` marker, and
`updateUserMetadata` replaces rather than merges. Stamping `demoRole` on a real
prod account puts it behind demo expiry and paper-order routing; stamping the
operator locks them out of `app.radon.run`. Affected users re-sign-up.

**Set `PUSHOVER_TOKEN` / `PUSHOVER_USER` on the `radon-demo` Vercel project** to
arm the provisioning alert; it no-ops silently while unset.
