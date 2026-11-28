# 0008. One scanner historical vol

## Status

Accepted. 2026-11-28.

## Context

The strength and theta scanners defined the same close-to-close historical
vol: sample standard deviation of log returns, sqrt(252), percentage points,
dropping a non-positive close.

`utils.realized_vol.compute_realized_vol` is the numpy contract for CRI and
RV-ratio. It does not drop those closes. `leap_scanner_uw.calculate_hv`
does not require the later close to be positive. Those are different filters.

## Decision

`scripts/utils/close_to_close_hv.py:close_to_close_hv` is the scanner
formula. Both scanners bind it as `calculate_hv`.

`scripts/tests/test_close_to_close_hv_single_source.py` rejects a second
copy of the non-positive filter.

## Consequences

A change to scanner historical vol happens in one file. CRI, RV-ratio, and
the leap scanner are not moved onto it.
