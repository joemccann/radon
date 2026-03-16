---
name: html-report
description: Generate styled HTML reports using the Terminal Dashboard theme. Use when the user needs to create an HTML report, dashboard, data visualization, or styled output document. Triggers include requests to "generate a report", "create an HTML file", "make a dashboard", "visualize data", "export to HTML", or any task requiring formatted HTML output with the project's visual identity.
---

# HTML Report Generation

Generate professional HTML reports using the Terminal Dashboard theme — a precision-focused, monochromatic, high-density design supporting both dark and light modes.

## Quick Start

1. **Read the template:** `.pi/skills/html-report/template.html`
2. **Replace placeholders:**
   - `{{TITLE}}` → Report title (appears in browser tab)
   - `{{BODY}}` → Your report content HTML
3. **Write to:** `reports/[report-name].html`

```python
# Example generation pattern
template = read("/.pi/skills/html-report/template.html")
html = template.replace("{{TITLE}}", "LEAP IV Scan | 2026-03-03")
html = html.replace("{{BODY}}", body_content)
write("reports/leap-iv-scan.html", html)
```

## Template Features

The template (`template.html`) includes:

- ✅ Full CSS with dark/light mode support
- ✅ System preference detection (`prefers-color-scheme`)
- ✅ Theme toggle button (just add the button HTML in your body)
- ✅ Inter (UI/titles) + IBM Plex Mono (numeric/telemetry) fonts loaded
- ✅ All component styles pre-defined
- ✅ Print-friendly styles
- ✅ Responsive grid utilities

**You only write the `<body>` content — no `<head>` needed!**

## Body Structure Template

```html
<!-- HEADER (always include) -->
<header class="header">
  <div>
    <h1 class="title">Report Title</h1>
    <p class="subtitle">Optional subtitle or description</p>
  </div>
  <div class="header-actions">
    <span class="timestamp">Generated: 2026-03-03 11:30 PST</span>
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>

<!-- METRICS (optional) -->
<div class="metrics">
  <div class="metric">
    <div class="metric-label">Label</div>
    <div class="metric-value">Value</div>
    <div class="metric-change">Change note</div>
  </div>
  <!-- more metrics... -->
</div>

<!-- CONTENT PANELS -->
<div class="panel">
  <div class="panel-header">Section Title</div>
  <div class="panel-body">
    Content here
  </div>
</div>

<!-- OR TABLES -->
<div class="panel">
  <div class="panel-header">Data Table</div>
  <table>
    <thead>
      <tr>
        <th>Column</th>
        <th class="text-right">Number</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Value</td>
        <td class="text-right">123.45</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- FOOTER (optional) -->
<div class="footer">
  <p>Footer text · Additional info</p>
</div>
```

## Available CSS Classes

### Layout
| Class | Usage |
|-------|-------|
| `.container` | Auto-applied, max-width 1400px |
| `.header` | Report header with flex layout |
| `.grid-2` | Two-column grid (2fr 1fr) |
| `.grid-3` | Three equal columns |
| `.grid-4` | Four equal columns |
| `.divider` | Horizontal rule with margin |

### Panels & Cards
| Class | Usage |
|-------|-------|
| `.panel` | Container with border |
| `.panel-header` | Section title bar |
| `.panel-body` | Padded content area |
| `.panel-accent` | Highlighted border |
| `.card` | Standalone card |
| `.card-accent` | Highlighted card |

### Metrics
| Class | Usage |
|-------|-------|
| `.metrics` | Auto-fit grid container |
| `.metric` | Single metric block |
| `.metric-label` | Small uppercase label |
| `.metric-value` | Large value display |
| `.metric-value.large` | 32px value |
| `.metric-value.small` | 18px value |
| `.metric-change` | Secondary info with top border |

### Tables
| Class | Usage |
|-------|-------|
| `table` | Full-width, collapsed borders |
| `th` | Sticky header, uppercase |
| `tr:hover` | Row highlight on hover |
| `tr.highlight` | Permanently highlighted row |

### Text
| Class | Usage |
|-------|-------|
| `.text-right` | Right align |
| `.text-center` | Center align |
| `.text-muted` | Meta / supporting text (`#475569` dark) |
| `.text-positive` | Green |
| `.text-negative` | Red |
| `.text-warning` | Orange |
| `.text-small` | 11px |
| `.text-uppercase` | Uppercase + letter-spacing |
| `.text-mono` | IBM Plex Mono (numeric/telemetry) |
| `.text-sans` | Inter (UI/titles) |

### Pills / Badges
| Class | Usage |
|-------|-------|
| `.pill` | Default bordered badge |
| `.pill-accent` | Inverted (white/black) |
| `.pill-positive` | Green background |
| `.pill-negative` | Red background |
| `.pill-warning` | Orange background |

### Status Indicators
```html
<span class="status">
  <span class="status-dot"></span>
  LIVE
</span>

<!-- Variants -->
<span class="status-dot positive"></span>
<span class="status-dot negative"></span>
<span class="status-dot warning"></span>
<span class="status-dot static"></span>  <!-- No animation -->
```

### Buttons
| Class | Usage |
|-------|-------|
| `.btn` | Default button |
| `.btn-accent` | Inverted button |
| `.theme-toggle` | Theme switch button |

### Special Components
```html
<!-- Callout box -->
<div class="callout">
  <div class="callout-title">Note</div>
  <p>Content here</p>
</div>
<div class="callout positive">...</div>
<div class="callout negative">...</div>
<div class="callout warning">...</div>

<!-- Section header -->
<div class="section-header">Section Name</div>

<!-- Progress bar -->
<div class="bar-container">
  <div class="bar-fill" style="width: 75%"></div>
</div>
<div class="bar-container">
  <div class="bar-fill positive" style="width: 60%"></div>
</div>
```

