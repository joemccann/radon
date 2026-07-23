# Options Structure Catalog

Canonical reference for every known options structure. Used by:
- **Web app** (`web/lib/nakedShortGuard.ts`) — order entry guard decisions
- **Python scripts** (`scripts/naked_short_audit.py`) — post-sync audit
- **Trade spec reports** — structure classification and P&L attribution
- **Agent evaluation** — order form defaults, structure labeling

**58 structures** | **41 allowed** | **14 blocked** | **3 guard gaps**

---

## Guard Decision Logic

```
BUY anything              → ALLOW (no short exposure)
SELL put                  → ALLOW (cash-secured, defined risk)
Combo: BUY C + SELL C     → ALLOW (vertical spread)
Combo: BUY C + SELL P     → ALLOW (risk reversal — defined)
Combo: SELL C + BUY P     → ⚠ GAP (synthetic short — naked call)
SELL call, no stock       → BLOCK (naked short call)
SELL call, insufficient   → BLOCK (short a tail)
SELL stock, no shares     → BLOCK (naked short stock)
SELL stock, qty > held    → BLOCK (oversell)
```

## Guard Gaps

### Long Risk Reversal
**Legs:** BUY CALL (higher) + SELL PUT (lower)
**Guard says:** ALLOW (incorrectly)
**Gap:** CRITICAL GAP: Risk reversal (bullish) has unlimited loss on the short put side if underlying crashes. However, guard allows because it detects both BUY and SELL legs. The short put creates undefined risk that is NOT offset by a long call at a different strike. Guard logic is insufficient for asymmetric risk reversal.

### Jade Lizard
**Legs:** SELL CALL (higher) + BUY CALL (highest) + SELL PUT (lower) + BUY PUT (lowest)
**Guard says:** ALLOW (incorrectly)
**Gap:** CRITICAL GAP: Jade Lizard is defined-gap (no stock), and involves SELL call + SELL put. While guard detects BUY + SELL, it does not verify that the BUY legs are sufficient to cover the SELL legs at ALL price levels. A Jade Lizard with wide SELL strikes may create naked exposure if underlying breaks the long call or long put strikes.

### Seagull Spread
**Legs:** BUY CALL (highest) + SELL CALL (middle) + SELL PUT (lowest)
**Guard says:** ALLOW (incorrectly)
**Gap:** CRITICAL GAP: Seagull has a short put with no hedge. The short call is covered by the long call, but the short put is naked. Guard allows because it detects both BUY and SELL, but does not verify that each SELL leg has a corresponding BUY leg of sufficient width.

---

## Single Leg

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Call _Bullish Call, ATM Call_ | BUY CALL (ATM) | bullish | defined | unlimited | premium paid | **ALLOW** | BUY anything → always allowed |
| Long Put _Protective Put, Downside Insurance_ | BUY PUT (ATM) | bearish | defined | strike - premium | premium paid | **ALLOW** | BUY anything → always allowed |
| Short Call (Naked) _Uncovered Call, Naked Short Call_ | SELL CALL (ATM) | bearish | undefined | premium received | unlimited | **BLOCK** | SELL call without stock coverage → no long shares to cover |
| Short Put (Cash-Secured) _Naked Short Put, Cash-Secured Put_ | SELL PUT (ATM) | bullish | defined | premium received | strike × 100 | **ALLOW** | SELL put → cash-secured, defined risk |
| Short Stock (Naked) _Naked Short Stock_ | SELL STOCK [N/A] | bearish | undefined | stock price received | unlimited | **BLOCK** | SELL stock without sufficient long shares held → no shares to short |
| Long Stock (no options) _Equity Position_ | BUY STOCK [N/A] | bullish | undefined | unlimited | stock price | **ALLOW** | BUY anything → always allowed |

