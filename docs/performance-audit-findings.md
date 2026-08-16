# Performance refactor - adversarial audit defect register

Three independent adversarial reviewers audited the round-1 refactor. Every defect below was
reproduced by running code in this worktree. This is the remediation backlog.



==========================================================================================
## LENS: math
==========================================================================================

Cleaned up all probe files. Findings below; every core formula in `twr_math.py` was re-derived independently and matches (see note at the end).

---

## D1 — `build_benchmark_block` crashes the whole builder on a constant portfolio return series

`scripts/lib/twr_math.py:717` guards only the **benchmark** side (`statistics.stdev(pairs.benchmark) == 0`). `scripts/lib/twr_math.py:726` then calls `statistics.correlation(portfolio, benchmark)`, which raises when **either** input is constant. Nothing catches it: `scripts/perf_twr_builder.py:974-990` (`_benchmark_block`) has no try, and `build_payload` only catches `DuplicateNavDate` / `NonFiniteInput`.

Failing input — 46 weekday NAV rows all `100000.0` (cash-only / dormant account), a moving SPY close series, `FlowSet(OK, {})`:

```
File ".../scripts/perf_twr_builder.py", line 859, in build_payload
    block, benchmark_reason = _benchmark_block(
File ".../scripts/lib/twr_math.py", line 726, in build_benchmark_block
    correlation = statistics.correlation(portfolio, benchmark)
statistics.StatisticsError: at least one of the inputs is constant
```

Wrong output: **no payload at all** — the caller gets a traceback instead of the `degraded`/`benchmark: null` payload the spec's §C.6 promises for every status. `statistics.covariance`/`variance` do *not* raise, so `beta` would have been computed as `0.0`; only `correlation` (and hence `r_squared`) blows up. The fixture generator deliberately avoids this case (`tests/fixtures/twr_scenarios.py:boundary_returns` comment: "keeps each boundary test about the GATE and not about a degenerate zero-variance series"), so it is untested.

---

## D2 — a NAV of exactly `0.0` is chained as a genuine −100% session and published `status: "ok"`

`scripts/lib/twr_math.py:281-282`, inside `_is_suspect`:

```python
if ret == _TOTAL_LOSS_RETURN:
    return False
```

The quarantine that exists precisely for "unexplained discontinuity with `C == 0`" is switched off at the single most extreme value it can see. Every neighbouring value is caught (NAV → `0.01` gives `r = -0.9999999` → quarantined + `degraded`); only exactly `0.0` slips through.

And `0.0` is exactly what the ingest manufactures. `scripts/perf_twr_builder.py:224` (`out[report_date] = _safe_float(node.get("total"))`) and `:234` route a missing / blank / `"nan"` attribute through `_safe_float` (`:127-133`), whose default is `0.0` — the row is not dropped:

```
parse_nav_entries(<... reportDate="20260113" />)          # no total attribute
  -> {'2026-01-12': 106680.59, '2026-01-13': 0.0, '2026-01-14': 0.0, '2026-01-15': 107000.0}
_nav_rows_to_map([{"date": "2026-01-13"}])  -> {'2026-01-13': 0.0}
```

Failing input — 30 weekday NAVs rising 0.2%/day with row 15 zeroed, empty `FlowSet(OK)`, `nav_source="flex_live"`:

```
status          = ok
twr.cum_return  = -1.0
n_returns       = 28   n_suspect = 0
warning codes   = ['SUBPERIOD_SKIPPED']      severities = ['info']
equity          = {'starting': 100000.0, 'ending': 105965.36, 'net_external_flows': 0,
                   'investment_pnl': 5965.36}
max_drawdown    = {'value': -1.0, ..., 'unavailable_reason': None}
last twr_index  = 0.0
```

The damage is permanent, not local: `growth *= 1 + (-1)` zeroes the accumulator at `twr_math.py:375`, so every later `growth *= (1 + r)` stays `0` and `cum_return` is pinned at exactly `-1.0` no matter what the account does for the rest of the year.

Rendered consequence — `web/components/PerformancePanel.tsx:259-269`: the value is non-null and status is `ok`, so `toCard` takes the `present` branch and the TWR Total card reads **`-100.00%`** with the subtitle **`+$5,965 investment P&L`**, no banner. That is the same failure mode the spec was written to make unreachable, just with the sign flipped.

---

