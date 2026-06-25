# demo.radon.run — Demo Environment Plan

A public, Clerk-gated demo of Radon that real users can self-serve try for **max 3 trading days**, fully isolated from the real IB Gateway / brokerage account / production Turso. Scoped 2026-06-25. **Planning doc — build is phased; nothing is live until the infra below is provisioned.**

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Clerk | **Same instance**, demo users gated by `publicMetadata.demoRole='trial'` |
| 2 | Host | **New Hetzner VM** (hard physical isolation from prod) |
| 3 | Demo data | **Separate Turso DB** (`radon-demo-*`) |
| 4 | Rate limiting | **Upstash** Redis + `@upstash/ratelimit` |
| 5 | Quote feed | **Realtime** (real entitled relay feed) |
| 6 | AI keys | **Dedicated low-budget demo keys** (hard provider spend caps) |
| 7 | Signup gating | **Email-verify** required before a trial starts |

## Why a separate deployment (not a demo-mode flag)

FastAPI short-circuits auth on `is_trusted_local_request` (`scripts/api/auth.py:110`), returning `{"sub":"localhost","local":True}` before reading the Bearer token — so the backend **cannot** tell a demo user from the operator on server-to-server calls. Any in-prod demo flag would depend on flawless Next.js-layer discipline against a backend wired to the real IB pool + real Turso. A separate VM makes the blast radius **structurally zero**.

## Architecture

- **New Hetzner VM** (own public IP) running its own docker-compose: `radon-nextjs-demo:3000`, `radon-api-demo:8321`, `ib-realtime-relay-demo:8765`, Caddy terminating TLS for `demo.radon.run`.
- **Cloudflare** (orange-cloud) in front: WAF + volumetric DDoS + IP rate-limiting before origin.
- FastAPI runs **`RADON_API_TEST_MODE=1`** → every IB path stubbed at source (`server.py:1760` returns synthetic permIds without calling `ib_place_order.py`). **No IB Gateway container, no Tailscale route to `ib-gateway:4001`** — IB is physically unreachable.
- **Realtime quote feed (decision 5):** the demo relay connects to the entitled market-data feed for realism. It is READ-ONLY market data; it can never trade. (Risk: shares IB data-farm entitlement capacity — monitor; fall back to a delayed feed if it pressures prod.)
- Same git repo deploys both; a **CI isolation guard** rejects any demo deploy whose env carries a prod `TURSO_DB_URL` or a reachable IB host.

## Isolation model (three independent guarantees)

1. **No IB** — `RADON_API_TEST_MODE=1` + no IB container/route on the VM. `ib_place_order.py` is never invoked; there is no IB endpoint to reach even on a code bug.
2. **Separate Turso** — reads go Next.js→Turso directly (`web/app/api/portfolio/route.ts`), and `portfolio_snapshots`/`journal`/`orders` are **global tables with no `user_id`**. A same-DB `is_demo` filter is one forgotten WHERE-clause from leaking the operator's real positions — so demo gets its own DB, where the operator's data is physically absent. Demo writes (simulated orders) go to the existing `paper_fills` table.
3. **Separate Clerk context** — demo's `ALLOWED_USER_IDS` never contains the operator's prod user id, so a stolen demo session can't impersonate the operator.

**Synthetic demo dataset:** `scripts/db/demo_seed.py` seeds the demo Turso once with a fabricated-but-consistent `portfolio_snapshots` row (~500K net liq, 3-4 synthetic SPY/QQQ/TSLA positions), matching journal + open-orders rows, modeled on `marketing-mockups/portfolio-recreation.html`. Demo users' simulated orders land in `paper_fills` (`account='PAPER'`), never mutating the seed.

## Guardrails (enforcement points)