## Color Tokens

Use CSS variables for theme compatibility:

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `--bg-base` | `#0a0f14` | `#FFFFFF` | Page background |
| `--bg-panel` | `#0f1519` | `#FFFFFF` | Panel background |
| `--bg-hover` | `#151c22` | `#f1f5f9` | Hover state |
| `--border-dim` | `#1e293b` | `#BBBFBF` | Default borders |
| `--border-focus` | `#05AD98` | `#05AD98` | Active borders |
| `--text-primary` | `#e2e8f0` | `#000000` | Main text |
| `--text-secondary` | `#94a3b8` | `#636363` | Secondary text |
| `--text-muted` | `#475569` | `#878787` | Meta / supporting text |
| `--accent-bg` | `#05AD98` | `#05AD98` | Inverted bg |
| `--accent-text` | `#0a0f14` | `#FFFFFF` | Inverted text |
| `--positive` | `#05AD98` | `#048A7A` | Positive / green |
| `--negative` | `#E85D6C` | `#D4183D` | Negative / red |
| `--warning` | `#F5A623` | `#D4910A` | Caution / orange |
| `--signal-core` | `#05AD98` | `#05AD98` | Core Radon accent |
| `--signal-strong` | `#0FCFB5` | `#048A7A` | High-confidence signal |
| `--signal-deep` | `#048A7A` | `#037066` | Deep data / selected |
| `--dislocation` | `#D946A8` | `#C026A0` | Structural dislocation |
| `--extreme` | `#8B5CF6` | `#7C3AED` | Extreme / rare state |
| `--fault` | `#E85D6C` | `#D4183D` | Feed fault / integrity |
| `--neutral` | `#94a3b8` | `#878787` | Neutral state |

## Example: Minimal Report

```html
{{BODY}} content:

<header class="header">
  <div>
    <h1 class="title">Daily Summary</h1>
  </div>
  <div class="header-actions">
    <span class="timestamp">2026-03-03</span>
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>

<div class="metrics">
  <div class="metric">
    <div class="metric-label">Positions</div>
    <div class="metric-value">12</div>
  </div>
  <div class="metric">
    <div class="metric-label">P&L</div>
    <div class="metric-value text-positive">+$4,500</div>
  </div>
</div>

<div class="panel">
  <div class="panel-header">Open Positions</div>
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th class="text-right">Value</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>AAPL</td>
        <td class="text-right">$15,000</td>
        <td><span class="pill pill-positive">PROFIT</span></td>
      </tr>
    </tbody>
  </table>
</div>
```

## File Naming Convention

```
reports/
├── portfolio-2026-03-03.html
├── leap-iv-scan-2026-03-03.html
├── flow-scan-AAPL.html
├── trade-journal-2026-03.html
└── pnl-EWY-2026-03-03.html
```

---

## Portfolio Report Template ⭐

**For the `portfolio` command, ALWAYS use the dedicated portfolio template.**

**Template:** `.pi/skills/html-report/portfolio-template.html`
**Script:** `scripts/portfolio_report.py`
**Output:** `reports/portfolio-{date}.html`

### When to Use

- `portfolio` command (auto-generates and opens in browser)
- Any request for portfolio status, P&L overview, position review
- Startup portfolio health check

### How It Works

The script (`portfolio_report.py`) is **self-contained**:
1. Connects to IB → fetches all positions + live prices
2. Groups legs into logical structures (spreads, risk reversals, etc.)
3. Fetches 5-day dark pool flow for every ticker **including today** (parallel, 8 workers)
4. Loads trade log for thesis comparisons
5. Fills the template placeholders → writes HTML → opens browser

**You do NOT need to fetch data separately.** Just run:
```bash
python3 scripts/portfolio_report.py
```

### 8 Required Sections

Every portfolio report MUST include these sections (in order):

| # | Section | Template Placeholder | Data Source |
|---|---------|---------------------|-------------|
| 1 | **Header** | `{{STATUS_CLASS}}`, `{{STATUS_TEXT}}`, `{{TIMESTAMP}}` | Computed from attention counts |
| 2 | **Data Freshness Banner** | `{{FRESHNESS_CLASS}}`, `{{FRESHNESS_TEXT}}` | Market hours check |
| 3 | **Summary Metrics** (6 cards) | `{{METRICS_HTML}}` | IB account values |
| 4 | **Quick-Stat Badges** | `{{QUICK_STATS_HTML}}` | Position analysis |
| 5 | **Attention Callouts** | `{{ATTENTION_HTML}}` | Expiring, stops, winners, undefined risk |
| 6 | **Thesis Check** | `{{THESIS_SECTION_HTML}}` | Trade log + dark pool flow |
| 7 | **All Positions Table** | `{{POSITION_ROWS_HTML}}` | IB positions + live prices |
| 8 | **Dark Pool Flow** | `{{FLOW_ROWS_HTML}}` | UW dark pool API |
| — | **Footer** | `{{FOOTER_SUMMARY}}` | Computed summary |

### ⚠️ Today-Highlighting (MANDATORY for Sections 6, 7, 8)

Any section that displays time-series flow data **MUST visually highlight today's data point**:

**Sparkline bars** use the `.spark-bar.today` CSS class:
- Adds a **white outline ring** around today's bar
- The `today →` label appears below the sparkline
- Bars are colored: green (accumulation ≥70%), red (distribution ≤30%), grey (neutral)

**Today column** in the flow table shows the LIVE tag:
```html
<span class="flow-dir accumulation">72%</span><span class="today-tag">LIVE</span>
```

**Data freshness banner** at the top of the report shows market status:
- Market OPEN: green pulsing dot + "All prices and flow data include **today (YYYY-MM-DD)**"
- Market CLOSED: amber static dot + "Using closing prices from last session"

