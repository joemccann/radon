"""Systematic strategy runner (docs/quant-strategies.md).

Weight-based strategies on daily closes, ported from the quants.md /
QuantJourney example suite, run as a sleeve that is separate from the Four
Gates evaluation pipeline. numpy only; no torch, no pandas, no DB at import.

Modules: ``indicators`` (rolling maths), ``engine`` (rebalance calendar,
look-ahead-free weights backtest, vol-target overlay), ``strategies`` (the
registry with provenance), ``data`` (Turso-first close matrix with the
IB > Robinhood > UW > Yahoo backfill ladder).
"""
