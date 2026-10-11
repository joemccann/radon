# SPX breadth implementation contract

The supplied StockCharts chart dated September 18, 2026 establishes a descriptive three-pane indicator: S&P 500 price index, percentage of S&P 500 stocks above their 200-day moving average, and net 52-week new highs minus new lows percentage. It defines no binary trigger or crash probability.

The [source parity report](record-high-breadth-source-parity.md) and [sanitized authenticated MCP receipt](record-high-breadth-source-probe.json) validate the formulas and document provider differences. TradingView SPX matches 7650.50. Its S5TH reads 49.50% versus the reference 52.00%. MAHP/MALP read 5/29; net -24 normalized per 500 is -4.80, versus the original -4.40%. The actual daily universe denominator is unverified. The UI therefore labels this explicitly as a TradingView variant and the lower pane as net new highs/lows per 500, retaining raw counts and denominator provenance.

TradingView SPX is the comparison panel of this explicitly requested provider-coherent MCP variant. It does not replace the primary generic SPX price store or change IB/Robinhood/UW source priority elsewhere.

## Calculation and storage

Join SP:SPX, INDEX:S5TH, INDEX:MAHP and INDEX:MALP by New York exchange session date. Preserve source timestamps and fetch times. Percentage participation is the direct S5TH close; net breadth per 500 is 100 * (MAHP - MALP) / 500. Missing source observations are excluded, never filled with zero. Exclude the current session before 18:00 New York; provider finalization remains unverified even after that conservative cutoff.

The bounded 5,000-bar response is incomplete. Shared breadth observations begin in 2016 and do not validate January 1973, November 1999, century rarity, or all-time record highs. Persist date-keyed history, the canonical scan snapshot, and an atomic JSON cache through the existing scheduled-writer pattern. Reconcile the bounded source window on each collection to capture revisions.

## Delivery and verification

A protected read-only DB-first Next route serves the stored snapshot, with a disk fallback and explicit unavailable state. The Regime workspace renders three panels, range controls, freshness, methodology, historical coverage and reference parity. A post-close systemd timer updates the indicator and writes service health.

No local test suites are permitted. Formula, session alignment, invalid/missing inputs, persistence, API, UI and browser regressions run on GitHub CI at the exact PR head. Production OAuth credential provisioning is an operator deployment prerequisite; this change does not copy local tokens to the VPS.
