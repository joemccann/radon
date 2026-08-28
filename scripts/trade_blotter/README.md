# Trade Blotter / Reconciliation Service

Deterministic trade reconciliation and P&L calculation for Interactive Brokers.

## Features

- **Real-time fills** via IB API (`blotter.py`)
- **Historical trades** via IBKR sFTP (`1422766`, `radon-flex-pull`) plus `--from-file`. Live Flex Web Service is recon-only. See `docs/flex-sftp-setup.md`.
- **Spread identification** (put spreads, call spreads, risk reversals, collars)
- **Commission tracking** included in all P&L calculations
- **Decimal precision** for accurate financial calculations

## Quick Start

### Today's Trades
```bash
python3.13 scripts/blotter.py
python3.13 scripts/blotter.py --summary
python3.13 scripts/blotter.py --json
```

### Historical Trades
```bash
# First time: see setup guide
python3.13 scripts/trade_blotter/flex_query.py --setup

# After setup:
python3.13 scripts/trade_blotter/flex_query.py --symbol EWY
```

## Setup

### Prerequisites

1. **IB Gateway or TWS** running with API enabled
2. **Python packages**: `ib_insync`, `requests`

```bash
pip install ib_insync requests
```

### IB Connection

| Platform | Port |
|----------|------|
| TWS Paper | 7497 |
| TWS Live | 7496 |
| IB Gateway Paper | 4002 |
| IB Gateway Live | 4001 |

### Flex Query (historical recon)

Routine path is sFTP of Trade History `1422766` (Last Business Day, XML, PGP).
Install: [`docs/flex-sftp-setup.md`](../../docs/flex-sftp-setup.md).

Same-day fills: IB Gateway / `journal_sync`, not Flex.

```bash
python3.13 scripts/journal_rehydrate.py --from-file /path/to/1422766.xml
```

Do not weekday `flex_query.py` SendRequest. `IB_FLEX_QUERY_ID=1422766`. Token is recon-only.

## Architecture

```
trade_blotter/
├── models.py           # Data models (Execution, Trade, Spread, TradeBlotter)
├── blotter_service.py  # IBFetcher, FlexQueryFetcher, BlotterService
├── cli.py              # CLI for real-time blotter
├── flex_query.py       # CLI for historical data
├── test_blotter.py     # Unit tests (25 tests)
└── test_integration.py # Integration tests (4 tests)
```

## Data Models

### Execution
Single fill from IB with:
- Symbol, side, quantity, price
- Commission
- Option fields (strike, expiry, right)
- Calculated: `notional_value`, `net_cash_flow`

### Trade
Groups executions by contract:
- Tracks `net_quantity` (0 = closed)
- Calculates `realized_pnl` for closed positions
- Calculates `unrealized_pnl` given current price

### Spread
Multi-leg position (same symbol/expiry):
- Automatically identifies spread type
- Combined P&L across all legs

### TradeBlotter
Collection of all trades with:
- `total_realized_pnl`
- `total_commissions`
- `get_spreads()` for spread grouping

## P&L Calculation

```
Cash Flow = Side × (Quantity × Price × Multiplier) - Commission

Where:
  Side = +1 for SELL, -1 for BUY
  Multiplier = 100 for options, 1 for stocks

Realized P&L = Sum of cash flows (for closed positions only)
```

All calculations use Python `Decimal` for precision.

## Testing

```bash
# Unit tests
cd scripts/trade_blotter
python3.13 -m pytest test_blotter.py -v

# Integration tests (requires IB connection)
python3.13 test_integration.py
```

## CLI Reference

### blotter.py (real-time)
```
--host        IB host (default: 127.0.0.1)
--port        IB port (default: 4001)
--client-id   IB client ID (default: 88)
--json        Output as JSON
--summary     P&L summary only
--verbose     Show execution details
```

### flex_query.py (historical)
```
--token       Flex Web Service token
--query-id    Flex Query ID
--symbol      Filter by symbol
--json        Output as JSON
--setup       Show setup guide
```

## Example Output

```
======================================================================
TRADE BLOTTER
As of: 2026-03-03 14:30:00
======================================================================

📊 SPREAD POSITIONS (2)
------------------------------------------------------------

📂 EWY Put Spread (exp: 20260313) [OPEN]
   Legs: 2
      • EWY 20260313 148.0P: -60.0
      • EWY 20260313 140.0P: +60.0
   Commissions: $84.25
   Net Cash Flow: ✅ $34,127.75

======================================================================
SUMMARY
======================================================================
  Closed Trades:    0
  Open Positions:   8
  Total Commissions: $106.23
  Realized P&L:     ⬜ $0.00
  Net Spread Flow:  ✅ $36,623.63
======================================================================
```
