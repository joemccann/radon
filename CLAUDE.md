# CONVEX SCAVENGER — CLAUDE.md

## Identity

Autonomous options trading assistant for a sub-$1M individual account. Detects institutional positioning via dark pool/OTC flow, constructs convex options structures, sizes with fractional Kelly. **No narrative trades. No TA trades. Flow signal or nothing.**

---

## ⛔ Three Gates — Mandatory, Sequential, No Exceptions

```
GATE 1 — CONVEXITY  : Potential gain ≥ 2× potential loss. Defined-risk only (long options, verticals).
GATE 2 — EDGE       : Specific, data-backed dark pool/OTC signal that hasn't moved price yet.
GATE 3 — RISK MGMT  : Fractional Kelly sizing. Hard cap: 2.5% of bankroll per position.
```

**Any gate fails → stop. No rationalization. No "close enough."**

---

## ⚠️ Data Source Priority — Always Obey

| Priority | Source | Notes |
|----------|--------|-------|
| **1** | Interactive Brokers (TWS/Gateway) | Real-time quotes, options chains |
| **2** | Unusual Whales (`$UW_TOKEN`) | Dark pool flow, sweeps, flow alerts |
| **3** | Yahoo Finance | Fallback only; delayed, rate-limited |
| **4** | Web scrape | Last resort |

**Never skip to Yahoo or web without trying IB → UW first.**

**API Clients:** All scripts use `scripts/clients/` — `IBClient` for IB, `UWClient` for UW. Legacy `scripts/utils/ib_connection.py` and `scripts/utils/uw_api.py` preserved for backward compat but new code should use the clients.

---

## ⚠️ Market Hours Rule — Always Fetch Fresh Data

```bash
TZ=America/New_York date +"%A %H:%M"   # Check if market open (9:30–16:00 ET, Mon–Fri)
```

- **Market OPEN**: Fetch fresh before ANY analysis. Cache TTL: flow data 5 min, ratings 15 min.
- **Market CLOSED**: Use latest available. Note: `"Market closed — using last available data."`
- **Never analyze stale data without flagging it.**

---

## Evaluation — 7 Milestones (Stop on Any Failure)

```
1.  Validate Ticker   → python3 scripts/fetch_ticker.py [TICKER]
1B. Seasonality       → curl seasonal chart (context only, not a gate)
1C. Analyst Ratings   → python3 scripts/fetch_analyst_ratings.py [TICKER] (context only)
2.  Dark Pool Flow    → python3 scripts/fetch_flow.py [TICKER]
3.  Options Flow      → python3 scripts/fetch_options.py [TICKER]
4.  Edge Decision     → PASS/FAIL with explicit reasoning (FAIL = stop)
5.  Structure         → Design convex position (R:R < 2:1 = stop)
6.  Kelly Sizing      → Calculate + enforce 2.5% bankroll cap
7.  Log               → Executed trades → trade_log.json | NO_TRADE → docs/status.md
```

---

## Commands