**Why this matters:** A scan from yesterday may show ACCUMULATION but today's flow could be DISTRIBUTION. The today-highlight forces the reader to check whether the current day confirms or breaks the pattern.

### Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{DATE}}` | Report date | 2026-03-06 |
| `{{TIMESTAMP}}` | Full timestamp | 2026-03-06 09:20 PST |
| `{{STATUS_CLASS}}` | Header dot color | `positive` / `negative` / `warning` |
| `{{STATUS_TEXT}}` | Header status | `3 ACTIONS NEEDED` / `ALL POSITIONS ACTIVE` |
| `{{FRESHNESS_CLASS}}` | Banner class | `` (live) or `stale` (closed) |
| `{{FRESHNESS_TEXT}}` | Banner content | `📊 ... Market OPEN ... include today` |
| `{{METRICS_HTML}}` | 6 metric cards | Net Liq, P&L, Deployed, Margin, Positions, Kelly |
| `{{QUICK_STATS_HTML}}` | 3 badge panels | Expiring, At Stop, Winners |
| `{{ATTENTION_HTML}}` | Callout blocks | 🔴 Expiring, 🟡 Stop, 🟢 Winners, ⛔ Undefined |
| `{{THESIS_SECTION_HTML}}` | Full thesis table | Entry flow vs current flow with sparklines |
| `{{POSITION_ROWS_HTML}}` | `<tr>` rows | All positions sorted by DTE |
| `{{FLOW_ROWS_HTML}}` | `<tr>` rows | All tickers with sparkline + today cell |
| `{{FOOTER_SUMMARY}}` | Summary line | `27 positions · $1.2M net liq · 134% deployed` |

### Portfolio-Specific CSS Components

These are defined in the template (not in the base `template.html`):

| Component | CSS Class | Purpose |
|-----------|-----------|---------|
| Freshness banner | `.freshness-banner` | Data recency indicator at top |
| Sparkline | `.spark` + `.spark-bar` | Mini bar chart for daily flow |
| Today highlight | `.spark-bar.today` | White outline ring on today's bar |
| Today label | `.spark-today-label` | "today →" text under sparkline |
| Flow direction | `.flow-dir.accumulation/.distribution/.neutral` | Colored direction text |
| Today tag | `.today-tag` | Black/white "LIVE" inline badge |
| Progress bar | `.progress-container` + `.progress-fill` | For free-trade % (extensible) |
| Count badge | `.count-badge` + `.alert/.success` | Quick-stat numbers |

### Room for Innovation

The 8 sections above are the **required minimum**. You can add additional sections between Section 7 (Flow) and Section 8 (Footer) for ad-hoc analysis. Ideas:

- **Free Trade Progress** — Progress bars for multi-leg positions approaching free status
- **Sector Heatmap** — Group positions by sector, show aggregate flow
- **Expiry Calendar** — Visual timeline of upcoming expirations
- **Kelly Capacity** — Detailed breakdown of capital allocation vs. Kelly optimal
- **Flow Divergence Alerts** — Positions where flow reversed since entry
- **Correlation Matrix** — Which positions move together

Add these by inserting HTML before the `{{FOOTER_SUMMARY}}` replacement, or by adding new placeholder variables to the template.

### Reference Implementation

See: `reports/portfolio-2026-03-06.html`

---

## P&L Report Template

**For any trade P&L or reconciliation report, use the dedicated P&L template.**

**Template:** `.pi/skills/html-report/pnl-template.html`

### When to Use

- Trade closed → generate P&L report
- P&L reconciliation requested
- Historical trade analysis
- Spread P&L breakdown

### P&L Template Features

Everything in the base template PLUS:
- Timeline component for trade history
- Subtotal/total row styles for tables
- Panel accent variants (positive/negative border)
- Optimized metric sizing (28px default, fits 6-digit amounts)

### Required Sections

Every P&L report MUST include:

#### 1. Header with Status Pill
```html
<header class="header">
  <div>
    <h1 class="title">{{TICKER}} {{STRATEGY}} — P&L Reconciliation</h1>
    <p class="subtitle">{{DESCRIPTION}} · {{EXPIRY}}</p>
  </div>
  <div class="header-actions">
    <span class="pill pill-positive">CLOSED</span>  <!-- or pill-negative for loss -->
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>
```

#### 2. Summary Metrics (4 required)
```html
<div class="metrics">
  <div class="metric">
    <div class="metric-label">Realized P&L</div>
    <div class="metric-value text-positive">+$17,651</div>  <!-- or text-negative -->
    <div class="metric-change">Net of all commissions</div>
  </div>
  <div class="metric">
    <div class="metric-label">Total Commissions</div>
    <div class="metric-value">$168.60</div>
    <div class="metric-change">{{NUM_ROUNDTRIPS}} round-trips</div>
  </div>
  <div class="metric">
    <div class="metric-label">Hold Period</div>
    <div class="metric-value">{{DAYS}} days</div>
    <div class="metric-change">{{OPEN_DATE}} → {{CLOSE_DATE}}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Return on Risk</div>
    <div class="metric-value text-positive">+106.8%</div>  <!-- P&L / Capital at Risk -->
    <div class="metric-change">On ${{CAPITAL_AT_RISK}} {{DEBIT_OR_CREDIT}}</div>
  </div>
</div>
```

#### 3. Trade Summary Callout
```html
<div class="callout positive">  <!-- or "callout negative" for losses -->
  <div class="callout-title">Trade Summary</div>
  <p><strong>Strategy:</strong> {{STRATEGY_DESCRIPTION}}</p>
  <p><strong>Thesis:</strong> {{THESIS}}</p>
  <p><strong>Outcome:</strong> {{OUTCOME_DESCRIPTION}}</p>
</div>
```

