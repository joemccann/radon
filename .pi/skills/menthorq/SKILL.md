---
name: menthorq
description: MenthorQ data extraction — CTA positioning, dashboards, screeners, forex, summaries, and QUIN AI screener prompts. Triggers on "menthorq", "cta positioning", "menthorq dashboard", "menthorq screener", "quin screener", "menthorq forex", or any MenthorQ-related query.
---

# MenthorQ Skill

MenthorQ has no public API. All data is behind WordPress auth, rendered as HTML tables (scrapeable) or chart images (requires Claude Vision). The `MenthorQClient` handles authentication, navigation, scraping, and image-based extraction.

## Available Commands

| Command | Script | Description |
|---------|--------|-------------|
| `menthorq-cta` | `scripts/fetch_menthorq_cta.py` | Fetch institutional CTA positioning data (S3 image + Vision extraction) |
| `menthorq-dashboard [COMMAND]` | `scripts/fetch_menthorq_dashboard.py` | Fetch dashboard chart (vol, forex, eod, intraday, futures, cryptos_technical, cryptos_options). Supports `--ticker` for eod/intraday/futures/crypto |
| `menthorq-screener [CATEGORY] [SLUG]` | via `MenthorQClient.get_screener()` | Fetch screener data (6 categories, 45 sub-screeners) |
| `menthorq-forex` | via `MenthorQClient.get_forex_levels()` | Fetch forex gamma levels + blindspot data (14 pairs, 20+ fields) |
| `menthorq-summary [CATEGORY]` | via `MenthorQClient.get_summary()` | Fetch summary tables (futures: 93 rows, cryptos: 16 rows) |
| `menthorq-quin [PROMPT]` | QUIN AI screener | Execute a natural-language screener query against MenthorQ's QUIN AI |

## Credentials

| File | Variable | Purpose |
|------|----------|---------|
| `.env` (project root) | `MENTHORQ_USER` | MenthorQ login email |
| `.env` (project root) | `MENTHORQ_PASS` | MenthorQ login password |
| `web/.env` | `ANTHROPIC_API_KEY` | Vision extraction for chart images |

## Dashboard Commands

```
menthorq-dashboard vol                    # Volatility model
menthorq-dashboard forex                  # Forex overview
menthorq-dashboard cta                    # CTA positioning
menthorq-dashboard eod --ticker spx       # End-of-day for SPX
menthorq-dashboard intraday --ticker nvda # Intraday for NVDA
menthorq-dashboard futures --ticker spx   # Futures for SPX
menthorq-dashboard cryptos_technical      # Crypto technical
menthorq-dashboard cryptos_options        # Crypto options
```

### Valid Dashboard Tickers (16)

`spx`, `vix`, `ndx`, `rut`, `spy`, `qqq`, `iwm`, `smh`, `ibit`, `nvda`, `googl`, `meta`, `tsla`, `amzn`, `msft`, `nflx`

## Screener Categories & Slugs (45 total)

| Category | Slugs |
|----------|-------|
| `gamma` (5) | `highest_gex_change`, `highest_negative_dex_change`, `highest_negative_gex_change`, `biggest_dex_expiry_next_2w`, `biggest_gex_expiry_next_2w` |
| `gamma_levels` (5) | `closer_0dte_call_resistance`, `closer_0dte_put_support`, `closer_to_HVL`, `closer_call_resistance`, `closer_put_support` |
| `open_interest` (7) | `highest_call_oi`, `highest_oi`, `highest_pc_oi`, `highest_put_oi`, `lowest_pc_oi`, `highest_oi_change`, `highest_negative_oi_change` |
| `volatility` (6) | `highest_iv30`, `highest_ivrank`, `highest_hv30`, `lowest_iv30`, `lowest_ivrank`, `lowest_hv30` |
| `volume` (6) | `highest_call_volume`, `highest_put_volume`, `highest_total_volume`, `unusual_call_activity`, `unusual_put_activity`, `unusual_activity` |
| `qscore` (16) | `highest_option_score`, `lowest_option_score`, `highest_option_score_diff`, `lowest_option_score_diff`, `highest_volatility_score`, `lowest_volatility_score`, `highest_volatility_score_diff`, `lowest_volatility_score_diff`, `highest_momentum_score`, `lowest_momentum_score`, `highest_momentum_score_diff`, `lowest_momentum_score_diff`, `highest_seasonality_score`, `lowest_seasonality_score`, `highest_seasonality_score_diff`, `lowest_seasonality_score_diff` |

## Forex Card Slugs

`forex_gamma`, `forex_blindspot`

## Summary Categories

`futures` (93 rows), `cryptos` (16 rows)

## Cache

All data cached to `data/menthorq_cache/`:
- CTA: `cta_{DATE}.json`
- Dashboard: `{command}_{DATE}.json`
- Storage state: `menthorq_storage_state.json` (persistent login)

## Client

`scripts/clients/menthorq_client.py` — `MenthorQClient`

Key methods: `get_cta()`, `get_eod()`, `get_dashboard_image()`, `get_forex_levels()`, `get_summary()`, `get_screener()`, `get_screener_category()`, `get_all_screener_data()`, `get_futures_list/detail/contracts()`, `get_forex_list/detail()`, `get_crypto_list/detail()`, `get_intraday()`

---

## QUIN AI Screener — Preset Prompts

Full prompt reference: `docs/menthorq-prompts.md`

The QUIN AI screener accepts plain English queries against 97+ metrics per ticker. No special syntax required. Below are curated preset prompts by category.

### Screening & Rankings

```
Top 10 stocks by momentum score
Technology stocks with momentum score >= 4
Show me tier 1 stocks sorted by volatility score
Stocks with IV rank above 0.5 and positive VRP
ETFs with the highest option score
```

### Multi-Ticker Comparisons

```
Compare momentum score of AAPL vs MSFT
Compare VRP of TSLA and NVDA for the past 10 days
What's the skew for SPY, QQQ, and IWM?
Show IV rank and HV30 for tier 1 stocks
```

### Historical Data & Trends

```
VRP of NVDA for the last 10 trading days
Momentum Score of AAPL last 5 days
Show IV rank history for TSLA over the past 20 days
```

### Changes Over Time

```
Biggest momentum score increases vs yesterday
Which stocks had the largest IV rank change this week?
Stocks with price increase > 20% in the last 30 days
```

### Distance from Key Levels

```
Stocks closest to call resistance
Tickers within 2% of their high vol level
Which stocks are near their 52-week high?
```

### Extreme Positioning (Percentiles)

```
Stocks with GEX percentile (3M) above 90
Tickers with DEX percentile (1Y) below 10
Stocks with skew percentile (1Y) above 95
```

### Composite / Advanced

```
Show me the top 20 Stocks with Momentum Score above 4, IV Rank below 30%, and Positive GEX
Show me ETFs that are within 5% of Put Support
Show me Stocks near Call Resistance with high IV Rank and Positive GEX
Show me stocks with term structure slope = Contango
Compare Call OI vs Put OI buildup for NVDA over the past 10 days. Which side is accumulating faster?
Show me the VRP trend for SPY over the past 2 weeks. Is Implied Vol consistently overpricing or underpricing?
```
