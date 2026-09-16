# Vol/Skew MR scanner

Short-term top and bottom framing from spot extension versus IV and skew
path. Mode id `vol-skew-mr`. UI label **Vol/Skew MR**. Route
`/scanner?mode=vol-skew-mr`.

Framing inspired by Options Insight / Imran Lakha (strategy inspiration
only; Radon implements the gates against Unusual Whales and existing
Radon vol/skew feeds).

## Gates

1. Technicals: RSI(14) and/or Bollinger percent B(20, 2). RSI at or
   beyond 70/30, or percent B outside 0 to 1, marks an extended high or
   low.
2. IV path into the move:
   - Extended high + falling or flat IV -> `TOP_MR`
   - Rally + rising IV with spot -> `BREAKOUT`
3. Skew: when vol and skew both diverge from spot, suggest a put spread
   (tops) or call spread (bottoms).
4. Symmetric `BOTTOM_MR` / `BREAKDOWN` on an extended low.

Verdicts: `TOP_MR`, `BOTTOM_MR`, `BREAKOUT`, `BREAKDOWN`, `NO_SIGNAL`.

## Family contract

Mirrors strength / theta: `scripts/vol_skew_mr_scanner.py`, disk cache
`data/vol_skew_mr.json`, Turso `vol_skew_mr_snapshots` (migration 0075),
GET `/api/scanner/vol-skew-mr`, POST `/api/scanner/vol-skew-mr/scan`,
comma ticker search, NDX preset.