## D3 — chart still plots dollar NAV against a rebased benchmark, so deposits read as outperformance

`web/lib/performanceData.ts:99` keeps `dollarEquity = nav ?? equity`, and `web/lib/performanceChart.ts:174-188` rebases the benchmark to `startEquity` and plots it against `point.equity`. Spec §C.5 requires `twr_index` to win.

Failing input — v2 payload whose NAV doubles purely from a `+100,000` flow, `twr.cum_return: 0.0`:

```
data.twr.cum_return         = 0
series[].equity             = [100000, 100000, 200000]   <- plotted
series[].twr_index          = [100, 100, 100]            <- present, ignored
model.latestEquity          = 200000
model.rebasedBenchmarkValues= [100000, 110000.00000000001, 111000]
model.latestBenchmark       = 111000
```

The chart draws a portfolio line ending at $200,000 against a SPY line at $111,000 — a visual +80% vs +11% — for a period whose own published TWR is exactly 0%.

---

## D4 — `xirr` reports `no_sign_change` for cash flows that have real IRRs

`scripts/lib/twr_math.py:849-855` brackets on `[-0.9999999, 10]` and only widens **upward**. When NPV has the same sign at both ends with two roots inside, it bails. Spec §D.3 requires solving anyway and taking the root nearest 0.

Failing input — the textbook two-root stream, one year apart:

```
CF = [(2026-01-01, -1000), (2027-01-01, +2500), (2028-01-01, -1560)]
NPV(0.20) = 0.000000     NPV(0.30) = -0.000000
xirr(...) -> XirrResult(value=None, unavailable_reason='no_sign_change',
                        multiple_sign_changes=True, iterations=0)
```

`multiple_sign_changes` is correctly detected, then contradicted by the reason. `gateCopy` (`web/lib/performanceTwr.ts:160-161`) renders that as **"no net capital at risk"** for a stream with $1,000 at risk and two IRRs. Reachability from `build_cashflow_vector` is narrow — it needs a negative terminal cash flow (`N_M < C_M`) — so this is a correctness/spec gap, not an imminent production wrong number.

---

## D5 — `sessions_behind` counts the in-progress session

`scripts/perf_twr_builder.py:178-183` walks `cursor <= end` with `end = date.today()`, so the current day counts as a *completed* session at any hour, contradicting the docstring ("Completed trading sessions").

```
nav_as_of=2026-08-12 (Wed), today=2026-08-14 (Fri, pre-close) -> 2   (only Thu completed)
nav_as_of=2026-08-11 (Tue), today=2026-08-14 (Fri, pre-close) -> 3   (only Wed,Thu completed) -> stale=True
```

Wrong output: the amber banner reads "3 sessions behind" on a NAV that is 2 completed sessions old, and `NAV_STALENESS_BUDGET_SESSIONS = 2` effectively behaves as 1 during the trading day. Fails safe (over-reports staleness), so low severity.

---

## What I could not break

I re-derived every statistic in `twr_math.py` from first principles with an independent stdlib implementation and diffed against the module. Zero deltas on the golden 60-session fixture (`rf_daily`, `cum_return`, `volatility`, `sharpe`, `downside_deviation`, `sortino`) and agreement to float noise on the E.3/E.4 pins (`stdev`, `beta`, `alpha`, `correlation`, `r²`, `tracking_error`, `benchmark_return`). Specifically checked and found correct:

- EOD denominator `B_t`, `n_subperiods == n_obs - 1`, no synthetic leading `r=0`, seed-date flows excluded from both the chain and the MWR vector.
- Sortino divides by **N**, not by the downside count (the anti-pin holds: 3.1749, not 2.0080); `daily_risk_free` is geometric, not `rf/252`.
- Modified Dietz weights `(D - elapsed)/D` and denominator `B + Σ w·C` — golden `7217.35574011656 / 143750 = 0.05020769210515868` reproduces by hand.
- VaR interpolation at `(n-1)·0.05`, CVaR tail `max(1, floor(0.05n))`; `floor` is float-safe at every n from 20 to 200; `cvar ≤ var` holds structurally (`k-1 ≤ floor(0.05(n-1))`).
- Act/365 in `annualize_return` and `_npv`, 252 only in vol/ratio/alpha scaling; the `cum_return < -1` branch returns `invalid_total_return` rather than raising on a complex result.
- Drawdowns run off the return index, never dollar NAV; `series[].drawdown` last value equals `current_drawdown` by construction.
- `align_series` is a pure inner join — no zero-fill, no `prev` carry.