## Verticals

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Bull Call Spread _Call Spread, Long Call Spread_ | BUY CALL (lower) + SELL CALL (higher) | bullish | defined | width - debit paid | debit paid | **ALLOW** | Combo with both BUY and SELL legs → covered by long leg |
| Bear Call Spread _Short Call Spread_ | SELL CALL (higher) + BUY CALL (lower) | bearish | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs → covered by long leg |
| Bull Put Spread _Put Spread, Short Put Spread_ | SELL PUT (higher) + BUY PUT (lower) | bullish | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs → covered by long leg |
| Bear Put Spread _Long Put Spread_ | BUY PUT (higher) + SELL PUT (lower) | bearish | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs → covered by long leg |
| Call Spread (Standard 1x1) _Vertical Call Spread_ | BUY CALL (100) + SELL CALL (105) | bullish | defined | $5 - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs → short call covered by long call |
| Long Call Spread (Debit) _Bull Call Spread_ | BUY CALL (ATM) + SELL CALL (OTM) | bullish | defined | strike_width - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |
| Short Call Spread (Credit) _Bear Call Spread_ | SELL CALL (ATM) + BUY CALL (OTM) | bearish | defined | credit received | strike_width - credit | **ALLOW** | Combo with both BUY and SELL legs → short call covered by long call |
| Long Put Spread (Debit) _Bear Put Spread_ | BUY PUT (ATM) + SELL PUT (ITM) | bearish | defined | strike_width - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |
| Short Put Spread (Credit) _Bull Put Spread_ | SELL PUT (ATM) + BUY PUT (ITM) | bullish | defined | credit received | strike_width - credit | **ALLOW** | Combo with both BUY and SELL legs → short put hedged by long put |
| Call Debit Spread (Bullish) _Long Call Spread, Bull Call Spread_ | BUY CALL (ATM) + SELL CALL (OTM) | bullish | defined | width - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |
| Put Debit Spread (Bearish) _Long Put Spread, Bear Put Spread_ | BUY PUT (ATM) + SELL PUT (ITM) | bearish | defined | width - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |
| Call Credit Spread (Bearish) _Short Call Spread, Bear Call Spread_ | SELL CALL (ATM) + BUY CALL (OTM) | bearish | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs |
| Put Credit Spread (Bullish) _Short Put Spread, Bull Put Spread_ | SELL PUT (ATM) + BUY PUT (ITM) | bullish | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs |

## Horizontals / Calendars / Diagonals

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Diagonal Call Spread _Long-dated Buy, Short-dated Sell, Calendar Call_ | BUY CALL (same/lower) [back] + SELL CALL (same/higher) [front] | bullish | defined or undefined | undefined (variable) | debit paid if defined, or unlimited if not | **ALLOW** | Combo with both BUY and SELL legs → covered by long leg |
| Diagonal Put Spread _Long-dated Buy, Short-dated Sell (puts), Calendar Put_ | BUY PUT (same/higher) [back] + SELL PUT (same/lower) [front] | bearish | defined or undefined | undefined (variable) | debit paid if defined, or unlimited if not | **ALLOW** | Combo with both BUY and SELL legs |
| Calendar Call Spread (Same Strike) _Call Calendar, Time Spread_ | BUY CALL [back] + SELL CALL [front] | neutral | defined | short premium | long premium (debit paid) | **ALLOW** | Combo with both BUY and SELL legs → short call at same strike covered by long call at same strike |
| Calendar Put Spread (Same Strike) _Put Calendar_ | BUY PUT [back] + SELL PUT [front] | neutral | defined | short premium | long premium (debit paid) | **ALLOW** | Combo with both BUY and SELL legs |
| Double Calendar Spread _Double Time Spread, Two-Month Calendar_ | BUY CALL (ATM) [back] + SELL CALL (ATM) [mid] + SELL CALL (ATM) [front] | neutral | defined | both short premiums | long premium | **ALLOW** | Combo with both BUY and SELL legs |

