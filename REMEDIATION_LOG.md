# REMEDIATION_LOG.md — PART B execution log

**Contract:** `TEST_AUDIT.md` §9 (frozen backlog T-001…T-054). One task per change set; every new test demonstrated RED before GREEN; never weaken an assertion to pass; BLOCKED requires root-cause hypothesis.
**Branch:** `test-audit-remediation` (worktree off `2a75496a` — the main tree belongs to the concurrent session and is never touched).
**Baseline (worktree, clean HEAD):** recorded below before the first task.

| Task | Status | Commits | Evidence |
|---|---|---|---|
| T-001 | DONE | ec0d9596 | RED: `playwright test --list` imported e2e/prices-performance.test.js which printed "Starting Next.js server for testing..." and spawned `npm run dev` during LISTING (rtk tee 1786165991_playwright.log); full-run crash 7s in at audit time (runs/playwright-r1.log, spawn /bin/sh ENOENT). GREEN: with `testIgnore: ["**/*.test.js"]` in both configs, `--list` exits clean: "Total: 419 tests in 123 files", no side effects. |
| T-002 | DONE | (T-002 commit) | RED: audit 3x cloud runs — test_external_signal_status_is_preserved_after_recovery[int,hup] deterministic fail on darwin (communicate(timeout=5) → SIGKILL rc -9; runs/cloud-r{1,2,3}.log). GREEN: darwin-scoped skipif added beside the GNU-timeout skipif; `pytest -k external_signal` → `2 skipped in 0.03s`; params still collect and run on linux. |
| T-003 | DONE | (T-003 commit) | RED: fixture with period_label removed → both build_html tests FAIL (KeyError path, identical to the live-cache failure at scripts/performance_explainer_report.py:259): `2 failed, 1 passed, 1 skipped`. GREEN after restore + completing the fixture (series[].drawdown, last_sync discovered by execution): `3 passed, 1 skipped in 0.07s`. build_html now has deterministic CI coverage; live-cache pass kept behind RADON_LIVE_CACHE_SMOKE=1. |

| T-010 | DONE | (T-010 commit) | RED: 3 new tests in TestExitOrdersJournalFailureGuard all failed against current code (`3 failed in 0.23s` — place_order called TWICE across two cycles; no error surfaced; no heal). GREEN after fix: file 14/14; monitor_daemon+exit_order_service 271/271; full pytest layer 4932 passed/14 skipped in 69s. Fix: `_update_journal_trade` returns bool; `_unrecorded_placements` guard keyed (journal_trade_id, order_type) blocks re-placement while the row still reads PENDING, retries the journal write on later cycles (heal), and the cycle surfaces `result["error"]` so BaseHandler records state=error (watchdog visibility). |

| T-011 | DONE | (T-011 commit) | RED: new test — restart branch re-ran ib_place_order.py (`1 failed, 1 passed`; control proves ib_sync.py retry preserved). GREEN: `_NON_IDEMPOTENT_IB_SCRIPTS` carve-out returns an explicit INDETERMINATE error ("not automatically retried… check open orders") instead of re-running; targeted 4/4, scripts/api/tests 513 passed, full pytest layer 4935 passed/14 skipped. |

| T-012 | DONE | (T-012 commit) | RED: `2 failed, 2 passed` — trailing `{"progress":100}` and trailing `[1,1]` both shadowed the status-bearing result. GREEN: status-dict-wins rule (last status dict from the end; legacy last-parse fallback preserved for arrays/status-less outputs): file 23/23, full pytest layer 4939 passed/14 skipped. |

| T-013 | DONE | (T-013 commit) | RED: `1 failed, 1 passed` — AAOI and MU orders sharing orderId 5 produced the identical trade_id `fill-monitor:order-5:filled-10` (destructive ON CONFLICT overwrite); idempotence control passed. GREEN: key now `fill-monitor:con-{conId}:order-{permId||orderId}:{date}:filled-{n}` — distinct across contracts/sessions, stable for same-fill re-detection. File 22/22; full pytest layer 4941 passed/14 skipped. |

