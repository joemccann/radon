# Unusual Whales API Reference

**Base URL:** `https://api.unusualwhales.com`

**Authentication:** Bearer token in Authorization header
```
Authorization: Bearer {UW_TOKEN}
```

The `UW_TOKEN` environment variable should contain your API key.

---

## Core Endpoints for Convex Scavenger

### Dark Pool / OTC Flow (Primary Edge Source)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/darkpool/{ticker}` | GET | Dark pool trades for a ticker on a given day |
| `/api/darkpool/recent` | GET | Latest dark pool trades across all tickers |

**Dark Pool Ticker Parameters:**
- `ticker` (path, required): Stock symbol
- `date` (query, optional): ISO date (YYYY-MM-DD), defaults to current/last market day
- `min_premium`, `max_premium`: Filter by trade premium
- `min_size`, `max_size`: Filter by trade size
- `limit`: Max 500

**Response Fields:**
```json
{
  "data": [{
    "ticker": "AAPL",
    "executed_at": "2023-02-16T00:59:44Z",
    "price": "18.99",
    "size": 18600,
    "premium": "353214",
    "nbbo_bid": "18.99",
    "nbbo_ask": "19",
    "market_center": "L",
    "volume": 9940419
  }]
}
```

---

### Options Flow Alerts (Signal Detection)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/option-trades/flow-alerts` | GET | Latest flow alerts (sweeps, blocks, unusual activity) |

**Key Parameters:**
- `ticker_symbol`: Filter by ticker(s)
- `min_premium`, `max_premium`: Premium range
- `min_size`, `max_size`: Size range
- `is_sweep`: Boolean - intermarket sweeps only
- `is_floor`: Boolean - floor trades only
- `is_call`, `is_put`: Filter by option type
- `is_ask_side`, `is_bid_side`: Filter by trade side
- `all_opening`: Boolean - opening transactions only
- `min_dte`, `max_dte`: Days to expiration range
- `is_otm`: Boolean - OTM contracts only
- `rule_name[]`: Filter by alert type (RepeatedHits, FloorTradeLargeCap, etc.)
- `issue_types[]`: Common Stock, ETF, Index
- `limit`: Max 200

**Response Fields:**
```json
{
  "data": [{
    "alert_rule": "RepeatedHits",
    "ticker": "MSFT",
    "option_chain": "MSFT231222C00375000",
    "strike": "375",
    "expiry": "2023-12-22",
    "type": "call",
    "underlying_price": "372.99",
    "total_premium": "186705",
    "total_size": 461,
    "open_interest": 7913,
    "volume": 2442,
    "volume_oi_ratio": "0.308",
    "total_ask_side_prem": "151875",
    "total_bid_side_prem": "405",
    "has_sweep": true,
    "has_floor": false,
    "all_opening_trades": false
  }]
}
```

---

### Stock Information

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock/{ticker}/info` | GET | Company info, sector, market cap |
| `/api/stock/{ticker}/options-volume` | GET | Options volume & premium summary |
| `/api/stock/{ticker}/ohlc/{candle_size}` | GET | OHLC price data |

**Info Response:**
```json
{
  "data": {
    "ticker": "AAPL",
    "full_name": "Apple Inc.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "marketcap": "2850000000000",
    "avg30_volume": "73784934",
    "has_options": true,
    "is_s_p_500": true
  }
}
```

---

### Options Chain Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock/{ticker}/option-contracts` | GET | All option contracts for ticker |
| `/api/stock/{ticker}/expiry-breakdown` | GET | Available expirations with volume/OI |
| `/api/stock/{ticker}/greeks` | GET | Greeks for each strike at an expiry |
| `/api/option-contract/{id}/historic` | GET | Historical data for specific contract |

**Option Contracts Parameters:**
- `expiry`: Filter by expiration date
- `option_type`: call or put
- `vol_greater_oi`: Boolean - volume > OI filter
- `exclude_zero_vol_chains`: Boolean
- `maybe_otm_only`: Boolean - OTM only

---

### Greek Exposure (GEX)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock/{ticker}/greek-exposure` | GET | Total greek exposure over time |
| `/api/stock/{ticker}/greek-exposure/strike` | GET | GEX by strike |
| `/api/stock/{ticker}/greek-exposure/expiry` | GET | GEX by expiration |
| `/api/stock/{ticker}/greek-flow` | GET | Intraday delta/vega flow per minute |

---

