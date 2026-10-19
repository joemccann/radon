# demo.radon.run — Demo Environment Plan

A public, Clerk-gated demo of Radon that real users can self-serve try for **max 3 trading days**, fully isolated from the real IB Gateway / brokerage account / production Turso. Scoped 2026-06-25. The frontend and backend are deployed; the historical build checklist below is not a live inventory.

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Clerk | **Same instance**, demo users gated by `publicMetadata.demoRole='trial'`. MFA is scoped to the operator account (Clerk policy "optional" + operator TOTP enrolled), NOT required instance-wide, so demo signups stay frictionless (no second-factor enrollment). |
| 2 | Host | **New Hetzner VM** (hard physical isolation from prod) |
| 3 | Demo data | **Separate Turso DB** (`radon-demo-*`) |
| 4 | Rate limiting | **Upstash** Redis + `@upstash/ratelimit` |
| 5 | Quote feed | **Request-time sample snapshots** (no IB ticket or WebSocket) |
| 6 | AI keys | **Reuse the existing prod Anthropic/Cerebras/Exa keys** (revised 2026-06-27 — per-user `demo_ai_usage` quota + Upstash tier-D are the only spend guard; no separate provider cap) |
| 7 | Signup gating | **Email-verify** required before a trial starts |

## Why a separate deployment (not a demo-mode flag)

FastAPI short-circuits auth on `is_trusted_local_request` (`scripts/api/auth.py:110`), returning `{"sub":"localhost","local":True}` before reading the Bearer token — so the backend **cannot** tell a demo user from the operator on server-to-server calls. Any in-prod demo flag would depend on flawless Next.js-layer discipline against a backend wired to the real IB pool + real Turso. A separate VM makes the blast radius **structurally zero**.

## Architecture

- **Separate Hetzner VM** (own public IP) running FastAPI for bounded demo-only operations. The Vercel frontend does not require an IB relay for workstation reads.
- **Vercel frontend** at `demo.radon.run`, with its edge protection and Clerk trial gate. It calls the separate Caddy backend at `demo-api.radon.run` over public HTTPS; Cloudflare was the original plan, not the deployed frontend edge.
- FastAPI runs **`RADON_API_TEST_MODE=1`** → every IB path stubbed at source (`server.py:1760` returns synthetic permIds without calling `ib_place_order.py`). **No IB Gateway container, no Tailscale route to `ib-gateway:4001`** — IB is physically unreachable.
- **Sample quote feed (decision 5):** the client builds deterministic minute-bucketed quotes, depth, tape, fundamentals, and option marks for requested instruments. Demo pages never request an IB ticket or open a market-data WebSocket.
- Same git repo deploys both; a **CI isolation guard** (`scripts/ci/check_demo_isolation.py`, run by the `py-coverage` job in `.github/workflows/ci.yml` on pushes with the repository `TURSO_DEMO_DB_URL` / `TURSO_DEMO_APP_DB_URL` secrets provisioned on 2026-09-28) rejects any demo deploy whose env carries a prod `TURSO_DB_URL` or a reachable IB host. It checks BOTH `TURSO_DEMO_DB_URL` and `TURSO_DB_URL` against the prod marker: every account route reads through `dbExecute` → `getDb()` → `TURSO_DB_URL`, so that variable is the isolation boundary (`getDemoDb()` serves only `/api/admin/demo-users`). The two being equal on the demo VM is the desired state. Until 2026-08-23 nothing invoked the guard and it never inspected `TURSO_DB_URL` at all (RELIABILITY_AUDIT R-156). The CI guard is deploy-time only; the serving path also fails closed at runtime: `web/lib/demo/demoDbIsolation.ts` checks `TURSO_DB_URL` against the same prod marker per request, demo-principal DB reads return 403 `DEMO_DB_ISOLATION` (`web/lib/apiContracts.ts`) and routes fall back to fixtures (test: `web/tests/demo-db-isolation.test.ts`) — a mis-provisioned prod `TURSO_DB_URL` never serves live rows if the CI guard is unavailable.

## Isolation model (three independent guarantees)