`pytest tests/test_twr_math.py tests/test_perf_twr_flows.py tests/test_portfolio_performance.py -q` → `184 passed in 0.47s`. All five findings above are coverage gaps, not regressions against the existing suite.


==========================================================================================
## LENS: flows
==========================================================================================

Seven demonstrable defects under the flow-classification / integrity-gate lens. All reproduced by running the code in the worktree; scratch files removed afterwards.

---

## D1 — A single non-zero flow on the same date disables the suspect quarantine entirely
`scripts/lib/twr_math.py:279` — `if ret is None or flow != 0.0: return False`

§B.4 is implemented as an exact-zero test. Any recorded flow, however tiny, on the same date as an *unrecorded* transfer makes the session unquarantinable. There is no compensating warning: §B.4's `SUBPERIOD_EXTREME` and §C.3's `FLOW_DOMINANT` warning codes **are not emitted anywhere in the repo** (`grep -rn SUBPERIOD_EXTREME scripts web tests` → 0 hits; `FLOW_DOMINANT` appears only as the gate constant and the flag string).

Input: real production NAV `2026-02-05 → 2026-02-06` (246,713.50 → 972,215.53), flows `{2026-02-06: +1.00}` (the 725,502.03 ACATS still missing), `nav_source="flex_live"`, `nav_sessions_behind=0`.

```
status          : ok
warnings        : []
02-06 r         : 2.9406620634865948   flags: []   skip: None
cum_return      : 2.9519483009075462
investment_pnl  : 728285.5
```

`r=+2.9407` is the live broken value reproduced to 7 significant figures, under `status: "ok"` with an empty warnings array, and the deposit is reported as `investment_pnl`. F1/F3 defeated by a $1 row.

---

## D2 — A malformed external CashTransaction row is silently dropped and becomes `empty_verified`
`scripts/lib/flex_flows.py:143-144` — `if report_date is None or amount is None: continue`

The row was found and classified EXTERNAL, then discarded without incrementing `external_rows` and without a warning. If it was the only external row the FlowSet is `EMPTY_VERIFIED`, whose documented meaning (§A.4.1) is "fetched successfully, genuinely zero external flows". That is defect #1 wearing a different hat: "no deposits" is again *assumed*, not observed.

```
comma amount ("725,502.03")    status=empty_verified  by_date={}
amountInBase only (no @amount) status=empty_verified  by_date={}
us date ("02/06/2026")         status=empty_verified  by_date={}
```

`_amount(node, "amount")` reads only `@amount`; IBKR also emits `amountInBase`/`amountInBaseCurrency` on base-conversion statements, and `_normalize_date` accepts only `YYYYMMDD` / `YYYY-MM-DD`.

---

## D3 — `_has_flow_section` is an OR, so a CashTransactions-only statement hides every Transfer
`scripts/lib/flex_flows.py:118-119, 130-131`

§B.6 requires an absent flow section to be `FAILED`. The OR only fires when **both** sections are absent. A statement with `<CashTransactions>` and no `<Transfers>` parses as fully observed, and the entire `<Transfer>` class — the 725k — is invisible with no warning.

This is the production configuration, not a hypothetical. `scripts/perf_twr_builder.py:360`:
```python
flows_qid = _os.environ.get("IB_FLEX_FLOWS_QUERY_ID") or _os.environ.get("IB_FLEX_NAV_QUERY_ID")
```
`IB_FLEX_FLOWS_QUERY_ID` is defined **nowhere** in the repo, `.env.example`, or `cloud/` (only mentioned in a comment at `scripts/db/migrations/0035_perf_twr.sql:9`). Production has only `IB_FLEX_NAV_QUERY_ID=1497709`, documented as the CashTransactions query (repo CLAUDE.md; `scripts/cash_flow_sync.py:16`). So flows come from a query that structurally cannot contain a Transfer.

Replaying the real `2026-01-23 → 2026-01-26` pair (189,502.12 → 232,497.53) through such a statement:
```
FlowSet     : empty_verified {}
status      : ok
warnings    : []
cum_return  : 0.22688616887241153      <-- the live +22.69% "return"
equity      : {'net_external_flows': 0, 'investment_pnl': 42995.41}
```
A 42,995.41 deposit published as investment P&L under `status: "ok"`. It escapes quarantine because 22.7% < the 50% threshold.

