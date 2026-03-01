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
│   ├── SYSTEM.md                 # Core agent identity and trading rules (system prompt)
│   ├── AGENTS.md                 # Project workflow, commands, file references
│   ├── extensions/
│   │   ├── trading-tools.ts      # Kelly calculator tool
│   │   └── startup-protocol.ts   # Auto-loads docs/* into context
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
├── docs/
│   ├── prompt.md                 # Spec, constraints, deliverables
│   ├── plans.md                  # Milestone workflow with validation steps
│   ├── implement.md              # Execution runbook
│   └── status.md                 # Current state and decision audit log
├── scripts/
│   ├── discover.py               # Market-wide flow scanner for new candidates
│   ├── fetch_flow.py             # Dark pool + options flow from Unusual Whales
│   ├── fetch_ticker.py           # Ticker validation via dark pool activity
│   ├── fetch_options.py          # Options chain data (stub — bring your own source)
│   ├── kelly.py                  # Kelly criterion calculator
│   └── scanner.py                # Batch scan watchlist for flow signals
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

## Glossary & Definitions

### Acronyms

| Acronym | Definition |
|---------|------------|
| **ATM** | At-The-Money — option strike price ≈ current stock price |
| **DP** | Dark Pool — private exchanges where institutional orders execute away from public markets |
| **ITM** | In-The-Money — option has intrinsic value (call: stock > strike, put: stock < strike) |
| **IV** | Implied Volatility — market's expectation of future price movement, priced into options |
| **NBBO** | National Best Bid and Offer — the best available bid/ask prices across all exchanges |
| **OI** | Open Interest — total number of outstanding option contracts not yet closed or exercised |
| **OTC** | Over-The-Counter — trades executed directly between parties, not on public exchanges |
| **OTM** | Out-of-The-Money — option has no intrinsic value (call: stock < strike, put: stock > strike) |
| **R:R** | Risk-to-Reward ratio — potential loss vs potential gain (we require ≥1:2, i.e., gain ≥ 2× loss) |
| **UW** | Unusual Whales — data provider for dark pool and options flow |
| **Vol/OI** | Volume-to-Open-Interest ratio — today's volume ÷ open interest; high ratio signals unusual activity |

### Dark Pool Metrics

| Metric | Definition | How It's Calculated |
|--------|------------|---------------------|
| **Buy Ratio** | Percentage of dark pool volume classified as buying | `buy_volume / (buy_volume + sell_volume)` — trades at/above NBBO midpoint = buys |
| **Flow Direction** | Whether institutions are net buying or selling | >55% buy ratio = ACCUMULATION, <45% = DISTRIBUTION, else NEUTRAL |
| **Flow Strength** | Intensity of the directional imbalance (0-100) | `(buy_ratio - 0.5) × 200` for accumulation, inverse for distribution |
| **Prints** | Individual dark pool transaction records | Each print shows size, price, and NBBO context at execution time |
| **Sustained Days** | Consecutive trading days with same flow direction | Counts from most recent day backward until direction changes |

### Flow Directions

| Direction | Buy Ratio | Meaning |
|-----------|-----------|---------|
| **ACCUMULATION** | ≥55% | Institutions are net buyers — bullish positioning |
| **DISTRIBUTION** | ≤45% | Institutions are net sellers — bearish positioning or profit-taking |
| **NEUTRAL** | 45-55% | No clear directional bias — noise or balanced flow |

### Discovery Score Components

| Component | Weight | Definition | Scoring Logic |
|-----------|--------|------------|---------------|
| **DP Strength** | 30% | How strong is the buy/sell imbalance? | Raw flow strength (0-100) from aggregate dark pool data |
| **DP Sustained** | 20% | How many consecutive days in same direction? | 1 day = 20 pts, 2 days = 40 pts, ... 5 days = 100 pts |
| **Confluence** | 20% | Do options flow and dark pool agree? | 100 if bullish options + DP accumulation (or bearish + distribution), else 0 |
| **Vol/OI Ratio** | 15% | Is options volume unusually high vs open interest? | Normalized: 1.0 = normal, 2.0 = 50 pts, 4.0+ = 100 pts |
| **Sweeps** | 15% | Are there sweep orders (urgent, multi-exchange fills)? | 0 sweeps = 0 pts, 1 sweep = 50 pts, 2+ sweeps = 100 pts |

