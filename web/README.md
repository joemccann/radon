# Convex Scavenger Web

Next.js dashboard with real-time IB pricing and Claude-powered conversational interface.

## Prerequisites

- Node.js 20+
- Python 3.9+ (for IB scripts)
- Interactive Brokers TWS or IB Gateway running
- API keys in `web/.env`

## Quick Start

```bash
# 1. Install dependencies
npm install
pip install ib_insync websockets

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Start everything (Next.js + IB price server)
npm run dev

# 4. Open http://localhost:3000
```

The `npm run dev` command starts both:
- Next.js dev server (port 3000)
- IB real-time price server (port 8765)

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
                        │ server.py        │     │  Clients    │
                        │ (streaming)      │     └─────────────┘
                        └──────────────────┘
                                │
                                ▼
                        ┌──────────────────┐     ┌─────────────┐
                        │ /api/prices      │────▶│  usePrices  │
                        │ (SSE endpoint)   │     │  (React)    │
                        └──────────────────┘     └─────────────┘
```

### Pricing vs Sync (Separated)

| Component | Purpose | Update Frequency |
|-----------|---------|------------------|
| `ib_sync.py` | Portfolio positions, P&L, account values | Every 30 seconds |
| `ib_realtime_server.py` | Live bid/ask/last prices | Real-time (<1ms latency) |

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
- `IB_REALTIME_WS_URL` - WebSocket server URL (default: `ws://localhost:8765`)

## Real-Time Pricing

### Start the Price Server

```bash
# Default settings
python3 ../scripts/ib_realtime_server.py

# Custom ports
python3 ../scripts/ib_realtime_server.py --port 8765 --ib-port 4001
```

### API Endpoint

**Stream prices (SSE):**
```
GET /api/prices?symbols=AAPL,MSFT,NVDA
```

**Snapshot (one-time):**
```bash
curl -X POST http://localhost:3000/api/prices \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["AAPL", "MSFT"]}'
```

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
| `/api/orders` | GET | Read open/executed orders |
| `/api/orders` | POST | Sync orders from IB |
| `/api/prices` | GET | SSE stream for real-time prices |
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
- `/api/assistant` route (mock mode)
- PI command entrypoints (`fetch_ticker`, `fetch_flow`, `discover`, `scanner`)
- `kelly.py` output parsing
- Real-time price utilities and state management

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
# Start everything (Next.js + IB price server)
npm run dev

# Start Next.js only (no real-time prices)
npm run dev:next

# Start IB price server only
npm run dev:prices

# Build for production
npm run build

# Start production server
npm start

# Lint
npm run lint

# Test IB connectivity
npm run test:ib
```

## Troubleshooting

### IB Connection Issues

1. Ensure TWS or IB Gateway is running
2. Enable API: Configure → API → Settings → "Enable ActiveX and Socket Clients"
3. Check port: TWS Paper=7497, TWS Live=7496, Gateway=4001/4002

### Price Server Not Connecting

```bash
# Check if server is running
curl -s http://localhost:8765 || echo "Server not running"

# Check IB connection in server logs
python3 ../scripts/ib_realtime_server.py 2>&1 | head -20
```

### Rate Limiting (Yahoo Finance fallback)

If IB is unavailable, some features fall back to Yahoo Finance which has aggressive rate limits. Wait a few minutes and retry, or ensure IB is connected.