1. **No IB** — `RADON_API_TEST_MODE=1` + no IB container/route on the VM. `ib_place_order.py` is never invoked; there is no IB endpoint to reach even on a code bug.
2. **Separate Turso** — reads go Next.js→Turso directly (`web/app/api/portfolio/route.ts`), and `portfolio_snapshots`/`journal`/`orders` are **global tables with no `user_id`**. A same-DB `is_demo` filter is one forgotten WHERE-clause from leaking the operator's real positions — so demo gets its own DB, where the operator's data is physically absent. Demo writes (simulated orders) go to the existing `paper_fills` table.
3. **Separate Clerk context** — demo's `ALLOWED_USER_IDS` never contains the operator's prod user id, so a stolen demo session can't impersonate the operator.

**Synthetic demo dataset:** `scripts/db/demo_seed.py` seeds the demo Turso once with a fabricated-but-consistent `portfolio_snapshots` row (~500K net liq, 3-4 synthetic SPY/QQQ/TSLA positions), matching journal + open-orders rows, modeled on `marketing-mockups/portfolio-recreation.html`. Demo users' simulated orders land in `paper_fills` (`account='PAPER'`), never mutating the seed.

**Newsfeed schema rollout:** the Vercel frontend and demo Turso have an independent migration lifecycle from production. `/api/newsfeed/posts` joins `research_post_sources` when migration `0071` exists. If that specific table is absent, it reads the newest 500 legacy posts and excludes every `research-*` ID before the limit. Network/auth failures and other SQL errors retain the existing error or last-good-cache behavior. No request performs schema writes or copies private research into the demo database.

**Workstation sample contract:** live-service-dependent read surfaces are completed at request time after the Clerk principal resolves to `demo`. Performance, current executions, cash flows, theta, ticker flow, ticker company profile and seasonality, option calendars/chains/exposure, CRI, VCG, GRG, GEX, dispersion, TRIN, BPI, NYSE breadth, AI infrastructure, and catalysts therefore remain current without disk writers, Turso snapshots, FastAPI producers, or vendor credentials. Fixtures accept an injected clock, carry explicit sample provenance, and are covered at the route boundary. Seeded open orders remain intact when current-session executions are added.

**Mirrored snapshot window:** regime surfaces that still read mirrored `scan_snapshots` rows (VIX term structure, IV rank, skew, vol cone and the other freshness-gated indicators) resolve their stale window through `web/lib/demo/snapshotWindow.ts`. A demo principal gets at least 96h, the worst weekend gap of the Mon..Fri `radon-demo-mirror.timer`; operator windows are unchanged. `web/tests/demo-snapshot-window.test.ts` fails when a new freshness-gated route skips the helper.

## Guardrails (enforcement points)

- **No real orders** — (backend) VM `RADON_API_TEST_MODE=1`; (UX) `web/app/api/orders/place/route.ts` detects `demoRole` via `auth()` and routes to `/paper/place` → `paper_fills`. Both present; neither load-bearing alone.
- **AI quotas** — `demo_ai_usage` table (PK user_id+endpoint+day_et). Per-endpoint daily caps are `DEMO_AI_DAILY_LIMITS` in `web/lib/demo/aiQuota.ts`, which includes `newsfeed/explain`. The shared Upstash tier D (5/day) is the backstop for every path in `AI_PATHS` (`web/lib/demo/rateTier.ts`), including `/api/newsfeed/share`, which is not in the per-endpoint table. Clerk identity is required. Exceeding a cap returns 429 and the counter resets at 00:00 ET. An unreadable quota store returns 503 `DEMO_QUOTA_UNAVAILABLE` and does not call the model. Demo reuses the prod AI keys, so this quota plus tier D are the only AI-spend guard. There is no separate provider cap.
- **Rate-limit / DOS** — Vercel edge controls plus an app-layer Upstash sliding-window limiter keyed by Clerk userId. Cached GETs use Tier A; only producer mutations use Tier B; writes, AI, WebSocket tickets, headlines, and shell polling retain their dedicated tiers. Every handler with a local limiter declares its durable tier explicitly. Demo sync hooks load one GET, suppress automatic producer POSTs/polling/retries, and make manual refresh another GET.
- **Write spam** — per-trial caps in demo DB (journal ≤1000 + 10KB/note + 1/5s; alerts/watchlist bounded).

## Trial: 3 trading days

