# GEX Levels — Feature Specification

## Overview

**What:** Real-time Gamma Exposure (GEX) levels scanner and dashboard for SPX and SPY. Surfaces dealer gamma positioning by strike to identify price magnets, accelerators, flip points, and expected ranges.

**Why:** Dealer gamma positioning is the single strongest short-term directional signal in equity markets. When dealers are long gamma (positive GEX), they buy dips and sell rallies — stabilizing price. When short gamma (negative GEX), they amplify moves. Knowing the GEX flip, max magnet, and max accelerator tells you where price is attracted to and where it breaks down.

**User value:** Replaces Alphatica subscription (~$50/mo) with a first-party signal integrated into Radon's regime/risk framework. Enables GEX-informed position sizing and structure selection.

---

## Data Sources

### Primary: Unusual Whales

| Endpoint | Returns | Use |
|----------|---------|-----|
| `GET /stock/{ticker}/greek-exposure/strike` | `call_gex`, `put_gex`, `call_delta`, `put_delta` per strike | Core GEX profile by strike |
| `GET /stock/{ticker}/greek-exposure` | Aggregate GEX per day | Net GEX time series (history) |
| `GET /stock/{ticker}/greek-exposure/expiry` | GEX by expiry + `dte` | 0DTE filtering (toggle) |
| `GET /stock/{ticker}/greek-flow` | Intraday delta/vega flow per minute | Real-time flow momentum (future enhancement) |
| `GET /stock/{ticker}/greeks?expiry=X` | Per-strike IV, delta, gamma | ATM IV extraction |
| `GET /screener/stocks` | `put_call_ratio`, `call_volume`, `put_volume` | Vol P/C ratio |

All fields are string-encoded numerics. Parse with `float()`.

**Verified sign convention (Apr 2 2026 live data):**
- `call_gex` = **positive** (long call gamma exposure)
- `put_gex` = **negative** (put gamma destabilizes)
- `net_gex = call_gex + put_gex` — no negation needed
- Aggregate: `call=+302K, put=-406K, net=-104K` (correct for selloff environment)
- Max accelerator: strike 6500 (net=-13.5K), Max magnet: strike 7000 (net=+3.0K)

### Secondary: Interactive Brokers

| Data | Use |
|------|-----|
| SPX last price | Spot reference for "distance from flip" |
| SPX close | Day change calculation |
| Options chain OI + greeks | Cross-validation of UW data |

### Tertiary: MenthorQ

| Screener | Use |
|----------|-----|
| `gamma_levels/closer_to_HVL` | Cross-reference high-volume level |
| `gamma_levels/closer_call_resistance` | Validate call wall |
| `gamma_levels/closer_put_support` | Validate put wall |

---

## Computed Metrics

### Core GEX Computation

UW returns GEX directly via `call_gex` and `put_gex` fields. No dealer-inversion needed:

```python
net_gex[strike] = float(row['call_gex']) + float(row['put_gex'])
```

### GEX Flip Computation (Bucketed Cumulative)

Raw per-strike data has ~723 strikes with many zero/noise crossings. Must bucket first, then find the dominant flip:

```python
bucket_size = 25  # SPX/SPY/QQQ/NDX
buckets = defaultdict(float)
for row in strike_data:
    s = float(row['strike'])
    net = float(row['call_gex']) + float(row['put_gex'])
    bucket = round(s / bucket_size) * bucket_size
    buckets[bucket] += net

# Scan low→high, find last crossing below spot where cumulative goes negative→positive
sorted_buckets = sorted(buckets.items())
cumulative = 0
flip = None
for strike, gamma in sorted_buckets:
    prev_cum = cumulative
    cumulative += gamma
    if prev_cum <= 0 < cumulative and strike <= spot:
        flip = strike
```

For non-index tickers: `bucket_size = max(1, round(spot * 0.005))`.

### Derived Levels

| Metric | Computation | Alphatica Equivalent |
|--------|------------|---------------------|
| **GEX Flip** | Last strike (below spot) where bucketed cumulative GEX crosses zero upward | GEX FLIP (SUPPORT) |
| **Net GEX** | `sum(net_gex[all strikes])` in $ | Net GEX (-$235M) |
| **Net DEX** | `sum(call_delta + put_delta)` in shares | Net DEX (+37.2M) |
| **Max Magnet** | Bucket with highest positive net GEX | MAX MAGNET |
| **Max Accelerator** | Bucket with most negative net GEX | MAX ACCEL |
| **Put Wall** | Strike with highest absolute `put_gex` | PUT WALL |
| **Call Wall** | Strike with highest absolute `call_gex` | (implied) |
| **ATM IV** | IV at nearest-to-spot strike (via `greeks` endpoint) | ATM IV 19.7% |
| **Vol P/C** | `put_call_ratio` from `screener/stocks` endpoint | VOL P/C 1.42 |
| **Expected Range** | `spot * ATM_IV * sqrt(1/252)` | EXPECTED RANGE |
| **Days Above/Below Flip** | Consecutive sessions where close > flip (from history) | DAY 3 ABOVE GEX FLIP |
| **Flip Migration** | Track flip level across sessions | "6,435 → 6,494 → 6,537" |

