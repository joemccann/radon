# IB TWS API Reference (ib_insync)

Developer reference for the Interactive Brokers TWS API as used via the `ib_insync` Python library.

---

## Connection

### Ports
| Mode | IB Gateway | TWS |
|------|-----------|-----|
| Live | 4001 | 7496 |
| Paper | 4002 | 7497 |

### IB.connect()
```python
ib.connect(host='127.0.0.1', port=4001, clientId=0, timeout=4.0)
```

### Client ID Strategy

**Default to `clientId=0` (master client)** unless you need concurrent connections.

| clientId | Privileges | Use When |
|----------|-----------|----------|
| **0** (master) | Full control — can cancel/modify ANY order | Default for most operations |
| 1-999 | Can only manage own orders | Need concurrent connections |

**Why master client by default:**
- Can cancel orders placed via TWS or other clients
- Can modify any order regardless of origin
- Full visibility into account state

**When to use unique clientId:**
- Running multiple scripts simultaneously (real-time streaming + sync)
- Long-running background services that shouldn't block other connections
- Order placement (orders get tagged with clientId for tracking)

**Critical rule:** Only ONE connection can use `clientId=0` at a time. Duplicate disconnects the older session.

### Market Data Type
```python
ib.reqMarketDataType(type)
```
| Type | Description |
|------|-------------|
| 1 | Live (real-time) |
| 2 | Frozen (last known price when market closed) |
| 3 | Delayed (15-20 min delay, no subscription needed) |
| 4 | Delayed-frozen |

### Subscription Limits
- **Streaming**: 100 concurrent by default (up to 400 with IB permission)
- **Snapshot**: One-time fetch, does NOT count toward limit

---

## Contract Class

Base class for all financial instruments.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `conId` | `int` | `0` | Unique contract ID assigned by IB |
| `symbol` | `str` | `''` | Ticker symbol (e.g., 'AAPL') |
| `secType` | `str` | `''` | Security type: `STK`, `OPT`, `FUT`, `CASH`, `BAG`, `IND`, `BOND` |
| `lastTradeDateOrContractMonth` | `str` | `''` | Expiry: `YYYYMMDD` or `YYYYMM` |
| `strike` | `float` | `0.0` | Strike price (options) |
| `right` | `str` | `''` | `C` (Call) or `P` (Put) |
| `multiplier` | `str` | `''` | Contract multiplier (e.g., `'100'` for US options) |
| `exchange` | `str` | `''` | Exchange: `SMART`, `NYSE`, `NASDAQ`, `CBOE`, `GLOBEX`, `IDEALPRO` |
| `primaryExchange` | `str` | `''` | Primary exchange (disambiguation) |
| `currency` | `str` | `''` | Currency: `USD`, `EUR`, etc. |
| `localSymbol` | `str` | `''` | Exchange-specific local symbol |
| `tradingClass` | `str` | `''` | Trading class (disambiguation) |
| `includeExpired` | `bool` | `False` | Include expired contracts in searches |
| `secIdType` | `str` | `''` | `ISIN`, `CUSIP`, etc. |
| `secId` | `str` | `''` | Security identifier value |
| `comboLegsDescrip` | `str` | `''` | Combo legs description |
| `comboLegs` | `list[ComboLeg]` | `[]` | Legs for combo (BAG) contracts |
| `deltaNeutralContract` | `DeltaNeutralContract` | `None` | Delta-neutral hedge details |

### Contract Subclasses

```python
Stock(symbol, exchange='SMART', currency='USD')     # secType='STK'
Option(symbol, lastTradeDateOrContractMonth, strike, right, exchange='SMART', currency='USD')  # secType='OPT'
Future(symbol, lastTradeDateOrContractMonth, exchange, currency='USD')  # secType='FUT'
Forex(pair, exchange='IDEALPRO')                     # secType='CASH'
Index(symbol, exchange, currency='USD')              # secType='IND'
Bond(secIdType, secId)                               # secType='BOND'
```

