# Radon Web

Next.js dashboard with real-time IB pricing and Claude-powered conversational interface.

## Prerequisites

- Node.js 20+
- Python 3.13 (Python 3.14 has ib_insync/eventkit incompatibility)
- Interactive Brokers TWS or IB Gateway running
- API keys in `web/.env`

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Start everything (Next.js + IB price server)
npm run dev

# 4. Open http://localhost:3000
```

The `npm run dev` command starts four services:
- Next.js dev server (port 3000)
- IB real-time price server (port 8765)
- FastAPI server (port 8321) — Python script execution, IB Gateway auto-restart
- Market Ear newsfeed scraper (no port; polls `themarketear.com/newsfeed` every 120s via chrome-cdp)

**Authentication on localhost.** Clerk auth auto-bypasses on `localhost` / `127.0.0.1` / `::1` whenever `NODE_ENV !== "production"`, so `npm run dev` never hits the sign-in wall. `next build && next start` (production builds) still enforce Clerk. See [Authentication](#authentication) for details.

**Note:** Frontend data polling automatically respects market hours. During CLOSED market (weekends, holidays, overnight), all polls stop. During regular hours (9:30 AM - 4:00 PM ET) polling is most frequent. See [Market-Hours Polling](#market-hours-polling) for details.

**Disk-backed routes.** All Next.js GET handlers that read live disk state (`portfolio`, `journal`, `discover`, `flow-analysis`, `blotter`, `vcg`, `internals`, `performance`, `scanner`, `regime`, `gex`, `menthorq/cta`) export `dynamic = "force-dynamic"`; their corresponding hooks fetch with `cache: "no-store"`. Without those markers Next.js 16 statically prerenders the first GET and freezes it for the dev-server lifetime — the failure mode that surfaced "CTA CACHE STALE" while fresh data already existed on disk. The contract is enforced by `web/tests/api-routes-no-cache-contract.test.ts` (18 assertions).

## Architecture

### Data Flow

```
IB Gateway (4001)
  ├──▶ ib_sync.py ──▶ portfolio.json (positions, P&L, account)
  │
  └──▶ ib_realtime_server.js (port 8765)
         │
         ├──▶ Browser WebSocket (usePrices hook) ── live bid/ask/last
         └──▶ /api/prices POST (one-time snapshot)

FastAPI (port 8321)
  └──▶ Python scripts as async subprocesses (scanner, sync, perf, etc.)
```

### Pricing vs Sync (Separated)

| Component | Purpose | Update Frequency |
|-----------|---------|------------------|
| `ib_sync.py` | Portfolio positions, P&L, account values | Every 60 seconds (via FastAPI, **paused during CLOSED market**) |
| `ib_realtime_server.js` | Live bid/ask/last prices | Real-time (<1ms latency) |
| `scripts/api/server.py` | FastAPI bridge — runs Python scripts as async subprocesses | On-demand (API calls, **paused during CLOSED market**) |

## Market-Hours Polling

The frontend automatically adjusts polling intervals based on market state (OPEN, EXTENDED, CLOSED) computed from Eastern Time.

### Polling Behavior

| Market State | Time (ET) | Portfolio | Orders | Regime |
|--------------|-----------|-----------|--------|--------|
| **CLOSED** | Weekends / 8:00 PM – 4:00 AM | ❌ Paused | ❌ Paused | ❌ Paused |
| **EXTENDED** | 4:00 AM – 9:30 AM (premarket) | 30s ✅ | 30s ✅ | 5min ✅ |
| **OPEN** | 9:30 AM – 4:00 PM (regular) | 30s ✅ | 30s ✅ | 1min ✅ |
| **EXTENDED** | 4:00 PM – 8:00 PM (after hours) | 30s ✅ | 30s ✅ | 5min ✅ |

### Expected Savings

- **Weekends:** 41 API calls/60s → 0 API calls/60s (**-100% reduction**)
- **Overnight:** 328+ calls/night → 0 calls (**-100% reduction**)
- **Extended Regime:** 1-minute → 5-minute polling (**5x reduction**)

### Implementation

**Core hook:** `web/lib/useMarketHours.ts`
```typescript
import { useMarketHours, MarketState } from "@/lib/useMarketHours";