---

## D4 — The read path defaults a missing `status` to `"ok"`, reproducing the exact live payload F4 declares impossible
`web/lib/performanceData.ts:543-544` (`?? (series.length < 2 ? "insufficient_data" : "ok")`), `:545` (`flows_status ?? ""`), `:270` (`nav_sessions_behind ?? 0`)

Feeding the literal live v1 row (no `status`, no `flows_status`, `nav_source: "disk_cache"`, `period_end: 2026-03-20`, `total_return: 9.5128`) to `buildPerformanceView`:

```
view.status        = ok
view.warnings      = []
view.errorWarnings = []
view.navSource     = disk_cache
view.navSessionsBehind = 0
view.twrCumReturn  = 9.512801391701704      <-- +951.28%
view.isStale       = false
```

F4: *"The live combination (nav_source: disk_cache, period_end: 2026-03-20, status: ok, warnings: []) is unreproducible."* It is reproducible in one function call.

Three separate causes, all in the read path:
1. missing `status` → `"ok"`;
2. missing `flows_status` → `""`, so the suppressor at `:559` (`flowsStatus === "failed" || isDegraded ? null : rawCumReturn`) does not fire. Note `normalizePerformanceData:271` defaults the *same* field to `"failed"` — the two normalizers disagree and the UI uses the permissive one;
3. `nav_sessions_behind` defaults to `0` and is never derived from `nav_as_of`, so a 148-day-old series self-reports "0 sessions behind".

This is live-reachable: `scripts/api/server.py:3911` still runs `portfolio_performance.py` (which mirrors to `performance_snapshots` at `portfolio_performance.py:1466`), and `perf_twr_builder` has no caller. The v1 payload is what the page actually receives. The Python-side Gate 2 itself is sound — `sessions_behind('2026-03-20', 2026-08-15) == 101` and `_is_within_disk_budget({'2026-03-20': …}) == False` both check out — but nothing reaches it.

---

## D5 — Multi-account Flex statement: NAV keeps one account, flows sum all of them
`scripts/perf_twr_builder.py:220-225` (`out[report_date] = …` last-statement-wins) vs `scripts/lib/flex_flows.py:136-156` (`.//CashTransaction` across every `FlexStatement`). `twr_math.consolidate_accounts` exists but has **zero callers** in the builder.

Two-account statement, U111 (100,000 → 101,000, +50,000 deposit) and U222 (20,000 → 20,100, +7,000 deposit):
```
NAV  : {'2026-01-05': 20000.0, '2026-01-06': 20100.0}   <- U111 silently dropped
FLOWS: {'2026-01-06': 57000.0}                          <- both accounts summed
```
Through `build_payload`:
```
status    : ok
warnings  : []
r         : -2.845  flags: ['flow_dominant']
cum_return: -2.845
```
A TWR of **-284.5%** — below the -100% analytic floor — published as `ok` with an empty warnings array. `flow_dominant` is in `flags` but §C.3's `FLOW_DOMINANT` warning is never emitted (D1), so nothing surfaces. There is also no plausibility gate on `cum_return` itself; `IMPLAUSIBLE_ANNUALIZED` cannot fire because `annualized` is already `period_lt_1y`.

---

## D6 — A blank NAV attribute becomes 0.0 and publishes -100% as `ok`
`scripts/perf_twr_builder.py:224` — `out[report_date] = _safe_float(node.get("total"))`, default `0.0`

Combined with the total-loss exemption at `scripts/lib/twr_math.py:281-282` (`if ret == _TOTAL_LOSS_RETURN: return False`), which was added to satisfy fixture E.1 #11 and now punches a hole in the quarantine at exactly `r == -1.0`.

Input: three NAV nodes where the middle one has `total=""`; the account actually goes 100,000 → 101,500 (+1.5%).
```
parsed NAV: {'2026-01-05': 100000.0, '2026-01-06': 0.0, '2026-01-07': 101500.0}
status    : ok
warnings  : ["SUBPERIOD_SKIPPED"]        (info only)
cum_return: -1.0
   2026-01-06 r= -1.0  []    None        <- not flagged, not quarantined
   2026-01-07 r= None  ['zero_base'] zero_base
```
A malformed attribute becomes a published -100% TWR under `status: "ok"`. The same exemption means a genuine full withdrawal (NAV → 0, transfer row missing) is chained as -100%: `build_payload([100000, 0.0], FlowSet.empty_verified())` → `status: ok`, `warnings: []`, `cum_return: -1.0`.