### 0DTE Toggle

When user disables 0DTE in the UI:
1. Fetch `greek-exposure/expiry` for the ticker
2. Identify entries with `dte == 0`
3. Subtract 0DTE gamma from the per-strike profile before computing levels
4. Requires cross-referencing expiry-level data with strike-level data (may need `greek-exposure/strike-expiry` if available, otherwise fetch `greeks?expiry={0dte_expiry}` and subtract)

### Directional Bias Heuristic

| Condition | Bias |
|-----------|------|
| Spot > flip AND net_gex improving AND max_magnet above spot | CAUTIOUS BULL |
| Spot > flip AND net_gex strongly positive | BULL |
| Spot < flip AND net_gex negative AND max_accel below spot | CAUTIOUS BEAR |
| Spot < flip AND net_gex strongly negative | BEAR |
| Spot near flip (within 0.3%) | NEUTRAL |

---

## Architecture (VCG/CRI Pattern)

### File Map

| Component | File | Pattern Source |
|-----------|------|---------------|
| Scanner | `scripts/gex_scan.py` | `vcg_scan.py` |
| Cache | `data/gex.json` | `data/vcg.json` |
| Scheduled cache | `data/gex_scheduled/` | `data/cri_scheduled/` |
| FastAPI endpoint | `scripts/api/server.py` (`POST /gex/scan`) | `POST /vcg/scan` |
| Next.js route | `web/app/api/gex/route.ts` | `web/app/api/vcg/route.ts` |
| Staleness | `web/lib/gexStaleness.ts` | `web/lib/vcgStaleness.ts` |
| Hook | `web/lib/useGex.ts` | `web/lib/useVcg.ts` |
| UI Panel | `web/components/GexPanel.tsx` | `web/components/VcgPanel.tsx` |
| Share | `scripts/generate_gex_share.py` | `scripts/generate_vcg_share.py` |

### Data Flow

```
[Scheduled / On-demand]
  POST /gex/scan
    → FastAPI acquires async lock
    → Check 60s cooldown
    → python3 scripts/gex_scan.py --json --ticker SPX
      → UW: greek-exposure/strike (by strike)
      → UW: greek-exposure (aggregate, last 20 days)
      → UW: greeks (ATM IV)
      → IB: SPX spot price (via pool "data" role)
      → Compute: flip, magnets, accelerators, profile, bias
      → Output JSON to stdout
    → Write data/gex.json (atomic)
    → Return result

[Client Polling]
  useGex(marketState)
    → GET /api/gex (every 60s market hours, 300s extended)
    → Next.js reads data/gex.json
    → isGexDataStale() check
    → If stale: fire-and-forget POST /gex/scan
    → Return cached data with SWR headers
```

---

## Data Shape

### `GexData` (cache + API response)

```typescript
interface GexData {
  scan_time: string;             // ISO timestamp
  market_open: boolean;
  ticker: string;                // "SPX"
  spot: number;                  // Current price
  close: number;                 // Previous close
  day_change: number;            // spot - close
  day_change_pct: number;        // (spot - close) / close * 100
  contracts: number;             // Total option contracts
  expirations: number;           // Number of active expirations

  // Aggregate metrics
  net_gex: number;               // Total dealer gamma ($)
  net_dex: number;               // Total dealer delta (shares)
  atm_iv: number;                // ATM implied volatility (decimal, e.g. 0.197)
  vol_pc: number;                // Volume put/call ratio

  // Key levels
  levels: {
    gex_flip: number;            // GEX flip strike
    max_magnet: GexLevel;        // Highest positive gamma strike
    second_magnet: GexLevel;     // Second highest
    max_accelerator: GexLevel;   // Most negative gamma strike
    put_wall: GexLevel;          // Highest put gamma strike
    call_wall: GexLevel;         // Highest call gamma strike
  };

  // GEX profile (bucketed by strike)
  profile: GexBucket[];

  // Expected range
  expected_range: {
    low: number;
    high: number;
    iv_1d: number;               // 1-day implied move
  };

  // Directional bias
  bias: {
    direction: "BULL" | "CAUTIOUS_BULL" | "NEUTRAL" | "CAUTIOUS_BEAR" | "BEAR";
    reasons: string[];           // Human-readable reasons
    days_above_flip: number;     // Consecutive sessions above flip (negative = below)
    flip_migration: FlipHistory[]; // Last 5 sessions' flip levels
  };

  // History (last 20 sessions)
  history: GexHistoryEntry[];
}

interface GexLevel {
  strike: number;
  gamma: number;                 // Net dealer gamma at this strike ($)
  distance: number;              // Points from spot
  distance_pct: number;          // % from spot
  label: string;                 // e.g. "+$45.1M per $1"
}

interface GexBucket {
  strike: number;                // Bucket center
  net_gamma: number;             // Net dealer gamma ($)
  call_gamma: number;            // Call component
  put_gamma: number;             // Put component
  pct_from_spot: number;         // % distance from spot
  tag: string | null;            // "SPOT", "GEX FLIP", "MAGNET", "ACCELERATOR", "2ND MAGNET"
}

interface GexHistoryEntry {
  date: string;                  // YYYY-MM-DD
  net_gex: number;
  net_dex: number;
  gex_flip: number;
  spot: number;
  atm_iv: number;
  vol_pc: number;
  bias: string;
}

interface FlipHistory {
  date: string;
  flip: number;
}
```