function PortfolioComponent() {
  const marketState = useMarketHours();  // Returns OPEN/EXTENDED/CLOSED

  // Use in data fetching hooks
  const { data, syncing } = usePortfolio(marketState !== MarketState.CLOSED);
  const { data: orders } = useOrders(marketState !== MarketState.CLOSED);

  // Regime uses adaptive intervals automatically
  const { data: regime } = useRegime(marketState);
}
```

All polling hooks automatically respect market hours:
- `usePortfolio()` - Always performs one cached `GET` on mount, then stops sync/polling when market is CLOSED
- `useOrders()` - Always performs one cached `GET` on mount, then stops IB sync/polling when market is CLOSED
- `useRegime()` - Always performs one cached `GET` on mount, then uses adaptive intervals (60s OPEN / 300s EXTENDED / 0 CLOSED)

### Backward Compatibility

All changes maintain backward compatibility:
- `useOrders()` defaults to `active=true`
- `usePortfolio()` defaults to `active=true`
- `useRegime(true)` converts boolean → `MarketState.OPEN`

## API Keys

Create `web/.env` from the template:

```bash
cp .env.example .env
```

**Required:**
- `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_API_KEY` or `CLAUDE_API_KEY`)
- `UW_TOKEN` - Unusual Whales API key

**Optional:**
- `ANTHROPIC_MODEL` - Model override
- `ANTHROPIC_API_URL` - API endpoint override
- `CEREBRAS_API_KEY` - Cerebras free-tier key for the Market Ear **text** tagger (`gpt-oss-120b` primary, `qwen-3-235b` fallback). Used for posts without images.
- `ANTHROPIC_API_KEY` - Anthropic key for the Market Ear **vision** tagger (`claude-haiku-4-5`, ~$0.003/post). Used for posts with images (chart-heavy Market Ear posts where the image carries more signal than the caption). Either key alone is sufficient — the scraper routes per-post and falls back to whichever tagger is configured. Without both, tag hydration is skipped (scraping continues; posts just stay untagged).
- `IB_REALTIME_WS_URL` - Server-side websocket URL used by `/api/prices` for one-time snapshots (default: `ws://localhost:8765`)
- `NEXT_PUBLIC_IB_REALTIME_WS_URL` - Browser websocket URL for direct realtime subscriptions (default: `ws://localhost:8765`)

## Authentication

The trading terminal uses Clerk for production auth, but **on localhost in development the sign-in wall is automatically bypassed** so `npm run dev` Just Works without ever touching Clerk.

| Path | When auth is enforced | When auth is skipped |
|------|------------------------|----------------------|
| Next.js middleware (`web/middleware.ts`) | `NODE_ENV === "production"` OR a non-localhost hostname | `localhost` / `127.0.0.1` / `::1` AND `NODE_ENV !== "production"` (auto), OR `RADON_AUTHLESS_TEST=1` (explicit, used by Playwright) |
| FastAPI (`scripts/api/auth.py`) | External requests with valid Clerk JWT in `Authorization: Bearer …` | `request.client.host` ∈ `{127.0.0.1, ::1}` (auto, covers Next.js → FastAPI server-to-server) |
| WebSocket (`scripts/api/ws_ticket.py`) | Single-use 30-second tickets minted from a Clerk session | n/a — ticket flow is required |

**Production safety.** `next build && next start` sets `NODE_ENV=production`, so the auto-bypass cannot fire even if the host happens to resolve as localhost. The two helper functions live in `web/middleware.ts`:

- `isLocalDevAuthBypassEnabled(url, nodeEnv?)` — auto, dev-only.
- `isLocalAuthlessTestBypassEnabled(url, flag?)` — explicit, used by Playwright via `RADON_AUTHLESS_TEST=1` in `web/playwright.config.ts`.

Both are exercised by `web/tests/middleware-authless.test.ts` (8 cases — IPv6 `[::1]` form, production blocks bypass, `"test"` env treated as non-production, etc.).

## Real-Time Pricing

### Start the Price Server

```bash
# Default settings
node ../scripts/ib_realtime_server.js

# Custom ports
node ../scripts/ib_realtime_server.js --port 8765 --ib-port 4001
```

### API Endpoint

**Stream prices (WebSocket):**
```
ws://localhost:8765

Message:
{"action": "subscribe", "symbols": ["AAPL", "MSFT", "NVDA"]}
```

Index subscriptions use the same websocket action with an `indexes` array:

```json
{"action":"subscribe","symbols":["SPY"],"indexes":[{"symbol":"VIX","exchange":"CBOE"},{"symbol":"VVIX","exchange":"CBOE"},{"symbol":"COR1M","exchange":"CBOE"}]}
```

The realtime server preserves the typed IB contract for stock, option, and index subscriptions as soon as the websocket subscription arrives, so reconnect and cold-restore flows resubscribe `/regime` indexes as CBOE indices instead of rebuilding them as stocks.

**Stale quote guard (`safeInitialState`):** When a new client subscribes to an option contract, the relay immediately sends back the most recently cached `PriceData` snapshot. If that snapshot is from a prior trading session (timestamp older than 8 hours), `bid`/`ask`/`bidSize`/`askSize` are nulled before the initial push so the chain displays `---` instead of stale prices from yesterday's session. Fresh frozen-data ticks from the new `reqMktData` call repopulate the quote within seconds.