## Straddles

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Straddle _At-the-Money Straddle, Volatility Long_ | BUY CALL + BUY PUT | volatility long | defined | unlimited | premium paid (call + put) | **ALLOW** | BUY anything → always allowed |
| Short Straddle _Naked Straddle_ | SELL CALL + SELL PUT | volatility short | undefined | premium received | unlimited | **BLOCK** | SELL call without stock coverage → no long shares to cover |

## Strangles

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Strangle _OTM Straddle, Wide Strangle_ | BUY CALL (OTM) + BUY PUT (OTM) | volatility long | defined | unlimited | premium paid (call + put) | **ALLOW** | BUY anything → always allowed |
| Short Strangle _Naked Strangle_ | SELL CALL (OTM) + SELL PUT (OTM) | volatility short | undefined | premium received | unlimited | **BLOCK** | SELL call without stock coverage → no long shares to cover |
| Naked Strangle (Short 1xN Strangle) _Undefined Strangle_ | SELL CALL (OTM_high) + SELL PUT (OTM_low) + SELL PUT (OTM_low) | volatility short | undefined | premium received | unlimited | **BLOCK** | SELL call without stock coverage → no long shares |

## Butterflies

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Call Butterfly _Call Butterfly, Iron Butterfly (long)_ | BUY CALL (lower) + SELL CALL (middle) + SELL CALL (middle) + BUY CALL (upper) | neutral | defined | middle_strike - lower_strike - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs → short calls covered by long calls |
| Iron Butterfly _Iron Fly, Short Butterfly_ | BUY PUT (lower) + SELL PUT (middle-lower) + SELL CALL (middle-upper) + BUY CALL (upper) | neutral | defined | credit received | width - credit | **ALLOW** | Combo with both BUY and SELL legs → defined risk from hedges |
| Long Put Butterfly _Put Butterfly_ | BUY PUT (lower) + SELL PUT (middle) + SELL PUT (middle) + BUY PUT (upper) | neutral | defined | upper_strike - middle_strike - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs → covered by long puts |
| Broken Wing Butterfly _Uneven Butterfly_ | BUY CALL (lower) + SELL CALL (middle) + SELL CALL (middle+X) + BUY CALL (upper) | bullish or bearish | defined | skewed toward profitable side | narrower than standard butterfly | **ALLOW** | Combo with both BUY and SELL legs |
| Skip Strike Butterfly _Double Width Butterfly_ | BUY CALL (100) + SELL CALL (110) + SELL CALL (120) + BUY CALL (130) | bullish | defined | $5 - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |

## Condors

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Iron Condor _Iron Condor, Short Condor_ | BUY PUT (lower) + SELL PUT (higher-lower) + SELL CALL (higher-upper) + BUY CALL (upper) | neutral | defined | credit received | put_width - credit | **ALLOW** | Combo with both BUY and SELL legs → defined risk |
| Iron Albatross _Wide Iron Condor_ | BUY PUT (very_low) + SELL PUT (low) + SELL CALL (high) + BUY CALL (very_high) | neutral | defined | credit received | put_width - credit | **ALLOW** | Combo with both BUY and SELL legs → defined risk |

