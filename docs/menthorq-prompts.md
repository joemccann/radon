# MenthorQ QUIN AI Screener — Prompts & Documentation

Reference for querying the QUIN AI screener. Use plain English — no special syntax required. The system handles weekends/holidays automatically in time-based queries.

---

## Available Metrics (97+ per ticker)

### Identifiers & Classification
- Ticker Symbol, Tier (1–4), Ticker Type (Stocks, ETFs, Futures, Indices), Sector, Industry

### Company & Price
- Company Name, Market Cap, Close/Open/High/Low Price, Volume, 52-Week High/Low

### Volatility
- Implied Volatility 30-Day, IV Rank, IV Percentile (1Y & 3M), Historical Volatility 30-Day, Expected 1-Day Move (%)

### Options Flow
- Call/Put Volume, Total Option Volume, Open Interest (total, calls, puts), Put/Call OI Ratio

### Greeks
- Net Gamma Exposure (GEX), Total Gamma, GEX Put/Call Ratio
- Net Delta Exposure (DEX), Total Delta, DEX Put/Call Ratio

### Expiring Greeks (Today, 1W, 2W, 1M)
- Expiring GEX and DEX values per tenor

### IV by Tenor
- 0DTE, 1-Month, 3-Month 50-Delta IV

### Skew
- 0DTE, 1-Month, 3-Month Skew

### Volatility Risk Premium
- VRP, VRP 3-Month, Normalized VRP (NVRP)

### Term Structure
- Term Structure Slope (Contango / Backwardation)

### Percentiles (1Y & 3M windows)
- DEX, GEX, IV across tenors, Call/Put OI, VRP, Skew

### Q-Scores
- Option Score, Momentum Score, Volatility Score, Seasonality Score

### Level Data
- Call Resistance, Put Support, High Volatility Level (HVL), 0DTE variants, 1-Day Min/Max

### Swing Levels
- Swing Bias (Bullish/Bearish), 5-Day & 20-Day Upper/Lower Bands, Risk Triggers

---

## Prompt Categories & Examples

### 1. Screening & Rankings

Find the best/worst names by any metric. Returns ranked tables.

```
Top 10 stocks by momentum score
Technology stocks with momentum score >= 4
Show me tier 1 stocks sorted by volatility score
Stocks with IV rank above 0.5 and positive VRP
ETFs with the highest option score
```

### 2. Multi-Ticker Comparisons

Side-by-side metric comparisons across specific tickers.

```
Compare momentum score of AAPL vs MSFT
Compare VRP of TSLA and NVDA for the past 10 days
What's the skew for SPY, QQQ, and IWM?
Show IV rank and HV30 for tier 1 stocks
```

### 3. Historical Data & Trends

Time-series queries across multiple trading days.

```
VRP of NVDA for the last 10 trading days
Momentum Score of AAPL last 5 days
Show IV rank history for TSLA over the past 20 days
How has the 1-month skew changed for SPY this week?
```

### 4. Changes Over Time

Identify shifts vs prior periods.

```
Biggest momentum score increases vs yesterday
Which stocks had the largest IV rank change this week?
Stocks with price increase > 20% in the last 30 days
```

### 5. Distance from Key Levels

Search by proximity to gamma levels, 52-week extremes, swing levels.

```
Stocks closest to call resistance
Tickers within 2% of their high vol level
Which stocks are near their 52-week high?
Stocks furthest below their 52-week high
How far is NVDA from put support?
```

### 6. Change-Based Screening

Find tickers where metrics moved significantly vs prior periods.

```
Stocks that have seen an increase in open interest vs 1 week ago > 40%
Stocks that have seen an increase in IV Rank vs 1 month ago > 20%
```

### 7. Extreme Positioning with Percentiles

Identify extreme positioning relative to historical ranges.

```
Stocks with GEX percentile (3M) above 90
Tickers with DEX percentile (1Y) below 10
Stocks with skew percentile (1Y) above 95
Names with VRP percentile (3M) below 5
```

### 8. Composite / Advanced Prompts

Multi-condition screens combining several metrics.

```
Show the top 20 stocks by market cap with Swing Model Bias = Bullish
Show me the top 20 Stocks with Momentum Score above 4, IV Rank below 30%, and Positive GEX
Show me ETFs that are within 5% of Put Support
Show me Stocks near Call Resistance with high IV Rank and Positive GEX
Show me stocks with term structure slope = Contango
Show me the top 20 stocks with the highest skew 1m percentile 1y and market cap > 10 billions
Show me stocks that are in the 90% OI Calls Percentile 3m
Show me the strongest pinning zones on 0DTEs for SPX
Compare Call OI vs Put OI buildup for NVDA over the past 10 days. Which side is accumulating faster?
Compare Total Gamma Exposure for Tech vs Financial sector (XLK vs XLF) over the past month
Compare the Option Matrix of NVDA of today with 7 days ago
Show me the VRP trend for SPY over the past 2 weeks. Is Implied Vol consistently overpricing or underpricing?
```

---

## Query Syntax Notes

- **Plain English** — no special syntax, operators, or formatting required
- **Time references** work naturally: "past 10 days", "this week", "last month"
- **Multiple tickers** via comma or "and": "SPY, QQQ, and IWM"
- **Filtering** uses plain language operators: "above 0.5", ">= 4", "positive", "below 10"
- **Percentile windows** reference "1Y" (1-year) or "3M" (3-month)
- **Weekends/holidays** handled automatically in time-based queries

---

## QUIN Capabilities & Limitations

**Returns:**
- Ranked data tables with relevant columns
- Outlier and anomaly highlights
- Conflicting signal flags
- Sector concentration notes
- Suggested search refinements

**Does NOT provide:**
- Trade recommendations or strategies
- Buy/sell signals
- Price direction predictions
- Trading advice
