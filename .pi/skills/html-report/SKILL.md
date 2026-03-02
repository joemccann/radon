---
name: html-report
description: Generate styled HTML reports using the Terminal Dashboard theme. Use when the user needs to create an HTML report, dashboard, data visualization, or styled output document. Triggers include requests to "generate a report", "create an HTML file", "make a dashboard", "visualize data", "export to HTML", or any task requiring formatted HTML output with the project's visual identity.
---

# HTML Report Generation

Generate professional HTML reports using the Terminal Dashboard theme — a precision-focused, monochromatic, high-density design supporting both dark and light modes.

## Design Principles

- **Monochromatic**: No color accents, gradients, or shadows
- **High-density**: Data-first, surgical whitespace
- **Terminal aesthetic**: Sharp 1px borders, monospaced data, keyboard-first feel
- **Dual themes**: Dark and light modes via CSS custom properties

See `THEME.md` in this skill directory for full design specification.

## Quick Start Template

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Report Title</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    /* Theme Tokens */
    :root {
      --bg-base: #050505;
      --bg-panel: #0A0A0A;
      --bg-hover: #141414;
      --border-dim: #1C1C1C;
      --border-focus: #333333;
      --text-primary: #F0F0F0;
      --text-muted: #666666;
      --accent-bg: #FFFFFF;
      --accent-text: #000000;
    }
    
    [data-theme="light"] {
      --bg-base: #F5F5F5;
      --bg-panel: #FFFFFF;
      --bg-hover: #F0F0F0;
      --border-dim: #D9D9D9;
      --border-focus: #A3A3A3;
      --text-primary: #0A0A0A;
      --text-muted: #6B6B6B;
      --accent-bg: #000000;
      --accent-text: #FFFFFF;
    }

    /* Base Styles */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    body {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      line-height: 1.5;
      background: var(--bg-base);
      color: var(--text-primary);
    }

    /* Layout */
    .container {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }

    .header {
      border-bottom: 1px solid var(--border-dim);
      padding-bottom: 16px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .title {
      font-size: 14px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }

    .timestamp {
      font-size: 11px;
      color: var(--text-muted);
    }

    /* Panels */
    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border-dim);
      margin-bottom: 16px;
    }

    .panel-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
      font-size: 10px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: var(--text-muted);
    }

    .panel-body {
      padding: 16px;
    }

    /* Metrics Grid */
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1px;
      background: var(--border-dim);
      border: 1px solid var(--border-dim);
    }

    .metric {
      background: var(--bg-panel);
      padding: 16px;
    }

    .metric-label {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: var(--text-muted);
      margin-bottom: 8px;
    }

    .metric-value {
      font-size: 24px;
      font-weight: 500;
      letter-spacing: -0.02em;
    }

    .metric-change {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid var(--border-dim);
    }

    /* Tables */
    table {
      width: 100%;
      border-collapse: collapse;
    }

    th {
      font-size: 10px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: var(--text-muted);
      text-align: left;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
    }

    td {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
    }

    tr:hover {
      background: var(--bg-hover);
    }

    .text-right {
      text-align: right;
    }

    .text-muted {
      color: var(--text-muted);
    }

    /* Pills/Badges */
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border: 1px solid var(--border-dim);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }

    .pill-accent {
      background: var(--accent-bg);
      color: var(--accent-text);
      border-color: var(--accent-bg);
    }

    /* Status Indicators */
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .status-dot {
      width: 6px;
      height: 6px;
      background: var(--accent-bg);
      animation: pulse 2s infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    /* Theme Toggle */
    .theme-toggle {
      background: transparent;
      border: 1px solid var(--border-dim);
      color: var(--text-primary);
      padding: 4px 12px;
      font-family: inherit;
      font-size: 11px;
      cursor: pointer;
      transition: all 150ms ease-in-out;
    }

    .theme-toggle:hover {
      background: var(--bg-hover);
      border-color: var(--border-focus);
    }

    /* Two-column layout */
    .grid-2 {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 16px;
    }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg-base); }
    ::-webkit-scrollbar-thumb { background: var(--border-focus); }

    /* Print styles */
    @media print {
      body { background: white; color: black; }
      .theme-toggle { display: none; }
      .panel { border-color: #ccc; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header class="header">
      <h1 class="title">Report Title</h1>
      <div style="display: flex; align-items: center; gap: 16px;">
        <span class="timestamp">Generated: 2026-03-01 18:00 PST</span>
        <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
      </div>
    </header>

    <!-- Metrics Grid -->
    <div class="metrics">
      <div class="metric">
        <div class="metric-label">Metric One</div>
        <div class="metric-value">$100,000</div>
        <div class="metric-change">+2.5% from yesterday</div>
      </div>
      <div class="metric">
        <div class="metric-label">Metric Two</div>
        <div class="metric-value">42</div>
        <div class="metric-change">-3 from last week</div>
      </div>
      <div class="metric">
        <div class="metric-label">Metric Three</div>
        <div class="metric-value">98.7%</div>
        <div class="metric-change">Unchanged</div>
      </div>
      <div class="metric">
        <div class="metric-label">Status</div>
        <div class="metric-value">
          <span class="status"><span class="status-dot"></span> ACTIVE</span>
        </div>
        <div class="metric-change">Since 09:30 EST</div>
      </div>
    </div>

    <!-- Data Table -->
    <div class="panel" style="margin-top: 24px;">
      <div class="panel-header">Data Table</div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Name</th>
            <th class="text-right">Value</th>
            <th class="text-right">Change</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>AAPL</td>
            <td class="text-muted">Apple Inc.</td>
            <td class="text-right">$178.50</td>
            <td class="text-right">+1.2%</td>
            <td><span class="pill">WATCHING</span></td>
          </tr>
          <tr>
            <td>NVDA</td>
            <td class="text-muted">NVIDIA Corporation</td>
            <td class="text-right">$890.25</td>
            <td class="text-right">+3.8%</td>
            <td><span class="pill pill-accent">ACTIVE</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    function toggleTheme() {
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      html.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
    }
  </script>
</body>
</html>
```

## Component Reference

### Metrics Grid

Four-column responsive grid for KPIs:

```html
<div class="metrics">
  <div class="metric">
    <div class="metric-label">BANKROLL</div>
    <div class="metric-value">$100,000</div>
    <div class="metric-change">+2.5% MTD</div>
  </div>
  <!-- Repeat for each metric -->
</div>
```

### Data Table

Full-width table with hover states:

```html
<div class="panel">
  <div class="panel-header">Positions</div>
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
```

### Panel

Generic container with header:

```html
<div class="panel">
  <div class="panel-header">Section Title</div>
  <div class="panel-body">
    Content goes here
  </div>
</div>
```

### Status Indicator

Pulsing dot with label:

```html
<span class="status">
  <span class="status-dot"></span>
  LIVE
</span>
```

### Pills/Badges

```html
<span class="pill">DEFAULT</span>
<span class="pill pill-accent">HIGHLIGHTED</span>
```

### Two-Column Layout

```html
<div class="grid-2">
  <div class="panel">Main content (2/3)</div>
  <div class="panel">Sidebar (1/3)</div>
</div>
```

## Typography

| Element | Class/Style | Usage |
|---------|-------------|-------|
| Headings | `.title` | 14px uppercase, letter-spacing |
| Labels | `.metric-label`, `.panel-header` | 10px uppercase, muted |
| Data | Default body | 13px JetBrains Mono |
| Muted text | `.text-muted` | Secondary information |
| Timestamps | `.timestamp` | 11px, muted |

## Color Tokens

Use CSS custom properties for theme compatibility:

| Token | Usage |
|-------|-------|
| `--bg-base` | Page background |
| `--bg-panel` | Panel/card background |
| `--bg-hover` | Hover state background |
| `--border-dim` | Default borders |
| `--border-focus` | Focus/active borders |
| `--text-primary` | Main text |
| `--text-muted` | Secondary text |
| `--accent-bg` | Inverted background (buttons, badges) |
| `--accent-text` | Text on accent background |

## SVG Charts

For surgical line charts:

```html
<svg viewBox="0 0 400 100" style="width: 100%; height: 100px;">
  <!-- Grid lines -->
  <line x1="0" y1="25" x2="400" y2="25" 
        stroke="var(--border-dim)" stroke-dasharray="2 4"/>
  <line x1="0" y1="50" x2="400" y2="50" 
        stroke="var(--border-dim)" stroke-dasharray="2 4"/>
  <line x1="0" y1="75" x2="400" y2="75" 
        stroke="var(--border-dim)" stroke-dasharray="2 4"/>
  
  <!-- Data line -->
  <polyline 
    fill="none" 
    stroke="var(--accent-bg)" 
    stroke-width="1.5"
    vector-effect="non-scaling-stroke"
    points="0,80 50,60 100,70 150,40 200,45 250,30 300,35 350,20 400,25"/>
</svg>
```

## File Naming Convention

Save reports to `reports/` directory:

```
reports/
├── portfolio-2026-03-01.html
├── flow-scan-AAPL.html
├── leap-iv-scan.html
└── trade-journal-2026-03.html
```

## Generation Checklist

When generating an HTML report:

1. [ ] Use the full template with theme tokens
2. [ ] Set appropriate `<title>` and `.title` heading
3. [ ] Include generation timestamp
4. [ ] Use semantic tokens (not hardcoded colors)
5. [ ] Test dark/light theme toggle
6. [ ] Ensure numeric data is right-aligned
7. [ ] Use monospace for all data values
8. [ ] Save to `reports/` directory
9. [ ] Use descriptive filename with date

## Trading Report Templates

### Portfolio Summary

Key metrics: Bankroll, Deployed %, Open Positions, Max Capacity, Drawdown

### Flow Scan Report

Table columns: Ticker, Direction, Strength, Days Sustained, Last Price, Signal Date

### LEAP IV Scan

Table columns: Ticker, Expiry, Strike, IV, RV, Gap %, Premium, Delta

### Trade Journal

Sections: Date range, Decisions (TRADE/NO_TRADE), Win rate, P&L summary