## Ratio Spreads

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Long Call Ratio Spread _1x2 Call Ratio_ | BUY CALL (lower) + SELL CALL (higher) + SELL CALL (higher) | bullish | undefined | net credit at upper strike | unlimited above upper strike + 1 | **BLOCK** | Ratio: 1 long call vs 2 short calls. Short call not fully covered. |
| Long Put Ratio Spread _1x2 Put Ratio, Reverse Ratio Put_ | BUY PUT (higher) + SELL PUT (lower) + SELL PUT (lower) | bearish | undefined | net credit at lower strike | unlimited below lower strike - 1 | **BLOCK** | Ratio: 1 long put vs 2 short puts. Ratio creates undefined risk. |
| Short Call Ratio Backspread _1x2 Short Call Ratio_ | SELL CALL (lower) + BUY CALL (higher) + BUY CALL (higher) | bearish | undefined | unlimited above upper strike | lower_strike - debit | **BLOCK** | SELL call without stock coverage → naked short call |
| Short Put Ratio Backspread _1x2 Short Put Ratio_ | SELL PUT (higher) + BUY PUT (lower) + BUY PUT (lower) | bullish | undefined | unlimited below lower strike | higher_strike - debit | **ALLOW** | SELL put → cash-secured, defined risk |
| Ratio Call Spread (1x2 Short focused) _Excess Short Call, 1x2 Short Call Setup_ | BUY CALL (100) + SELL CALL (110) + SELL CALL (110) | bullish | undefined | $10 - debit per contract | unlimited above $120 | **BLOCK** | 1 long call vs 2 short calls. Extra short call is naked (not covered). |
| Ratio Put Spread (1x2 Long focused) _Excess Long Put Ratio_ | BUY PUT (lower) + BUY PUT (lower) + SELL PUT (higher) | bearish | defined | strike_width - debit | debit paid | **ALLOW** | Combo with both BUY and SELL legs |

## Synthetics

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Synthetic Long Stock _Long Call + Short Put (same strike), Synthetic Equity_ | BUY CALL + SELL PUT | bullish | undefined | unlimited | strike - premium | **ALLOW** | Combo with both BUY and SELL legs; SELL put is cash-secured |
| Synthetic Short Stock _Long Put + Short Call (same strike), Synthetic Short_ | BUY PUT + SELL CALL | bearish | undefined | strike - premium | unlimited | **BLOCK** | SELL call without stock coverage → no long shares to cover |
| Long Risk Reversal _Long Call + Short Put (different strikes), Call Spread Synthetic_ | BUY CALL (higher) + SELL PUT (lower) | bullish | undefined | unlimited | short_strike - long_strike + net credit | **ALLOW ⚠** | Combo with both BUY and SELL legs; SELL put is cash-secured |
| Short Risk Reversal _Short Call + Long Put (different strikes), Reverse Risk Reversal_ | SELL CALL (lower) + BUY PUT (higher) | bearish | undefined | short_strike - long_strike + net credit | unlimited | **BLOCK** | SELL call without stock coverage → no long shares to cover |

## Covered / Protective

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Covered Call _Buy-Write, Covered Short Call_ | BUY STOCK [N/A] + SELL CALL (OTM) | neutral | defined | premium + (strike - stock_price) | stock_price - premium | **ALLOW** | Short call fully covered by long stock at ≥100 shares per contract |
| Married Put (Protective Put) _Long Stock + Long Put, Insurance_ | BUY STOCK [N/A] + BUY PUT (ATM or OTM) | bullish | defined | unlimited | put premium | **ALLOW** | BUY put → always allowed |
| Covered Put (Stock + Long Put) _Married Put, Protective Put_ | BUY STOCK [N/A] + BUY PUT (ATM or OTM) | bullish | defined | unlimited | put premium | **ALLOW** | BUY anything → always allowed |
| Partially Covered Call (Excess Short Calls) _Naked Call + Covered Call, Tail Risk_ | BUY STOCK [N/A] + SELL CALL (OTM) + SELL CALL (OTM) | neutral | undefined | 2x premium + (strike - stock_price) | unlimited above stock_price + width | **BLOCK** | 2 short calls vs 1x100 shares. Only 1 call is covered. |

## Collars

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Collar (Zero-Cost Collar) _Protective Collar, Hedge Wrapper_ | BUY STOCK [N/A] + BUY PUT (lower) + SELL CALL (higher) | neutral | defined | upper_strike - lower_strike - stock_price | stock_price - lower_strike | **ALLOW** | Combo: long stock + long put + short call. Short call hedged by long put. |

## Complex / Exotic

