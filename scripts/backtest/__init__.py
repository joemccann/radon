"""Strategy backtester + walk-forward harness (F12).

Pure-math metrics + a vectorized, look-ahead-free walk-forward engine that
replays historical signal rows from the Turso snapshot tables and marks each
entry to market against forward returns. Mirrors the conventions in
``scripts/forecasting/`` (lean imports, numpy allowed, no torch at module
load) and reuses ``scripts/costs.py`` for round-trip cost modelling.
"""