| T-014 | DONE | (T-014 commit) | RED: reverse-chronological delivery ([SELL@14:05, BUY@14:00]) labelled the SELL as SELL_TO_OPEN (`1 failed`). GREEN: `_fills_to_entries` now walks `sorted(fills, key=_fill_exec_sort_key)` (numeric exception-proof key; stable for unknown times) — file 31/31, full pytest layer 4942 passed/14 skipped. Matches the sibling sort in journal_rehydrate.py:135 and the backfill importer. |

| T-015 | DONE | (T-015 commit) | RED: `2 failed, 2 passed` — production wiring (trade_log_path=None) bricked on an EMPTY journal table ("journal read failed: … empty; cannot recover") which also blocked the next-cycle upsert retry. GREEN: `_load_existing_from_journal(db, allow_empty=True)` on the prod path (recovery path still raises on empty); 4 prod-config tests now pin journal-table dedupe, reconciled==0, read-failure abort-without-upserts, and within-session upsert retry. File 35/35; full pytest layer 4946 passed/14 skipped. |

| T-022 | DONE | (T-022 commit) | RED: `1 failed, 8 passed` — journal net +10 vs IB position -10 passed `abs()==abs()` and applied a LONG lot's basis to a SHORT. GREEN: signed equality `journal_net == position_size` (None keeps legacy pass); zero-net-vs-open-position pin added; basis/carry/entry-date neighbors 29/29; full pytest layer 4948 passed/14 skipped. |

| T-023 | DONE | (T-023 commit) | RED (true, after an import fix in the test itself): "got 2026-06-26" / "got 2026-11-02" — UTC-aware evening fills stamped the NEXT day (incl. the DST-week case). GREEN: shared `et_session_date()` in handlers/base.py (aware→ET, naive assumed ET, no-arg=now ET); wired into journal_sync `_fill_to_entry` and fill_monitor's `fill_date`. Collateral fixed en route: the T-013 key made legacy `test_partial_fill_writes_to_journal` mock-address-dependent (`int(MagicMock())==1` shadowing orderId; '5'-in-id passed only when a mock repr contained 5) — fixture now carries realistic conId/permId(0), assertion untouched; `_identity_int` guards non-int identity junk. Stability: monitor_daemon 3x 270/270; full pytest layer 4951 passed/14 skipped. |

| T-043 | DONE | (T-043 commit) | Classifier pins green on arrival (by design for pins); teeth PROVEN by mutation: removing the `"stream"` marker → `3 failed, 10 passed`; reverted → 13/13. Bonus finding pinned loudly (not fixed, logged in NEW_FINDINGS): `_hrana_with_retry`'s isinstance bypass retries ANY HranaHttpError once — `_is_delete_transport_error` is effectively dead code at the real DELETE call sites. Full layer 4972 passed. |
| T-053 | DONE | (T-053 commit) | hrana_http failure-mode contract pinned via fake urlopen: socket.timeout + wrapped URLError classify as transport; statement-level `results[0].type=='error'` does NOT; malformed JSON raises HranaHttpError (unclassified — pinned); explicit timeout kwarg PROVEN to reach urlopen. 6 tests green (in the same 19-test run as T-043). |

| T-016 | DONE | (T-016 commit) | RED: `10 failed \| 10 passed` — zero bid/ask derived mid 0 and published last=$0.00; zero LAST/CLOSE/52w accepted. GREEN: `normalizePrice` (0 = no-quote for PRICE fields only; sizes/volume keep zero; -1 sentinel unchanged; one-sided books derive no mid per the existing two-sided gate). Tick-handler neighborhood 63/63; full vitest layer 5207 passed. Draft by opus agent, applied+verified+landed by lead. |
| T-017 | DONE | (T-017 commit) | RED: `9 failed \| 13 passed` — restoreSubscriptions rebroadcast raw prior-session state (9h-old quotes as live) bypassing safeInitialState. GREEN: `buildRestorePriceMessage` routes every reconnect broadcast through the same 8h guard as the three subscribe paths (boundary pinned); `node --check` on the relay clean; full vitest layer 5207 passed. |

