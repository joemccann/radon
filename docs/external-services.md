# External services

Radon is glued together from third-party services. The env-var matrix lives
in [`.env.example`](../.env.example) and [`web/.env.example`](../web/.env.example).
This page is why each service exists and where to sign up.

Production `.env` lives on the VPS at `/home/radon/radon-cloud/.env` (`0600`).
That path is the sole legacy-directory exception during the monorepo
migration, not a source of deploy code. Laptop dev uses the root `.env`
for FastAPI and scripts, plus `web/.env` for Next.js.

## Required (production)

| Service | Purpose | Env vars | Where |
|---|---|---|---|
| **Interactive Brokers** | Real-time quotes, options chains, order routing, positions. IB Gateway + IB Flex Web Service. | `TWS_USERID`, `TWS_PASSWORD`, `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID` (blotter), `IB_FLEX_NAV_QUERY_ID` (cash flows), `IB_GATEWAY_*` | [ibkr.com](https://www.interactivebrokers.com/) · IB Pro account · Flex Web Service enabled in Account Management |
| **Unusual Whales** | Dark pool flow, options flow, OI changes, sweeps, analyst data, LEAP IV. | `UW_TOKEN` | [unusualwhales.com](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64) |
| **Clerk** | JWT auth for the terminal + FastAPI. Localhost auto-bypassed in dev. | `CLERK_ISSUER`, `CLERK_JWKS_URL`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `ALLOWED_USER_IDS` | [clerk.com](https://clerk.com/) |
| **Turso (libSQL)** | Cloud-hosted SQLite. Canonical store for journal, service_health, snapshots. | `TURSO_DB_URL`, `TURSO_AUTH_TOKEN` | [turso.tech](https://turso.tech/) |
| **Anthropic Claude API** | Assistant chat, share-card OG renders, vision tagger (newsfeed), seasonality vision fallback. | `ANTHROPIC_API_KEY` (aliases `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`) | [console.anthropic.com](https://console.anthropic.com/) |
| **Backblaze B2** | Off-box cold archive for `portfolio_snapshots` older than ~30d (`radon-portfolio-archive` oneshot). S3-compatible API via `boto3`. Required on the production VPS; unit fails closed without keys. | `RADON_ARCHIVE_S3_ENDPOINT`, `RADON_ARCHIVE_S3_BUCKET`, `RADON_ARCHIVE_S3_ACCESS_KEY_ID`, `RADON_ARCHIVE_S3_SECRET_ACCESS_KEY`, `RADON_ARCHIVE_S3_REGION` | [backblaze.com/b2](https://www.backblaze.com/b2/cloud-storage.html) · bucket `radon-archive` |

## Required for specific subsystems

| Service | Subsystem | Env vars | Where |
|---|---|---|---|
| **MenthorQ** | `/menthorq/*` CTA / dashboard / screener / forex / summary / quin surfaces. Username/password login via Playwright. | `MENTHORQ_USER`, `MENTHORQ_PASS` | [menthorq.com](https://menthorq.com/) |
| **MarketDataWorks (MDW)** | Inbound shared-secret used by MDW → FastAPI pushes that feed CTA enrichment. Validates `X-API-Key` header. | `MDW_API_KEY` | Vendor-issued |
| **The Market Ear** | Real-time intraday news scraped by `scripts/newsfeed/`. Headless Playwright login; session cached at `data/newsfeed-storage.json` (~30d), full re-auth ~6h. | `THEMARKETEAR_EMAIL`, `THEMARKETEAR_PASSWORD` | [themarketear.com](https://themarketear.com/) (paid subscription) |
| **Cerebras** | Newsfeed text tagger (gpt-oss-120b → qwen-3 fallback). Falls back to Anthropic when unset. | `CEREBRAS_API_KEY` | [cerebras.ai](https://www.cerebras.ai/inference) |
| **Artificial Analysis** | LLM Token Expenditure Index (`/regime/llm`, daily timer). Free tier 1000 req/day. | `ARTIFICIAL_ANALYSIS_API_KEY` | [artificialanalysis.ai](https://artificialanalysis.ai/login) → Insights dashboard |
| **Exa** | Company and market research surfaces. | `EXA_API_KEY` | [dashboard.exa.ai](https://dashboard.exa.ai/api-keys) |

## Infrastructure (production)

| Service | Purpose | Notes |
|---|---|---|
| **Hetzner Cloud** | VPS that hosts FastAPI, IB Gateway (docker), the WS relay, the monitor daemon, the newsfeed, Caddy, and `media.radon.run`. Host secrets in `/home/radon/radon-cloud/.env` include Turso + Backblaze B2 archive keys. | Resolved as `ib-gateway` via Tailscale on the laptop |
| **Backblaze B2** | Cold storage for archived portfolio snapshot months (`portfolio_snapshots/YYYY-MM.jsonl.gz`). | Bucket `radon-archive` |
| **Tailscale** | Mesh VPN between laptop and VPS. Laptop reaches `ib-gateway:4001` over Tailscale; FastAPI on the VPS binds to localhost-only. | [tailscale.com](https://tailscale.com/) |
| **Caddy** | TLS termination + reverse proxy on the VPS. Serves `app.radon.run` and `media.radon.run`. | Canonical config: [`cloud/caddy/`](../cloud/caddy/) |
| **GitHub Actions** | `git push origin main` triggers `.github/workflows/ci.yml`, which runs the Vitest + pytest gate then deploys the tested monorepo SHA on green. | Confirm: `gh run list --workflow=ci.yml --limit 1` |

## Optional alerting / fallback data

| Service | Purpose | Env vars | Where |
|---|---|---|---|
| **Pushover** | Watchdog P1 (emergency) starts laptop Grok auto-fix. Grok follow-up and live deploys are normal-priority (`radon grok:`, `radon deploy live`) and must never be P1. P2/P3 stay in `service_health`. Absent vars degrade gracefully. Spec: [`docs/grok-page-responder.md`](grok-page-responder.md). | `PUSHOVER_USER`, `PUSHOVER_TOKEN` | [pushover.net](https://pushover.net/) |
| **FRED (St. Louis Fed)** | Risk-free rate (DFF) for Black-Scholes implied value. No key required; 24h cache + 0.0 fallback. | none | Public API |
| **Cboe** | COR1M historical fallback when IB / UW are missing the series. | none | Public CSV feed |
| **Yahoo Finance** | Last-resort price fallback when IB and UW both fail. Never the first or second source. | none | Public API |
