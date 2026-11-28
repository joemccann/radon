# 0007. One Cboe daily CSV parser

## Status

Accepted. 2026-11-28.

## Context

`fetch_straddle` and `fetch_vixts` walked a Cboe daily-prices CSV with the
same date format and the same skip rule. `fetch_cor` and `fetch_vixcor`
did the same walk with the column fixed to CLOSE. Panic index and credit
VIX already called the VIX term-structure copy.

`fetch_vix_model_spread.parse_index_csv` is a different schema: it checks
the header, strips a BOM, and validates the symbol before the walk.
`fetch_msr` uses the same date and float parse, then drops non-positive
and non-finite closes. That filter stays in the MSR job.

## Decision

`scripts/utils/cboe_csv.py:parse_daily_prices` owns the walk. Each job
keeps `parse_index_csv` as the name tests and callers already use, and
that function only picks the column.

`scripts/tests/test_cboe_csv_single_source.py` rejects a second copy of
the DATE strptime used by this walk.

## Consequences

A change to how a bad Cboe daily row is skipped happens in one file.
The VIX model spread parser is unchanged.
