# Convex Scavenger

An AI-powered options trading agent built on [PI](https://github.com/mariozechner/pi-coding-agent) that hunts for asymmetric bets using institutional dark pool flow data. It enforces a strict three-gate discipline — convexity, edge, risk management — on every trade decision.

## What It Does

The Convex Scavenger operates as an autonomous trading assistant for a sub-$1M individual account. It detects institutional positioning through dark pool and OTC flow signals, constructs convex options structures around those signals, and sizes positions using fractional Kelly criterion.

**It does not generate trade ideas from narratives or technical analysis.** Every trade must pass three gates in order:

1. **Convexity** — Potential gain must be >=2x potential loss. Only defined-risk positions (long options, vertical spreads).
2. **Edge** — A specific, data-backed dark pool/OTC flow signal that hasn't yet moved price.
3. **Risk Management** — Fractional Kelly sizing with a hard cap of 2.5% of bankroll per position.

If any gate fails, no trade is taken.

## Project Structure

```
convex-scavenger/
├── .pi/                          # PI agent configuration
│   ├── AGENTS.md                 # Agent persona and workflow rules
│   ├── extensions/
│   │   └── trading-tools.ts      # Kelly calculator tool + portfolio command
│   ├── prompts/
│   │   ├── evaluate.md           # /evaluate [TICKER] — full trade analysis
│   │   ├── journal.md            # /journal — log decisions to trade_log.json
│   │   ├── portfolio.md          # /portfolio — position and exposure report
│   │   └── scan.md               # /scan — daily dark pool signal sweep
│   └── skills/
│       └── options-analysis/
│           └── SKILL.md          # Options chain analysis capability
├── data/
│   ├── portfolio.json            # Open positions, bankroll, exposure
│   ├── trade_log.json            # Append-only decision journal
│   ├── watchlist.json            # Tickers under surveillance
│   └── ticker_cache.json         # Local cache of ticker → company name mappings
├── scripts/
│   ├── discover.py               # Market-wide flow scanner for new candidates
│   ├── fetch_flow.py             # Dark pool + options flow from Unusual Whales
│   ├── fetch_ticker.py           # Ticker validation via dark pool activity
│   ├── fetch_options.py          # Options chain data (stub — bring your own source)
│   ├── kelly.py                  # Kelly criterion calculator
│   └── scanner.py                # Batch scan watchlist for flow signals
├── persona.md                    # Full agent persona specification
└── README.md
```

## Prerequisites

- [PI coding agent](https://github.com/mariozechner/pi-coding-agent) installed and configured
- Python 3.10+
- An [Unusual Whales](https://unusualwhales.com) API key for dark pool / flow data

## Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USER/convex-scavenger.git
cd convex-scavenger
```

### 2. Set your API key

The `fetch_flow.py` script requires an Unusual Whales API token:

```bash
export UW_TOKEN="your-unusual-whales-api-key"
```

Add this to your shell profile (`.zshrc`, `.bashrc`, etc.) to persist across sessions.

### 3. Configure your options data source

`scripts/fetch_options.py` is a stub. Replace the placeholder with your preferred data source:

- Yahoo Finance (free, delayed)
- Tradier API
- Schwab / TD Ameritrade API
- Interactive Brokers TWS API
- CBOE DataShop

### 4. Launch the agent

Open the project in PI:

```bash
pi convex-scavenger/
```

The agent loads the persona from `.pi/AGENTS.md` and has four core commands:

| Command | What it does |
|---|---|
| `scan` | Run the daily signal scanner across your watchlist |
| `evaluate [TICKER]` | Full three-gate analysis: edge confirmation, convexity screening, Kelly sizing |
| `portfolio` | Current positions, exposure, capacity, and drawdown |
| `journal` | Log a trade decision (open, close, or skip) to the trade log |

### 5. Seed your watchlist

Edit `data/watchlist.json` to add tickers you want the scanner to monitor:

```json
{
  "last_updated": null,
  "tickers": [
    {"ticker": "AAPL", "sector": "Technology", "notes": "Watching for accumulation"},
    {"ticker": "XOM", "sector": "Energy", "notes": "Distribution pattern forming"}
  ]
}
```

### 6. Run your first scan

Tell the agent `scan` and it will pull dark pool flow data for every ticker in your watchlist, classify signals, and report candidates worth evaluating.

## Workflow

A typical session looks like this:

1. **Scan** — `scan` to pull fresh flow data and identify candidates
2. **Evaluate** — `evaluate AAPL` to run the three-gate analysis on a candidate
3. **Execute** — If all gates pass, the agent specifies the exact structure and size
4. **Journal** — `journal` to log the decision with full rationale
5. **Monitor** — `portfolio` to check positions, exposure, and approaching expiries

## Tools

The agent has a built-in Kelly calculator available as a PI extension tool (`kelly_calc`). It accepts probability of winning, odds ratio, Kelly fraction, and optional bankroll to output full Kelly, fractional Kelly, dollar sizing, and a recommendation.

The Python scripts can also be run standalone:

```bash
# Discover new candidates from market-wide options flow
python scripts/discover.py --min-premium 500000 --dp-days 3

# Fetch dark pool flow for a ticker (last 5 trading days)
python scripts/fetch_flow.py AAPL --days 5

# Validate a ticker and check cache
python scripts/fetch_ticker.py AAPL

# Add a ticker to the local cache
python scripts/fetch_ticker.py AAPL --add-cache "Apple Inc." "Technology"

# Calculate Kelly sizing
python scripts/kelly.py --prob 0.35 --odds 3.5 --fraction 0.25 --bankroll 100000

# Scan entire watchlist
python scripts/scanner.py --top 15
```

## Discovery & Scoring

### Finding New Candidates

The `discover.py` script scans market-wide options flow to find tickers **not already on your watchlist** that show unusual institutional activity. It then cross-references with dark pool data to confirm the signal.

```bash
python scripts/discover.py --min-premium 500000 --min-alerts 1 --dp-days 3
```

**Filters (not scoring components):**
- `--min-premium` — Minimum premium per options alert (default $500K)
- `--min-alerts` — Minimum number of alerts for a ticker (default 1)
- `--dp-days` — Days of dark pool history to analyze (default 3)

### Normalized Scoring (0-100 Scale)

Candidates are scored on **edge quality**, not dollar size. The score is a weighted sum of normalized components:

| Component | Weight | What It Measures | Scale |
|-----------|--------|------------------|-------|
| **DP Strength** | 30% | Dark pool flow strength (buy/sell imbalance) | 0-100 from aggregate |
| **DP Sustained** | 20% | Consecutive days in same direction | 0-5 days → 0-100 |
| **Confluence** | 20% | Options bias aligns with DP direction | Binary: 0 or 100 |
| **Vol/OI Ratio** | 15% | Unusual options volume vs open interest | Normalized, capped |
| **Sweeps** | 15% | Sweep trades present (urgency signal) | 0/1/2+ → 0/50/100 |

**Score interpretation:**
- **60-100**: Strong signal — sustained flow with confluence, worth full evaluation
- **40-59**: Moderate signal — some components missing, monitor closely
- **20-39**: Weak signal — early stage or conflicting data
- **0-19**: No actionable signal

**Example score breakdown:**

```
TLT Score: 56.9/100
├── DP Strength:  16.4 pts (54.5% strength × 30% weight)
├── DP Sustained: 12.0 pts (3 days × 20% weight)  
├── Confluence:   20.0 pts (bullish options + DP accumulation)
├── Vol/OI:        1.0 pts (1.14 ratio, slightly above normal)
└── Sweeps:        7.5 pts (1 sweep trade)
```

### Why Premium Isn't in the Score

Dollar premium is used as a **filter** (minimum $500K to qualify), not a scoring component, because:

1. Large-cap names naturally have higher premium flow
2. $25M in NVDA flow ≠ stronger edge than $2M in a mid-cap
3. Edge quality comes from sustained direction and confluence, not size

### Trading Day Logic

All scripts automatically skip weekends and market holidays when fetching historical data. This prevents empty data issues on Saturdays/Sundays and ensures accurate "last N trading days" calculations.

### Ticker Cache

The `data/ticker_cache.json` file stores `{ticker: {company_name, sector}}` mappings locally. This reduces API calls and provides company metadata that Unusual Whales doesn't include in flow data.

```bash
# Add to cache manually
python scripts/fetch_ticker.py NVDA --add-cache "NVIDIA Corporation" "Technology/Semiconductors"
```

The cache is checked automatically during ticker validation.

## Data Files

| File | Purpose |
|---|---|
| `data/portfolio.json` | Tracks bankroll, peak value, open positions, total deployment, and Kelly-derived position limits |
| `data/trade_log.json` | Append-only journal of every trade decision — opens, closes, and skips with full rationale |
| `data/watchlist.json` | Tickers under active surveillance with sector tags and notes |