**Snapshot (one-time):**
```bash
curl -X POST http://localhost:3000/api/prices \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["AAPL", "MSFT"]}'
```

`GET /api/prices` is intentionally deprecated (`405`) because real-time streaming now uses direct WebSocket subscriptions to the Node server.
Node now owns the live stream directly; Next.js only provides one-time snapshot support.

### React Hook

```tsx
import { usePrices } from "@/lib/usePrices";

function PriceDisplay() {
  const { prices, connected, error } = usePrices({
    symbols: ["AAPL", "MSFT", "NVDA"],
    onPriceUpdate: (update) => {
      console.log(`${update.symbol}: $${update.data.last}`);
    }
  });

  return (
    <div>
      {Object.entries(prices).map(([symbol, data]) => (
        <div key={symbol}>
          {symbol}: ${data.last} (bid: {data.bid} / ask: {data.ask})
        </div>
      ))}
    </div>
  );
}
```

### WebSocket Protocol

```json
// Client → Server
{"action": "subscribe", "symbols": ["AAPL", "MSFT"]}
{"action": "subscribe", "symbols": ["SPY"], "indexes": [{"symbol": "VIX", "exchange": "CBOE"}]}
{"action": "unsubscribe", "symbols": ["AAPL"]}
{"action": "snapshot", "symbols": ["NVDA"]}
{"action": "ping"}

// Server → Client
{"type": "price", "symbol": "AAPL", "data": {"last": 175.50, "bid": 175.48, ...}}
{"type": "subscribed", "symbols": ["AAPL", "MSFT"]}
{"type": "status", "ib_connected": true}
{"type": "pong"}
```

## API Routes

### Portfolio and Performance

| Route | Method | Description |
|-------|--------|-------------|
| `/api/portfolio` | GET, POST | Positions and exposure (GET=read, POST=IB sync). Stale-while-revalidate. **Frontend always does one cached GET; POST sync/polling stops during CLOSED market.** |
| `/api/performance` | GET, POST | YTD performance metrics (hidden — see `docs/performance-reconstruction.md`) |
| `/api/blotter` | GET, POST | Today's fills and closed trades |
| `/api/journal` | GET | Trade log (append-only) |
| `/api/journal/sync` | POST | Import new IB trades from reconciliation |
| `/api/attribution` | GET | P&L attribution data |

### Orders

| Route | Method | Description |
|-------|--------|-------------|
| `/api/orders` | GET, POST | Open/executed orders (GET=read, POST=IB sync). **Frontend poll stops during CLOSED market.** |
| `/api/orders/place` | POST | Place stock/option/combo orders (naked short guard enforced) |
| `/api/orders/cancel` | POST | Cancel by orderId or permId |
| `/api/orders/modify` | POST | Modify price/quantity/outsideRth or replace combo |

### Market Data and Regime

| Route | Method | Description |
|-------|--------|-------------|
| `/api/prices` | POST | One-time price snapshot (GET deprecated) |
| `/api/previous-close` | POST | Previous-day closing prices (IB → UW → Yahoo fallback) |
| `/api/regime` | GET, POST | CRI regime data. **Frontend always does one cached GET; polling uses adaptive intervals: 1min (OPEN), 5min (EXTENDED), paused (CLOSED).** |
| `/api/internals` | GET, POST | Market internals and skew history. **Frontend always does one cached GET; polling uses adaptive intervals: 1min (OPEN), 5min (EXTENDED), paused (CLOSED).** |
| `/api/scanner` | GET, POST | Watchlist scan results with cache metadata |
| `/api/discover` | GET, POST | Market-wide flow scanning |
| `/api/flow-analysis` | GET, POST | Portfolio flow analysis (supports/against/watch) |
| `/api/menthorq/cta` | GET | CTA positioning with sync health metadata |
| `/api/flex-token` | GET | IB Flex token expiry status |

### Ticker Data

| Route | Method | Description |
|-------|--------|-------------|
| `/api/ticker/info` | GET | Company info (UW + Exa, cached) |
| `/api/ticker/news` | GET | News headlines (UW → Yahoo fallback) |
| `/api/ticker/ratings` | GET | Analyst ratings and price targets |
| `/api/ticker/seasonality` | GET | Monthly seasonality (UW → EquityClock Vision → cache) |
| `/api/options/chain` | GET | Options chain for symbol |
| `/api/options/expirations` | GET | Available expiration dates |

### AI and Commands

| Route | Method | Description |
|-------|--------|-------------|
| `/api/assistant` | POST | Claude conversation |
| `/api/pi` | POST | Execute PI commands (scanner, evaluate, etc.) |

### Share Cards