---

## Staleness Rules

Same pattern as VCG/CRI:

| Condition | Stale? | Action |
|-----------|--------|--------|
| `scan_time` missing/unparseable | YES | Immediate scan |
| Session date != today ET | YES | Background scan |
| Market open + age > 60s | YES | Background scan |
| Market closed + date = today | NO | Serve cached EOD |

---

## UI Design

### Location

New tab on `/regime` page alongside CRI and VCG tabs. Tab label: **GEX**.

### Panel Layout (following Radon brand spec)

**1. Header Strip**
- Ticker + "Gamma Exposure Levels" + session date
- Day counter badge: "DAY N ABOVE/BELOW GEX FLIP"
- Sync timestamp + scan button

**2. Metrics Row** (6 cards, same style as VCG/CRI)

| Card | Value | Subtext |
|------|-------|---------|
| SPOT | `6,582.69` | `+7.32 (+0.11%)` |
| GEX FLIP | `6,537` | `-0.7% — 46 pts below` |
| NET GEX | `-$235M` | `retreat from -$137M` |
| NET DEX | `+37.2M` | `positive 3 straight days` |
| ATM IV | `19.7%` | `-84 pt implied move` |
| VOL P/C | `1.42` | `put buying picked up` |

**3. Key Levels Row** (5 cards, sorted by proximity to spot)

GEX Flip | Max Magnet | 2nd Magnet | Max Accelerator | Put Wall

**4. GEX Profile Chart** (horizontal bar chart)
- Y-axis: strike price (sorted ascending)
- X-axis: net dealer gamma ($)
- Green bars: positive gamma (stabilizing)
- Red bars: negative gamma (destabilizing)
- Labels on right: `+$45M MAGNET`, `-$70M MAX ACCEL`, `GEX FLIP`, `SPOT`
- Labels on left: `pct_from_spot` (e.g. `-0.7%`, `+1.8%`)

**5. Bottom Row**
- Left: Expected Range bar (horizontal, showing key levels)
- Right: Directional Bias card (BULL/BEAR + reasons)

**6. History Table** (collapsible, 20 sessions)
- Columns: Date, Spot, GEX Flip, Net GEX, Net DEX, ATM IV, Vol P/C, Bias

### Colors (Radon brand tokens)