- **No real orders** — (backend) VM `RADON_API_TEST_MODE=1`; (UX) `web/app/api/orders/place/route.ts` detects `demoRole` via `auth()` and routes to `/paper/place` → `paper_fills`. Both present; neither load-bearing alone.
- **AI quotas** — new `demo_ai_usage` table (PK user_id+endpoint+day_et); guard at the top of the 3 Next.js LLM routes (`assistant` 5/day, `ticker/seasonality` 10/day, `ticker/info` 20/day) where Clerk identity exists; 429 on exceed; reset 00:00 ET. Backstop: **demo-scoped keys with hard provider spend caps**.
- **Rate-limit / DOS** — Cloudflare edge (IP limits, Bot Fight, OWASP WAF) + a **greenfield** app-layer tiered sliding-window limiter via `@upstash/ratelimit` keyed by Clerk userId (Tier A reads ~100/hr, B expensive ~10/hr, C mutations 5/day, D AI 5/day). No rate-limiting exists today.
- **Write spam** — per-trial caps in demo DB (journal ≤1000 + 10KB/note + 1/5s; alerts/watchlist bounded).

## Trial: 3 trading days

Counted via the existing market calendar (`scripts/utils/market_calendar.py:get_last_n_trading_days` — weekends + US holidays + IBKR closures excluded), expiring at **16:00 ET of the 3rd trading day** from signup. Marked at signup on the Clerk `user.created` webhook → `publicMetadata.{demoRole,demoTrialStartedAt,demoTrialExpiresAt}` (expiry computed via a small FastAPI helper since the calendar logic is Python-only) + mirrored to a demo Turso `demo_users` row. Enforced in `web/middleware.ts` (block expired) + a scheduled sweep. **Email-verify (decision 7)** required before the trial clock starts (deters bot signups burning fresh quotas).

## Operator runbook

- **Self-serve signup:** `demo.radon.run` landing → "Start 3-day demo" → standard Clerk `/sign-up` (email-verify) → `user.created` webhook sets metadata + inserts `demo_users`. No operator action to start a trial.
- **Management:** new **`/admin/demo-users`** tab in the existing operator panel (gated by `ALLOWED_USER_IDS` like other `/api/admin/*`): LIST (email, started, expiry countdown, status, today's AI burn), INSPECT (paper fills + per-endpoint quota), REVOKE / EXTEND (write Clerk metadata + `demo_users`).

## Build phases

**Operator-provisioned infra (you; I provide configs/checklists):**
- **VM** — new Hetzner VM + docker-compose (demo stack, no IB container/route), Caddy for `demo.radon.run`.
- **DNS + Cloudflare** — `demo.radon.run` A record; Cloudflare zone with WAF + IP rate-limit + Bot Fight.
- **Turso** — `radon-demo-*` DB + read-mostly auth token.
- **Upstash** — Redis instance + token.
- **AI keys** — demo-scoped Anthropic/Cerebras/Exa keys with hard caps.
- **Clerk** — enable email-verify; configure the `user.created` webhook endpoint; confirm same-instance `demoRole` metadata.

**Code (I build, in-repo, testable ahead of infra):**
0. Isolation skeleton + the **CI isolation guard** (reject demo deploy with prod Turso / reachable IB).
1. Demo DB migrations (`demo_users`, `demo_ai_usage`) + `demo_seed.py` (synthetic dataset).
2. Clerk `user.created` webhook route + the trial-expiry helper + middleware expiry gate.
3. Order blockade (demo `orders/place` → `paper/place`) at the OrderRiskGate placement resolver.
4. AI quota guard module + wire into the 3 LLM routes.
5. App-layer Upstash tiered rate-limiter + write-spam caps.
6. `/admin/demo-users` panel + the list/inspect/revoke/extend admin API.

## Risks + mitigations

- **Single-codebase drift** → CI isolation guard + demo-targeted env checks.
- **Edge-runtime middleware trap** → keep the trial-expiry gate free of `node:*` imports (Edge runtime; a prior prod bug — `feedback_middleware_edge_runtime`).
- **FastAPI per-user blindness** → never rely on FastAPI to gate per-user; VM `TEST_MODE` + Next.js `auth()` are the two guarantees.
- **Clerk webhook failure** → a user could exist without `demoRole`/expiry → unlimited access. Add a reconciliation sweep + default-deny (no `demoRole` = no demo access).
- **Realtime feed entitlement pressure** (decision 5) → monitor IB data-farm capacity; fall back to delayed feed if it pressures prod.
- **Provider key exhaustion** → demo-scoped keys with hard caps are the real backstop behind the per-user quota.
- **Cost/ops** → second VM + Turso + Upstash + Cloudflare zone. No separate IB paper account needed (TEST_MODE).