Counted via the existing market calendar (`scripts/utils/market_calendar.py:get_last_n_trading_days` — weekends + US holidays + IBKR closures excluded), expiring at **16:00 ET of the 3rd trading day** from signup. Marked at signup on the Clerk `user.created` webhook → `publicMetadata.{demoRole,demoTrialStartedAt,demoTrialExpiresAt}` (expiry computed via a small FastAPI helper since the calendar logic is Python-only) + mirrored to a demo Turso `demo_users` row. Enforced in `web/middleware.ts` (block expired) + a scheduled sweep. **Email-verify (decision 7)** required before the trial clock starts (deters bot signups burning fresh quotas).

## Network access and isolation

The [network overview](spof-host-split.md#deployed-topology-and-access) includes
both demo layers. `fw-radon-demo` permits public HTTP/HTTPS and restricts SSH
to the configured recovery IPv4 `/32`; public IPv6 SSH is denied. FastAPI and
Caddy admin ports are container-only. The VM has no Tailscale interface, no
production private-network attachment and no IB Gateway. Vercel's frontend
protection does not replace this backend firewall.

Use the [network-trust owner](operations.md#network-trust-ops-plane-step-2)
for rollout and [recovery-IP changes](operations.md#recovery-ip-changes-and-roaming)
when roaming or the ISP changes the address. Do not enroll demo in the
production tailnet to avoid maintaining a recovery address.

## Operator runbook

- **Self-serve signup:** `demo.radon.run` landing → "Start 3-day demo" → standard Clerk `/sign-up` (email-verify) → `user.created` webhook sets metadata + inserts `demo_users`. No operator action to start a trial.
- **Management:** new **`/admin/demo-users`** tab in the existing operator panel (gated by `ALLOWED_USER_IDS` like other `/api/admin/*`): LIST (email, started, expiry countdown, status, today's AI burn), INSPECT (paper fills + per-endpoint quota), REVOKE / EXTEND (write Clerk metadata + `demo_users`).

## Environment variables (the demo's runtime dependency)

The staging notes in this table record the original rollout, not current
secret or deployment status. Inspect the operator-managed project settings
and backend environment before making a change.

The demo reads the vars below. They live in **two places**: `web/.env` (local dev) and the **`radon-demo` Vercel project** env (encrypted, all targets). **Prod must NOT carry `TURSO_DEMO_*` / `UPSTASH_*`** — their absence is what guarantees prod can never reach the demo DB (`web/lib/db.ts:getDemoDb()` throws when unset). Read paths: `getDemoDb()` (Turso), `web/lib/demo/demoGate.ts:demoRateLimit` (Upstash), and the AI routes named by `DEMO_AI_DAILY_LIMITS` and `AI_PATHS`.

| Var(s) | Source | Initial provisioning notes (historical) |
|---|---|---|
| `TURSO_DEMO_DB_URL` · `TURSO_DEMO_AUTH_TOKEN` | demo Turso DB `radon-demo` (aws-us-west-2, isolated from `radon-joemccann`) | ✅ provisioned + staged |
| `UPSTASH_REDIS_REST_URL` · `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis `radon-demo-ratelimit` (us-east-1, co-located with Vercel `iad1`) | ✅ provisioned + staged |
| `ANTHROPIC_API_KEY` · `CEREBRAS_API_KEY` · `EXA_API_KEY` | **reused from prod `web/.env`** (decision 6 revised) | ✅ staged to Vercel |
| `CLERK_WEBHOOK_SECRET` | Clerk `user.created` webhook signing secret (`whsec_…`) | ⏳ pending (Clerk step) |
| `RADON_API_URL` | demo VM FastAPI over TLS (e.g. `demo-api.radon.run`) | ⏳ pending (VM) |
| Clerk publishable / secret keys | same instance (decision 1) | ⏳ to stage |

**Topology revised from the original plan (2026-06-27):** the **frontend runs on Vercel** (`demo.radon.run`, project `radon-demo`, root `web/`) behind **Vercel WAF + rate-limiting** (100 req/min/IP challenge, 1000 req/min/IP deny) — replacing the Cloudflare layer described above. The Hetzner VM runs **Caddy + FastAPI**; the Vercel frontend calls the VM backend over the public internet via `RADON_API_URL`, so the backend requires the configured service token or valid JWT (no localhost-trust bypass); see the Vercel-to-VM authentication contract below. The managed OWASP WAF ruleset is Enterprise-only and not enabled on Pro.

**Ignored Build Step:** `web/vercel.json` → `scripts/vercel-ignore-build.mjs` skips the `radon-demo` build unless `web/` or `lib/tools` changed (the `@tools` webpack alias). Docs/tasks/site-only pushes cancel with "Canceled by Ignored Build Step", matching the marketing `site/` project. Defaults to continuing the build if the diff cannot be determined.

## Historical build phases (not a live infrastructure checklist)

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
- **Sample-feed realism** (decision 5) → keep sample provenance visible and validate quote/depth/option relationships in fixture tests; never imply broker entitlement or execution availability.
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

### VM backend redeploy, 2026-09-04

The demo VM was frozen at 2026-07-03 (~50 commits behind on `scripts/api`).
Redeployed by shipping `scripts/ requirements.txt pyproject.toml` over ssh+tar
to `/opt/radon-demo/app` and rebuilding (`docker compose build api && up -d`).
`data/` on the VM is NOT shipped — it holds the copied `flow_reports/*.json`
that the TEST_MODE per-ticker flow guard serves.

Rollback: `radon-demo-api:rollback` (the pre-redeploy image) and
`/opt/radon-demo/app.bak` are both kept on the VM.

The redeploy surfaced one break: `TrustedHostMiddleware` gained a host pin
after the VM's last deploy, and `demo-api.radon.run` was never on the list, so
every proxied request returned `400 Invalid host header` while `127.0.0.1`
health stayed 200. The host is pinned in `scripts/api/server.py`
`_ALLOWED_HOSTS` (89eed3df) rather than left to `RADON_ALLOWED_HOSTS` on the
VM, pinned by `scripts/api/tests/test_loopback_browser_bypass.py`.

**Correction (2026-10-18): that pin is not on the VM yet.** The 09-04 ship was
02db257b, which predates 89eed3df; `/opt/radon-demo/app/scripts/api/server.py`
has no `demo-api.radon.run` entry, and the VM serves only because its `.env`
still sets `RADON_ALLOWED_HOSTS`. "A fresh VM cannot reproduce it" becomes true
only after the next redeploy below.

Post-redeploy verification: `/health` 200 externally, `/health/lite` 401 without
the service token and 200 with it, `POST /vcg/scan` and `/breadth/scan` 200 from
the seeded snapshots, `RADON_API_TEST_MODE=1` confirmed in the running
container, container `TURSO_DB_URL` on `radon-demo-joemccann`, no tailscale and
no reachable IB port on any prod host.

### VM backend deploy and drift check (2026-10-18)

The 09-04 ship was a manual ssh+tar from macOS: it carried 2,193 AppleDouble
`._*` files into the image, wrote no record of which commit it was, and by
2026-10-18 the VM was 847 commits behind main in `scripts/` with 8 routes the
demo frontend calls missing (`/ai-cycle` 404 x213). Nothing can push to the VM
from GitHub: `fw-radon-demo` admits SSH only from the recovery /32.

**Auto-deploy (pull-based).** `radon-demo-autodeploy.timer` on the VM
(`cloud/demo-vm/`) fires every 15 minutes. `demo_vm_autodeploy.py`
(stdlib, `python3 -I`, root) reads the newest `ci.yml` push run on `main`
that concluded `success` from the public GitHub API: a green run means the
full test gate passed and prod deployed that sha. It compares the
`scripts` / `requirements.txt` / `pyproject.toml` tree ids at that sha with
the live `app/REVISION`. Equal trees are a no-op with no download. Otherwise it
downloads the commit tarball, repacks only those paths (no `._*`, no links),
and runs the INSTALLED `/usr/local/lib/radon-demo/deploy-demo-vm-remote.sh`,
the last known-good copy, with the gates and rollback below. After a green
deploy it refreshes both installed tools from that commit. A sha that fails is
written to `/var/lib/radon-demo-autodeploy/failed-sha` and is not rebuilt every
15 minutes; the next green main sha is tried normally. No credential, no
inbound access, no dependency on the Mac mini. Logs:
`journalctl -u radon-demo-autodeploy.service`.

One-time install (or upgrade of the units) from the operator host:
`cloud/scripts/deploy-demo-vm.sh --install-autodeploy "$(git rev-parse origin/main)"`
(`--dry-run` contacts no host).

**Manual deploy** from the operator host (the Mac mini, which has `ssh radon-demo`),
from a checkout that has fetched the target commit:

```
git fetch origin
cloud/scripts/deploy-demo-vm.sh --dry-run "$(git rev-parse origin/main)"
cloud/scripts/deploy-demo-vm.sh "$(git rev-parse origin/main)"
```

`deploy-demo-vm.sh` takes only a full 40-hex sha. It `git archive`s
`scripts requirements.txt pyproject.toml` at that sha (object store only: no
`._*`, no local WIP), ships it, and runs `deploy-demo-vm-remote.sh` on the VM,
which:

- refuses an archive carrying `._*` entries or a top-level `data/`, and
  refuses to start unless `.env` sets `RADON_API_TEST_MODE=1` and a
  `radon-demo` (never `radon-joemccann`) `TURSO_DB_URL`;
- copies `app/data/` (the `flow_reports` seed) verbatim into the new tree and
  never writes the live copy; the previous tree, seed included, becomes
  `app.bak`;
- writes `app/REVISION` = sha, tags the running image `radon-demo-api:rollback`,
  then `docker compose build api && docker compose up -d api`;
- gates on: in-container `/health` reporting the new `revision`;
  `RADON_API_TEST_MODE=1` and a `radon-demo` `TURSO_DB_URL` inside the running
  container (values never printed); `/health` through Caddy with the real
  `demo-api.radon.run` Host header (the host-pin trap above); `/health/lite`
  401 without and 200 with the service token (read from the container, passed
  on stdin, never in argv);
- on any failure restores `app.bak` and `radon-demo-api:rollback` and restarts.

The operator side then requires the off-box `https://demo-api.radon.run/health`
to report the same `revision`, and tells the VM to roll back if it does not.
Locks: `flock` on `/opt/radon-demo/.deploy.lock` on the VM, a lock dir on the
operator host. Re-running the live sha is a no-op. Manual rollback:
`ssh radon-demo bash -s -- rollback < cloud/scripts/deploy-demo-vm-remote.sh`.

**Revision.** `/health` returns `revision` (validated 40-hex from
`<app>/REVISION`) to every caller, public included; hosts without the file
(prod) keep the old payload.

**Drift check.** `.github/workflows/demo-vm-drift.yml` runs
`scripts/ci/check_demo_vm_drift.py` every 6 hours on main. It fails when
`/health` reports no revision, a revision not on main, or when the oldest main
commit touching the backend paths that the VM lacks is more than 1 day old,
which means the auto-deploy agent is failing or stopped. The failed-run email
is the alert.

### Second root cause: Svix auto-disabled the endpoint (found 2026-09-04)

Restoring `demo_webhook_events` was necessary but not sufficient. The Svix
endpoint `https://demo.radon.run/api/webhooks/clerk` was **Disabled** with a
69.9% error rate, last updated 2026-08-19 — Svix auto-disabled it six days into
the 500 storm. After that no delivery was even attempted, so the endpoint could
not recover on its own once the underlying bug was fixed.

Re-enabled via the Svix app portal (Clerk Backend API `POST /v1/webhooks/svix_url`
returns a one-time login link), then `Replay → Replay missing messages` scoped to
the narrowest window so only the new signup was replayed, never the ~60 stranded
accounts.

Verified end to end 2026-09-04: a real Google-OAuth signup produced
`publicMetadata {demoRole: trial, demoTrialExpiresAt: 2026-09-08T16:00:00-04:00}`,
a `demo_users` row, and a `demo_webhook_events` claim row.

**Detection gap this leaves.** The Pushover alert added alongside the table fix
fires from inside the webhook handler, so it is silent in exactly this failure
mode — a disabled endpoint means the handler never runs. The durable signal is
the provisioning RATE: alert when `user.created` signups occur with no
corresponding `demo_users` row, or poll the Svix endpoint's enabled/error-rate
state. Neither exists yet.