### Options Flow by Ticker

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock/{ticker}/flow-per-strike` | GET | Flow aggregated by strike |
| `/api/stock/{ticker}/flow-per-expiry` | GET | Flow aggregated by expiration |
| `/api/stock/{ticker}/net-prem-ticks` | GET | Net premium ticks (1-min intervals) |

---

### Volatility Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock/{ticker}/volatility/realized` | GET | IV vs realized volatility |
| `/api/stock/{ticker}/volatility/term-structure` | GET | IV term structure by expiry |
| `/api/stock/{ticker}/volatility/stats` | GET | Comprehensive volatility statistics |
| `/api/stock/{ticker}/iv-rank` | GET | IV rank data over time |

---

### Analyst Ratings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/screener/analysts` | GET | Analyst ratings and price targets |

**Parameters:**
- `ticker`: Filter by ticker
- `action`: initiated, reiterated, downgraded, upgraded, maintained
- `recommendation`: buy, hold, sell
- `limit`: Max 500

**Response:**
```json
{
  "data": [{
    "ticker": "MSFT",
    "action": "maintained",
    "recommendation": "buy",
    "analyst_name": "Tyler Radke",
    "firm": "Citi",
    "target": "420.0",
    "sector": "Technology",
    "timestamp": "2023-09-11T11:21:12Z"
  }]
}
```

---

### Seasonality

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/seasonality/{ticker}/monthly` | GET | Average return by month |
| `/api/seasonality/{ticker}/year-month` | GET | Returns per month per year |
| `/api/seasonality/market` | GET | Market-wide seasonality (SPY, QQQ, etc.) |
| `/api/seasonality/{month}/performers` | GET | Best/worst performers for a month |

---

### Institutional Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/institution/{ticker}/ownership` | GET | Institutional ownership of ticker |
| `/api/institution/{name}/holdings` | GET | Holdings for an institution |
| `/api/institutions` | GET | List of institutions |

---

### Short Interest

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/shorts/{ticker}/interest-float/v2` | GET | Short interest and float data |
| `/api/shorts/{ticker}/data` | GET | Short data including borrow rate |
| `/api/shorts/{ticker}/volume-and-ratio` | GET | Short volume and ratio |
| `/api/short_screener` | GET | Screen for high short interest |

---

### Insider Trading

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/insider/transactions` | GET | Insider buy/sell transactions |
| `/api/insider/{ticker}` | GET | Insiders for a ticker |
| `/api/insider/{ticker}/ticker-flow` | GET | Aggregated insider flow |

---

### Congress Trading

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/congress/recent-trades` | GET | Latest congress trades |
| `/api/congress/congress-trader` | GET | Trades by congress member |

---

### ETF Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/etfs/{ticker}/info` | GET | ETF information |
| `/api/etfs/{ticker}/holdings` | GET | ETF holdings |
| `/api/etfs/{ticker}/exposure` | GET | ETFs containing a ticker |

---

### Market Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/market/market-tide` | GET | Market-wide options sentiment |
| `/api/market/sector-etfs` | GET | SPDR sector ETF stats |
| `/api/market/total-options-volume` | GET | Total market options volume |
| `/api/market/oi-change` | GET | Biggest OI changes |
| `/api/market/economic-calendar` | GET | Economic events |
| `/api/market/fda-calendar` | GET | FDA calendar events |

---

### Earnings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/earnings/premarket` | GET | Premarket earnings |
| `/api/earnings/afterhours` | GET | Afterhours earnings |
| `/api/earnings/{ticker}` | GET | Historical earnings for ticker |

---

### Screeners

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/screener/stocks` | GET | Stock screener with many filters |
| `/api/screener/option-contracts` | GET | Options contract screener (Hottest Chains) |

---

### WebSocket Streaming

**WebSocket URL:** `wss://api.unusualwhales.com/socket?token={UW_TOKEN}`

**Channels:**
| Channel | Description |
|---------|-------------|
| `option_trades` | All option trades (6-10M/day) |
| `option_trades:{TICKER}` | Option trades for specific ticker |
| `flow-alerts` | Live flow alerts |
| `price:{TICKER}` | Live price updates |
| `gex:{TICKER}` | Live GEX updates |
| `news` | Live headline news |
| `lit_trades` | Exchange trades |
| `off_lit_trades` | Dark pool trades |

**Join Channel:**
```json
{"channel": "option_trades:AAPL", "msg_type": "join"}
```

---

## Error Handling

| Status | Description |
|--------|-------------|
| 200 | Success |
| 404 | Ticker not found |
| 422 | Invalid parameters |
| 500 | Internal server error |

---

## Rate Limits

- Standard tier: Varies by endpoint
- Advanced tier: Higher limits + WebSocket access
- Full tape download: Advanced tier only

---

## Full API Spec

See `docs/unusual_whales_api_spec.yaml` for complete OpenAPI specification.
