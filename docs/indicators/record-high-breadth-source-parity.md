# Record-high breadth: formula and source parity

## Reference and scope

The operator supplied a StockCharts daily chart dated **2026-09-18**:
S&P 500 close **7650.50**, S&P 500 percent of stocks above their 200-day
moving average **52.00%**, and new highs minus new lows percent **-4.40%**.
The associated observation describes index strength alongside weakening
participation. This is a three-panel observation, not a binary trading rule.
The later September 21 rally rule is a separate observation and is not the
formula for this indicator. Neither historical analogues nor a crash
probability can be reconstructed from this chart alone.

## Validated formulas

- Participation: `100 * count(close > SMA200) / total_issues`.
- Net new highs/lows percent: `100 * (new_52_week_highs - new_52_week_lows) / total_issues`.
- The top panel is the S&P 500 price index, not the SPY ETF.

These formulas are supported by StockCharts' primary documentation for
[Percent Above Moving Average](https://chartschool.stockcharts.com/table-of-contents/market-indicators/percent-above-moving-average)
and [High-Low Percent](https://chartschool.stockcharts.com/table-of-contents/market-indicators/high-low-percent).
Do not substitute Record High Percent, which uses highs divided by highs plus
lows, or the High-Low Index, which smooths that different ratio.

StockCharts publishes breadth after the market close, usually by **18:00 ET**.
It maintains index component lists and uses **dividend-adjusted** stock prices.
Its documentation explicitly states that component and price differences can
produce differences from other sources. See the primary catalogs for
[High-Low Percent](https://chartschool.stockcharts.com/table-of-contents/index-and-market-indicator-catalog/stockcharts-high-low-percent)
and [Percent Above Moving Average](https://chartschool.stockcharts.com/table-of-contents/index-and-market-indicator-catalog/stockcharts-percent-above-moving-average).
Their historical component lists are then-current, not today's membership
projected backwards. Neither the screenshot nor the vendor documentation
establishes an exact daily denominator of 500 or 503 for TradingView counts.

## Live MCP comparison

Authenticated official TradingView MCP reads were performed on October 10,
2026 (America/Los_Angeles); receipt timestamps are stored in UTC. No credentials
or tokens are included in the [sanitized receipt](record-high-breadth-source-probe.json).

| Series | TradingView symbol | September 18 value | Reference | Parity |
| --- | --- | ---: | ---: | --- |
| S&P 500 close | `SP:SPX` | 7650.50 | 7650.50 | exact |
| Above 200-day average | `INDEX:S5TH` | 49.50% | 52.00% | mismatch: -2.50 percentage points |
| New 52-week highs | `INDEX:MAHP` | 5 | not separately supplied | unverified |
| New 52-week lows | `INDEX:MALP` | 29 | not separately supplied | unverified |
| Net count | `MAHP - MALP` | -24 | not separately supplied | unverified |

For illustration only, dividing -24 by a fixed 500 produces **-4.80%**, which
does not match the supplied **-4.40%**. Dividing by 503 gives approximately
**-4.771%**, also a mismatch. Neither denominator is verified as the count
provider's daily universe. A fixed denominator requires an explicit display
label such as "net new highs/lows per 500" and must not be described as exact
StockCharts High-Low Percent.

Authenticated fuzzy MCP searches for `S&P 500 high low percent`,
`total issues S&P 500`, and `SPXHLP` returned unrelated index/FINRA matches.
No direct High-Low Percent or contemporaneous total-issues series was resolved
by these searches. This is a discovery result, not proof no such series exists.

The official symbol pages identify
[S5TH](https://www.tradingview.com/symbols/INDEX-S5TH/),
[MAHP](https://www.tradingview.com/symbols/INDEX-MAHP/), and
[MALP](https://www.tradingview.com/symbols/INDEX-MALP/) as S&P 500 series.
NYSE `MAHN`/`MALN` and `HIGN`/`LOWN` are different universes and cannot fill
these panels. Live search resolves S5TH to `INDEX:S5TH`.

**Result: source parity is a mismatch.** A TradingView implementation can
display the analogous source variant with its exact symbols and source label;
it cannot claim reproduction of StockCharts' 52.00%/-4.40% readings.

## Coverage and finalization

Daily helpers requested 5,000 bars and returned:

| Symbol | Returned | First observed UTC date | Last observed UTC date |
| --- | ---: | --- | --- |
| `SP:SPX` | 5000 | 2006-11-22 | 2026-10-09 |
| `INDEX:S5TH` | 5000 | 2006-09-04 | 2026-10-09 |
| `INDEX:MAHP` | 2584 | 2016-07-01 | 2026-10-09 |
| `INDEX:MALP` | 2545 | 2016-07-05 | 2026-10-09 |

Every normalized response retains `history_complete=false`. These are observed
response windows, not proven listing inception or continuous historical
coverage. First dates differ, and missing observations are not zero counts.
These windows cannot establish January 1973 or November 1999 comparables.

Unit inspection of the same raw history found **94 MAHP** and **566 MALP**
observations with close **0.01**, which is not an integer issue count. No
primary methodology establishes that 0.01 is a representation of zero.
These points must therefore remain quarantined missing counts with original
timestamps and explicit invalid-observation metadata. Do not round them,
invent zero counts, or abort otherwise usable current history because of an
older invalid point. If the latest required session has an invalid count,
its net measure is missing and collection health is degraded.

The September 18 source bars have timestamp `1789738200`, or 13:30 UTC / 09:30
New York. This is a session anchor, not publication time or proof of a finalized
close. StockCharts' 18:00 ET documentation must not be applied to TradingView.
Use a conservative post-close collection window and record the actual fetch
time, source timestamp, missing/lagging sessions, and provider finalization as
unverified unless an explicit TradingView contract supplies it.

## Implementation boundaries

- Retain the three-panel observational design, source attribution, percent units,
  and neutral missing-data states.
- Publish the provider participation percentage directly; do not reconstruct a
  constituent count from rounded S5TH percentages.
- Store raw high/low counts and exact denominator provenance. An unverified
  denominator must remain explicit or leave true High-Low Percent unavailable.
- Align bars by explicit exchange session date while preserving original Unix
  timestamps. Never forward-fill missing breadth as zero.
- Show historical coverage and source parity. Do not imply 1973/1999 validation,
  all-time index highs, statistical rarity, or a binary trading signal.