### Contract Qualification
```python
# Synchronous (blocks)
qualified = ib.qualifyContracts(contract1, contract2)

# Async (non-blocking, better for batches)
qualified = await ib.qualifyContractsAsync(contract1, contract2)
```
**Required fields**: `symbol`, `secType`, `currency`, `exchange`
**Filled by IB**: `conId`, `exchange`, `primaryExchange`, `localSymbol`, `tradingClass`, `multiplier`

---

## ComboLeg Class

One leg of a combination (BAG) contract.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `conId` | `int` | `0` | Contract ID of the leg instrument |
| `ratio` | `int` | `0` | Quantity ratio of this leg |
| `action` | `str` | `''` | `BUY` or `SELL` |
| `exchange` | `str` | `''` | Exchange for this leg |
| `openClose` | `int` | `0` | 0=same as order, 1=open, 2=close, 3=unknown |
| `shortSaleSlot` | `int` | `0` | 1=clearing broker, 2=third party |
| `designatedLocation` | `str` | `''` | Short sale location |
| `exemptCode` | `int` | `-1` | Short sale exemption code |

### Combo Order Patterns

All combos use `secType="BAG"` on the parent contract.

#### Bull Call Spread
```python
contract = Contract(symbol="AAPL", secType="BAG", currency="USD", exchange="SMART")
contract.comboLegs = [
    ComboLeg(conId=<lower_call_conId>, ratio=1, action="BUY",  exchange="SMART"),
    ComboLeg(conId=<higher_call_conId>, ratio=1, action="SELL", exchange="SMART"),
]
order = LimitOrder("BUY", 1, lmtPrice=2.50)  # net debit
```

#### Bear Put Spread
```python
contract = Contract(symbol="AAPL", secType="BAG", currency="USD", exchange="SMART")
contract.comboLegs = [
    ComboLeg(conId=<higher_put_conId>, ratio=1, action="BUY",  exchange="SMART"),
    ComboLeg(conId=<lower_put_conId>,  ratio=1, action="SELL", exchange="SMART"),
]
order = LimitOrder("BUY", 1, lmtPrice=2.00)  # net debit
```

#### Risk Reversal (Long Call + Short Put)
```python
contract = Contract(symbol="AAPL", secType="BAG", currency="USD", exchange="SMART")
contract.comboLegs = [
    ComboLeg(conId=<call_conId>, ratio=1, action="BUY",  exchange="SMART"),
    ComboLeg(conId=<put_conId>,  ratio=1, action="SELL", exchange="SMART"),
]
order = LimitOrder("BUY", 1, lmtPrice=1.50)  # net debit or credit
```

#### Iron Condor (4 legs)
```python
contract.comboLegs = [
    ComboLeg(conId=<otm_put_buy>,   ratio=1, action="BUY",  exchange="SMART"),  # wing
    ComboLeg(conId=<otm_put_sell>,  ratio=1, action="SELL", exchange="SMART"),  # body
    ComboLeg(conId=<otm_call_sell>, ratio=1, action="SELL", exchange="SMART"),  # body
    ComboLeg(conId=<otm_call_buy>,  ratio=1, action="BUY",  exchange="SMART"),  # wing
]
```

#### Straddle (Same strike, Call + Put)
```python
contract.comboLegs = [
    ComboLeg(conId=<call_conId>, ratio=1, action="BUY", exchange="SMART"),
    ComboLeg(conId=<put_conId>,  ratio=1, action="BUY", exchange="SMART"),
]
```

#### Strangle (Different strikes, OTM Call + OTM Put)
```python
contract.comboLegs = [
    ComboLeg(conId=<otm_call_conId>, ratio=1, action="BUY", exchange="SMART"),
    ComboLeg(conId=<otm_put_conId>,  ratio=1, action="BUY", exchange="SMART"),
]
```

#### Butterfly (3 strikes)
```python
contract.comboLegs = [
    ComboLeg(conId=<low_strike>,  ratio=1, action="BUY",  exchange="SMART"),
    ComboLeg(conId=<mid_strike>,  ratio=2, action="SELL", exchange="SMART"),
    ComboLeg(conId=<high_strike>, ratio=1, action="BUY",  exchange="SMART"),
]
```

