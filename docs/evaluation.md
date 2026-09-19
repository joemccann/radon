# Trade Evaluation — 7 Milestones

Pipeline for converting a ticker into a trade decision. Stop on failure at any gate.

---

## Milestones

1. **Validate ticker** → `scripts/fetch_ticker.py`
   - 1B Seasonality (UW → EquityClock Vision → cache fallback)
   - 1C Analyst ratings
   - 1D News

2. **Dark pool flow** → `scripts/fetch_flow.py`
   - During market hours, partial data is volume-weighted interpolated. **Always output BOTH actual and interpolated.** Details in §Intraday Dark Pool Interpolation below.

3. **Options flow** → `scripts/fetch_options.py`
   - 3B OI changes → `fetch_oi_changes.py` (REQUIRED, not optional)

4. **Edge decision — PASS / FAIL**
   - FAIL = stop. No structure, no Kelly, no logging.

5. **Structure — convex**
   - R:R < 2:1 = stop.
   - Generate the trade-spec HTML report at this milestone (mandatory) — see `docs/reports.md`.

6. **Kelly sizing**
   - Enforce 2.5% bankroll cap per position. Hard cap, not advisory.
   - `evaluate.py` M6 is still a placeholder (`PENDING`, exit 2); the wiring plan and Fortune's Formula gap table are in `docs/risk/kelly-fortunes-formula.md`.

7. **Log**
   - Executed → `data/trade_log.json` (append-only).
   - NO_TRADE → `docs/status.md`.

---

## Intraday Dark Pool Interpolation

`progress = minutes since 9:30 ET / 390`
Projected total = actual / progress
Blend: `(projected × progress) + (prior_5d_avg × (1 - progress))`
Pace = actual / (avg_prior × progress)

| Progress | Confidence | Prior weight |
|---|---|---|
| 0–25%   | VERY_LOW | 75%+ |
| 25–50%  | LOW      | 50–75% |
| 50–75%  | MEDIUM   | 25–50% |
| 75–100% | HIGH     | <25% |

Use interpolated for the edge decision. LOW / VERY_LOW → re-evaluate after 2 PM ET. Pace > 1.2× → flow is real. Actual opposite prior → likely reversal.

---

## Signal Interpretation

- **P/C Ratio:** >2.0 BEAR | 1.2–2.0 LEAN_BEAR | 0.8–1.2 NEUTRAL | 0.5–0.8 LEAN_BULL | <0.5 BULL
- **Flow Side:** Ask-dominant = buying | Bid-dominant = selling
- **Analyst Buy%:** ≥70% BULL | 50–69% LEAN_BULL | 30–49% LEAN_BEAR | <30% BEAR
- **Seasonality:** >60% FAVORABLE | 50–60% NEUTRAL | <50% UNFAVORABLE

> Seasonality / ratings = context, not gates. Strong flow overrides weak seasonality.

---

## Seasonality Fallback

UW → EquityClock Vision (Claude Haiku) → Cache (`data/seasonality_cache/{TICKER}.json`).

Route: `web/app/api/ticker/seasonality/route.ts`.

Required API keys (any one): `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`.

---

## Four Gates Cross-Reference

Methodology gates (from project root `CLAUDE.md`):

| Gate | Rule |
|---|---|
| 1. Convexity | Gain ≥ 2× loss. Defined-risk only. |
| 2. Edge | Specific, data-backed dark-pool / OTC signal that hasn't moved price. |
| 3. Risk | Fractional Kelly. Hard cap 2.5% bankroll / position. |
| 4. ~~No naked shorts~~ | **DISABLED 2026-04-30.** Re-enable: `docs/naked-short-reenable.md`. |

A milestone-5 structure must satisfy gate 1. A milestone-4 PASS must satisfy gate 2. Milestone 6 enforces gate 3.