#### 4. Execution Table (per leg for spreads)
```html
<div class="panel">
  <div class="panel-header">{{LEG_DESCRIPTION}}</div>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Action</th>
        <th class="text-right">Qty</th>
        <th class="text-right">Price</th>
        <th class="text-right">Cash Flow</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>{{DATE}}</td>
        <td><span class="text-positive">● BUY TO OPEN</span></td>
        <td class="text-right">{{QTY}}</td>
        <td class="text-right">${{PRICE}}</td>
        <td class="text-right text-negative">-${{AMOUNT}}</td>
      </tr>
      <!-- More rows... -->
      <tr class="subtotal">
        <td colspan="3"><strong>{{LEG}} Subtotal</strong></td>
        <td class="text-right"><strong>CLOSED</strong></td>
        <td class="text-right text-positive"><strong>+${{LEG_PNL}}</strong></td>
      </tr>
    </tbody>
  </table>
</div>
```

#### 5. Combined P&L Panel (for spreads)
```html
<div class="panel panel-accent">  <!-- or panel-accent negative -->
  <div class="panel-header">Combined Spread P&L</div>
  <table>
    <tbody>
      <tr>
        <td>{{LEG_1}} P&L</td>
        <td class="text-right text-positive">+${{LEG_1_PNL}}</td>
      </tr>
      <tr>
        <td>{{LEG_2}} P&L</td>
        <td class="text-right text-negative">-${{LEG_2_PNL}}</td>
      </tr>
      <tr>
        <td>Total Commissions</td>
        <td class="text-right text-muted">(${{TOTAL_COMM}})</td>
      </tr>
      <tr class="total">
        <td><strong>NET REALIZED P&L</strong></td>
        <td class="text-right text-positive"><strong>+${{NET_PNL}}</strong></td>
      </tr>
    </tbody>
  </table>
</div>
```

#### 6. Trade Timeline
```html
<div class="panel">
  <div class="panel-header">Trade Timeline</div>
  <div class="panel-body">
    <div class="timeline">
      <div class="timeline-item buy">
        <div class="timeline-date">{{OPEN_DATE_FORMATTED}}</div>
        <div class="timeline-action">OPENED {{STRATEGY}}</div>
        <div class="timeline-detail">
          {{OPEN_DETAILS}}<br>
          <strong>Net {{DEBIT_OR_CREDIT}}: ${{OPEN_AMOUNT}}</strong>
        </div>
      </div>
      <div class="timeline-item sell">
        <div class="timeline-date">{{CLOSE_DATE_FORMATTED}}</div>
        <div class="timeline-action">CLOSED {{STRATEGY}}</div>
        <div class="timeline-detail">
          {{CLOSE_DETAILS}}<br>
          <strong>Net {{CREDIT_OR_DEBIT}}: ${{CLOSE_AMOUNT}}</strong>
        </div>
      </div>
    </div>
  </div>
</div>
```

#### 7. Footer
```html
<div class="footer">
  <p>Generated by Trade Blotter · Data from IB Flex Query + Real-time API · {{DATE}}</p>
</div>
```

### Return on Risk Calculation

**Always calculate Return on Risk as:**

```
Return on Risk = Realized P&L / Capital at Risk

Where Capital at Risk =
  - For DEBIT spreads: Net debit paid to open
  - For CREDIT spreads: Max loss (spread width - credit received)
  - For long options: Premium paid
  - For stock: Total cost basis
```

### Example P&L Report Reference

See: `reports/ewy-pnl-reconciliation-2026-03-03.html`

---

## Trade Specification Template ⭐ PRIMARY

**For ANY trade recommendation, ALWAYS use this template.**

**Template:** `.pi/skills/html-report/trade-specification-template.html`

### When to Use

- **ALWAYS** when recommending a trade after evaluation
- **ALWAYS** when presenting a trade for execution confirmation
- Ticker evaluation results (whether TRADE or NO_TRADE)
- Full milestone-based evaluation output

### Template Structure

The trade specification template includes ALL sections needed for a complete evaluation:

1. **Header** — Ticker, company, price, gate status
2. **Summary Metrics** — 6 key metrics (signal score, buy ratio, flow strength, convexity, position size, max gain)
3. **Milestone Summary** — All 7 milestones with pass/fail status
4. **Dark Pool Flow Section** — Daily breakdown + aggregate analysis
5. **Options Flow Section** — Chain bias, institutional flow, combined signal
6. **Context Section** — Seasonality + analyst ratings
7. **Structure & Kelly** — Position structure and Kelly sizing
8. **Trade Specification** — Exact order details ready for execution
9. **Thesis & Risk Factors** — Callouts with reasoning
10. **Three Gates Summary** — Final gate check table

### Template Variables

Replace these placeholders with actual values:

| Variable | Description | Example |
|----------|-------------|---------|
| `{{TICKER}}` | Stock symbol | GOOG |
| `{{COMPANY_NAME}}` | Full company name | Alphabet Inc. (Class C) |
| `{{SECTOR}}` | Industry sector | Communication Services |
| `{{CURRENT_PRICE}}` | Current stock price | 302.38 |
| `{{DATE}}` | Evaluation date | 2026-03-04 |
| `{{TIMESTAMP}}` | Full timestamp | 2026-03-04 06:43 PST |
| `{{STATUS_TEXT}}` | Gate status | ALL GATES PASSED |
| `{{STATUS_CLASS}}` | CSS class | positive / negative / warning |
| `{{SIGNAL_SCORE}}` | Combined signal score | 129.7 |
| `{{BUY_RATIO}}` | Dark pool buy ratio % | 94.87 |
| `{{FLOW_STRENGTH}}` | Flow strength 0-100 | 89.7 |
| `{{RR_RATIO}}` | Risk:reward ratio | 3.0 |
| `{{POSITION_SIZE}}` | Total position cost | 27,544 |
| `{{POSITION_PCT}}` | % of bankroll | 2.46 |
| `{{MAX_GAIN}}` | Maximum profit | 82,456 |
| `{{NUM_CONTRACTS}}` | Number of contracts | 44 |
| `{{DTE}}` | Days to expiration | 43 |
| `{{NET_DEBIT}}` | Net debit per spread | 6.26 |
| `{{THESIS}}` | Trade thesis | Extraordinary institutional... |