### Smart Combo Routing
```python
order.smartComboRoutingParams = [
    TagValue("NonGuaranteed", "1"),  # legs can fill independently (faster)
]
# OR
order.smartComboRoutingParams = [
    TagValue("NonGuaranteed", "0"),  # all legs fill together (guaranteed)
]
# Priority:
order.smartComboRoutingParams = [
    TagValue("NonGuaranteed", "1"),
    TagValue("cboPriority1", "0;1"),  # leg index 0 has priority
]
```

---

## Order Class

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `orderId` | `int` | `0` | Unique order ID (assigned by IB) |
| `clientId` | `int` | `0` | Client ID |
| `permId` | `int` | `0` | Permanent order ID |
| `action` | `str` | `''` | `BUY` or `SELL` |
| `totalQuantity` | `float` | `0.0` | Total quantity |
| `orderType` | `str` | `''` | See Order Types below |
| `lmtPrice` | `float` | `0.0` | Limit price |
| `auxPrice` | `float` | `0.0` | Stop price / trail amount |
| `tif` | `str` | `''` | Time in force (see below) |
| `ocaGroup` | `str` | `''` | One-cancels-all group |
| `ocaType` | `int` | `0` | 1=cancel on fill, 2=reduce, 3=reduce non-OCA |
| `orderRef` | `str` | `''` | User-defined reference |
| `transmit` | `bool` | `True` | Transmit to IB (False = hold) |
| `parentId` | `int` | `0` | Parent order ID (bracket orders) |
| `outsideRth` | `bool` | `False` | Allow outside regular trading hours |
| `hidden` | `bool` | `False` | Hidden (iceberg) |
| `goodAfterTime` | `str` | `''` | Format: `YYYYMMDD HH:MM:SS` |
| `goodTillDate` | `str` | `''` | Format: `YYYYMMDD HH:MM:SS` |
| `allOrNone` | `bool` | `False` | All-or-none |
| `minQty` | `int` | `0` | Minimum fill quantity |
| `trailStopPrice` | `float` | `0.0` | Trailing stop price |
| `trailingPercent` | `float` | `0.0` | Trailing stop percent |
| `whatIf` | `bool` | `False` | Margin simulation only |
| `algoStrategy` | `str` | `''` | `Vwap`, `Twap`, etc. |
| `algoParams` | `list[TagValue]` | `[]` | Algo strategy parameters |
| `smartComboRoutingParams` | `list[TagValue]` | `[]` | Combo routing params |
| `orderComboLegs` | `list[float]` | `[]` | Per-leg prices |
| `blockOrder` | `bool` | `False` | Block order |
| `sweepToFill` | `bool` | `False` | Sweep to fill |
| `displaySize` | `int` | `0` | Iceberg display size |
| `triggerMethod` | `int` | `0` | Stop trigger method |
| `percentOffset` | `float` | `0.0` | Relative order offset |
| `discretionaryAmt` | `float` | `0.0` | Discretionary amount |
| `notHeld` | `bool` | `False` | Not held (institutional) |
| `rule80A` | `str` | `''` | Rule 80A designation |

### Order Type Constants
| Code | Name |
|------|------|
| `MKT` | Market |
| `LMT` | Limit |
| `STP` | Stop |
| `STP LMT` | Stop-Limit |
| `TRAIL` | Trailing Stop |
| `REL` | Relative/Pegged |
| `VWAP` | VWAP |
| `MOC` | Market on Close |
| `LOC` | Limit on Close |
| `MIT` | Market if Touched |
| `LIT` | Limit if Touched |

### Time in Force
| Code | Description |
|------|-------------|
| `DAY` | Valid for trading day |
| `GTC` | Good till cancel |
| `IOC` | Immediate or cancel |
| `GTD` | Good till date (needs `goodTillDate`) |
| `OPG` | Opening order |
| `FOK` | Fill or kill |
| `DTC` | Day till cancelled |

### Convenience Constructors
```python
LimitOrder(action, totalQuantity, lmtPrice)      # orderType='LMT'
MarketOrder(action, totalQuantity)                 # orderType='MKT'
StopOrder(action, totalQuantity, stopPrice)        # orderType='STP'
StopLimitOrder(action, totalQuantity, lmtPrice, stopPrice)  # orderType='STP LMT'
```