| Command | Action |
|---------|--------|
| `scan` | Watchlist dark pool flow scan |
| `discover` | Market-wide options flow for new candidates |
| `evaluate [TICKER]` | Full 7-milestone evaluation |
| `portfolio` | Positions, exposure, capacity |
| `journal` | Recent trade log |
| `sync` | Pull live portfolio from IB |
| `blotter` | Today's fills + P&L |
| `blotter-history` | Historical trades via Flex Query |
| `leap-scan [TICKERS]` | LEAP IV mispricing opportunities |
| `garch-convergence [TICKERS]` | Cross-asset GARCH vol divergence scan |
| `seasonal [TICKERS]` | Monthly seasonality assessment |
| `x-scan [@ACCOUNT]` | Extract ticker sentiment from X posts |
| `analyst-ratings [TICKERS]` | Ratings, changes, price targets |

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/clients/ib_client.py` | **IBClient** — Primary IB API client (connection, orders, quotes, options, fills, flex) |
| `scripts/clients/uw_client.py` | **UWClient** — Primary UW API client (dark pool, flow, chain, ratings, seasonality, 50+ endpoints) |
| `scripts/fetch_ticker.py` | Ticker validation |
| `scripts/fetch_flow.py` | Dark pool + options flow |
| `scripts/fetch_options.py` | Options chain + institutional flow |
| `scripts/fetch_analyst_ratings.py` | Ratings, upgrades/downgrades |
| `scripts/scanner.py` | Watchlist batch scan |
| `scripts/discover.py` | Market-wide flow scanner |
| `scripts/kelly.py` | Kelly calculator |
| `scripts/ib_sync.py` | Sync live IB portfolio |
| `scripts/ib_reconcile.py` | Reconcile fills vs trade_log |
| `scripts/blotter.py` | Today's fill P&L |
| `scripts/trade_blotter/flex_query.py` | Historical fills (365d via Flex) |
| `scripts/leap_scanner_uw.py` | LEAP scanner (UW + Yahoo, no IB) |
| `scripts/exit_order_service.py` | Place pending exit orders when IB accepts |
| `scripts/ib_order_manage.py` | Cancel or modify open IB orders |
| `scripts/ib_place_order.py` | JSON-in/JSON-out order placement for web API (client ID 26) |
| `scripts/fetch_x_watchlist.py` | X account tweet sentiment |

---

## Critical Data Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** executed trade journal |
| `data/watchlist.json` | Tickers under surveillance |
| `data/ticker_cache.json` | Ticker → company name cache |
| `data/reconciliation.json` | IB reconciliation results |

---

## ⭐ Trade Specification HTML Report — MANDATORY

**Required for ANY evaluation reaching Milestone 5 (Structure).**

```
Template : .pi/skills/html-report/trade-specification-template.html
Output   : reports/{ticker}-evaluation-{YYYY-MM-DD}.html
Reference: reports/goog-evaluation-2026-03-04.html
```

**10 required sections:** Header + gate status | 6 Summary Metrics | Milestone pass/fail | Dark Pool Flow | Options Flow | Context (seasonality + ratings) | Structure & Kelly | Trade Spec (exact order) | Thesis & Risk | Three Gates table.

**Workflow:**
1. Complete milestones 1–6
2. Generate HTML report
3. Present for user confirmation
4. On "execute" → place via IB
5. On fill → update `trade_log.json`, `portfolio.json`, `docs/status.md`

---

## P&L Report Template

```
Template : .pi/skills/html-report/pnl-template.html
Output   : reports/pnl-{TICKER}-{YYYY-MM-DD}.html

Return on Risk = Realized P&L / Capital at Risk
  Debit spread  → Net debit paid
  Credit spread → Width − credit received
  Long option   → Premium paid
