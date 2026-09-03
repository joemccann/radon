# External services

Radon is glued together from third-party services. The env-var matrix lives
in [`.env.example`](../.env.example) and [`web/.env.example`](../web/.env.example).
This page is why each service exists and where to sign up. Keys entered in
the profile Credentials tab live in the host-local encrypted secret store and
WIN over these `.env` values at FastAPI startup — rotation and key-loss
recovery: [`docs/operations.md`](operations.md) "Encrypted credential store".

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
| **Hetzner Cloud** | VPS that hosts FastAPI, IB Gateway (docker), the WS relay, the monitor daemon, the newsfeed, Caddy, and `media.radon.run`. Host secrets live in `/etc/radon/env` (0640 root:radon, canonical; `/home/radon/radon-cloud/.env` is the compatibility symlink) — IB Flex, Turso, Backblaze B2 archive, and the Robinhood MCP bootstrap keys. The Robinhood token file is the one secret NOT in that env file: it sits at `/etc/radon/rh-mcp.json` (0600) because the refresh loop must rewrite it. | Resolved as `ib-gateway` via Tailscale on the laptop |
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
| **Robinhood** | READ-ONLY quote/historicals failover before Yahoo, plus the popular-watchlist / scan retail-crowding overlay (`rh_crowding`). Official trading MCP only: `https://agent.robinhood.com/mcp/trading` (OAuth 2.1 + PKCE, Streamable HTTP; discovery `https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading`). Execution stays on IB — Radon never calls a place_*/cancel_* tool. **Access tokens expire ~3 days, so refresh is mandatory in production:** tokens live in a 0600 JSON file (`ROBINHOOD_MCP_TOKEN_FILE`; fields `access_token`, `refresh_token`, `client_id`, `token_type`, `expires_at`; default `data/rh_mcp_token.json`, gitignored; production `/etc/radon/rh-mcp.json` — never commit it) that the client rewrites atomically on every refresh against `https://api.robinhood.com/oauth2/token/` (`grant_type=refresh_token` + `client_id`, form-encoded, public client, `token_endpoint_auth_method=none` — no secret). Refresh fires when the access token is missing, within 1h of `expires_at`, or on an MCP 401/403, except when the rejected token was itself minted within the last 60s (`minted_recently`): then the client disables Robinhood for the rest of the process (the same clean skip to Yahoo) instead of re-minting per symbol across a scan; a rotated `refresh_token` replaces the old one. Env vars bootstrap the file on first run. No credentials at all = clean skip to Yahoo; `invalid_grant` degrades the process to the same skip. Options probed live (2026-08-30, 67 tools): `get_option_quotes` takes `instrument_ids` (UUIDs), returns real-time quotes + official prior-session close, no greeks/IV/OI — never a vol-surface source. **Failure classes and the 300s breaker (REL-174):** the client sorts failures into `auth`, `rate_limited` (HTTP 429), `network` and `token_endpoint`. `auth` is structural and disables the rung for the rest of the process; the other three open a circuit breaker for `BREAKER_COOLDOWN_S` (300s) so a dead or throttled MCP is hit once per scan instead of once per symbol. The class rides on the ladder heartbeat, so an `ok` row can name a Robinhood demotion in `last_error`. **Rotated-token recovery:** if the endpoint has already spent the old refresh token and the 0600 store cannot be rewritten, the new pair is written to a `rh-mcp-rotated-*.json` file in the system temp dir and the error names the path to restore it to. That file is the only copy of a working refresh token — restore it to `ROBINHOOD_MCP_TOKEN_FILE` (mode 0600) before the temp dir is cleaned, or the whole rung needs a fresh OAuth grant. Full order: IB > UW > Cboe > Robinhood > Yahoo. Operator setup: [agentic trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) · [trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/). | `ROBINHOOD_MCP_URL` (default the trading MCP), `ROBINHOOD_MCP_TOKEN` (access, ~3d expiry), `ROBINHOOD_MCP_REFRESH_TOKEN` (required for production), `ROBINHOOD_MCP_CLIENT_ID` (public OAuth client), `ROBINHOOD_MCP_TOKEN_FILE` (0600 JSON) | [agentic trading support](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) (the `agent.robinhood.com` root is an MCP endpoint, not a console — it 404s in a browser) |
| **Yahoo Finance** | Last-resort price fallback when IB, UW and Robinhood all fail. Never the first or second source. | none | Public API |

**Robinhood non-dependencies (deliberate):** no pip package — `requests` speaks the MCP JSON-RPC directly; unofficial wrappers (robin-stocks, meow-meow-hood, private `api.robinhood.com` scrapers) are forbidden; the Banking MCP (`banking-agent.robinhood.com`) is out of scope; the crypto REST surface (`trading.robinhood.com`) is out of scope; execution stays on IB; the `rh_crowding` series is descriptive retail-crowding context only and cannot trip the three gates (convexity, edge, fractional Kelly).