---

## Ticker Class

Real-time market data. Fields default to `float('nan')` (not None) when no data.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `contract` | `Contract` | `None` | Associated contract |
| `time` | `datetime` | `None` | Last update timestamp |
| `bid` | `float` | `nan` | Current bid |
| `bidSize` | `float` | `nan` | Bid size |
| `ask` | `float` | `nan` | Current ask |
| `askSize` | `float` | `nan` | Ask size |
| `last` | `float` | `nan` | Last traded price |
| `lastSize` | `float` | `nan` | Last traded size |
| `high` | `float` | `nan` | Daily high |
| `low` | `float` | `nan` | Daily low |
| `open` | `float` | `nan` | Daily open |
| `close` | `float` | `nan` | **Previous day's close** |
| `volume` | `float` | `nan` | Daily volume |
| `vwap` | `float` | `nan` | VWAP |
| `ticks` | `list` | `[]` | Tick-by-tick data |
| `domBids` | `list` | `[]` | Depth of market bids |
| `domAsks` | `list` | `[]` | Depth of market asks |

**NaN handling**: IB sends `NaN` for unavailable fields. Check with `math.isnan(value)` or `value != value`.

### Event
```python
ticker.updateEvent += on_ticker_update  # fires on every tick change
```

---

## Generic Tick Types (for reqMktData)

```python
ticker = ib.reqMktData(contract, genericTickList='233', snapshot=False)
```

| ID | Name | Description |
|----|------|-------------|
| 100 | Option Volume | Call/put volume breakdown |
| 101 | Option Open Interest | Open interest |
| 104 | Historical Volatility | 30-day HV |
| 106 | Implied Volatility | Option IV |
| 162 | Index Future Premium | Future vs cash premium |
| 165 | Misc Stats | 52-week high/low, etc. |
| 166 | Mark Price | Used for margin calcs |
| 221 | Bond Factor Multiplier | Bond multiplier |
| 225 | Auction Values | Auction price/volume/imbalance |
| 232 | RT Volume | Real-time time & sales |
| 233 | RT Volume (extended) | Real-time volume with last price/size |
| 236 | Shortable | 0=not shortable, >0=shortable |
| 256 | Fundamental Ratios | P/E, dividend yield, etc. |
| 258 | Volatility | Real-time volatility |
| 293 | Time Zone | Instrument time zone |
| 411 | RT Historical Volatility | Real-time HV |
| 456 | IB Dividends | Ex-date, amount |
| 586 | News | News availability |
| 587 | Short Term Volume | 3-min, 5-min volume |

---

## Trade, Fill, Execution

### Trade
| Property | Type | Description |
|----------|------|-------------|
| `contract` | `Contract` | The contract |
| `order` | `Order` | The order |
| `orderStatus` | `OrderStatus` | Current status |
| `fills` | `list[Fill]` | List of fills |
| `log` | `list` | Status update log |

### Fill
| Property | Type | Description |
|----------|------|-------------|
| `contract` | `Contract` | The contract |
| `execution` | `Execution` | Execution details |
| `commissionReport` | `CommissionReport` | Commission |
| `time` | `datetime` | Fill timestamp |

### Execution
| Property | Type | Description |
|----------|------|-------------|
| `execId` | `str` | Unique execution ID |
| `time` | `datetime` | Execution timestamp |
| `acctNumber` | `str` | Account number |
| `exchange` | `str` | Execution exchange |
| `side` | `str` | `BOT` (bought) or `SLD` (sold) |
| `shares` | `float` | Shares executed |
| `price` | `float` | Execution price |
| `permId` | `int` | Permanent order ID |
| `clientId` | `int` | Client ID |
| `orderId` | `int` | Order ID |
| `liquidation` | `int` | 0=no, 1=yes |
| `cumQty` | `float` | Cumulative quantity |
| `avgPrice` | `float` | Average price |
| `orderRef` | `str` | Order reference |
| `evRule` | `str` | Economic value rule |
| `evMultiplier` | `float` | EV multiplier |
| `modelCode` | `str` | Model code |