### Watchlist Scanner Score Components

The watchlist scanner (`scanner.py`) uses a simpler additive scoring:

| Component | Points | Condition |
|-----------|--------|-----------|
| **Base** | 0-100 | Aggregate DP flow strength |
| **Sustained Bonus** | +20 | 2+ consecutive days same direction |
| **Sustained Bonus** | +20 | 4+ consecutive days same direction |
| **Recent Confirms** | +15 | Most recent day confirms aggregate direction with strength >50 |
| **Recent Contradicts** | -30 | Most recent day contradicts aggregate direction |
| **Low Prints Penalty** | -10 to -20 | <100 prints (statistically unreliable) |

### Options Flow Metrics

| Metric | Definition |
|--------|------------|
| **Call/Put Ratio** | `call_premium / put_premium` — >1.5 = bullish bias, <0.67 = bearish bias |
| **Premium** | Total dollar value of options contracts traded (`price × size × 100`) |
| **Sweep** | Large order split across multiple exchanges for fast execution — signals urgency |
| **Options Bias** | Directional lean: BULLISH, BEARISH, or MIXED based on call/put premium ratio |

### Trading Concepts

| Concept | Definition |
|---------|------------|
| **Convexity** | Asymmetric payoff structure where potential gain >> potential loss. We require ≥2:1. |
| **Edge** | A quantifiable, data-backed reason to believe the market is mispricing an outcome. |
| **Kelly Criterion** | Optimal bet sizing formula: `f* = p - (q/b)` where p = win probability, q = 1-p, b = odds |
| **Fractional Kelly** | Using 25-50% of full Kelly to account for estimation errors in probability |
| **Confluence** | Multiple independent signals pointing in the same direction (e.g., DP + options flow agree) |

### Signal Quality Tiers

| Score Range | Label | Interpretation |
|-------------|-------|----------------|
| **60-100** | STRONG | Sustained flow + confluence — proceed to full evaluation |
| **40-59** | MODERATE | Some components present — monitor closely, may develop |
| **20-39** | WEAK | Early stage or conflicting signals — not actionable yet |
| **0-19** | NONE | No detectable edge — skip |

### Position Sizing Rules

| Rule | Value | Rationale |
|------|-------|-----------|
| **Max per position** | 2.5% of bankroll | Hard cap regardless of Kelly output |
| **Kelly fraction** | 0.25× to 0.5× | Conservative multiplier for estimation error |
| **Max positions** | `Kelly_optimal / 2.5%` | Total exposure governed by average conviction |
| **Kelly > 20%** | Restructure | Insufficient convexity if Kelly suggests huge bet |

## Data Files

| File | Purpose |
|---|---|
| `data/portfolio.json` | Tracks bankroll, peak value, open positions, total deployment, and Kelly-derived position limits |
| `data/trade_log.json` | Append-only journal of every trade decision — opens, closes, and skips with full rationale |
| `data/watchlist.json` | Tickers under active surveillance with sector tags and notes |
| `data/ticker_cache.json` | Local cache of ticker symbols to company names and sectors |

## Web UI Console

A Next.js chat interface wraps the PI workflow and exposes the same command surface used in TUI mode.

### Run the web app

```bash
cd web
npm install
npm run dev
```

Visit `http://localhost:3000` and use slash commands in chat.

### Quick checks

```bash
cd web
npm run lint
npm run build
```

### API helper route

`/api/chat` accepts `POST` with JSON body:

```json
{ "message": "/scan --top 20" }
```

It returns normalized command payloads used by the UI to render summaries, details, and command outputs.

### Command coverage

- `/scan` → executes `scripts/scanner.py`
- `/discover` → executes `scripts/discover.py`
- `/evaluate TICKER` → executes `scripts/fetch_ticker.py`, `scripts/fetch_flow.py`, and `scripts/fetch_options.py`
- `/portfolio` → reads `data/portfolio.json`
- `/journal [--limit N]` → reads `data/trade_log.json`
- `/watchlist add|remove|list` → reads/writes `data/watchlist.json`