| Route | Method | Description |
|-------|--------|-------------|
| `/api/regime/share` | POST | Generate regime PNG card |
| `/api/menthorq/cta/share` | POST | Generate CTA PNG card |
| `/api/internals/share` | POST | Generate internals PNG card |

## Tests

```bash
# Unit tests (Vitest)
npm test

# E2E tests (Playwright)
npx playwright test

# Mock mode (no API keys needed)
ASSISTANT_MOCK=1 npm test
```

**Unit tests** (`web/tests/`): Route logic, price utilities, naked short guard, WebSocket state machine, regime/CRI staleness, CTA freshness, share cards, P&L calculations, day change, exposure breakdown, stale option quote guard (`stale-option-quote-guard.test.ts`).

**E2E tests** (`web/e2e/`): Regime live index streaming, CTA stale banners, share P&L rendering, options chain sticky headers, market-closed EOD values, chain strikes-per-side selector including ±100 and All modes (`chain-strikes-selector.spec.ts`).

### IB Connectivity Tests

```bash
python3.13 ../scripts/test_ib_realtime.py            # Full test
python3.13 ../scripts/test_ib_realtime.py --ib-only   # IB only
python3.13 ../scripts/test_ib_realtime.py --ws-only   # WebSocket only
```

## Development

```bash
# Start everything (Next.js + IB WS relay + FastAPI)
npm run dev

# Start Next.js only (no real-time prices, no FastAPI)
npm run dev:next

# Start IB price server only
npm run dev:prices

# Health check (FastAPI + IB Gateway status)
curl http://localhost:8321/health

# Build for production
npm run build

# Start production server
npm start

# Lint
npm run lint

# Test IB connectivity
npm run test:ib
```

**Note:** When developing on weekends or outside market hours, data polling will be paused. To test live polling behavior:
- Use browser DevTools console to simulate different times (modify `useMarketHours.ts` temporarily)
- Wait for market hours (9:30 AM - 4:00 PM ET on weekdays) to observe full-rate polling

## Documentation

API specifications, strategy docs, and implementation notes live in the project root `docs/` directory (`../docs/` from here):

| File | Description |
|------|-------------|
| `docs/unusual_whales_api.md` | Unusual Whales API reference |
| `docs/unusual_whales_api_spec.yaml` | UW OpenAPI spec |
| `docs/ib_tws_api.md` | Interactive Brokers TWS/Gateway API |
| `docs/strategies.md` | Trading strategy documentation |
| `docs/status.md` | Current portfolio status and recent evaluations |
| `docs/plans.md` | Implementation plans |
| `docs/implement.md` | Implementation notes |
| `docs/prompt.md` | System prompt reference |
| `docs/performance-reconstruction.md` | Performance page analysis — TWR approaches tried, why shelved |

## Troubleshooting

### Verifying Market-Hours Polling

To verify that market-hours aware polling is working correctly:

1. **Open browser DevTools Network tab**
   - Open Chrome DevTools → Network tab
   - Filter by "Fetch/XHR"
   - Visit any page (e.g., `/portfolio`, `/orders`, `/regime`)

2. **On weekends (CLOSED market):**
   - Verify one initial cached `GET` per mounted route (`/api/portfolio`, `/api/orders`, `/api/regime`, `/api/internals`) and no recurring polling/POST sync afterward
   - Closed-market pages should render cached data instead of hanging on loading placeholders

3. **On weekdays:**
   - **4:00 AM ET (premarket):** Portfolio/orders polling at 30s, regime/internals at 5min
   - **9:30 AM ET (market open):** All polls resume at full rate (30s for portfolio/orders, 1min for regime/internals)
   - **4:00 PM ET (after hours):** Regime/internals reduce to 5min, portfolio/orders continue at 30s
   - **8:00 PM ET (market closed):** All polling stops

4. **Debug in console:**
   ```javascript
   // Check current market state
   // Open browser console and inspect Network tab timing
   ```

### IB Connection Issues

1. Ensure TWS or IB Gateway is running
2. Enable API: Configure → API → Settings → "Enable ActiveX and Socket Clients"
3. Check port: TWS Paper=7497, TWS Live=7496, Gateway=4001/4002

### Price Server Not Connecting

```bash
# Start the server with explicit IB port and verify startup logs
node ../scripts/ib_realtime_server.js --ib-port 4001
```

- Confirm logs include:
  - `IB realtime server listening on ws://0.0.0.0:8765`
  - `IB target 127.0.0.1:4001`
  - `IB connected` (once TWS/Gateway is available)

- `curl` is not a valid check for a WebSocket endpoint; use normal UI reconnect flow or a WebSocket client to validate connectivity.

### Rate Limiting (Yahoo Finance fallback)

If IB is unavailable, some features fall back to Yahoo Finance which has aggressive rate limits. Wait a few minutes and retry, or ensure IB is connected.
