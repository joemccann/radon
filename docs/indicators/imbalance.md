# Imbalance

Requested by Joe McCann. Source chart: daily TradingView
`TRDS.ASK.US - TRDS.BID.US` with EMA(10) of that close and SPX in a lower
pane. Public excerpt of
https://x.com/benkizemchuk/status/2119045970805604478 called this "volume of
shares traded at ask minus ... bid". The full post was not accessible. Public
TradingView pages title the exact symbols **NUMBER OF TRADES AT ASK/BID**,
not share volume:

- https://www.tradingview.com/symbols/USI-TRDS.ASK.US/
- https://www.tradingview.com/symbols/USI-TRDS.BID.US/

Live official TradingView MCP `get_ohlcv` on 2026-11-07 confirmed last
completed session 2026-11-06: ASK 13,124,065 minus BID 12,215,551 equals
BATD 908,514 equals the source-chart print. Share-volume siblings
`USI:VOL.ASK.US` / `USI:BAVD.US` are a different series (billions of shares)
and are not used.

## Identity

| Field | Value |
|---|---|
| Slug / service | `imbalance` |
| Route | `/regime/imbalance` |
| Tab label | `IMBALANCE` |
| Ingest | `scripts/fetch_imbalance.py` |
| Timer | `radon-imbalance.timer` `*-*-* 18:25:00 America/New_York` |
| Migration | `0108_imbalance.sql` |

## Formula

Published close is **ASK minus BID trade counts**, never BATD as a silent
substitute. BATD (`USI:BATD.US`) is a parity check only.

```
imbalance_t = close(USI:TRDS.ASK.US)_t - close(USI:TRDS.BID.US)_t
ema10_t     = EMA(imbalance, 10)   # TradingView ta.ema: SMA seed, k = 2/11
```

Zero trade counts are valid. Missing ASK or BID is a gap (`null`), never
zero. EMA walks finite observations only; a gap does not seed 0 and does
not reset the window. EMA is computed on the full stored history at ingest,
not on the visible chart window.

No z-score, percentage, or cumulative transform. Horizontal lines on the
source screenshots (917,345,763 and 238,453,685) have unknown meaning and
are not signal thresholds.

## Units

`trade_count`. Verified. Do not format as shares or dollars. The X excerpt
is wrong relative to the chart formula and the public USI titles.

## Source

| Priority | Result |
|---|---|
| IB | No US-wide ask/bid trade-count index |
| Robinhood | No equivalent |
| Unusual Whales | No equivalent |
| Official Cboe / FINRA | No equivalent |
| Yahoo | No equivalent |
| TradingView official MCP | Exact USI statistical indices under existing Ultimate OAuth |

Tick-direction reconstruction from OHLCV is a forbidden proxy.

Licensing matches shipped `record-high-breadth`: operator-private JWT
dashboard, no public redistribution, existing TradingView Ultimate OAuth.
`history_complete=false` (MCP `has_more`). Last bar may still be forming;
18:00 America/New_York is a conservative cutoff, not a provider-finality
claim.

Unverified (labeled in the tab): universe, regular-session vs all prints,
Lee-Ready / eligible-trade classification, overnight, TRF / ATS mix.

## Cadence

Daily calendar-day timer at 18:25 ET, five minutes after
`record-high-breadth` (18:15 ET) so the two jobs do not refresh the same
TradingView OAuth store concurrently. Weekend and holiday runs heartbeat
unchanged data. Watchdog window 26h. `MAX_BARS=5000` per symbol,
sequential `get_ohlcv`.

## Persistence

Turso `imbalance_history` keyed by session date, ON CONFLICT upsert.
Complete rows require finite ASK and BID. SPX may be null. Empty or
unjoined collection refuses overwrite (last-good retained). Scan snapshot
service key `imbalance`. HTTP 200 `{missing:true, scan_time:null,
data_date:null, current:null, series:[]}` when nothing is stored.

## Departures from the source chart

- EMA(10) is in the strip and chart tooltip. `CriHistoryChart` takes two
  series; signed teal/red bars consume both slots, so the yellow overlay
  is not drawn on the histogram.
- Screenshot prints 908,514.000 and 237,332,929 are source-chart
  observations only. Tests pin live MCP arithmetic, not those figures as
  fake stored rows.
- `docs/indicators/imbalance-tab.png` is a Playwright labeled-fixture
  still, not a production Turso read.