---

## D7 — The builder fetches the same Flex query twice in a row
`scripts/perf_twr_builder.py:332` and `:364` both call `fetch_flex_xml(token, <same qid>)` when `IB_FLEX_FLOWS_QUERY_ID` is unset (D3), seconds apart, instead of parsing NAV and flows out of the one document already in memory.

Code path is concrete; the consequence is inferred, not live-verified (read-only constraint): IBKR throttles repeat requests for the same query id (the repo's own `cash_flow_sync` throttle-embargo incident, `project_cash_flow_sync_incident_2026_08_04`). A 1018/1019 on the second call produces `FlowSet.failed` → `status: "degraded"`, `twr: null` on every run. Fail-safe rather than fail-open, but it would make the page permanently blank once wired up (F12.1).

---

**Not defects** (checked and clean): `FlowSet.__post_init__` invariants hold — `FlowSet(FAILED, by_date={…})` and `FlowSet.failed("")` both raise; `classify_flow_type` raises `UnknownFlowType` on `"Deposit Advance Reversal"` and `"Sharebuilder"` and the raise propagates through `parse_flows` to `get_external_flows_for_nav:365` → `FlowSet.failed`, never swallowed; `build_payload`'s `not flows.is_usable` branch correctly emits `FLOWS_FETCH_FAILED` with `twr: null`; `load_nav_from_turso`'s bare `except: pass` fails toward `"none"`/`unavailable`, the safe direction; `_resolve_status` precedence matches §C.1; `place_flows` correctly drops a seed-dated flow as already inside `N_0`.


==========================================================================================
## LENS: tests
==========================================================================================

Seven defects, each reproduced. Tree restored to its pre-review state (verified: pytest `tests/` 208 passed, `performance-twr-math` 19 passed).

---

**1. `FLOW_DOMINANT` warning is never emitted, and the golden test pins its absence**
`scripts/perf_twr_builder.py:489` (`_subperiod_warnings`) handles only `nav_gap`, `suspect_no_flow` and generic skips. Spec §C.3 requires one `FLOW_DOMINANT` info warning per flow-dominant subperiod.

Input: `build_payload(golden_nav(), ok_flows(GOLDEN_FLOWS))`
```
flow_dominant subperiods: [('2026-02-13', 100000.0)]   # |C|/B = 0.975
warnings                : []
```
`tests/test_perf_twr_flows.py:797 test_counts_block_is_internally_consistent` asserts `payload["warnings"] == []` on exactly this payload — the suite actively pins the missing warning.

**2. `SUBPERIOD_EXTREME` is never emitted → a partially-recorded transfer chains silently under `status: "ok"`**
Spec §B.4: `|r_t| > 0.50` with a nonzero `C_t` must chain but emit `SUBPERIOD_EXTREME`. Nothing in the repo emits that code.

Input: NAV `{2026-01-05: 100000, 2026-01-06: 200000}`, flows `{2026-01-06: 1.00}`
```
r      = 0.99999
flags  = []   skip = None
status = ok
warns  = []
```
A +100% session publishes clean. This is the residual of the headline bug: an ACATS whose cash leg is captured but whose `positionAmountInBase` leg is not defeats the suspect gate (`C != 0`) *and* the flow-dominant flag (`|C|/B = 1e-5`). No test exercises `|r| > 0.5` with a nonzero flow — `test_20` (`:498`) uses `r = +0.2%`.

**3. Degraded payloads emit `equity: {}` and `calendar_days: 0`; the web fixture hides it**
`scripts/perf_twr_builder.py:639` `_suppressed_payload` hardcodes `calendar_days=0` (`:666`) and never fills `equity`.

Input: golden 61-observation NAV + `FlowSet.failed("timeout")`
```
period_start 2026-01-02   period_end 2026-03-27   calendar_days 0
equity {}
counts {'n_nav_observations': 61, 'n_subperiods': 60, 'n_returns': 0, ...}
mwr.period_return.n = 60          # contradicts counts.n_returns = 0
```
Feeding that *actual* payload through `PerformancePanel` renders:
```
HERO SUBTITLE = Ending equity $0.00 / as of 2026-03-27 / N=0
```
The web tests never see it: `web/tests/fixtures/performanceScenarios.ts:325 flowsFailedDegradedPayload()` spreads `goldenOkPayload()` and inherits its `equity`, `counts`, `calendar_days`, `series`, `subperiods` — a shape the builder cannot produce. And `tests/test_perf_twr_flows.py:725 test_every_branch_emits_the_same_v2_keys` compares only `set(payload)`, so §C.5's "same keys, only values change" is unenforced below the top level.

**4. `test_74` passes on a still-contaminated number**
`tests/test_perf_twr_flows.py:835` asserts `abs(published) < 0.60`. Actual on the live replay with no flows:
```
cum_return   0.532119750498659
2026-01-26 r 0.2268861688724116   flags []   skip None
```
The real production 2026-01-26 deposit sits under the 0.50 suspect threshold, is chained as a +22.7% session, and the test asserts nothing about it. F1's "plausible" bound is being met at +53.2% with 0.068 of headroom over a number that is ~23 points of deposit.

**5. `consolidate_accounts` has no production caller; `NAV_ACCOUNT_GAP` is never emitted**
`scripts/lib/twr_math.py:407`. Grep for callers returns only `tests/test_twr_math.py:311` and `tests/test_portfolio_performance.py:373,388`. `build_payload` has no per-account entrypoint, and no code path converts `gap_dates` into the `NAV_ACCOUNT_GAP` warning §C.3 requires. E.1 #22 tests a function that cannot execute in production — the same "no caller" class as the spec's own defect #9.

**6. `gateCopy("benchmark_coverage")` prints `n/min_n`, not coverage**
`web/lib/performanceTwr.ts:122-130`. `coveragePercent = round(n / min_n * 100)`, but for this reason `n` is `n_common` and `min_n` is `MIN_N_BENCHMARK`, not `n_returns`.
```
gateCopy(suppressed("benchmark_coverage", 50, 40), "Beta")
  -> "SPY covers 125% of sessions"      # true coverage was 50/60 = 83%
```
The only assertion is `web/tests/performance-twr.test.ts:209` `toContain("SPY covers")`, which cannot catch a nonsensical percentage.

**7. `benchmarkReason` is produced and never rendered; the benchmark gateCopy branches are dead**
`web/lib/performanceData.ts:436/582/642` compute it; grep shows zero consumers in `PerformancePanel.tsx` or `MobilePerformancePanel.tsx` — both do `if (benchmark) { push beta/alpha/TE }` and otherwise omit the cards entirely.
```
buildPerformanceView(<ok payload, n_common 50, n_returns 60>)
  -> benchmark: null   benchmarkReason: "benchmark_coverage"   # rendered nowhere
```
Spec §C.2/§C.6 require the reason on screen. The guarding test (`performance-panel-twr-payload.test.tsx:216`) asserts only `queryByTestId("performance-card-beta") === null`, which is satisfied identically by "reason on a `--` card" and by "card silently deleted" — so `benchmark_coverage`, `benchmark_degenerate` and the benchmark `insufficient_n` copy are unreachable.

---

Two smaller notes:

- `performance-panel-twr-payload.test.tsx:266-276` — the `expect(body).not.toContain("951.28")` half of "guards an absurd annualized magnitude" passes because `livePathologicalPayload()` is `status: "degraded"`, not because of a guard. Flipping the same fixture to `status: "ok"` renders hero `+951.28%` (verified). The annualized half of that test is real (`isImplausibleAnnualized` does fire and the card reads `--`); the cum_return half is not.
- Leftover implementer scratch in the worktree: `tmp_probe_a.py` … `tmp_probe_g.py`, `web/tests/tmp_probe_chart.test.ts`.

Checked and found sound: the gate-parity test *does* exist (`web/tests/performance-twr-math.test.ts:107`, regex over the annotated Python lines — mutating `MIN_N_DISPERSION` in the TS mirror fails 3 tests), and every numeric pin in §E.3/§E.4/§E.6 reproduces from an independent stdlib recomputation built from the spec text (golden `cum_return`, vol, sharpe, sortino, downside dev, max DD, VaR/CVaR, Dietz, XIRR@420, beta/alpha/corr/TE/benchmark return, all NAV checkpoints) — the pins are not tautological.