### Milestone Variables

For each milestone (M1, M1B, M1C, M1D, M2, M3, M3B, M4, M5, M6):

| Variable Pattern | Values |
|------------------|--------|
| `{{MX_CLASS}}` | `complete` or `failed` or empty |
| `{{MX_DESCRIPTION}}` | Description text |
| `{{MX_GATE_CLASS}}` | `gate-pass` or `gate-fail` |
| `{{MX_RESULT}}` | `✓ PASS` or `✗ FAIL` or result text |

### Gate Summary Variables

| Variable | Description |
|----------|-------------|
| `{{GATE1_ACTUAL}}` | e.g., "3.0:1" |
| `{{GATE1_PILL}}` | `pill-positive` or `pill-negative` |
| `{{GATE1_STATUS}}` | `PASS` or `FAIL` |
| `{{GATE2_ACTUAL}}` | e.g., "89.7, 5 days" |
| `{{GATE3_ACTUAL}}` | e.g., "2.46%" |

### NO_TRADE Reports

When evaluation fails a gate, still generate the report but:

1. Set `{{STATUS_TEXT}}` to failing gate (e.g., "EDGE FAILED")
2. Set `{{STATUS_CLASS}}` to `negative`
3. Mark failed milestone with `failed` class
4. Omit Structure & Kelly sections (not reached)
5. Omit Trade Specification section
6. Include callout explaining why trade was rejected

### Output Location

```
reports/{ticker}-evaluation-{date}.html

Examples:
  reports/goog-evaluation-2026-03-04.html
  reports/amd-evaluation-2026-03-04.html
```

### Reference Implementation

See: `reports/goog-evaluation-2026-03-04.html`

---

## Risk Reversal Report Template ⭐

**For the `risk-reversal` command, ALWAYS use the dedicated risk reversal template.**

**Template:** `.pi/skills/html-report/risk-reversal-template.html`
**Script:** `scripts/risk_reversal.py`
**Output:** `reports/{ticker}-risk-reversal-{date}.html`

### When to Use

- `risk-reversal [TICKER]` command (auto-generates and opens in browser)
- Any request involving selling puts + buying calls (or inverse) as a directional bet
- IV skew analysis for a specific ticker's options chain

### How It Works

The script (`risk_reversal.py`) is **self-contained**:
1. Fetches dark pool flow and options flow for context (via subprocess to fetch_flow.py / fetch_options.py)
2. Connects to IB → fetches spot price, option chains, live greeks (25-50Δ puts and calls)
3. Builds the full risk reversal matrix across 2-5 expirations (14-60 DTE)
4. Computes IV skew per delta bucket per expiry
5. Selects 3 recommendations: Primary (costless), Alternative (different expiry), Aggressive (credit)
6. Fills the template → writes HTML → opens browser

**You do NOT need to fetch data separately.** Just run:
```bash
python3 scripts/risk_reversal.py IWM
```

### 8 Required Sections

Every risk reversal report MUST include these sections (in order):

| # | Section | Template Placeholder | Data Source |
|---|---------|---------------------|-------------|
| 1 | **Header** | `{{TICKER}}`, `{{DIRECTION}}`, `{{TIMESTAMP}}` | Script args + clock |
| 2 | **Summary Metrics** (6 cards) | `{{METRICS_HTML}}` | Spot, skew, DP, options flow, bankroll, net cost |
| 3 | **Thesis Callout** | `{{THESIS_HTML}}` | DP flow + skew rationale |
| 4 | **Dark Pool Flow** | `{{FLOW_HTML}}` | UW dark pool API (today-highlighted) |
| 5 | **IV Skew Analysis** | `{{SKEW_HTML}}` | IB greeks — put IV vs call IV per delta |
| 6 | **Recommended Trades** (3) | `{{PRIMARY_HTML}}`, `{{ALTERNATIVE_HTML}}`, `{{AGGRESSIVE_HTML}}` | Matrix analysis |
| 7 | **Full Combos Matrix** | `{{MATRIX_HTML}}` | All near-costless combos per expiry |
| 8 | **Risk & Compliance + Execution** | `{{RISK_HTML}}`, `{{EXECUTION_HTML}}` | Sizing + commands |

### Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{TICKER}}` | Stock/ETF symbol | IWM |
| `{{COMPANY_NAME}}` | Name or symbol | iShares Russell 2000 ETF |
| `{{DATE}}` | Report date | 2026-03-06 |
| `{{TIMESTAMP}}` | Full timestamp | 2026-03-06 10:32 AM PT |
| `{{DIRECTION}}` | BULLISH or BEARISH | BULLISH |
| `{{DIRECTION_LABEL}}` | Bullish or Bearish | Bullish |
| `{{DIRECTION_DETAIL}}` | Leg description | Sell Put / Buy Call |
| `{{STATUS_CLASS}}` | CSS class for status dot | positive |
| `{{METRICS_HTML}}` | 6 metric card divs | Spot, Skew, DP, P/C, Bankroll, Net |
| `{{THESIS_HTML}}` | Callout with thesis | Dark pool + skew reasoning |
| `{{FLOW_HTML}}` | Flow panel with table | Daily DP breakdown + sparklines |
| `{{SKEW_HTML}}` | Skew panel with tables | Put vs Call IV per delta per expiry |
| `{{PRIMARY_HTML}}` | Primary trade panel | Costless, balanced Δ, longer DTE |
| `{{ALTERNATIVE_HTML}}` | Alt trade panel | Different expiry |
| `{{AGGRESSIVE_HTML}}` | Aggressive trade panel | Credit-generating |
| `{{MATRIX_HTML}}` | Full combos table panel | All near-costless combos |
| `{{RISK_HTML}}` | Risk + compliance panels | Grid-2 with risk table + warning |
| `{{EXECUTION_HTML}}` | Execution commands panel | Copy-paste ib_execute.py commands |