| T-037 | DONE | (T-037 commit) | RED: `8 failed \| 20 passed` — NaN excess_liquidity returned level "none" with cushionPct NaN; +Infinity NLV fired a FALSE CRITICAL (cushion = el/Inf = 0). GREEN: Number.isFinite guards + additive `degraded` flag (insufficient-data "none" distinguishable from healthy "none"); equality boundaries pinned per the documented strict thresholds (0.05/0.01 strict, ewl<=mmr*1.10 inclusive). File 28/28; vitest layer 5218 passed. |
| T-038 | DONE | (T-038 commit) | RED: `7 failed, 4 passed` — bankroll -100k returned size -22,000 (np.minimum picks the MORE negative), NaN propagated, and CLI `--bankroll 0` silently omitted every sizing key (falsy-zero). GREEN: non-finite/<=0 bankroll clamps to 0 before broadcast; CLI uses `is not None`; kelly neighbors 49/49; pytest layer 4984 passed. CLI negative-bankroll arithmetic flagged as follow-up in NOTES (out of scope). |

| T-030 | DONE | (T-030 commit) | Contract pin green on arrival (kwargs were wired verbatim); teeth PROVEN by mutation: hardcoding `tif="GTC"` at ib_place_order.py:318 → `1 failed, 5 passed`; reverted → 6/6. Covers action/qty/price/tif(GTC+DAY)/outsideRth(True+False). |
| T-032 | DONE | (T-032 commit) | RED (with T-034 cluster): `8 failed, 3 passed` — terminal-error and ok returns omitted fill fields. GREEN: `_build_terminal_error` carries `filled`/`avg_fill_price`; ok path carries `filled`/`remaining`/`avgFillPrice` (naming matches each dict's existing convention); partial-fill-then-Cancelled no longer reports a bare error. Neighborhood 38/38. |
| T-033 | DONE | (T-033 commit) | Deadline pin: mutation `12.0→6.0` for combo → `1 failed, 1 passed`; reverted → 2/2. Combo 12s / single-leg 6s confirm budget now enforced by test. |
| T-034 | DONE | (T-034 commit) | RED in cluster run above. GREEN: quantity<=0, non-integral quantity, and limitPrice<=0 refused with operator-readable errors BEFORE IBClient construction (mirrors modify guard ib_order_manage.py:189-192). Full pytest layer 5007 passed/14 skipped. |

| T-024 | DONE | (T-024 commit) | RED: `1 failed, 3 passed` — failure on the first INSERT after DELETE-all left the table EMPTY over an autocommit-faithful fake (real working orders invisible on /orders). GREEN: upsert-new-snapshot-first (chunked multi-row per Hrana bounding), delete-extraneous-last — any-point failure leaves old/new/union, never empty; error still propagates. Writer neighborhood 80/80; full pytest layer 5011 passed/14 skipped. |

| T-031 | DONE | (T-031 commit) | RED: `2 failed` — the permId==0 + errorEvent exit returned no ib_error_code/ib_error_text. GREEN: structured fields added (matching the terminal-failed builder); order identity (orderId/permId=0) preserved. Place-path 10/10; full pytest layer 5014 passed/14 skipped. |

| T-046 | DONE | (T-046 commit) | RED: `2 failed, 1 passed` — the incident-born ladder was dead code under test AND `total += batch_size` inflated the reported count (200 for a 7-row tail, written into service_health detail). GREEN: page COUNT(*) before the batched DELETE; fallback-then-re-arm and wedged-page-abort now pinned against a scriptable Hrana seam. Neighbors 73/73; full pytest layer 5018 passed/14 skipped. |

## Baseline

Worktree (clean 2a75496a + T-001/T-002 applied): **pytest rc=0** (4927 passed / 14 skipped, 74.9s — perf-explainer skips here because data/performance.json is absent, proving the T-003 CI-blindness), **vitest rc=0** (41.6s), **cloud rc=0** (723 passed / 4 skipped incl. the 2 darwin skips, 102.6s). Logs: scratchpad runs/wt-baseline-*.log.

## Entries

### T-001 — un-break the Playwright runner
- **AC:** `npx playwright test --list` exits 0 and lists ~123 specs; no spec self-spawns a server. **Met.**
- **Files:** `web/playwright.config.ts`, `web/playwright.no-server.config.ts` (added `testIgnore: ["**/*.test.js"]` + rationale comment). The legacy script is retained on disk (it is a manual perf harness, not a spec); it is simply no longer collected.
- **Note:** the red demonstration itself proved the blast radius — during `--list` the file spawned a real `npm run dev`; stray-process sweep afterwards confirmed clean (no listeners on 3000/8321/8765).