| Structure | Legs | Bias | Risk | Max Gain | Max Loss | Guard | Reason |
|-----------|------|------|------|----------|----------|-------|--------|
| Jade Lizard _Superior Iron Butterfly_ | SELL CALL (higher) + BUY CALL (highest) + SELL PUT (lower) + BUY PUT (lowest) | neutral | undefined | credit received | unlimited | **ALLOW ⚠** | Combo with both BUY and SELL legs → hedges present |
| Christmas Tree (Vertical) _Tree Spread_ | SELL CALL (lower) + BUY CALL (middle) + SELL CALL (middle+5) + SELL CALL (upper) | bearish | undefined | net credit | unlimited above upper strike | **BLOCK** | Multiple short calls; only 1 long call → naked short calls above the long call strike |
| All-Long Combo (3+ legs, no shorts) _Debit Spread Combo, Multi-leg Long_ | BUY CALL (100) + BUY CALL (110) + BUY PUT (90) | bullish | defined | unlimited (if calls) or bounded (if mixed) | total premium paid | **ALLOW** | BUY anything → always allowed |
| Box Spread (Synthetic Future) _Long Box, Risk-Free Arbitrage_ | BUY CALL (lower) + SELL CALL (higher) + SELL PUT (lower) + BUY PUT (higher) | neutral | defined | strike_width - net debit | net debit paid | **ALLOW** | Combo with both BUY and SELL legs → all hedged |
| Reverse Conversion _Reverse Arbitrage, Synthetic Short + Long Stock_ | BUY STOCK [N/A] + SELL CALL (ATM) + BUY PUT (ATM) | neutral | defined | credit from short call - premium for put | debit for stock - credit from call + premium for put | **ALLOW** | Stock + short call (covered by stock) + long put. Combo with BUY and SELL. |
| Conversion (Arbitrage) _Synthetic Long + Short Stock, Cash-and-Carry_ | SELL STOCK [N/A] + BUY CALL (ATM) + SELL PUT (ATM) | neutral | defined | credit from short put - cost of call | debit for short stock + cost of call - credit for put | **BLOCK** | SELL stock without sufficient long shares → naked short stock |
| Seagull Spread _Zero-Cost Collar + extension; UI label "Risk Reversal Call Spread" (chain builder, since 2026-07-22); put-side mirror labels "Risk Reversal Put Spread"_ | BUY CALL (highest) + SELL CALL (middle) + SELL PUT (lowest) | bullish | undefined | middle_strike - lowest_strike - net_cost | unlimited | **ALLOW ⚠** | Combo with BUY call + SELL call + SELL put. BUY and SELL present. |

---

## Usage

### Python
```python
import json
from pathlib import Path

with open(Path(__file__).parent.parent / "docs/options-structures.json") as f:
    STRUCTURES = json.load(f)

# Look up a structure by name
struct = next((s for s in STRUCTURES if s["name"] == "Bull Call Spread"), None)
```

### TypeScript / Next.js
```typescript
import { readDataFile } from "@tools/data-reader";

const result = await readDataFile("docs/options-structures.json");
const structures = result.data as OptionsStructure[];
```

### JSON schema (one entry)
```json
{
  "name": "Short Put (Cash-Secured)",
  "aliases": [
    "Naked Short Put",
    "Cash-Secured Put"
  ],
  "category": "single",
  "legs": [
    {
      "type": "put",
      "action": "SELL",
      "strike": "ATM",
      "expiry": "standard"
    }
  ],
  "bias": "bullish",
  "risk_profile": "defined",
  "max_gain": "premium received",
  "max_loss": "strike \u00d7 100",
  "has_naked_short_call": false,
  "has_naked_short_stock": false,
  "short_put_only": true,
  "guard_decision": "ALLOW",
  "guard_reason": "SELL put \u2192 cash-secured, defined risk",
  "guard_correct": true,
  "guard_gap": null,
  "notes": "Collateral requirement = strike \u00d7 100 per contract. Guard treats as always safe because margin/cash covers assignment."
}
```

---

_Generated 2026-03-18 · 58 structures · Radon Gate 4 — No Naked Shorts_