### ⚠️ Manager Override Badge (MANDATORY)

Every risk reversal report MUST include the `MANAGER OVERRIDE` warning pill in the header:
```html
<span class="pill pill-warning">MANAGER OVERRIDE</span>
```

And the compliance panel MUST include the undefined risk callout explaining this is an explicit override.

### Reference Implementation

See: `reports/iwm-risk-reversal-2026-03-06.html`

---

## CRI Report Template

**For the `cri-scan` command, ALWAYS use the dedicated CRI template.**

**Template:** `.pi/skills/html-report/cri-template.html`
**Script:** `scripts/cri_scan.py`
**Output:** `reports/cri-scan-{date}.html`

### When to Use

- `cri-scan` command (auto-generates and opens in browser)
- Any request for crash risk assessment, CTA deleveraging analysis, systematic risk monitoring
- Market-wide stress analysis

### How It Works

The script (`cri_scan.py`) is **self-contained**:
1. Fetches 1Y daily bars for VIX, VVIX, SPY, and Cboe COR1M implied correlation (IB primary, official Cboe COR1M dashboard history fallback before Yahoo last resort)
2. Reads the latest COR1M level and 5-session change as the CRI correlation input
3. Computes 20-day realized volatility, 100-day SPX moving average
4. Scores four CRI components (VIX, VVIX, Correlation, Momentum) — each 0-25, total 0-100
5. Models CTA exposure (vol-targeting) and estimates forced selling pressure
6. Evaluates crash trigger conditions (SPX < 100d MA + RVol > 25% + COR1M > 60)
7. Fills the template → writes HTML → opens browser

**You do NOT need to fetch data separately.** Just run:
```bash
python3 scripts/cri_scan.py
```

### 7 Required Sections

Every CRI report MUST include these sections (in order):

| # | Section | Description |
|---|---------|-------------|
| 1 | **Header** | Title, date, market status, CRI level pill |
| 2 | **CRI Score Display** | Large score number with progress bar and level labels |
| 3 | **Metric Cards** (6) | VIX, VVIX, COR1M Implied Correlation, SPY vs 100d MA, Realized Vol, Crash Trigger status |
| 4 | **Component Breakdown** | Bar chart showing each component's contribution (VIX, VVIX, Correlation, Momentum) out of 25 |
| 5 | **CTA Exposure Model** | Realized vol, implied exposure, forced reduction %, estimated selling pressure |
| 6 | **Crash Trigger Conditions** | Pass/fail table for all 3 conditions with actual values |
| 7 | **Rolling 10-Day History** | Daily VIX, VVIX, SPY, vs MA%, VIX RoC |

### Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{TITLE}}` | Report title | CRI Scan — 2026-03-06 |
| `{{BODY}}` | Full report body HTML | (generated by script) |

### CRI-Specific CSS Components

These are defined in the CRI template (not in the base `template.html`):

| Component | CSS Class | Purpose |
|-----------|-----------|---------|
| Score display | `.cri-score-display` | Centered large score with level |
| Score number | `.cri-score-number` | 72px score value |
| Score bar | `.cri-bar` + `.cri-bar-fill` | Full-width score progress bar |
| Bar labels | `.cri-bar-labels` | LOW / ELEVATED / HIGH / CRITICAL labels |
| Component row | `.component-row` | Flex row for component bars |
| Component bar | `.component-bar` + `.component-bar-fill` | Individual component progress |
| Gauge | `.gauge-container` + `.gauge-fill` | CTA exposure gauge |

### Signal Level Colors

| Level | Score | Pill Class | Bar Color |
|-------|-------|-----------|-----------|
| LOW | 0-24 | `pill-positive` | `var(--positive)` |
| ELEVATED | 25-49 | `pill-warning` | `var(--warning)` |
| HIGH | 50-74 | `pill-warning` | `var(--warning)` |
| CRITICAL | 75-100 | `pill-negative` | `var(--negative)` |

### Reference Implementation

See: `reports/cri-scan-2026-03-06.html`

---

## Stress Test Report Template ⭐

**For the `stress-test` command, ALWAYS use the dedicated stress test template.**

**Template:** `.pi/skills/html-report/stress-test-template.html`
**Analysis Engine:** `scripts/scenario_analysis.py`
**Report Generator:** `scripts/scenario_report.py` (reference implementation)
**Output:** `reports/stress-test-{date}.html`

### When to Use

- `stress-test` command (interactive — prompts user for scenario, then generates)
- Any request for portfolio stress testing, scenario analysis, "what if" modeling
- Market crash simulations, sector shock analysis

### How It Works

**Two-phase interaction:**
1. Agent asks: "What is the change in the overall market?"
2. User describes scenario (e.g., "Oil up 25%, VIX at 40, SPX down 3%")
3. Agent parses scenario → updates `scenario_analysis.py` parameters → runs model → generates HTML

**The model (`scenario_analysis.py`) computes:**
- Per-ticker stock moves via: `β_SPX × ΔSPX + OilSens × ΔOil + VIX_crash_beta`
- Options repricing via Black-Scholes with IV expansion proportional to VIX change
- Three scenarios: Bear (amplified), Base (as described), Bull (dampened)

### 10 Required Sections

Every stress test report MUST include:

