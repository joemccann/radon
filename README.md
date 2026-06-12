# Radon

<p align="center">
  <img src=".github/hero.png" alt="Radon - reconstructing market structure" width="900" />
</p>

![CI](https://github.com/joemccann/radon/actions/workflows/ci.yml/badge.svg)
![version](https://img.shields.io/badge/version-0.7.0-05AD98)
![license](https://img.shields.io/badge/license-proprietary-1e293b)

**Market-structure reconstruction.** Radon surfaces convex options trades from dark pool and OTC flow, the volatility surface, and cross-asset positioning. The system runs every candidate through a hard three-gate framework before sizing it.

Flow signal or nothing. No narrative trades, no chart-pattern trades.

## Contents

- [What it does](#what-it-does)
- [Three gates, in order](#three-gates-in-order)
- [Quick start](#quick-start)
- [External services](#external-services)
- [Architecture at a glance](#architecture-at-a-glance)
- [Recent additions](#recent-additions)
- [What's where](#whats-where)
- [Project layout](#project-layout)
- [Data source priority](#data-source-priority)
- [Deployment](#deployment)
- [Tests](#tests)
- [Glossary](#glossary)

## What it does

- Detects institutional positioning through Interactive Brokers, [Unusual Whales](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64), MenthorQ CTA, and cross-asset feeds.
- Designs convex options structures and sizes them with fractional Kelly.
- Streams live quotes, greeks, P&L, and order state to a Next.js terminal at `localhost:3000` (laptop dev) and `app.radon.run` (production).
- Auto-deploys to a Hetzner VPS on every push to `main`.

## Three gates, in order

| Gate | Rule |
|------|------|
| **Convexity** | Potential gain >= 2x potential loss. Defined-risk default. |
| **Edge** | Specific, data-backed signal that has not fully moved price. |
| **Risk** | Fractional Kelly, hard cap 2.5% of bankroll per position. |

Any gate fails, no trade. Full rules in [`CLAUDE.md`](CLAUDE.md). Strategy specs in [`docs/strategies.md`](docs/strategies.md).

## Quick start

**Prerequisites**

- Python 3.13 (3.14 has an `ib_insync` / `eventkit` incompatibility)
- Node.js 18+ and `bun` (npm is not used for the JS stack)
- Interactive Brokers Gateway (cloud via Tailscale, Docker, or local TWS)
- Accounts at the external services listed below — start with [`.env.example`](.env.example) and [`web/.env.example`](web/.env.example), both fully annotated with sign-up URLs

```bash
git clone https://github.com/joemccann/radon.git
cd radon
cp .env.example .env             # then fill in
cp web/.env.example web/.env     # then fill in
pip install -r requirements.txt
cd web && bun install && cd ..
```

The two `.env.example` files are the canonical variable reference — every required and optional key has an inline comment with purpose, format, and required-vs-optional. Read those before the operations runbook.

**Dev launchers**

```bash
scripts/cloud.sh    # default: laptop runs Next.js + newsfeed, VPS serves FastAPI/relay/IB Gateway over Tailscale
scripts/local.sh    # fully local: laptop runs everything including the IB Gateway Docker container
```

`cloud.sh` is the everyday workflow. `local.sh` is for offline dev or when the VPS is down. Mode persists to `.env.ib-mode`; toggle later via `scripts/ib mode local|cloud`.

Open `http://localhost:3000`. Clerk auto-bypasses on localhost in non-production.

## External services

Radon is glued together from a long list of third-party services. The full env-var matrix lives in [`.env.example`](.env.example) and [`web/.env.example`](web/.env.example); the table below summarises why each one is there and where to sign up.

### Required (production)

| Service | Purpose | Env vars | Where |
|---|---|---|---|
| **Interactive Brokers** | Real-time quotes, options chains, order routing, positions. IB Gateway + IB Flex Web Service. | `TWS_USERID`, `TWS_PASSWORD`, `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID` (blotter), `IB_FLEX_NAV_QUERY_ID` (cash flows), `IB_GATEWAY_*` | [ibkr.com](https://www.interactivebrokers.com/) · IB Pro account · Flex Web Service enabled in Account Management |
| **Unusual Whales** | Dark pool flow, options flow, OI changes, sweeps, analyst data, LEAP IV. | `UW_TOKEN` | [unusualwhales.com](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64) |
| **Clerk** | JWT auth for the terminal + FastAPI. Localhost auto-bypassed in dev. | `CLERK_ISSUER`, `CLERK_JWKS_URL`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `ALLOWED_USER_IDS` | [clerk.com](https://clerk.com/) |
| **Turso (libSQL)** | Cloud-hosted SQLite. Canonical store for journal, service_health, snapshots. | `TURSO_DB_URL`, `TURSO_AUTH_TOKEN` | [turso.tech](https://turso.tech/) |
| **Anthropic Claude API** | Assistant chat, share-card OG renders, vision tagger (newsfeed), seasonality vision fallback. | `ANTHROPIC_API_KEY` (aliases `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`) | [console.anthropic.com](https://console.anthropic.com/) |

### Required for specific subsystems

| Service | Subsystem | Env vars | Where |
|---|---|---|---|
| **MenthorQ** | `/menthorq/*` CTA / dashboard / screener / forex / summary / quin surfaces. Username/password login via Playwright. | `MENTHORQ_USER`, `MENTHORQ_PASS` | [menthorq.com](https://menthorq.com/) |
| **MarketDataWorks (MDW)** | Inbound shared-secret used by MDW → FastAPI pushes that feed CTA enrichment. Validates `X-API-Key` header. | `MDW_API_KEY` | Vendor-issued |
| **The Market Ear** | Real-time intraday news scraped by `scripts/newsfeed/`. Headless Playwright login; session cached at `data/newsfeed-storage.json` (~30d), full re-auth ~6h. | `THEMARKETEAR_EMAIL`, `THEMARKETEAR_PASSWORD` | [themarketear.com](https://themarketear.com/) (paid subscription) |
| **Cerebras** | Newsfeed text tagger (gpt-oss-120b → qwen-3 fallback). Falls back to Anthropic when unset. | `CEREBRAS_API_KEY` | [cerebras.ai](https://www.cerebras.ai/inference) |
| **Artificial Analysis** | LLM Token Expenditure Index (`/regime/llm`, daily timer). Free tier 1000 req/day. | `ARTIFICIAL_ANALYSIS_API_KEY` | [artificialanalysis.ai](https://artificialanalysis.ai/login) → Insights dashboard |
| **Exa** | Company and market research surfaces. | `EXA_API_KEY` | [dashboard.exa.ai](https://dashboard.exa.ai/api-keys) |

### Infrastructure (production)

| Service | Purpose | Notes |
|---|---|---|
| **Hetzner Cloud** | VPS that hosts FastAPI, IB Gateway (docker), the WS relay, the monitor daemon, the newsfeed, Caddy, and `media.radon.run`. | Resolved as `ib-gateway` via Tailscale on the laptop |
| **Tailscale** | Mesh VPN between laptop and VPS. Laptop reaches `ib-gateway:4001` over Tailscale; FastAPI on the VPS binds to localhost-only. | [tailscale.com](https://tailscale.com/) |
| **Caddy** | TLS termination + reverse proxy on the VPS. Serves `app.radon.run` and `media.radon.run`. | Config in the sibling `radon-cloud` repo |
| **GitHub Actions** | `git push origin main` triggers `.github/workflows/ci.yml` which runs the Vitest + pytest gate then deploys on green: it SSHes to Hetzner and runs `bash scripts/deploy.sh`. | Confirm: `gh run list --workflow=ci.yml --limit 1` |

### Optional alerting / fallback data

| Service | Purpose | Env vars | Where |
|---|---|---|---|
| **Pushover** | Watchdog P1 alerts that cut through iOS Do Not Disturb. P2/P3 land in `service_health` only. Absent vars degrade gracefully. | `PUSHOVER_USER`, `PUSHOVER_TOKEN` | [pushover.net](https://pushover.net/) |
| **FRED (St. Louis Fed)** | Risk-free rate (DFF) for Black-Scholes implied value. No key required; 24h cache + 0.0 fallback. | none | Public API |
| **Cboe** | COR1M historical fallback when IB / UW are missing the series. | none | Public CSV feed |
| **Yahoo Finance** | Last-resort price fallback when IB and UW both fail. Never the first or second source. | none | Public API |

Production `.env` lives on the VPS at `/home/radon/radon-cloud/.env`. Laptop dev uses the root `.env` for FastAPI and scripts, plus `web/.env` for Next.js (some keys are duplicated because Next.js can't read the root file from inside `web/`).

## Architecture at a glance

```
       Unusual Whales ─┐
   Interactive Brokers ├──> Signal Detection ──> Strategy Evaluation
            MenthorQ ──┘                              │
                                                      ▼
                                          Convex Structure Builder
                                                      │
                                                      ▼
                                          Kelly Position Sizing
                                                      │
                                                      ▼
                                          Execution / Monitoring
                                                      │
                                                      ▼
                                              Radon Terminal
```

**Process layout**

- `localhost:3000` for the Next.js 16 terminal
- `:8321` for FastAPI (40+ endpoints, JWT-gated, localhost bypass for server-to-server)
- `:8765` for the IB realtime WebSocket relay
- 120s loop for the newsfeed scraper (headless Playwright)

**Storage**

- Turso libSQL cloud DB (canonical). Every Radon process talks directly to cloud — no embedded replica anywhere (retired 2026-05-20; see [`docs/cloud-services.md`](docs/cloud-services.md))
- JSON files in `data/` as fallback / DR archive
- Hetzner-hosted `media.radon.run` for newsfeed images

Full architecture and the Phase 0-6 migration history live in [`docs/cloud-services.md`](docs/cloud-services.md). The developer runbook is [`CLAUDE.md`](CLAUDE.md).

## Recent additions

Things that shipped in the last few weeks and are worth knowing about:

- **No-replica DB architecture (2026-05-20).** Every Radon process now goes direct-to-cloud — the code default since DUR-07, with the replica opt-in only via `RADON_DB_USE_REPLICA=1` and `RADON_DB_NO_REPLICA=1` kept as a fleet-wide systemd kill switch. The libsql embedded-replica model (`data/replica.db`) was retired after two same-day incidents: multi-writer WAL contention then single-writer frame conflicts. Reads cost +30–60 ms cloud round-trip, absorbed by SWR caching.
- **Stuck-awaiting-2FA self-heal (2026-05-20).** `ib_watchdog.is_stuck_awaiting_2fa()` fires a fresh IBKR Mobile push after 3 cycles of `auth_state=awaiting_2fa` with no push lock holder. Eliminates the human-in-the-loop dependency where the system used to sit stuck overnight.
- **Authoritative footer IB status (2026-05-20).** Sidebar + MobileAppBar derive a single `displayStatus` (`CONNECTED` / `AWAITING 2FA` / `DEGRADED` / `UNREACHABLE` / `OFFLINE` / `RELAY OFFLINE`) from FastAPI `/health` rather than the WS-relay's stale `ib_connected` flag. Footer and banner can no longer contradict each other.
- **monitor_daemon handlers on `client_id="auto"` (2026-05-20).** `fill_monitor`, `exit_orders`, `journal_sync` rotate across `SUBPROCESS_ID_RANGE` instead of hardcoded 70/71/72 — eliminates the half-open-socket "client id already in use" failure mode.
- **Closing-trade exception in risk model (2026-05-20).** `OrderRiskLeg.coveringLongContracts` lets the risk panel recognise a SELL of a held LONG as a close (or partial close) instead of flagging it UNBOUNDED. Symmetric for puts. SELL beyond held quantity flags only the excess.
- **Autonomous Hetzner timers** for `vcg-scan`, `portfolio-sync`, and `cta-sync` replaced the previous browser-driven refresh model. Data stays fresh even when no tab is open.
- **Service-health watchdog** with four buckets (`intraday`, `continuous`, `daily`, `error`), Pushover routing (P1 only), cooldown, hysteresis, and `python -m scripts.watchdog ack <service>` to silence noise.
- **Banner categories.** `scheduled` services flip red on stale; `on-demand` services show an amber dormant chip and are excluded from alerting.
- **`/usr/local/bin/radon`** operator CLI auto-enumerates every loaded `radon-*` unit, so new timers don't require script edits. Installed durably by `setup-vps.sh`.
- **Cash flow throttle backoff.** IBKR Flex codes 1001 / 1018 / 1019 trip an exponential circuit breaker (24h to 168h cap) so the script doesn't perpetuate a sliding-window throttle.
- **CRI history zoom.** The CRI spread chart now carries ~251 trading days of history with a brush-driven zoom UI and preset range chips.
- **Banner humanization.** `service_health.last_error` JSON is rewritten into operator-friendly copy before render.
- **`parseScanTime`** normalises naive Python ISO timestamps on the JS side so date-day drift can't surface in the UI.
- **2FA-aware IB Gateway restart** with exponential backoff, cross-process push lock, and `auth_state` reporting. `POST /ib/reset-backoff` is the operator escape hatch.

## What's where

| Topic | Doc |
|-------|-----|
| Developer runbook, gates, calculations, component cheat sheet | [`CLAUDE.md`](CLAUDE.md) |
| Cloud architecture, two-mode deploy, Turso DB | [`docs/cloud-services.md`](docs/cloud-services.md) |
| Background services, watchdogs, deploy flow, env vars | [`docs/operations.md`](docs/operations.md) |
| CLI commands and test runners | [`docs/scripts-reference.md`](docs/scripts-reference.md) |
| Strategy specs (Dark Pool, LEAP, GARCH, VCG-R, CRI, Risk Reversal) | [`docs/strategies.md`](docs/strategies.md) |
| VCG-R research notes | [`docs/cross_asset_volatility_credit_gap_spec_(VCG).md`](docs/cross_asset_volatility_credit_gap_spec_(VCG).md) |
| GARCH convergence strategy | [`docs/strategy-garch-convergence.md`](docs/strategy-garch-convergence.md) |
| Options structures catalogue | [`docs/options-structures.md`](docs/options-structures.md) |
| Chart system | [`docs/chart-system.md`](docs/chart-system.md) |
| Brand identity and design tokens | [`docs/brand-identity.md`](docs/brand-identity.md) |
| IB Gateway Docker setup | [`docs/ib-gateway-docker.md`](docs/ib-gateway-docker.md) |
| IB connection troubleshooting | [`docs/ib-connection-troubleshooting.md`](docs/ib-connection-troubleshooting.md) |
| Unusual Whales API reference | [`docs/unusual_whales_api.md`](docs/unusual_whales_api.md) |
| Performance reconstruction | [`docs/performance-reconstruction.md`](docs/performance-reconstruction.md) |
| OAuth subscription auth | [`docs/oauth-subscription-auth.md`](docs/oauth-subscription-auth.md) |

## Project layout

```
radon/
├─ scripts/              Python scanners, evaluators, broker integrations
│  ├─ clients/           Broker and data-provider adapters
│  ├─ monitor_daemon/    Background fill/exit/rebalance daemon
│  ├─ db/                Turso writers + migrations
│  └─ watchdog/          Service-health alerting
├─ web/                  Next.js 16 terminal + FastAPI server scripts
├─ site/                 Standalone marketing site (separate Vercel project)
├─ docs/                 Topic-scoped documentation
├─ data/                 Runtime artifacts (gitignored except taxonomy + presets)
├─ config/               launchd plists and service configuration
├─ brand/                Design system and tokens
└─ CLAUDE.md             Authoritative developer runbook
```

## Data source priority

Strict order for any price / flow / chain lookup. The full external-service inventory is in [External services](#external-services) above.

1. **Interactive Brokers** for real-time quotes, options chains, and portfolio state
2. **Unusual Whales** for dark pool flow, sweeps, options flow, and analyst data
3. **Cboe official feeds** for COR1M historical fallback
4. **Yahoo Finance** as a strict last resort

Never skip to Yahoo or web scrape without trying IB then Unusual Whales first. Research surfaces (Exa) and news (themarketear, MenthorQ) are orthogonal — they don't substitute for missing price data.

## Deployment

`git push origin main` is the deploy. `.github/workflows/ci.yml` runs the Vitest + pytest gate then deploys on green: it SSHes to the Hetzner VPS and runs `bash scripts/deploy.sh`:

1. `git reset --hard origin/main`
2. `pip install -r requirements.txt`
3. `npm install` (the VPS uses npm, not bun) then `next build --experimental-build-mode=compile`
4. `sudo systemctl restart radon-{nextjs,api,relay,monitor,newsfeed}` with health-gated rollback

Confirm with `gh run list --workflow=ci.yml --limit 1`. Full operational detail in [`docs/operations.md`](docs/operations.md). The systemd units, Caddy config, and Docker Compose project for IB Gateway live in a sibling `radon-cloud` repo.

## Tests

```bash
python3.13 scripts/run_pytest_affected.py        # scoped Python tests
python -m pytest scripts/tests/ -v               # full Python suite
cd web && bun test                               # Vitest
cd web && npx playwright test                    # E2E
```

Mocked API calls cover most of the surface, so development rarely needs a live broker session. Order-route integration uses an isolated test-mode FastAPI harness (`web/tests/fastapiHarness.ts`) that never reuses the broker-backed `localhost:8321` server.

## Glossary

| Term | Definition |
|------|------------|
| **Convexity** | Asymmetric payoff where expected upside materially exceeds downside |
| **CRI** | Crash Risk Index, composite crash-risk and CTA deleveraging model |
| **CTA** | Commodity Trading Advisor, typically systematic trend-following funds |
| **Dark Pool** | Private off-exchange venue used for institutional trading |
| **Edge** | A specific reason the market is mispricing an outcome |
| **GEX** | Gamma exposure surface across the options chain |
| **Kelly Criterion** | Position-sizing framework that scales exposure to edge and odds |
| **VCG-R** | Volatility-Credit Gap, VIX>28 + VCG>2.5σ risk-off trigger |
