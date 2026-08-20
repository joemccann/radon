# Radon

<p align="center">
  <img src=".github/hero.png" alt="Radon - reconstructing market structure" width="900" />
</p>

![CI](https://github.com/joemccann/radon/actions/workflows/ci.yml/badge.svg)
![version](https://img.shields.io/badge/version-0.7.0-05AD98)
![license](https://img.shields.io/badge/license-proprietary-1e293b)

**Market-structure reconstruction.** Radon surfaces convex options trades from dark pool and OTC flow, the volatility surface, and cross-asset positioning. Every candidate runs a hard three-gate framework before sizing.

Flow signal or nothing. No narrative trades, no chart-pattern trades.

## Contents

- [What it does](#what-it-does)
- [Three gates, in order](#three-gates-in-order)
- [Quick start](#quick-start)
- [Architecture at a glance](#architecture-at-a-glance)
- [Now true](#now-true)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Data source priority](#data-source-priority)
- [Deployment](#deployment)
- [Tests](#tests)
- [Maintainers and help](#maintainers-and-help)

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
- Node.js 18+ and `bun` for the terminal (`web/`). `site/` still uses npm. See [`DEVELOPMENT.md`](DEVELOPMENT.md).
- Interactive Brokers Gateway (cloud via Tailscale, Docker, or local TWS)
- Accounts at the services in [`.env.example`](.env.example), [`web/.env.example`](web/.env.example), and [`docs/external-services.md`](docs/external-services.md)

```bash
git clone https://github.com/joemccann/radon.git
cd radon
cp .env.example .env             # then fill in
cp web/.env.example web/.env     # then fill in
pip install -r requirements.txt
cd web && bun install && cd ..
```

The two `.env.example` files are the canonical variable reference. Read those before the operations runbook.

**Dev launchers**

```bash
scripts/cloud.sh    # default: laptop runs Next.js + newsfeed, VPS serves FastAPI/relay/IB Gateway over Tailscale
scripts/local.sh    # fully local: laptop runs everything including the IB Gateway Docker container
```

`cloud.sh` is the everyday workflow. `local.sh` is for offline dev or when the VPS is down. Mode persists to `.env.ib-mode`; toggle later via `scripts/ib mode local|cloud`.

Open `http://localhost:3000`. Clerk auto-bypasses on localhost in non-production.

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
- `:8321` for FastAPI (JWT-gated, localhost bypass for server-to-server)
- `:8765` for the IB realtime WebSocket relay
- 120s loop for the newsfeed scraper (headless Playwright)

**Storage**

- Turso libSQL cloud DB (canonical). Direct-to-cloud. Embedded replica retired 2026-05-20. See [`docs/cloud-services.md`](docs/cloud-services.md).
- JSON files in `data/` as fallback / DR archive
- Hetzner-hosted `media.radon.run` for newsfeed images

Developer runbook: [`CLAUDE.md`](CLAUDE.md).

## Now true

Durable facts. History and mechanism live in the owner file, not here.

- **Indicators.** Regime tabs at `/regime/{skew,skew2d,straddle,cor,curve}`. Cheap-wing scanner at `/scanner?mode=vol-cone`. Specs: [`docs/indicators/`](docs/indicators/README.md).
- **CMD+J.** Quotes, priced UW chains, and `evaluate.py` run from the in-app assistant. KB miss is not a dead end.
- **Stop orders.** Desktop and mobile tickets place `STP` and `STP LMT` through `/api/orders/place`.
- **Incidents.** Watchdog artifacts under `data/incidents/`. Triage with `/incident <path>`. Cases: [`docs/incident-runbook.md`](docs/incident-runbook.md).
- **Factory.** GitHub issues labeled `factory` become draft PRs via Foreman in [`joemccann/radon-factory`](https://github.com/joemccann/radon-factory). Contract: [`docs/factory.md`](docs/factory.md).

## Project layout

```
radon/
├─ scripts/              Python scanners, evaluators, broker integrations
│  ├─ clients/           Broker and data-provider adapters
│  ├─ api/               FastAPI (:8321)
│  ├─ monitor_daemon/    Background fill/exit/rebalance daemon
│  ├─ db/                Turso writers + migrations
│  ├─ knowledge/         radon-kb MCP (journal, evals, incidents)
│  └─ watchdog/          Service-health alerting
├─ web/                  Next.js 16 terminal (bun)
├─ site/                 Marketing site (npm, separate Vercel project)
├─ cloud/                VPS systemd, Caddy, deploy, IB Gateway compose
├─ docker/ib-gateway/    Laptop IB Gateway compose
├─ lib/tools/            Pi tools (Vitest + CI)
├─ tests/                TWR money-math (CI collects this directory)
├─ docs/                 Topic-scoped documentation (index: docs/README.md)
├─ config/               Laptop launchd plists
├─ brand/                Design system and tokens
└─ CLAUDE.md             Authoritative developer runbook
```

## Documentation

Index: [`docs/README.md`](docs/README.md). External services: [`docs/external-services.md`](docs/external-services.md). Equibles: [`docs/equibles-api.md`](docs/equibles-api.md). Toolchain map: [`DEVELOPMENT.md`](DEVELOPMENT.md).

## Data source priority

Strict order for any price / flow / chain lookup. Full inventory: [`docs/external-services.md`](docs/external-services.md).

1. **Interactive Brokers** for real-time quotes, options chains, and portfolio state
2. **Unusual Whales** for dark pool flow, sweeps, options flow, and analyst data
3. **Cboe official feeds** for COR1M historical fallback
4. **Yahoo Finance** as a strict last resort

Never skip to Yahoo or web scrape without trying IB then Unusual Whales first. Research surfaces (Exa) and news (themarketear, MenthorQ) are orthogonal. They do not substitute for missing price data.

## Deployment

`git push origin main` is the deploy. After the CI gates pass, GitHub Actions extracts `cloud/` from the exact tested SHA into an immutable VPS runner and runs its deploy contract.

Canonical infra: [`cloud/`](cloud/). Recovery: [`cloud/CLAUDE.md`](cloud/CLAUDE.md) and [`docs/monorepo-cloud-migration.md`](docs/monorepo-cloud-migration.md). Confirm: `gh run list --workflow=ci.yml --limit 1`.

## Tests

```bash
python3.13 scripts/run_pytest_affected.py        # scoped Python tests
python -m pytest scripts/tests/ -v               # full Python suite
cd web && bun test                               # Vitest
cd web && bunx playwright test                   # E2E
```

Mocked API calls cover most of the surface. Order-route integration uses an isolated test-mode FastAPI harness (`web/tests/fastapiHarness.ts`) that never reuses the broker-backed `localhost:8321` server.

## Maintainers and help

Maintained by Joe McCann. Single operator. Clones are unsupported. See [`SUPPORT.md`](SUPPORT.md).

Security reports: [`SECURITY.md`](SECURITY.md). Do not open a public issue for a vulnerability.