| Element | Token |
|---------|-------|
| Positive gamma bars | `--signal-core` (#05AD98) |
| Negative gamma bars | `--fault` (#E85D6C) |
| Spot marker | `--signal-strong` (#0FCFB5) |
| GEX Flip marker | `--warning` (#F5A623) |
| Magnet label | `--signal-core` |
| Accelerator label | `--fault` |
| Bias BULL | `--signal-core` |
| Bias BEAR | `--fault` |
| Bias NEUTRAL | `--neutral` (#94a3b8) |

### Chart Library

D3 (consistent with `CriHistoryChart.tsx`). Horizontal bar chart with annotations.

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Scan latency | < 10s (UW API is fast; no heavy computation) |
| Cache TTL | 60s (market hours), EOD (closed) |
| Cooldown | 60s between scans |
| Profile buckets | ~30-50 strikes visible (filter to spot +/- 10%) |
| History depth | 20 sessions |
| Ticker support | SPX and SPY |

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| UW API down | Return cached `data/gex.json` with `is_stale: true` |
| UW rate limit | Skip, serve cached, retry next interval |
| No option data (new ticker) | Return empty profile with error message |
| IB down (no spot price) | Use UW's last known spot or previous close |
| Scan timeout (>30s) | Kill subprocess, serve cached |

---

## Acceptance Criteria

### Scanner

- **Given** SPX options are active, **when** `gex_scan.py --json --ticker SPX` runs, **then** output contains valid `net_gex`, `gex_flip`, `profile[]`, and `history[]`
- **Given** UW returns strike-level gamma, **when** scanner computes GEX flip, **then** flip is the strike where cumulative dealer gamma crosses zero
- **Given** profile data, **when** scanner identifies max magnet, **then** it is the strike with highest positive dealer gamma
- **Given** a history of 5+ sessions, **when** scanner computes `days_above_flip`, **then** count matches consecutive closes above the flip level

### API

- **Given** cached data exists, **when** `GET /api/gex` is called, **then** response includes full `GexData` shape
- **Given** data is stale (age > 60s during market hours), **when** `GET /api/gex` is called, **then** a background `POST /gex/scan` is triggered
- **Given** scan was run < 60s ago, **when** `POST /gex/scan` is called, **then** cached data is returned without re-scanning

### UI

- **Given** GEX data is loaded, **when** user views the GEX tab, **then** all 6 metric cards display correct values
- **Given** profile data, **when** chart renders, **then** positive gamma bars are green (`--signal-core`) and negative are red (`--fault`)
- **Given** spot price, **when** chart renders, **then** SPOT marker appears at correct strike position
- **Given** GEX flip level, **when** chart renders, **then** flip is labeled and visually distinct
- **Given** market is closed, **when** user views GEX tab, **then** EOD data displays without polling

---

## Implementation TODO

### Phase 1: Scanner + Cache (Backend)

- [ ] Create `scripts/gex_scan.py`
  - [ ] Fetch UW `greek-exposure/strike` for ticker
  - [ ] Fetch UW `greek-exposure` for 20-day aggregate history
  - [ ] Fetch UW `greeks` for ATM IV (nearest expiry, nearest strike)
  - [ ] Compute dealer gamma per strike (verify sign convention)
  - [ ] Compute GEX flip (zero-crossing)
  - [ ] Identify max magnet, 2nd magnet, max accelerator, put/call walls
  - [ ] Bucket profile into $25 intervals (SPX)
  - [ ] Compute expected range from ATM IV
  - [ ] Compute directional bias heuristic
  - [ ] Track flip migration (last 5 sessions from history)
  - [ ] Track days above/below flip
  - [ ] Output JSON matching `GexData` shape
  - [ ] `--json`, `--ticker`, `--no-cache` CLI flags
- [ ] Add tests: `scripts/tests/test_gex_scan.py`
  - [ ] Flip computation with known data
  - [ ] Magnet/accelerator identification
  - [ ] Profile bucketing
  - [ ] Bias heuristic edge cases
  - [ ] Expected range math

### Phase 2: FastAPI + Next.js Route

- [ ] Add `POST /gex/scan` to `scripts/api/server.py`
  - [ ] 60s cooldown, async lock, atomic cache write
  - [ ] 120s subprocess timeout
- [ ] Create `web/app/api/gex/route.ts`
  - [ ] GET: read cache, check staleness, trigger background scan
  - [ ] SWR headers (15s max-age, 120s stale-while-revalidate)
- [ ] Create `web/lib/gexStaleness.ts`
  - [ ] Same rules as VCG staleness
- [ ] Create `web/lib/useGex.ts`
  - [ ] Wrap `useSyncHook` with GEX config
  - [ ] Market-state-aware polling (60s open, 300s extended, off when closed)
- [ ] Add tests
  - [ ] `web/tests/gex-staleness.test.ts`
  - [ ] `web/tests/gex-route.test.ts`

### Phase 3: UI Panel

- [ ] Create `web/components/GexPanel.tsx`
  - [ ] Header strip with day counter badge
  - [ ] 6-card metrics row
  - [ ] 5-card key levels row
  - [ ] D3 horizontal bar chart (GEX profile)
  - [ ] Expected range bar
  - [ ] Directional bias card
  - [ ] 20-session history table (collapsible)
- [ ] Add GEX tab to regime page
- [ ] Add tests: `web/tests/gex-panel.test.ts`
- [ ] E2E test: `web/e2e/gex-panel.spec.ts`

### Phase 4: Share + Polish

- [ ] Create `scripts/generate_gex_share.py` (X share card)
- [ ] Add `POST /gex/share` to FastAPI
- [ ] Share button in GEX panel
- [ ] Scheduled scan via launchd (30-min intervals during market hours)
- [ ] Update CLAUDE.md with GEX section

---

## Resolved Questions

1. **UW sign convention** — `call_gex` = positive, `put_gex` = negative. `net = call_gex + put_gex` directly. Verified with live SPX data (Apr 2 2026). No negation needed.
2. **Ticker scope** — SPX and SPY.
3. **Location** — Tab on `/regime` page alongside CRI and VCG.
4. **Vol P/C source** — `GET /screener/stocks` returns `put_call_ratio` field. Already implemented as `client.get_stock_screener()`.
5. **0DTE** — UI toggle (on/off). When off, subtract 0DTE gamma from profile using `greek-exposure/expiry` (`dte=0`) data.