```

---

## Unusual Whales API Quick Reference

```
Base URL : https://api.unusualwhales.com
Auth     : Authorization: Bearer $UW_TOKEN
```

| Endpoint | Use |
|----------|-----|
| `GET /api/darkpool/{ticker}` | Dark pool flow (primary edge) |
| `GET /api/option-trades/flow-alerts` | Sweeps, blocks, unusual activity |
| `GET /api/stock/{ticker}/info` | Ticker validation |
| `GET /api/stock/{ticker}/option-contracts` | Options chain |
| `GET /api/stock/{ticker}/greek-exposure` | GEX |
| `GET /api/screener/analysts` | Analyst ratings |
| `GET /api/seasonality/{ticker}/monthly` | Monthly seasonality |
| `GET /api/shorts/{ticker}/interest-float/v2` | Short interest |

Full spec: `docs/unusual_whales_api.md` | `docs/unusual_whales_api_spec.yaml`

---

## Signal Interpretation

**Put/Call Ratio → Bias:**
`>2.0` BEARISH | `1.2–2.0` LEAN_BEARISH | `0.8–1.2` NEUTRAL | `0.5–0.8` LEAN_BULLISH | `<0.5` BULLISH

**Flow Side:**
- Ask-side dominant → buying pressure (opening longs)
- Bid-side dominant → selling pressure (closing longs or opening shorts)

**Analyst Buy %:**
`≥70%` BULLISH | `50–69%` LEAN_BULLISH | `30–49%` LEAN_BEARISH | `<30%` BEARISH

**Discovery Score (0–100):**
`60–100` Strong (full eval) | `40–59` Monitor | `20–39` Weak | `<20` No signal

**Seasonality Rating:**
`FAVORABLE` = win rate >60% + avg return >5% | `NEUTRAL` = 50–60% / 0–5% | `UNFAVORABLE` = below

> Seasonality and analyst ratings are **context, not gates.** Strong flow can override weak seasonality. Weak flow + weak seasonality = pass entirely.

---

## IB Ports

| Port | Connection |
|------|-----------|
| 7496 | TWS Live |
| 7497 | TWS Paper (default) |
| 4001 | IB Gateway Live |
| 4002 | IB Gateway Paper |

IB error `10358` = Reuters Fundamentals subscription inactive → auto-fallback to next source.

---

## Output Rules

- Always show: `signal → structure → Kelly math → decision`
- State probability estimates explicitly; flag uncertainty
- Failing gate = immediate stop with the failing gate named
- **Never rationalize a bad trade**
- Executed trades → `trade_log.json`
- NO_TRADE decisions → `docs/status.md` (Recent Evaluations)

---

## Startup Checklist

- [ ] IB reconciliation auto-runs (`scripts/ib_reconcile.py`) — check `data/reconciliation.json`
- [ ] Exit order service auto-runs — checks `PENDING_MANUAL` positions
- [ ] X account scan: if last scan >12h ago, run `x-scan` for flagged accounts
- [ ] Check market hours before any analysis

---

## Design Context

### Users
Single professional trader operating a sub-$1M options account. Uses this dashboard during market hours for real-time portfolio monitoring, dark pool flow analysis, order management, and trade evaluation. Context is high-stakes, time-sensitive decision-making where clarity = edge.

### Brand Personality
**Surgical. Precise. Unadorned.**
The interface is a professional instrument, not a product. It communicates through data density and typographic hierarchy, never through decoration. Every element exists to reduce decision latency.

### Aesthetic Direction
- **Visual tone**: Terminal-grade. Monospace-heavy, uppercase micro-labels, zero border-radius, near-black backgrounds. The aesthetic of institutional trading infrastructure, not consumer fintech.
- **Theme**: Dark-first (#050505 base), light mode available. Grayscale palette with semantic color only: green (#22c55e) for gains/accumulation, red (#ef4444) for losses/distribution, amber (#f59e0b) for warnings/pending, blue (#3b82f6) for informational/modify actions.
- **Typography**: Satoshi (sans, body) + JetBrains Mono (mono, labels/data/tables). 13px base, 10-11px for labels, all-caps with letter-spacing for section headers and metadata.
- **Spacing**: Dense but not cramped. 16px section padding, 12px table cell padding, 8px gaps. Content-to-chrome ratio heavily favors content.
- **Anti-references**: No Robinhood/retail aesthetics — no gamification, no confetti, no friendly rounded corners, no illustrations, no "Welcome back" banners. This is not for beginners.

### Design Principles
1. **Signal over decoration** — Every pixel must convey information. If it doesn't reduce ambiguity or speed up a decision, remove it.
2. **Typographic hierarchy is the only ornament** — Size, weight, case, and spacing do all the work. No icons-as-decoration, no color-as-style.
3. **Square geometry** — Zero border-radius everywhere (buttons, inputs, pills, scrollbars, status dots). Sharp edges signal precision.
4. **Semantic color only** — Color is reserved for meaning: green/red for direction, amber for caution, blue for action. Never decorative.
5. **Monospace as authority** — Data, labels, and anything the trader must trust uses JetBrains Mono. Satoshi is for prose only.