### CommissionReport
| Property | Type | Description |
|----------|------|-------------|
| `execId` | `str` | Execution ID |
| `commission` | `float` | Commission amount |
| `currency` | `str` | Commission currency |
| `realizedPNL` | `float` | Realized P&L |
| `yield_` | `float` | Yield (bonds) |
| `yieldRedemptionDate` | `int` | Redemption date (bonds) |

---

## OrderStatus Values

| Status | Meaning |
|--------|---------|
| `PendingSubmit` | Pending submission to server |
| `PendingCancel` | Cancel pending |
| `PreSubmitted` | Submitted, not yet acknowledged |
| `Submitted` | Acknowledged by server |
| `ApiCancelled` | Cancelled via API before submission |
| `Cancelled` | Cancelled |
| `Filled` | Fully filled |
| `Inactive` | Inactive (e.g., outside market hours) |

---

## Position & Portfolio

### Position (from reqPositions)
| Property | Type | Description |
|----------|------|-------------|
| `account` | `str` | Account ID |
| `contract` | `Contract` | Instrument |
| `position` | `float` | Quantity (+long, -short) |
| `avgCost` | `float` | Average cost per unit |

### PortfolioItem (from reqAccountUpdates)
| Property | Type | Description |
|----------|------|-------------|
| `contract` | `Contract` | Instrument |
| `position` | `float` | Quantity (+long, -short) |
| `marketPrice` | `float` | Current market price |
| `marketValue` | `float` | position * marketPrice |
| `averageCost` | `float` | Average cost per unit |
| `unrealizedPNL` | `float` | Unrealized P&L |
| `realizedPNL` | `float` | Realized P&L |
| `accountName` | `str` | Account ID |

### AccountValue (from reqAccountUpdates)
| Property | Type | Description |
|----------|------|-------------|
| `key` | `str` | Metric name (e.g., `NetLiquidation`) |
| `value` | `str` | Value |
| `currency` | `str` | Currency |
| `accountName` | `str` | Account ID |

---

## DeltaNeutralContract

| Property | Type | Description |
|----------|------|-------------|
| `conId` | `int` | Contract ID of underlying |
| `delta` | `float` | Delta for hedging |
| `price` | `float` | Underlying price for delta calc |

---

## TagValue

Key-value pair for algo params and routing.

| Property | Type | Description |
|----------|------|-------------|
| `tag` | `str` | Key name |
| `value` | `str` | Value |

---

## BarData (Historical)

| Property | Type | Description |
|----------|------|-------------|
| `date` | `datetime/str` | Bar timestamp |
| `open` | `float` | Open price |
| `high` | `float` | High price |
| `low` | `float` | Low price |
| `close` | `float` | Close price |
| `volume` | `float` | Volume |
| `average` | `float` | Average price |
| `barCount` | `int` | Number of trades |

---

## Key IB Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `connect` | `host, port, clientId, timeout` | `None` | Connect to TWS/Gateway |
| `disconnect` | | `None` | Disconnect |
| `isConnected` | | `bool` | Check connection |
| `qualifyContracts` | `*contracts` | `list[Contract]` | Fill in contract details (sync) |
| `qualifyContractsAsync` | `*contracts` | `list[Contract]` | Fill in contract details (async) |
| `reqMktData` | `contract, genericTickList, snapshot, regulatorySnapshot` | `Ticker` | Request market data |
| `cancelMktData` | `contract` | `None` | Cancel market data |
| `reqHistoricalData` | `contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate, keepUpToDate` | `list[BarData]` | Historical bars |
| `placeOrder` | `contract, order` | `Trade` | Place order |
| `cancelOrder` | `order` | `Trade` | Cancel order |
| `reqPositions` | | `list[Position]` | Current positions |
| `reqAccountUpdates` | | | Account + portfolio updates |
| `reqAccountSummary` | `group, tags` | `list` | Account summary |
| `reqOpenOrders` | | `list[Trade]` | Open orders |
| `reqAllOpenOrders` | | `list[Trade]` | All open orders (all clients) |
| `reqExecutions` | `execFilter` | `list[Fill]` | Execution history |
| `reqContractDetails` | `contract` | `list[ContractDetails]` | Full contract info |
| `reqMarketDataType` | `marketDataType` | `None` | Set data type (1-4) |
| `reqFundamentalData` | `contract, reportType` | `str` | Fundamental data (XML) |