| # | Section | Description |
|---|---------|-------------|
| 1 | **Header** | Title, scenario description, timestamp |
| 2 | **Scenario Assumptions** | Callout with bear/base/bull definitions + model description |
| 3 | **Summary Metrics** (6) | Net Liq, Bear P&L, Base P&L, Bull P&L, VIX shock, Position count |
| 4 | **Winners/Losers** | Side-by-side callouts: 5 biggest losers + 5 biggest winners |
| 5 | **Natural Hedges** | Analysis of which positions offset losses |
| 6 | **Full Position Matrix** | Table with expandable ▶ detail rows per position |
| 7 | **Factor Attribution** | 3-column grid: Bear/Base/Bull breakdown by SPX/Oil/VIX/Vega |
| 8 | **P&L Waterfall** | Visual bar chart sorted by impact |
| 9 | **Key Takeaways** | Numbered action items |
| 10 | **Methodology** | Model description, limitations |

### Expandable Detail Rows (▶ Chevron)

**This is the key differentiator.** Every position row has a ▶ chevron that expands to show:

```html
<tr class="detail-row" id="detail-{N}" style="display:none;">
  <td colspan="16">
    <div style="padding:20px 24px; background:var(--bg-hover);">
      <!-- 4-panel grid -->
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
        <div>🛢️ Oil Impact (sensitivity coefficient)</div>
        <div>📉 S&P 500 Beta (beta × SPX move)</div>
        <div>📊 VIX Stress (crash-beta multiplier)</div>
        <div>📋 Position Structure & P&L (options mechanics)</div>
      </div>
      <!-- Net Assessment bar -->
      <div>Net Assessment: [BENEFITS/HURT/NEUTRAL] + explanation</div>
      <!-- Price scenarios -->
      <div>Bear/Base/Bull prices + current + IV data if options</div>
    </div>
  </td>
</tr>
```

The narrative MUST explain:
- **WHY** oil/commodity prices help or hurt this specific name
- **HOW** SPX beta translates to this ticker's expected move
- **WHAT** VIX stress multiplier does (crash-beta, momentum unwind, safe haven)
- **HOW** the options structure converts the stock move to P&L (vega vs delta, spread caps, assignment risk)

### Modeling Rules (HARD CONSTRAINTS)

| Rule | Why |
|------|-----|
| Single per-ticker IV | Never estimate IV per-leg (causes impossible spread states) |
| Spread P&L clamped | Debit spread: `[-debit, +max_width]`. Credit spread: `[-max_width, +credit]` |
| Long option P&L floored | Can't lose more than premium paid |
| LEAP IV dampening | >180 DTE: 50% of VIX expansion. 60-180: 75%. <60: 100% |
| VIX crash-beta threshold | Only activates when scenario VIX > 30 |
| Oil sensitivity is additive | Added on top of beta, not multiplicative |

### Template Variables

| Variable | Description |
|----------|-------------|
| `{{TITLE}}` | Report title with scenario summary |
| `{{BODY}}` | Full report body (all 10 sections) |

### Reference Implementation

See: `reports/scenario-stress-test-2026-03-08.html`
See: `scripts/scenario_report.py` (full report generation with narratives)

---

## Generation Checklist

### General Reports

1. [ ] Read template from `.pi/skills/html-report/template.html`
2. [ ] Replace `{{TITLE}}` with report title
3. [ ] Create body HTML with header (include theme toggle!)
4. [ ] Replace `{{BODY}}` with your content
5. [ ] Save to `reports/` with descriptive filename
6. [ ] Open in browser to verify

### P&L Reports

1. [ ] Read template from `.pi/skills/html-report/pnl-template.html`
2. [ ] Replace `{{TITLE}}` with "{TICKER} P&L | {DATE}"
3. [ ] Build body with ALL 7 required sections (see above)
4. [ ] Calculate Return on Risk correctly (P&L / Capital at Risk)
5. [ ] Use `text-positive` for profits, `text-negative` for losses
6. [ ] Save to `reports/pnl-{TICKER}-{DATE}.html`
7. [ ] Open in browser to verify formatting fits

### Trade Specification Reports

1. [ ] Read template from `.pi/skills/html-report/trade-specification-template.html`
2. [ ] Replace header variables: `{{TICKER}}`, `{{COMPANY_NAME}}`, `{{SECTOR}}`, `{{CURRENT_PRICE}}`, `{{DATE}}`, `{{TIMESTAMP}}`
3. [ ] Set gate status: `{{STATUS_TEXT}}` and `{{STATUS_CLASS}}` (`positive` / `negative` / `warning`)
4. [ ] Fill all 6 summary metrics: signal score, buy ratio, flow strength, R:R, position size, max gain
5. [ ] Fill milestone summary (M1–M6 including M1D): `{{MX_CLASS}}`, `{{MX_DESCRIPTION}}`, `{{MX_GATE_CLASS}}`, `{{MX_RESULT}}`
6. [ ] Build dark pool flow section with daily breakdown **including today's data**
7. [ ] Build options flow section with chain bias + institutional flow + combined signal
8. [ ] Build context section: seasonality rating + analyst ratings
9. [ ] Build structure & Kelly section with position details and Kelly math
10. [ ] Build trade specification with exact order details (contracts, strike, expiry, limit price)
11. [ ] Build thesis callout (positive) and risk factors callout (warning)
12. [ ] Fill three gates summary table: `{{GATE1_ACTUAL}}`, `{{GATE2_ACTUAL}}`, `{{GATE3_ACTUAL}}` with pills and status
13. [ ] For NO_TRADE: set `{{STATUS_CLASS}}` to `negative`, mark failed milestone, omit structure/Kelly/trade spec sections, add rejection callout
14. [ ] Save to `reports/{ticker}-evaluation-{date}.html`
15. [ ] Open in browser to verify all sections render correctly
16. [ ] Reference implementation: `reports/goog-evaluation-2026-03-04.html`

