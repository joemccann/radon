# RELIABILITY_LOG.md — PART B execution log

**Contract:** `RELIABILITY_AUDIT.md` §5 (frozen backlog REL-001…REL-022). Every fix ships with its own fault-injection proof: red before the fix, green after, full suite green before commit. BLOCKED requires a root-cause hypothesis. New discoveries → NEW_FINDINGS appendix, not mid-loop chases.

| Task | Status | Commits | Evidence |
|---|---|---|---|
| REL-001 | DONE | (this commit) | RED: `python3.13 -m pytest scripts/tests/test_position_reconcile_spine.py` → `10 failed, 1 passed` against current code (quantity_mismatch never populated; main() exit 0 + no health row on IB-connect failure; no daemon handler). GREEN: 11/11 — per-contract signed-quantity drift detected (LONG 5v3, SHORT −5v−3, matched book clean); `main()` exits 2 + `position-reconcile` error row on connect failure; ok/error row on every run keyed to `needs_attention`; new `PositionReconcileHandler` (30-min RTH, alert-only, IB truth) registered in `create_daemon()` and in BOTH watchdog catalogs (`services.py` intraday bucket + `serviceHealthWindows.ts`, requires_ib pins updated on both sides; handler uses a literal `service_name` so the AST registration contract now enforces it). `/journal/reconcile` route now 502s instead of `{"ok": true}` when reconcile can't run (rc≠0 via existing `run_script_raw` handling). Full gates: pytest `5171 passed, 1 skipped`; vitest (repo root) `5414 passed`. |

## NEW_FINDINGS

(none yet)
