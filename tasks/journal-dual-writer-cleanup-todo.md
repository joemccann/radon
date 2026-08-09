# Journal dual-writer dedup — Flex-ground-truth cleanup (open)

Created 2026-08-09 after incident `20260809T162000Z` remediation (commits
`2b4b5402`, `05b24bfa`, `f1050cc4`). Context memory:
`feedback_journal_dual_writer_duplication.md`.

## Problem

The Turso `journal` records the SAME fill under two writer conventions:
real-time daemon (bare-root ticker, dotted-hex IB exec ids) and Flex
rehydrate (OCC-symbol ticker, numeric / `+`-composite Flex ids, dates can
differ by a day; also `CLOSED` round-trip rows and historical
`SELL_TO_OPEN` mislabels of closes). The expiry sweep
(`scripts/monitor_daemon/handlers/expiry_sweep.py`) dedups what it can and
REFUSES the rest: its every cycle logs the refused contracts and the
`journal-expiry-sweep` service_health detail carries `skipped_guarded`
(14 as of 2026-08-09; the ambiguous set included TSLA 425/475C 0618,
CRCL 110C 0618, KWEB 31C 0717, EWY 215C 0717, MU 1050C 0717,
ETHA 15C 0618, CBRS 215C 0717, SNDK 1500P 0724, SPCX 120P/135C/155C 0731,
MSFT 460C 0803, EWY 130P 0313).

These contracts remain open-looking in the journal (position views,
isOpen stats, realized P&L all affected), and any other job that nets
journal rows inherits the same double-count hazard.

## Task

- [ ] Pull authoritative trade history for each guarded contract from IB
      Flex (`IB_FLEX_TOKEN` + trade query, journal rehydrate uses query
      `1442520`; see `scripts/journal_rehydrate.py` for the client).
- [ ] For each contract, reconcile journal rows against Flex executions:
      identify which rows are duplicates of the same fill vs distinct
      trades; produce a per-contract verdict (true net at expiry).
- [ ] Repair: mark/merge duplicate rows (design decision — dedup marker
      column vs row deletion vs canonical-id rewrite; journal is
      append-only by convention, so prefer marking over deleting), then
      let the expiry sweep close the now-unambiguous contracts.
- [ ] Extend the fix to the WRITERS so new duplicates stop being created
      (rehydrate + daemon should agree on one contract identity/exec-id
      convention — the "trade_log dedup pending" item in
      `project_knowledge_base.md`).
- [ ] Verify: expiry-sweep cycle reports `skipped_guarded: 0` (or only
      genuinely-unresolvable rows), realized P&L spot-checks against IB
      statements for 2-3 repaired contracts.

## Guardrails

- Alert-only conservatism: never guess on ambiguous rows
  (`feedback_ib_auto_recovery_conservative`, sweep's ambiguity guard).
- TDD with production shapes lifted verbatim (see
  `scripts/tests/test_monitor_daemon/test_expiry_sweep.py`
  `TestCrossConventionDedup` for the pattern).
- Turso Hrana I/O bounding for any bulk repair writes
  (`scripts/CLAUDE.md`).
- Verify in live Turso, not `data/*.json`.

## Related open items (same diagnosis, separate tickets)

- `exposureBreakdown.ts:50` hard-codes ±0.5 delta for expired options
  (its test hardcodes a past expiry, masking it).
- `realizedPnl` layer never labels/realizes `Ep` expiration round trips.
- No expiry gate on PENDING exit orders (`exit_orders.py:94` stale GTC
  risk) + hardcoded past fallback expiry in `exit_order_service.py:202`.
- `journal_gap_sli.py:159` same content-as-error conflation.
- Phantom longs can satisfy the covered-call gate (`nakedShortGuard.ts:105`).