### IB Sentinel Value
```python
IB_SENTINEL = 1.7976931348623157e308  # IB uses this for "no value" (DBL_MAX)
```

---

## Common Exchange Codes

| Code | Exchange |
|------|----------|
| `SMART` | IB Smart Routing |
| `NYSE` | New York Stock Exchange |
| `NASDAQ` | NASDAQ |
| `CBOE` | Chicago Board Options Exchange |
| `GLOBEX` | CME Globex (futures) |
| `IDEALPRO` | Forex (large sizes) |
| `ARCA` | NYSE Arca |
| `AMEX` | American Stock Exchange |
| `BATS` | BATS Exchange |
| `ISE` | International Securities Exchange |
| `BOX` | Boston Options Exchange |
| `PSE` | Pacific Stock Exchange |

---

## Client ID Registry (this project)

| ID | Script | Purpose |
|---:|--------|---------|
| **0** | `ib_order_manage.py` | **Master client** — cancel/modify ANY order |
| 1 | `ib_sync.py` | Portfolio sync |
| 2 | `ib_order.py` | Order placement |
| 11 | `ib_orders.py` | Order sync |
| 52 | `ib_fill_monitor.py` | Fill monitoring |
| 60 | `exit_order_service.py` | Exit order daemon |
| 90 | `ib_reconcile.py` | Reconciliation |
| 99 | `fetch_analyst_ratings.py` | Analyst data |
| 100 | `ib_realtime_server.py` | Real-time streaming (Python) |
| 101 | `ib_realtime_server.js` | Real-time streaming (Node.js) |

### Master Client (clientId=0)

The **master client** has special privileges:
- Can see ALL open orders from all clients (including TWS)
- Can cancel ANY order regardless of which client placed it
- Can modify ANY order

**Important:** Only ONE connection can use `clientId=0` at a time. If TWS is using it, the API connection will be rejected (or vice versa).

Use `ib_order_manage.py` for cancel/modify operations — it connects as master to handle TWS-placed orders.

---

## IBC Gateway Automation

IB Gateway is managed by IBC v3.23.0 (`vendor/ibc/`), installed as a launchd service.

### Service Management
```bash
./scripts/setup_ibc.sh install    # Detect Gateway version, patch config, install service
./scripts/setup_ibc.sh uninstall  # Remove service
./scripts/setup_ibc.sh status     # Service state, Gateway PID, config
./scripts/setup_ibc.sh logs       # Tail log files
./scripts/setup_ibc.sh start      # Manual foreground start (for 2FA)
./scripts/setup_ibc.sh stop       # Send STOP via IBC command server
```

### Lifecycle
| Time | Action | 2FA Required |
|------|--------|-------------|
| Mon-Fri 00:00 | launchd starts Gateway via IBC | First login only |
| Daily 11:58 PM | IBC auto-restart (reuses auth session) | No |
| Sunday 07:05 | Cold restart (full re-auth) | Yes |

### Config (`~/ibc/config.ini`)
Credentials are in this file (never committed). The setup script patches operational settings only:
- `ExistingSessionDetectedAction=primary` — Gateway reconnects if bumped
- `AutoRestartTime=11:58 PM` — before IB's forced restart window
- `ColdRestartTime=07:05` — Sunday re-auth
- `CommandServerPort=7462` — enables `echo "STOP" | nc localhost 7462`

### Ports
| Port | Service |
|------|---------|
| 4001 | IB Gateway API (Live) |
| 7462 | IBC Command Server (stop/restart) |

### Logs
| File | Content |
|------|---------|
| `~/ibc/logs/ibc-gateway.log` | stdout/stderr from launchd |
| `~/ibc/logs/ibc-{version}_GATEWAY-{ver}_{day}.txt` | IBC diagnostic log (login, 2FA, config) |