### Portfolio Reports

1. [ ] Run `python3 scripts/portfolio_report.py` — script is fully self-contained
2. [ ] Verify IB connection succeeded (positions + live prices fetched)
3. [ ] Verify dark pool flow fetched for all tickers **including today's date**
4. [ ] Verify data freshness banner shows correct market status (OPEN with green dot / CLOSED with amber dot)
5. [ ] Verify all 8 sections present:
   - [ ] Header with status dot and action count
   - [ ] Data freshness banner with today's date highlighted in bold
   - [ ] 6 summary metric cards (Net Liq, P&L, Deployed, Margin, Positions, Kelly)
   - [ ] 3 quick-stat badges (Expiring, At Stop, Winners)
   - [ ] Attention callouts (🔴 Expiring, 🟡 At Stop, 🟢 Winners, ⛔ Undefined Risk)
   - [ ] Thesis check table with today-highlighted sparklines and `LIVE` tags
   - [ ] All positions table sorted by DTE with risk/status pills
   - [ ] Dark pool flow table with today-highlighted sparklines and `LIVE` tags
6. [ ] Verify today-highlighting in sparklines: rightmost bar has white outline ring + "today →" label
7. [ ] Verify "Today" column in flow tables shows `XX% LIVE` for tickers with today's data
8. [ ] Verify no unresolved `{{PLACEHOLDER}}` variables remain in output HTML
9. [ ] Report auto-opens in browser (unless `--no-open`)
10. [ ] Output saved to `reports/portfolio-{date}.html`

### Risk Reversal Reports

1. [ ] Run `python3 scripts/risk_reversal.py [TICKER]` — script is fully self-contained
2. [ ] Verify IB connection succeeded (spot price + option greeks fetched)
3. [ ] Verify dark pool flow fetched (context for thesis section)
4. [ ] Verify IV skew tables populated for ≥2 expirations (put IV > call IV at each delta)
5. [ ] Verify all 8 sections present:
   - [ ] Header with direction pill (BULLISH/BEARISH) + MANAGER OVERRIDE pill
   - [ ] 6 summary metric cards (Spot, Skew, DP Buy Ratio, P/C Ratio, Bankroll, Net Cost)
   - [ ] Thesis callout with DP flow + skew reasoning
   - [ ] Dark pool flow table with today-highlighted sparklines
   - [ ] IV skew analysis with per-expiry put/call IV comparison tables
   - [ ] 3 recommended trades: Primary (costless), Alternative (diff expiry), Aggressive (credit)
   - [ ] Full combos matrix with near-costless combinations per expiry
   - [ ] Risk & Compliance panels (with undefined risk warning) + Execution commands
6. [ ] Verify Primary recommendation is costless or near-costless (within ±$0.10)
7. [ ] Verify compliance panel includes MANAGER OVERRIDE callout
8. [ ] Verify execution commands use `ib_execute.py` with correct strikes/expiries/quantities
9. [ ] Verify no unresolved `{{PLACEHOLDER}}` variables remain in output HTML
10. [ ] Report auto-opens in browser (unless `--no-open`)
11. [ ] Output saved to `reports/{ticker}-risk-reversal-{date}.html`
12. [ ] Reference implementation: `reports/iwm-risk-reversal-2026-03-06.html`

### CRI Reports

1. [ ] Run `python3 scripts/cri_scan.py` — script is fully self-contained
2. [ ] Verify data fetched for all 4 instruments (VIX, VVIX, SPY, COR1M)
3. [ ] Verify all 7 sections present:
   - [ ] Header with CRI level pill (LOW/ELEVATED/HIGH/CRITICAL)
   - [ ] CRI score display with progress bar
   - [ ] 6 metric cards (VIX, VVIX, COR1M, SPY vs MA, Realized Vol, Crash Trigger)
   - [ ] Component breakdown bars (VIX, VVIX, Correlation, Momentum — each /25)
   - [ ] CTA Exposure Model (vol, exposure %, forced reduction, est. selling)
   - [ ] Crash trigger conditions table (SPX < MA, RVol > 25%, COR1M > 60)
   - [ ] Rolling 10-day history table
4. [ ] Verify CRI score color matches level (green=LOW, amber=ELEVATED/HIGH, red=CRITICAL)
5. [ ] Verify crash trigger shows PASS/FAIL for each of 3 conditions
6. [ ] Verify no unresolved `{{PLACEHOLDER}}` variables remain in output HTML
7. [ ] Report auto-opens in browser (unless `--no-open`)
8. [ ] Output saved to `reports/cri-scan-{date}.html`

### Stress Test Reports

1. [ ] Read template from `.pi/skills/html-report/stress-test-template.html`
2. [ ] Parse user's scenario into quantitative parameters (SPX move, VIX, sector shocks)
3. [ ] Update `scripts/scenario_analysis.py` with scenario-specific parameters and sensitivities
4. [ ] Run `python3 scripts/scenario_analysis.py` to generate `/tmp/scenario_analysis.json`
5. [ ] Generate per-position narratives explaining oil/SPX/VIX/structure impact
6. [ ] Build HTML body with all 10 required sections
7. [ ] Verify all defined-risk P&L is within `[-debit, +max_width]` bounds
8. [ ] Verify expandable ▶ chevron rows work for every position (toggle on click)
9. [ ] Verify 4-panel detail grid present (Oil, SPX, VIX, Structure) per position
10. [ ] Verify Bear/Base/Bull totals sum correctly across all positions
11. [ ] Replace `{{TITLE}}` and `{{BODY}}` in template
12. [ ] Save to `reports/stress-test-{date}.html`
13. [ ] Open in browser
14. [ ] Reference implementation: `reports/scenario-stress-test-2026-03-08.html`
