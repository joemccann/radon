# Radon Web

Next.js dashboard with real-time IB pricing and Claude-powered conversational interface.

## Prerequisites

- Node.js 20+
- Python 3.9+ (for other IB scripts)
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

The `npm run dev` command starts three services:
- Next.js dev server (port 3000)
- IB real-time price server (port 8765)
- FastAPI server (port 8321) — Python script execution, IB Gateway auto-restart

## Architecture

### Data Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│   IB Gateway    │────▶│  ib_sync.py      │────▶│ portfolio   │
│   (TWS/4001)    │     │  (periodic sync) │     │   .json     │
└─────────────────┘     └──────────────────┘     └─────────────┘
        │
        │               ┌──────────────────┐     ┌─────────────┐
                        └──────────────▶│ ib_realtime_     │◀───▶│  WebSocket  │
                        │ server.js        │     │  Clients    │
                        │ (streaming)      │     └─────────────┘
                        └──────────────────┘
                                │
                                ▼
                        ┌──────────────────┐     ┌─────────────┐
                        │ /api/prices      │────▶│  usePrices  │
                        │ (snapshot only)  │     │  (React)    │
                        └──────────────────┘     └─────────────┘
```

### Pricing vs Sync (Separated)

| Component | Purpose | Update Frequency |
|-----------|---------|------------------|
| `ib_sync.py` | Portfolio positions, P&L, account values | Every 60 seconds (via FastAPI) |
| `ib_realtime_server.js` | Live bid/ask/last prices | Real-time (<1ms latency) |
| `scripts/api/server.py` | FastAPI bridge — runs Python scripts as async subprocesses | On-demand (API calls) |

### Portfolio Price Table Indicators

Portfolio tables now visually mark live price updates on each tick:

- `Last Price` and leg `market_price` cells flash briefly on change:
  - Green flash for an uptick
  - Red flash for a downtick
- Direction arrows remain visible after the flash:
  - Green up arrow for price increases
  - Red down arrow for price decreases
- Flash duration is `2.5s` to keep updates readable without being too aggressive.

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
- `IB_REALTIME_WS_URL` - Server-side websocket URL used by `/api/prices` for one-time snapshots (default: `ws://localhost:8765`)
- `NEXT_PUBLIC_IB_REALTIME_WS_URL` - Browser websocket URL for direct realtime subscriptions (default: `ws://localhost:8765`)

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

| Route | Method | Description |
|-------|--------|-------------|
| `/api/portfolio` | GET | Read portfolio.json |
| `/api/portfolio` | POST | Trigger IB sync |
| `/api/performance` | GET | Read cached YTD performance metrics, refresh `portfolio.json` through `ibSync` when it is behind the current ET session, rebuild when the cache lags the latest portfolio sync, and let `/performance` revalidate immediately after the shell portfolio snapshot advances |
| `/api/performance` | POST | Rebuild YTD performance metrics from the Python engine |
| `/api/menthorq/cta` | GET | Read the latest MenthorQ CTA cache, attach `cache_meta` plus `sync_health`/`sync_status`, and trigger one background CTA refresh when the latest closed trading day is missing |
| `/api/orders` | GET | Read open/executed orders |
| `/api/orders` | POST | Sync orders from IB |
| `/api/prices` | GET | Deprecated (real-time SSE removed) |
| `/api/prices` | POST | One-time price snapshot |
| `/api/assistant` | POST | Claude conversation |
| `/api/pi` | POST | Execute PI commands |

## Tests

```bash
# Run all tests
npm test

# Run with mock mode (no API keys needed)
ASSISTANT_MOCK=1 npm test
```

Tests cover:

- CTA freshness contract and `/cta` stale/degraded rendering in unit tests (`web/tests/cta-route-freshness.test.ts`, `web/tests/cta-page-freshness.test.ts`)
- CTA route compatibility coverage in `web/tests/menthorq-cta-route.test.ts`
- Browser CTA stale-banner coverage in `web/e2e/cta-stale-banner.spec.ts` and the `/cta` stale-state browser contract in `web/e2e/cta-page.spec.ts`
- `/regime` live index websocket coverage, including cold-start contract preservation and live `VIX` / `VVIX` / `COR1M` strip rendering in `web/tests/ib-index-stream-contracts.test.ts`, `web/tests/use-previous-close-indexes.test.ts`, `web/e2e/regime-live-index-streaming.spec.ts`, and `web/e2e/regime-live-index-stream.spec.ts`
- `/api/assistant` route (mock mode)
- PI command entrypoints (`fetch_ticker`, `fetch_flow`, `discover`, `scanner`)
- `kelly.py` output parsing
- Real-time price utilities and state management
- Share P&L popover defaults and share image rendering behavior (`web/e2e/share-pnl.spec.ts`, `web/tests/share-pnl.test.ts`, `/api/share/pnl`)

### Python Tests

```bash
# Test IB connectivity
python3 ../scripts/test_ib_realtime.py

# Test IB only (no WebSocket server needed)
python3 ../scripts/test_ib_realtime.py --ib-only

# Test WebSocket server only
python3 ../scripts/test_ib_realtime.py --ws-only
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

## Troubleshooting

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

### Recent UI Changes

- `f0f50e4` — Added persistent directional arrows and 2.5s flash highlighting for `Last Price` / `market_price` updates in portfolio tables.
