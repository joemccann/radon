# Radon

<p align="center">
  <img src=".github/hero.png" alt="Radon - Reconstructing Market Structure" width="900" />
</p>

<p align="center">
  <img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" />
  <img alt="Next.js 16" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white" />
  <img alt="Test stack" src="https://img.shields.io/badge/Tests-pytest%20%7C%20Vitest%20%7C%20Playwright-0A7F6F" />
</p>

**Reconstructing market structure from institutional signals.**

Radon is a market-structure reconstruction system that detects institutional positioning and turns it into convex options trades using dark pool flow, volatility signals, and cross-asset positioning data.

- Detects hidden positioning through dark pool, options flow, and cross-asset signals
- Evaluates every trade through a strict three-gate framework
- Generates portfolio, scan, and scenario reports with a real-time trading terminal on top

**No narrative trades. No TA trades. Flow signal or nothing.**

---

## What Radon Does

Radon reconstructs market structure from multiple institutional signals and converts that information into executable trade ideas, risk reports, and portfolio decisions.

**Inputs**

- Dark pool and OTC flow from [Unusual Whales](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64)
- Options flow, volatility surface, and open-interest change data
- Real-time quotes and options chains from [Interactive Brokers](https://ibkr.com/referral/joseph5632)
- Cross-asset volatility relationships, CTA positioning, and analyst context

**Processing**

- Signal detection and scoring
- Strategy-specific modeling
- Convex options structure design
- Fractional Kelly sizing and portfolio risk checks

**Outputs**

- Trade candidates and evaluations
- HTML reports for scans, portfolio state, and stress tests
- Live portfolio and order-state monitoring in the web terminal
- Execution and post-trade management through [Interactive Brokers](https://ibkr.com/referral/joseph5632)

## Trade Validation Framework

Every trade must pass three sequential gates. If any gate fails, no trade is taken.

### 1. Convexity

Potential gain must be at least **2x potential loss**.

- Default posture: defined-risk structures
- Typical structures: long options, vertical spreads, calendars
- Exception: risk reversals are explicit manager-override trades

### 2. Edge

A trade needs a **specific, data-backed signal** that has not fully moved price yet.

- Dark pool accumulation or distribution
- LEAP implied volatility mispricing
- Cross-asset volatility divergence
- Credit-volatility dislocations or crash-risk regime shifts

### 3. Risk Management

Trades are sized using **fractional Kelly** with hard limits.

- Max position size: **2.5% of bankroll**
- No pyramiding into weak signals
- Portfolio-level exposure is monitored continuously

## Strategies

Six active strategies drive the system.

| Strategy | Signal | Market Inefficiency | Typical Structure | Timeframe | Risk |
|----------|--------|---------------------|-------------------|-----------|------|
| **Dark Pool Flow** | Institutional accumulation or distribution | Price lag versus hidden liquidity | Calls, puts, vertical spreads | 2-6 weeks | Defined |
| **LEAP IV Mispricing** | Realized vol materially above long-dated IV | Long-dated volatility underpricing | Long LEAPs, diagonals | Weeks-9 months | Defined |
| **GARCH Convergence** | Cross-asset vol repricing lag | Surface adjustment is slower than regime change | Calendars, verticals | 2-8 weeks | Defined |
| **Risk Reversal** | Skew distortion between puts and calls | Put demand richer than equivalent call demand | Risk reversal | 2-8 weeks | Undefined |
| **Volatility-Credit Gap (VCG-R)** | VIX>28 + VCG>2.5σ divergence | Credit is lagging elevated vol stress | HYG/JNK puts, bear put spreads | 1-5 days | Defined |
| **Crash Risk Index (CRI)** | CTA deleveraging plus COR1M implied-correlation stress | Systematic positioning unwind | Index puts, tactical hedges | 3-5 days | Defined |

Full strategy specs live in [docs/strategies.md](docs/strategies.md). VCG research notes live in [docs/cross_asset_volatility_credit_gap_spec_(VCG).md](docs/cross_asset_volatility_credit_gap_spec_(VCG).md).

## System Architecture

```text
Interactive Brokers ----\
Unusual Whales ---------+--> Signal Detection Engine --> Strategy Evaluation
MenthorQ / CTA Data ----/                                  |
Exa / Research ---------/                                  v
                                                   Convex Structure Builder
                                                            |
                                                            v
                                                   Kelly Position Sizing
                                                            |
                                                            v
                                                   Execution / Monitoring
                                                            |
                                                            v
                                                       Radon Terminal
```

At a high level:

- `scripts/` contains scanners, evaluators, pricing logic, reporting, and broker integrations
- `web/` contains the Next.js terminal for portfolio, performance, flow, orders, regime, and AI-assisted workflows
- `site/` contains the standalone marketing website
- `data/` holds runtime artifacts and scan outputs

## Quick Start

**Prerequisites**

- Python `3.13` (Python 3.14 has ib_insync/eventkit incompatibility)
- Node.js `18+`
- [Interactive Brokers](https://ibkr.com/referral/joseph5632) Gateway (cloud via Tailscale, Docker, or local TWS)
- [Unusual Whales](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64) API access

**Install and run**

```bash
git clone https://github.com/joemccann/radon.git
cd radon
pip install -r requirements.txt
cd web && npm install && cd ..
python scripts/ib_sync.py
python scripts/scanner.py --top 15
```

## Setup Details

### Environment Variables

**Web app** in `web/.env`:

```bash
ANTHROPIC_API_KEY=your-anthropic-key
UW_TOKEN=your-unusual-whales-key
EXA_API_KEY=your-exa-key
```

**Python scripts and IB Gateway** in the project root `.env`:

```bash
MENTHORQ_USER=your-menthorq-email
MENTHORQ_PASS=your-menthorq-password

# IB Gateway connection
IB_GATEWAY_HOST=ib-gateway    # Cloud (Tailscale MagicDNS) or 127.0.0.1 (local)
IB_GATEWAY_PORT=4001
IB_GATEWAY_MODE=cloud          # "cloud" | "docker" | "launchd"
```

The dedicated CTA sync service and wrapper scripts source the project root `.env` directly. Keep MenthorQ credentials there so the scheduled `4:15 PM ET` and `5:00 PM ET` CTA runs, plus any `RunAtLoad` catch-up execution after reboot/login/wake, use the same auth context as manual CLI fetches.
`scripts/run_cta_sync.sh` now parses `.env` values literally instead of shell-sourcing them, so unquoted secrets containing shell metacharacters such as `$` remain intact in the scheduled/background CTA path.

**Optional shell exports**:

```bash
export XAI_API_KEY="your-xai-api-key"
```

### Additional Dependencies

MenthorQ-based workflows require Playwright and `httpx`:

```bash
pip install playwright httpx
playwright install chromium
```

[Interactive Brokers](https://ibkr.com/referral/joseph5632) connects on port `4001` (Gateway) or `7497` (TWS). No broker API key is required, but a Gateway instance must be reachable before live workflows. See the IB Gateway section below for cloud, Docker, and local setup options.

## Radon Terminal

Radon includes a real-time trading terminal built with **Next.js 16**. It streams [Interactive Brokers](https://ibkr.com/referral/joseph5632) prices, computes live greeks, visualizes portfolio exposures, and serves as the operator interface for scans, evaluation, and monitoring.

```bash
cd web
npm install
npm run dev
```

Visit `http://localhost:3000`.

**Key capabilities**

- Real-time price streaming with live greeks
- Shared quote telemetry across ticker, instrument, and modify-order views with `BID`, `MID`, `ASK`, and `SPREAD` rendered in a single layout contract; spread displays use raw quote width plus midpoint percent
- Multi-leg position monitoring and per-leg P&L
- YTD portfolio performance analytics with reconstructed institutional metrics that revalidate against the latest workspace portfolio sync, and a route-side ET-session refresh guard so stale prior-session `portfolio.json` snapshots do not block the current day’s reconstruction
- Closed-market route mounts still render cached portfolio, performance, regime, and internals data immediately while background sync remains paused outside market hours
- Shared `/regime` strip renderer with a responsive `5-up -> 3x2 -> stacked telemetry rail` contract so label, value, delta, and context remain readable on narrower viewports
- Regime history charts with cached 20-session RVOL and COR1M context
- RVOL/COR1M relationship view with spread, quadrant state, and normalized divergence
- Order management, including combo spread workflows
- Flow analysis, regime views, and thesis checks
- AI chat interface for command execution and analysis

### RVOL/COR1M Relationship States

On `/regime`, the relationship view classifies the latest RVOL and COR1M point against their own rolling 20-session means. The labels are relative-state labels, not fixed threshold buckets.

| State | Rule | Meaning |
|-------|------|---------|
| **Systemic Panic** | RVOL >= 20-session mean and COR1M >= 20-session mean | Realized volatility is elevated and the options market expects broad index constituents to move together. This is the most defensive state: stress is already in the tape and correlation risk is still being bid. |
| **Fragile Calm** | RVOL < 20-session mean and COR1M >= 20-session mean | Realized volatility looks calm, but implied correlation is elevated. The market is quiet on the surface while options still price herd behavior or crash-risk demand. |
| **Stock Picker's Market** | RVOL >= 20-session mean and COR1M < 20-session mean | Realized volatility is elevated, but implied correlation is still contained. Moves are happening, but they are not yet being priced as full-system lockstep stress. |
| **Goldilocks** | RVOL < 20-session mean and COR1M < 20-session mean | Both realized volatility and implied correlation are below their recent norms. This is the cleanest diversification backdrop in the relationship model. |

Implementation note: when live data is available, the latest relationship-state calculation uses the current intraday RVOL and/or live COR1M value on top of the cached 20-session history.

Responsive note: the live-strip cards on `/regime` now share a dedicated renderer that holds a fixed information hierarchy across widths. Desktop stays five-up, narrower desktop/tablet collapses to a symmetric `3 x 2`, and the small-screen stack switches each row to a compact telemetry rail so the delta arrow, context text, and timestamp stay scan-friendly instead of collapsing into dead whitespace.

## Marketing Site

The repo also includes a standalone Next.js site in `site/`.

```bash
cd site
npm install
npm run dev
```

Set `NEXT_PUBLIC_SITE_URL` in the site environment so `canonical`, JSON-LD, `robots.txt`, and `sitemap.xml` all reference the production hostname correctly.

For Vercel, the project should use `site/` as the Root Directory. The site app includes an ignored-build step in [site/vercel.json](/Users/joemccann/dev/apps/finance/radon/site/vercel.json) so pushes only trigger a site deploy when files under `site/` changed.

The marketing app is intentionally separate from `web/`: it carries the Radon landing-page narrative and its own deployment guardrails. To verify the site locally without colliding with another live Next.js process:

```bash
cd site
npm run lint
NEXT_DIST_DIR=.next-build npm run build
python3.13 scripts/seo_audit_report.py
```

`site/scripts/seo_audit_report.py` audits the rendered page metadata plus `robots.txt`, `sitemap.xml`, `manifest.webmanifest`, and the Open Graph/Twitter image routes, then writes a branded HTML report to `reports/`.

To generate an operator-facing SEO report against a live local instance:

```bash
python3.13 scripts/site_seo_audit.py --url http://127.0.0.1:3333 --open
```

If local port binding is unavailable, build the static site and point the audit at `site/.next-build/server/app` instead.

## Example Workflow

1. Run `scan` to surface dark pool and regime-aware candidates.
2. Run `evaluate NVDA` to execute the full seven-milestone validation flow.
3. If edge passes, design a convex structure and size it with Kelly constraints.
4. Send or stage the trade through [Interactive Brokers](https://ibkr.com/referral/joseph5632).
5. Monitor the position in the Radon Terminal and portfolio report.

## CLI Commands

### Scanning

| Command | Description |
|---------|-------------|
| `scan` | Watchlist dark pool flow scan with CRI regime overlay and HTML report |
| `discover` | Market-wide or targeted discovery scan for new candidates |
| `leap-scan [TICKERS]` | Find LEAP IV mispricing opportunities |
| `garch-convergence [TICKERS]` | Cross-asset implied-versus-realized volatility divergence scan |
| `seasonal [TICKERS]` | Monthly seasonality analysis from EquityClock |
| `analyst-ratings [TICKERS]` | Ratings, price targets, and recent changes |

### Evaluation And Risk

| Command | Description |
|---------|-------------|
| `evaluate [TICKER]` | Full seven-milestone trade evaluation |
| `stress-test` | Interactive bear/base/bull scenario report for the current portfolio |
| `risk-reversal [TICKER]` | IV-skew analysis for directional risk-reversal structures |
| `vcg` | VCG-R scan — VIX/VVIX/HYG regression, risk-off (VIX>28+VCG>2.5), severity tiers |
| `cri-scan` | Crash Risk Index with CTA exposure model |

### Portfolio And Operations

| Command | Description |
|---------|-------------|
| `portfolio` | Live portfolio report with dark pool thesis checks |
| `free-trade` | Analyze multi-leg positions for free-trade progression |
| `journal` | View recent trade log entries |
| `sync` | Pull live portfolio data from [Interactive Brokers](https://ibkr.com/referral/joseph5632) |
| `blotter` | Today's fills, grouped spreads, and commission totals |
| `blotter-history` | Historical trades via IB Flex Query |

### Research And System

| Command | Description |
|---------|-------------|
| `strategies` | Show the strategy registry |
| `menthorq-cta` | Fetch or backfill institutional CTA positioning data manually |
| `x-scan [@ACCOUNT]` | Fetch X sentiment through xAI |
| `x-scan-browser [@ACCOUNT]` | Fetch X sentiment through browser scraping |
| `commands` | Display the full command registry |

## Project Structure

```text
radon/
├── scripts/              # Python scanners, evaluators, broker integrations
│   ├── clients/          # Broker and data-provider adapters
│   ├── monitor_daemon/   # Background fill/exit/rebalance daemon
│   ├── benchmarks/       # Performance benchmarks (scanner timing)
│   └── tests/            # Python test suite
├── web/                  # Next.js terminal
│   ├── components/       # Terminal UI components
│   └── app/              # Next.js routes and API
├── docs/                 # Strategy and implementation documentation
│   └── autoresearch/     # Benchmark results and optimization notes
├── tasks/                # Plans, progress reports, and task tracking
├── brand/                # Radon design system and tokens
├── data/                 # Runtime data and generated artifacts
├── config/               # launchd and service configuration
├── logs/                 # Daemon logs (auto-rotated, gitignored)
├── requirements.txt      # Python dependencies
├── CLAUDE.md             # Agent and workflow rules
└── .pi/                  # Command registry and agent skills
```

## Data Sources

Market-data priority is intentionally strict:

1. **[Interactive Brokers](https://ibkr.com/referral/joseph5632)** for real-time quotes, options chains, and portfolio state
2. **[Unusual Whales](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64)** for dark pool flow, sweeps, options flow, and analyst data
3. **Exa** for company and market research
4. **Cboe official index feeds** for COR1M historical fallback before any generic web source
5. **Yahoo Finance** as a strict last-resort fallback

Auxiliary sources:

- **MenthorQ** for CTA positioning used in CRI analysis
- **xAI / browser scraping** for X-account sentiment workflows

## Testing

Radon includes Python, frontend, and end-to-end test coverage.

- **Python**: `pytest` for scanners, evaluation logic, utilities, and adapters
- **Frontend**: `Vitest` for web logic
- **E2E**: `Playwright` for browser workflows

```bash
python3.13 scripts/run_pytest_affected.py
python3.13 scripts/run_pytest_affected.py --files scripts/ib_sync.py scripts/tests/test_combo_entry_date.py -- -q
python -m pytest scripts/tests/ -v
cd web && npm test
cd web && npx playwright test
```

For scoped Python work, prefer `scripts/run_pytest_affected.py` over a full repo `pytest` run. The helper resolves changed Python files to the matching affected tests under `scripts/tests/` and `scripts/trade_blotter/`, and skips pytest entirely when the current change set has no Python impact.

Unit tests use mocked API calls where possible, so most development work does not require a live IB or [Unusual Whales](https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64) connection.

Order-route integration coverage now includes a dedicated FastAPI test harness:

- `web/tests/order-e2e.test.ts` boots an isolated test-mode FastAPI instance through `web/tests/fastapiHarness.ts`
- the harness sets `RADON_API_TEST_MODE=1`, points `RADON_API_URL` at the isolated server, and never reuses the live broker-backed `localhost:8321` process unless that server explicitly reports `test_mode: true`
- test mode disables IB Gateway / pool startup and stubs order placement, modify, cancel, and refresh endpoints so the Vitest suite does not touch an active IBC or IB session

## Services

The repo includes background-service support for the live trading environment:

| Service | Purpose |
|---------|---------|
| IB Gateway (cloud/Docker/launchd) | Broker session for live quotes, execution, and reports — cloud mode via Tailscale, Docker with auto-restart, or legacy IBC launchd |
| CRI scan service | Refreshes crash-risk regime data intraday and writes atomic CRI cache snapshots |
| CTA sync service | Refreshes the latest closed-session MenthorQ CTA cache at `4:15 PM ET` and `5:00 PM ET`, with `RunAtLoad` catch-up after reboot/login/wake, and writes machine-readable health state for stale-data detection |
| Monitor daemon | Tracks fills and exit orders during market hours, plus off-hours preset rebalance and Flex token checks (logs auto-rotated at 10MB) |
| Data refresh services | Keeps portfolio and order-state data current and repairs post-close CRI cache history when needed |

Historical setup helpers remain in `scripts/`, and the broader implementation notes live in [docs/implement.md](docs/implement.md).

CTA freshness is now an explicit contract:

- `data/menthorq_cache/cta_{DATE}.json` remains the daily cache artifact.
- `data/menthorq_cache/health/cta-sync-latest.json` is the primary machine-readable health record, and `data/menthorq_cache/health/history/cta-sync-*.json` preserves run history. For older tooling, the latest record is also mirrored to `data/service_health/cta-sync.json`.
- `scripts/run_cta_sync.sh` is the launchd-safe wrapper. It resolves the repo Python runtime, parses the root `.env` literally without shell expansion, and delegates to `scripts/cta_sync_service.py`.
- `/api/menthorq/cta` compares the latest cache date against the latest closed trading day, triggers one background CTA sync when stale, and returns explicit `cache_meta` plus `sync_health` metadata (with a `sync_status` compatibility alias) so `/cta` can show stale/degraded state instead of silently presenting old data as current.

For the `/regime` RVOL/COR1M chart, the CRI cache now preserves enough trailing SPY closes to rebuild the full prior 20 sessions of realized volatility. COR1M history now falls back to the official Cboe dashboard feed before Yahoo, and the API prefers the richer CRI cache candidate when scheduled snapshots lag and backfills missing `history[].realized_vol` values from cached closes before rendering the chart.

For `/internals`, the skew charts use the live `/internals/skew-history` backfill only during active ET market hours. On weekends and other closed sessions, `/api/internals` skips the live skew fetch and serves the newest shared long-range cache artifact from `data/cache/internals_skew_history_*.json` so the page keeps its full SPX/NDX history without attempting a non-trading-day refresh.

### IB Gateway

Three deployment modes controlled by `IB_GATEWAY_MODE` in the root `.env`:

| Mode | Description |
|------|-------------|
| **`cloud`** (default) | Gateway runs on a Hetzner VM via Tailscale MagicDNS at `ib-gateway:4001`. No local restart capability — health check is TCP port probe only. |
| `docker` | Local Docker Compose with `restart: unless-stopped` and healthcheck. |
| `launchd` | Legacy IBC launchd service on macOS. |

**Cloud setup** requires Tailscale on both the Gateway host and development machine. The Gateway host runs `ghcr.io/gnzsnz/ib-gateway` in Docker with IBC for automated 2FA handling.

**How it connects:** `ib_client.py` loads the root `.env` via `dotenv` at import time, setting `DEFAULT_HOST` before any module reads it. The Node WS relay (`ib_realtime_server.js`) also loads `.env` at startup. All scripts use `DEFAULT_HOST` — no hardcoded `127.0.0.1` in IB connection code.

**Connection pool:** FastAPI maintains three persistent connections (sync=3, orders=4, data=5) with retry (3 attempts, 2s backoff) and 1s stagger between roles.

**Troubleshooting:**

```bash
# Health check
curl -s http://localhost:8321/health | python3.13 -m json.tool

# Gateway reachable?
bash -c 'echo > /dev/tcp/ib-gateway/4001' && echo OK || echo FAIL

# Check connections on remote host
ssh root@ib-gateway "ss -tnp | grep 4001"

# Test a fresh client ID
python3.13 -c "from ib_insync import IB; ib=IB(); ib.connect('ib-gateway',4001,clientId=99,timeout=10); print('OK'); ib.disconnect()"
```

**Rollback to local:** Set `IB_GATEWAY_HOST=127.0.0.1` and `IB_GATEWAY_MODE=docker` (or `launchd`) in `.env`.

## Glossary

| Term | Definition |
|------|------------|
| **Convexity** | An asymmetric payoff where expected upside materially exceeds downside |
| **CRI** | Crash Risk Index, a composite crash-risk and deleveraging model |
| **CTA** | Commodity Trading Advisor, typically systematic trend-following funds |
| **Dark Pool** | Private off-exchange venue used for institutional trading |
| **Edge** | A specific reason the market is mispricing an outcome |
| **Kelly Criterion** | Position-sizing framework used to scale exposure to edge and odds |
| **VCG-R** | Volatility-Credit Gap, a divergence model — VIX>28 + VCG>2.5σ triggers risk-off |
