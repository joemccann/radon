# Cash-flow sync overhaul

Status: PLAN. Nothing in here has been executed.
Written 2026-08-16 from three independent diagnoses (failure history, request accounting, data audit).

Hard constraint while this plan is being executed: the Flex token is at `throttle_count 3` of 4,
`blocked_until 2026-08-15T21:09:01Z`. 2026-08-15 was a Saturday, so the next eligible probe is
**Monday 2026-08-17 17:00 ET**, and it is a single request. One more `1001` promotes the ladder to
the 168h cap. Every item below must be landed and tested **without a network call**. Land the fixes
before Monday so the first probe after the embargo runs the fixed code.

---

## A. Root causes, ranked

Ranking is by share of observed failures removed, then by blast radius.

### A1. The handler kills the script before the script can finish polling (dominant, ~85% of observed failures)

`scripts/monitor_daemon/handlers/cash_flow_sync.py:310` runs the sync as a subprocess with
`timeout=180`. `scripts/cash_flow_sync.py:251-291` polls up to `max_polls=40` with a capped
exponential sleep (2/4/8/15/15...), a wall budget of roughly 569s. Cumulative time before each GET is
2, 6, 14, 29, 44, ... 179, so **14 of the 40 polls fit inside the subprocess timeout**. The script's
own `RuntimeError("Flex statement not ready after 40 polls")` at `:291` has never fired in
production and is unreachable under the daemon.

Evidence: `service_health_events` over 2026-06-16..2026-08-07 holds 31 transitions;
**14 are `cash_flow_sync timed out after 180s`**, 2 are `Flex throttle (code 1001)`, 0 are parse,
DB, auth or scheduling. The recovery signature is unambiguous: error at 21:03:0x (180s after the
21:00 UTC fire), then `ok` at 21:09-21:10 the next trading day, which is the following day's soft
retry succeeding after that day's first attempt also timed out. Successes at 21:03:0x are ~3-minute
runs that barely beat the wall.

The 2026-05-14 fix (`162349b7`, polls 20 to 40 plus exponential backoff) has been dead code in
production since the day it shipped, because the subprocess ceiling was never moved with it.

Cost of each lost race: 1 SendRequest plus ~14 GetStatements, spent, then SIGKILL, then a 5-minute
soft embargo and up to 2 more attempts that day (`MAX_SOFT_ATTEMPTS_PER_ET_DAY = 3`,
`handlers/cash_flow_sync.py:71`). **A timeout day costs about 3 SendRequests and ~45 HTTP calls and
writes zero rows.** That sustained burn is what walked the token into the sliding window.

Eliminate: reconcile the two numbers (one number, one owner) and set `max_runtime_seconds` on the
handler above it. See C1, C2. This is entirely ours, not IBKR's.

### A2. Three-plus uncontrolled Flex consumers share one token, and two of them are page-driven

`1018` ("too many requests from this token") is scoped to the **token**, not the query id. The
census as of `origin/main`:

| Caller | Query id | Trigger | Requests per invocation | Breaker |
|---|---|---|---|---|
| `scripts/cash_flow_sync.py` via daemon handler | NAV `1442520` | 17:00 ET trading days | 1 SR + up to 14 GS | full ladder |
| `scripts/perf_twr_builder.py:239,408,428` via `server.py:3951` `POST /performance` | NAV `1442520` | **on demand, page-driven** | 1 SR + up to 30 GS | none, `_fetch_nav_document:415` swallows to stderr and returns `None` |
| `scripts/perf_twr_builder.py` via `radon-perf-twr.timer` 20:45 ET | NAV `1442520` | scheduled | 1 SR + up to 30 GS | none, and the timer is deliberately **not installed** (`cloud/config/drift-allowlist.conf`) |
| `scripts/trade_blotter/flex_query.FlexQueryFetcher` via `journal_rehydrate.py:673` via `server.py:2825` via `web/app/api/blotter/route.ts` | trade `1422766` | **`/orders` mount plus every 5 min** | 1 SR + up to 40 GS | none |
| `scripts/portfolio_performance.py:361,479,647` | NAV + trade | **no production caller** (unwired by `b7da28ef`) | 1 SR each | `except Exception: return None` |

Measured load: one `/performance` tab open through RTH is roughly 26 SendRequests and up to 780
polls per session. One `/orders` tab is roughly 78 SendRequests per session on the trade query.
An operator hammering `POST /api/performance` (rate limit `limit: 2, windowMs: 60_000`) reaches
120 SendRequests per hour. **The daemon, at 1 request per day, is not the burner. It is the victim
that carries the breaker.**

Eliminate: exactly one component may talk to the Flex web service. See section B and C7, C8.

### A3. Per-consumer circuit breakers cannot work, structurally

The breaker state lives inside the monitor-daemon handler state
(`handlers/cash_flow_sync.py:114-130`, persisted in `data/daemon_state.json`). FastAPI and the
systemd oneshot cannot read or write it. Generalising the existing breaker to N consumers gives each
consumer its own free first hit, so N consumers cost N throttle hits before anything trips. Worse,
throttle detection is a substring match on the subprocess's last three stderr lines
(`handlers/cash_flow_sync.py:223-225`); `perf_twr_builder` prints a different message shape and
returns `None`, so the same detector would silently classify its throttles as soft failures.

The breaker has to move off the consumer and onto the token. See C8.

### A4. No implementation inspects the GetStatement body for an error code

`cash_flow_sync.py:287`, `perf_twr_builder.py:259` and `trade_blotter/flex_query.py` all test the
poll response for a substring only. A `1018` returned on the **poll** leg is indistinguishable from
"not ready", so a throttled token drives 30-40 further requests at speed while already throttled.
`scripts/tests/test_cash_flow_sync_flex_errors.py` covers the SendRequest leg only. This is the
single biggest amplifier per unit of code changed.

### A5. Failure classification is stringly-typed across a subprocess boundary

- Throttle detection is `f"code {code}" in message` over `stderr.splitlines()[-3:]`. An undocumented
  throttle code, or a stderr tail that pushes the code out of the last three lines, routes to the
  soft ladder and spends three more SendRequests that day.
- `_FlexAppError` (`cash_flow_sync.py:144-148`) documents "not retryable" and then loses that intent
  the moment it crosses the subprocess boundary as a plain string. A revoked token or a bad query id
  therefore burns 3 SendRequests every trading day, forever, never escalating.
- Missing env returns `{"status": "skip"}` (`handlers/cash_flow_sync.py:298`), `skip` is not `error`,
  so `execute()` falls through to `_mark_success()` at `:209`, which **resets the circuit breaker**
  and heartbeats `ok`. A dropped env var reports healthy forever and syncs nothing. This is the worst
  single hole in the file.

### A6. The daemon's 120s handler deadline now preempts the 180s subprocess timeout (latent, fires Monday)

`scripts/monitor_daemon/daemon.py:28 DEFAULT_HANDLER_DEADLINE_SECONDS = 120.0` (REL-008, landed
2026-08-09). `CashFlowSyncHandler` declares no `max_runtime_seconds`. All 14 timeout events predate
it and the handler has been embargoed since 2026-08-07, so this path has never actually run.
When the embargo clears it will, and:

1. `daemon.py:302` writes the error row with no `next_attempt_at`, so the 2026-08-04 embargo
   suppression fix (`8cbe06fd`) structurally cannot see it and the false-P2 page storm returns.
2. It bypasses `CashFlowSyncHandler._record_failure` entirely, so the backoff state does not advance
   and `MAX_SOFT_ATTEMPTS_PER_ET_DAY` is not incremented.
3. The abandoned thread later writes its own 180s row and mutates in-memory state; a deploy or
   SIGTERM in that window loses the counter.

### A7. The breaker's exit condition is a lottery it never makes more winnable

`_throttle_backoff.record_success` is the **only** reset for `throttle_count`. No time decay, no
operator lever, no partial credit. Each escalation lengthens the interval between tickets while
doing nothing to improve the odds of the single probe that must win. That is why this reads as
chronic rather than acute: 2026-08-07 to 2026-08-16 is ten days of `error` produced by three coin
flips.

### A8. Data-model defects that corrupt silently (not a cause of the outage; the reason the outage is worth fixing properly)

- **`cash_flows.id TEXT PRIMARY KEY` is wrong.** IBKR issues one `transactionID` per monthly interest
  posting batch and emits one row per sub-category. `41191444701` has three rows on 2026-07-06
  (-23.71, +61.89, +182.03, net +220.21). Turso holds one row, amount 182.03, last write wins.
  **$38.18 destroyed**, and which row survives depends on document order. The parser yields 264 rows
  from the statement; Turso holds 262.
- **`cash_flows` has no `<Transfer>` rows.** `cash_flow_sync` walks `.//CashTransaction` only. The
  2026-02-06 ACATS in-transfer (+655,497.16, plus a +289.69 cash leg on 2026-02-13) is absent. Turso
  says net external flow is -420,375.00; including transfers the true figure is +235,411.85. **The
  table has the wrong sign on the year's capital flow.**
- **Three parsers, three answers, and the disagreement is query-config dependent.** Over the legacy
  365-day statement: `cash_flow_sync` -420,375.00, `portfolio_performance._extract_cash_flows`
  -840,750.00, `flex_flows.parse_flows` -840,750.00 with `status=OK`. Consumers 2 and 3 double-count
  every external flow exactly 2x because neither skips the id-less aggregate rows. Over the new
  DETAIL-only statement the 2x vanishes. **Changing a checkbox in the IBKR query builder silently
  halves or doubles the performance page.**
- **`_classify` fails open, and its substring ordering already mis-sorts real IBKR strings.**
  `"withdrawal"` is tested before `"fee"` (`:98` before `:107`), so `Withdrawal Fee` becomes a
  capital `Withdrawal`; this account already has 12 rows whose description is literally
  `WITHDRAWAL FEE`, typed `Other Fees` today, one IBKR relabel away.
  `"deposit"` beats `"interest"`, so `FDIC Insured Bank Deposit Interest` becomes a contribution.
  `Internal Transfers` classifies `Other` here and `EXTERNAL` in `flex_flows`. Meanwhile
  `flex_flows.classify_flow_type` fails **closed** (`raise UnknownFlowType`) on 8 of 24 probed
  strings and takes the TWR build down. Same input, opposite failure modes.
- **No provenance.** No `source`, `statement_id`, `flex_query_id`, `account_id`, `settle_date`,
  `first_seen_at`, `superseded_at`. `ON CONFLICT ... SET synced_at = excluded.synced_at` rewrites the
  timestamp on every row of every pull, so `synced_at` means "last touched by any sync". The fixture
  replay this morning stamped all 262 rows, including October 2025 rows, with today's time, which
  destroyed the only observable trace of the ten-day outage.
- **Currency is unguarded.** All 554 real rows are USD and `fxRateToBase` is 1, but `amount` is
  transaction-currency, `amountInBase` is never read, and every consumer sums `amount` naively. The
  first non-USD row is silent corruption.

### A9. IBKR's fault. Absorb, do not try to fix.

- **Statement generation latency at the EOD spike.** Query 1442520 routinely takes 2.5 to 3.5 minutes
  around 17:00 ET. This is the physical reason A1 hurts. Mitigation is patience (a wider window) and
  scheduling off the spike, not fewer polls.
- **Sliding-window rate limiting where failures also count.** Every request during throttle pushes
  the reset out. There is no published numeric budget anywhere in IBKR's docs or this repo; the only
  characterisation in the codebase is prose at `_throttle_backoff.py:4-7`. We must treat the budget
  as unknown and minimise requests unconditionally.
- **~1 day settlement lag on CashTransaction publication.** Same-day flows are simply not available.
  Mitigation is cadence choice, not retries. See C15.
- **Undocumented / new error codes.** We know 1001, 1018, 1019. Assume there are others; classify by
  "any `<ErrorCode>` present" plus an allowlist of known-transient, and treat unknown codes as
  non-retryable rather than as soft.
- **Query-shape drift.** Whether `levelOfDetail` and `<Transfer>` appear at all is a checkbox in the
  IBKR query builder. Detect and assert on shape rather than trusting it.

---

## B. Target architecture

**The one invariant: exactly one component may open a socket to `gdcdyn.interactivebrokers.com`.
Every other consumer reads a persisted artifact.**

### Current

```
 17:00 ET daemon ──► cash_flow_sync.py ─┐
                     (fetch+parse+write)│
                                        │
 POST /performance ─► perf_twr_builder ─┤
 (page-driven, no   (fetch+parse+build) │──► gdcdyn Flex Web Service
  breaker)                              │    NAV query 1442520
 /orders mount +5m ► journal_rehydrate ─┤    trade query 1422766
                     (FlexQueryFetcher) │    ONE TOKEN, sliding window
                                        │
 (dead) portfolio_performance ──────────┘

 observed: 1 scheduled request/day + ~100 page-driven requests/day
 three independent parsers, three different answers, one breaker on the
 consumer that makes the fewest requests
```

### Target

```
                       ┌──────────────────────────────────────────┐
 radon-flex-pull.timer │  scripts/lib/flex_client.py              │
   20:45 ET, per query │  the ONLY module that may open gdcdyn    │
   id, oneshot ───────►│  - owns SendRequest AND GetStatement     │
                       │  - inspects <ErrorCode> on BOTH legs     │
                       │  - typed FlexThrottleError / FlexAppError│
                       │  - takes the token lease before any call │
                       └───────────────┬──────────────────────────┘
                                       │ raw XML + sha256 + period
                                       ▼
                       ┌──────────────────────────────────────────┐
                       │ Turso `flex_statements`                  │
                       │  (query_id, fetched_at) PK               │
                       │  gzipped xml, sha256, account_id,        │
                       │  period_from/to, when_generated          │
                       │  retain ~14 rows per query id            │
                       │  + data/flex_statements/ disk mirror     │
                       └───────────────┬──────────────────────────┘
                                       │ load_statement(query_id, max_age)
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
  parse_cash_transactions      flex_flows.parse_flows     flex trade parser
  (pure, exists today)         (pure, exists today)       (pure, to extract)
            │                          │                          │
            ▼                          ▼                          ▼
     cash_flows table            TWR flows map              journal rows
            │                          │                          │
            ▼                          ▼                          ▼
     GET /cash-flows            POST /performance           GET /blotter
     (zero Flex requests)   (zero Flex requests, may   (zero Flex requests)
                             rebuild as often as it likes)
```

Ownership, concretely:

- **Fetches:** `scripts/lib/flex_client.py`, invoked only by `scripts/flex_pull.py` (new), run only
  by `radon-flex-pull@<query_id>.timer`. Nothing else imports `urlopen` against gdcdyn. Enforce with
  a pytest that greps the tree for `gdcdyn` and allowlists exactly one file.
- **Parses:** pure functions taking `xml_text`. `parse_cash_transactions` already is one
  (`cash_flow_sync.py:210`, `a6afcb48`). `perf_twr_builder` and `journal_rehydrate` get the same
  split.
- **Writes:** `scripts/db/writer.py` only, chunked, wrapped, never able to escalate a Turso failure
  into a Flex request.
- **Reads:** FastAPI routes read Turso. No route triggers a fetch, ever.

**Flex requests per day, steady state:**

| Query | Today (measured) | Target |
|---|---|---|
| NAV / cash `1442520` | 1 scheduled + up to 26 per open `/performance` tab | **1** |
| Trade `1422766` | up to 78 per open `/orders` tab | **1** (plus operator-initiated, lease-gated) |
| Total SendRequests/day | ~100 with two tabs open | **2**, hard ceiling 4 |

Polls are bounded by the client: 1 SendRequest plus at most 40 GetStatements, and the poll loop
aborts immediately on any `<ErrorCode>` in the body.

**The token lease** is the backstop, not the primary mechanism. A `flex_lease` row
(`token_fingerprint`, `last_request_at`, `min_interval_seconds`, `blocked_until`, `throttle_count`)
is taken by `flex_client` before any live call. Lease unavailable means serve the artifact. This
makes an accidentally added fourth consumer harmless instead of catastrophic, and it moves the
breaker to the thing the limit is actually scoped to.

Why not the alternatives:

- *In-process fetch-and-cache*: insufficient. The consumers are three separate OS processes (daemon
  subprocess, FastAPI `run_script` subprocess, systemd oneshot). There is no shared memory to cache
  into. It has to be a persisted artifact.
- *Generalise the existing per-consumer breaker*: wrong shape, see A3.
- *Lease alone, no artifact*: stops the storm but every consumer still blocks on a live fetch, so one
  throttle still blanks all of them at once. The artifact is what decouples availability from the
  rate limit, and it is what makes failures replayable.
- *Artifact alone, no lease*: a bug or a manual run bypasses it.

---

## C. The work

Ordered by (failure rate removed) / (risk). Percentages are share of the 16 recorded
`service_health_events` failure transitions removed, unless stated otherwise.

### C0. Freeze: no Flex request until the fixed code is on the host — UNAMBIGUOUS

**Change:** nothing in code. Do not run `cash_flow_sync.py`, `perf_twr_builder.py`,
`journal_rehydrate.py` or `scripts/cash_flow_sync.py --json` locally or on the host until C1-C4 have
landed. The Monday 17:00 ET probe is one ticket and it should be spent by the fixed code.
**Test:** `scripts/tests/test_no_gdcdyn_outside_flex_client.py` (added in C7) is the durable version.
**Risk:** none. **Removes:** protects against promotion to the 168h cap.

### C1. Reconcile the poll budget with the subprocess timeout — UNAMBIGUOUS

**Change:** one number, one owner. Set the script's own wall budget explicitly
(`max_polls`/`poll_secs` derived from a single `FLEX_POLL_BUDGET_SECONDS = 420` constant) and set the
subprocess timeout to that budget plus a 60s margin (480s). Add `max_runtime_seconds = 540` to
`CashFlowSyncHandler` so the daemon deadline (A6) sits above both. All three must move together or
the daemon deadline simply replaces the subprocess one, with worse metadata.
**Files:** `scripts/cash_flow_sync.py:251-291`,
`scripts/monitor_daemon/handlers/cash_flow_sync.py:107-133,310`.
**Test:** extend `scripts/tests/test_monitor_daemon/test_cash_flow_sync_timeout_retry_budget.py`
with an assertion that the handler's subprocess timeout strictly exceeds the script's computed poll
budget, and that `handler.max_runtime_seconds` strictly exceeds the subprocess timeout. Red first:
the assertion fails against today's 180 vs 569 vs 120.
**Risk:** low. A genuinely wedged statement now occupies a daemon handler slot for up to 9 minutes.
`daemon.py:267-284` already suppresses overlap. Verify no other handler starves (the daemon runs
handlers in threads with a 30s cycle).
**Removes:** ~85% (14/16).

### C2. Route the daemon-deadline kill through the handler's `_record_failure` — UNAMBIGUOUS

**Change:** at `daemon.py:302`, when a handler exceeds its deadline, call the handler's own failure
recorder if it exposes one, so `next_attempt_at` and the soft-attempt counter are written. Fall back
to the current generic row only for handlers without one.
**Files:** `scripts/monitor_daemon/daemon.py:286-310`,
`scripts/monitor_daemon/handlers/cash_flow_sync.py:217-249`.
**Test:** new `scripts/tests/test_monitor_daemon/test_handler_deadline_records_embargo.py`: a stub
handler that sleeps past its deadline must produce a `service_health` error row containing
`next_attempt_at`, and must have advanced its soft-attempt counter.
**Risk:** low, touches the shared daemon path; run the whole `test_monitor_daemon` suite.
**Removes:** 0% of past failures, prevents the reintroduction of the 2026-08-04 page storm (20
`/incident` runs on one fingerprint).

### C3. Classify failures by typed exit code, not by a stderr substring — UNAMBIGUOUS

**Change:** `scripts/cash_flow_sync.py:main()` returns a distinct exit code per class:
`0` ok, `10` throttle (any known-transient Flex code), `11` permanent Flex application error
(auth, unknown query id, unknown error code), `12` statement-not-ready timeout, `13` parse failure,
`14` write failure. Emit a machine-readable last line on stdout
(`{"status": ..., "class": ..., "code": ...}`) as well. The handler branches on `returncode`, and
keeps the substring match only as a deprecated fallback with a log warning.
**Files:** `scripts/cash_flow_sync.py:302-360`,
`scripts/monitor_daemon/handlers/cash_flow_sync.py:217-249,287-325`.
**Test:** extend `scripts/tests/test_cash_flow_sync_flex_errors.py` with one case per exit code
(monkeypatching `urlopen`), and a handler test asserting exit 10 advances the throttle ladder,
exit 11 escalates to a hard error with no retry, exit 12/13/14 take the soft lane.
**Risk:** low. **Removes:** ~0% directly; removes the misclassification path that turns one permanent
error into 3 SendRequests per trading day forever.

### C4. `skip` must not mark success — UNAMBIGUOUS

**Change:** missing `IB_FLEX_TOKEN` / query id must not reach `_mark_success()` and must not
heartbeat `ok`. It is a **config error**: heartbeat `error` with
`"IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured"`, do not touch `throttle_count`, do not latch
`last_run`.
**Files:** `scripts/monitor_daemon/handlers/cash_flow_sync.py:170-215,297-298`.
**Test:** new case in `scripts/tests/test_monitor_daemon/test_cash_flow_sync_cadence.py`: with the
env unset and `throttle_count = 2` in state, one `execute()` must leave `throttle_count == 2` and
write an `error` row.
**Risk:** none. **Removes:** 0% observed; closes a silent-green hole that would mask every future
failure and wipe the breaker.

### C5. Delete `portfolio_performance.py`'s Flex entry points — UNAMBIGUOUS

**Change:** delete `fetch_ib_nav_series` (`:361`), `_extract_cash_flows` (`:479`), the `:441` helper
and `fetch_flex_trade_fills` (`:647`) outright. They have zero production callers since `b7da28ef`
unwired the module from `POST /performance`, they are unreachable by test (inline `urlopen` in the
function body), they swallow `1001` into `return None`, and `_extract_cash_flows` double-counts every
external flow 2x on the legacy query shape. Delete, do not leave dormant: a single `import` re-arms
four network entry points. `docs/performance-refactor-spec.md:51` already marks two as dead.
**Files:** `scripts/portfolio_performance.py`, plus any import cleanup.
**Test:** the gdcdyn allowlist test from C7; plus `pytest scripts/tests` green after removal.
**Risk:** low-medium. Confirm with `grep -rn "fetch_ib_nav_series\|_extract_cash_flows\|fetch_flex_trade_fills"`
across `scripts/`, `web/`, `cloud/` before deleting.
**Removes:** removes 4 latent uncontrolled consumers.

### C6. Stop `/orders` from firing a full Flex rehydrate every 5 minutes — NEEDS-DECISION on the client half, UNAMBIGUOUS on the server half

**Server half (do it):** put a cooldown floor on `POST /journal/rehydrate` (`server.py:2818-2825`).
If the last successful rehydrate is under N minutes old, return the cached result with
`{"skipped": true, "reason": "cooldown"}` and make zero Flex requests. This is the same shape as the
5-minute floor added to `POST /performance` in `route.ts:75`, but server-side, so it survives a
Next.js deploy and covers the direct-API path.

**Client half (decide):** `web/hooks/useSyncHook.ts:44` defaults `hasPost = true` and
`web/hooks/useBlotter.ts:15-22` does not override it, so `HistoricalTradesSection` POSTs on mount and
every 5 minutes. Options:
- (a) `hasPost: false` in `useBlotter`, rely on the daily scheduled trade-query pull plus the
  monitor daemon's fill handlers for intraday rows. Removes ~78 SendRequests per session. Cost: the
  operator loses "click /orders, get IBKR truth right now" unless they press an explicit refresh.
- (b) keep the POST but let the server cooldown absorb it. Zero UX change, but the client still
  believes it is refreshing, and the first load after any cooldown expiry still spends a request.
- (c) keep the POST, gate it behind an explicit "Sync from IBKR" button.
Tradeoff: (a) is the biggest single request reduction in the repo; (c) is the honest UI. Do not pick
unilaterally.
**Files:** `scripts/api/server.py:2818-2825`, `web/hooks/useBlotter.ts`, `web/hooks/useSyncHook.ts`,
`web/app/api/blotter/route.ts`.
**Test:** pytest for the server cooldown (two calls in a row, second makes no subprocess call);
vitest for whichever client option is chosen.
**Risk:** medium on the client half (visible behaviour change on `/orders`).
**Removes:** ~78 SendRequests per browsing session, the largest uncounted consumer in the repo.

### C7. The single fetch owner and the raw-statement artifact — UNAMBIGUOUS in direction, NEEDS-DECISION on storage

**Change:**
1. New `scripts/lib/flex_client.py`: `send_request(token, query_id) -> reference_code` and
   `get_statement(token, reference_code) -> xml`, both inspecting `<ErrorCode>` (this closes A4 in
   one place instead of three), both raising typed `FlexThrottleError` / `FlexAppError`, both taking
   the lease from C8. Move `_send_request_once`, `_request_reference_code` and the poll loop out of
   `cash_flow_sync.py` into it verbatim, then have `cash_flow_sync` import them.
2. New `scripts/flex_pull.py --query-id <id>`: fetch once, persist the artifact, exit. This is the
   only caller of `flex_client` that runs on a timer.
3. New migration `scripts/db/migrations/00NN_flex_statements.sql` (check Turso `MAX(version)` and
   in-flight worktrees before numbering, per the collision lesson):
   `flex_statements(query_id TEXT, fetched_at TEXT, account_id TEXT, period_from TEXT, period_to TEXT,
   when_generated TEXT, sha256 TEXT, xml_gz BLOB, byte_len INTEGER, PRIMARY KEY(query_id, fetched_at))`
   plus a retention delete keeping the newest ~14 per `query_id`. Mirror to
   `data/flex_statements/<query_id>/<fetched_at>.xml.gz` as the disk fallback.
4. `load_statement(query_id, max_age) -> (xml_text, meta)` in `flex_client`, Turso-first with disk
   fallback, per the Turso-first read rule.
5. New `scripts/tests/test_no_gdcdyn_outside_flex_client.py`: grep the tree for `gdcdyn`; the only
   permitted matches are `scripts/lib/flex_client.py` and docs. This is the architectural invariant,
   enforced.

**NEEDS-DECISION:** where the XML lives. Options:
- (a) gzipped BLOB in Turso plus disk mirror. Survives a host rebuild, is queryable, is the pattern
  the rest of the repo uses. The 365-day statement is ~1-2 MB raw, well under 200 KB gzipped, times
  14 rows times 2 query ids. Cost: a multi-hundred-KB BLOB over Hrana, which the I/O-bounding lesson
  says to be careful with.
- (b) disk only, Turso holds metadata plus a path. Cheapest I/O, but host-local files are ephemeral
  on the VPS after a deploy, which is exactly the failure mode `docs`/CLAUDE.md warns about.
- (c) B2 cold archive (`RADON_ARCHIVE_S3_*`, already provisioned for portfolio snapshots) with Turso
  metadata. Durable and cheap, adds a dependency to the read path.
Recommendation to weigh, not to assume: (a) for the newest 3 per query id, (c) for the tail.

**Files:** new `scripts/lib/flex_client.py`, new `scripts/flex_pull.py`, new migration, new
`cloud/services/radon-flex-pull@.{service,timer}` (remember the three CI contracts and the
`installed-units.sha256` bump), `scripts/cash_flow_sync.py`, `scripts/perf_twr_builder.py:239-263,408-430,496-510`,
`scripts/journal_rehydrate.py:660-680`.
**Test:** `test_flex_client.py` covering both legs' error-code inspection with `urlopen`
monkeypatched to raise if called unexpectedly; `test_flex_statements_artifact.py` covering
persist/load/retention against the saved fixture; the gdcdyn allowlist test.
**Risk:** high (it is the whole refactor). Land it behind the existing consumers rather than in one
cut: add `flex_client` and the artifact first, migrate `cash_flow_sync` to read the artifact with a
live-fetch fallback, verify a week, then migrate the other two and remove the fallback.
**Removes:** all page-driven Flex load, roughly 98% of daily request volume. Also makes every failure
replayable after the fact, which is what the operator's manual export is substituting for today.

### C8. Move the breaker onto the token: `flex_lease` — UNAMBIGUOUS

**Change:** a single Turso row per token fingerprint holding `last_request_at`,
`min_interval_seconds`, `blocked_until`, `throttle_count`, `last_error`. `flex_client` takes it
before any live call and refuses (raising `FlexLeaseUnavailable`, which callers translate to "serve
the artifact") when `now < blocked_until` or `now - last_request_at < min_interval_seconds`.
`record_throttle` / `record_success` move here from the daemon handler state, which also fixes A7's
"invisible to other processes" half. Keep `_throttle_backoff.py`'s ladder logic; change only where
the state lives.
**Files:** new migration column set, `scripts/lib/flex_client.py`,
`scripts/monitor_daemon/handlers/_throttle_backoff.py`,
`scripts/monitor_daemon/handlers/cash_flow_sync.py:114-130`.
**Test:** `test_flex_lease.py`: two concurrent callers, only one gets the lease; a lease held during
`blocked_until` refuses without touching `urlopen`; the ladder advances identically to the existing
`test_throttle_backoff.py` expectations.
**Risk:** medium. Migrating the breaker state out of `daemon_state.json` must not lose the current
`throttle_count 3` / `blocked_until`. Seed the row from the live daemon state at migration time.
**Removes:** makes a future fourth consumer harmless.

### C9. Add throttle-count decay so the breaker is not a success-only ratchet — NEEDS-DECISION

**Change:** `throttle_count` currently resets only on success (`_throttle_backoff.py:81-83`). Today
that means ten days of `error` produced by three coin flips, with the interval between tickets
growing and the odds unchanged.
Options:
- (a) time decay: decrement `throttle_count` by 1 for every 72h with no throttle, floor 0. Simple,
  bounded, no operator action.
- (b) explicit operator reset: a `radon flex reset-breaker` CLI. Honest, but requires a human and
  trains the operator to reach for it reflexively.
- (c) leave the ratchet and rely on C1 plus C7 to make the probe reliably win.
Tradeoff: (a) risks re-probing a token that IBKR still considers hot, which is precisely how the
sliding window bites; (c) is the purist answer and is probably correct **if** C1 removes the timeout
storm, but leaves no recovery lever when IBKR itself is degraded. Recommend (a)+(b) together, but do
not choose unilaterally.
**Files:** `scripts/monitor_daemon/handlers/_throttle_backoff.py`, CLI.
**Test:** `test_throttle_backoff.py` decay cases with window-relative dates.
**Risk:** medium, directly governs request volume.

### C10. A real CLI: `--from-file`, `--dry-run`, `--since`, `--stdout-xml` — UNAMBIGUOUS

**Change:** `scripts/cash_flow_sync.py` gains:
- `--from-file PATH` parse a saved statement, no network, at all, ever (assert `urlopen` unreachable
  on this path);
- `--dry-run` parse and diff against Turso, print what would change, write nothing;
- `--since YYYY-MM-DD` bound the write set;
- `--stdout-xml` (on `flex_pull.py`) dump the fetched artifact for manual inspection;
- `--from-artifact <query_id>[@fetched_at]` replay the newest or a specific persisted statement.
This is the smoking gun from the diagnosis: the parent session had to hand-copy the parse loop into a
scratch file to ingest the operator's export. `a6afcb48` extracted `parse_cash_transactions`; this
item is what makes the extraction usable from a terminal.
**Files:** `scripts/cash_flow_sync.py:302-360`, `scripts/flex_pull.py`.
**Test:** `scripts/tests/test_cash_flow_sync_cli.py` running `--from-file` against the committed
fixture with `urlopen` monkeypatched to raise, asserting the exact row count and a `--dry-run` that
performs zero writes.
**Risk:** none. **Removes:** 0% of failures; removes most of the operator time each failure costs,
which is the actual harm here.

### C11. One classifier, and unknown types fail loud without failing the run — NEEDS-DECISION on the policy

**Change:** delete `cash_flow_sync._classify`'s substring ladder and route both consumers through a
single classifier in `scripts/lib/flex_flows.py`. Fix the two live mis-sorts regardless of the policy
choice (`Withdrawal Fee` must not be a `Withdrawal`; `... Deposit Interest` must not be a `Deposit`)
by matching on exact normalized type strings rather than substrings.
**NEEDS-DECISION** on unknown-type policy, because the two modules currently sit at opposite
extremes:
- (a) fail open into `Other` (today's `cash_flow_sync`): never breaks a build, silently mislabels,
  and nothing anywhere alerts on `Other`.
- (b) fail closed with `UnknownFlowType` (today's `flex_flows`): takes the TWR build down on a single
  new IBKR string.
- (c) quarantine: classify as `Unknown`, write the row with its `raw_type`, exclude it from every
  external-capital sum, and raise a **non-fatal** `service_health` warning naming the string, once
  per new string. The build completes, the number is not silently wrong, and the operator gets one
  actionable line.
Recommend (c). Tradeoff against (b): (c) means a genuinely external flow can be excluded from TWR for
as long as it takes a human to triage, which understates or overstates return in the interim.
**Files:** `scripts/lib/flex_flows.py:19-71`, `scripts/cash_flow_sync.py:84-110`.
**Test:** a table-driven test over the 24 probed IBKR type strings asserting the classification of
each, including the two current mis-sorts (red first).
**Risk:** medium. `server.py:4866`'s `/cash-flows` summary hardcodes
`type == "Deposit" / "Withdrawal" / "Dividend"`; adding or renaming a bucket silently drops it from
the totals while `net` still includes it. Fix that in the same change.

### C12. Fix the primary key so duplicate transactionIDs survive — NEEDS-DECISION on the key shape

**Change:** `cash_flows.id TEXT PRIMARY KEY` destroys rows today (A8: $38.18, 2 rows). The parser
already keeps all three rows and has a test for it
(`test_keeps_every_row_sharing_a_duplicated_transaction_id`); the writer throws two away.
**NEEDS-DECISION:**
- (a) composite PK `(transaction_id, raw_type, description)`. Faithful to IBKR's actual semantics
  (one id per posting batch, one row per sub-category). Fragile if IBKR reworries a description.
- (b) content hash PK `sha256(transaction_id|report_date|raw_type|amount|currency|description)`,
  keeping `transaction_id` as an indexed column. Stable and collision-proof, but an amount revision
  by IBKR creates a second row rather than updating the first, which is arguably correct (see the
  `superseded_at` tombstone in C14) but changes what "idempotent" means.
Tradeoff: (a) preserves in-place revision; (b) preserves history. Both need a backfill migration and
both change `SUM(amount)` on `/orders` by +38.18, which will look like a regression to anyone
watching the number.
**Files:** new migration, `scripts/db/writer.py:530-560`, `scripts/api/server.py:4808-4870`.
**Test:** red first: write two parsed rows sharing a `txn_id` through `upsert_cash_flow` and assert
both survive with the correct sum. There is no test for the writer today at all.
**Risk:** medium. Requires a backfill from the persisted artifact (C7 makes this possible without a
network call).

### C13. Ingest `<Transfer>` into `cash_flows` — NEEDS-DECISION

**Change:** `cash_flow_sync` walks `.//CashTransaction` only, so the largest capital event in the
dataset (2026-02-06 ACATS, +655,497.16) is absent and the table reports the wrong sign for the year
(-420,375.00 stored vs +235,411.85 true). `flex_flows.parse_flows` already reads `<Transfer>` and
`flex_flows._transfer_amount` already handles the direction and amount-field variants.
**NEEDS-DECISION:** whether `cash_flows` should hold transfers at all.
- (a) ingest them into `cash_flows` with `type = 'Transfer'`. One table, one truth, `/cash-flows` and
  TWR finally agree. Changes every existing total on `/orders`, visibly and dramatically.
- (b) leave `cash_flows` as a cash-transaction table and have TWR keep reading transfers from the
  artifact, documenting that `cash_flows` is not the capital-flow table. Cheaper, but preserves the
  two-pictures problem that produced this whole class of bug.
Tradeoff: (a) is correct and loud; (b) is safe and quietly wrong. Note `flex_flows.flows_from_rows`
(`:197`), the function designed to feed TWR from Turso rows, is **dead code called from nowhere**;
under (b) it must stay dead, because using it today would import the Transfers hole into TWR.
**Also note:** whether `<Transfer>` appears at all is a query-builder checkbox. Whichever option is
chosen, assert on statement shape (see C16).
**Files:** `scripts/cash_flow_sync.py:210-248`, migration, `scripts/api/server.py:4808-4870`,
`web/components/.../CashFlowsSection`.
**Test:** parse the 365-day fixture and assert the ACATS legs land with the right sign and date.
**Risk:** high on the UI numbers, zero on stability.

### C14. Provenance columns and a `first_seen_at` that means something — UNAMBIGUOUS

**Change:** add `source`, `flex_query_id`, `statement_fetched_at` (FK to `flex_statements`),
`account_id`, `settle_date`, `first_seen_at` (written once, never touched by `ON CONFLICT DO UPDATE`),
`superseded_at` (tombstone so an IBKR revision is a new row, not an overwrite). Stop rewriting
`synced_at` on unchanged rows: only update when a field actually changed.
**Why:** today you cannot answer "which statement produced this row", "did IBKR revise this amount",
"was this row present before the embargo", or even "is this account U4698258". The fixture replay
this morning stamped all 262 rows with the current time and erased the only trace of the ten-day
outage.
**Files:** migration, `scripts/db/writer.py:530-560`, `scripts/cash_flow_sync.py:210-248`.
**Test:** re-run the same statement twice, assert `first_seen_at` is unchanged and `synced_at` is
unchanged on rows whose content did not change.
**Risk:** low.

### C15. Move the pull off the 17:00 ET spike — NEEDS-DECISION

**Change:** the current 17:00 ET cadence was chosen to be 1h after the close, but IBKR publishes
CashTransaction once per day with a ~1 day settlement lag, so nothing time-sensitive is gained by
being early, and 17:00 ET is exactly when statement generation is slowest (the 2.5-3.5 min figure
behind A1).
Options:
- (a) 20:45 ET, sharing the slot the `radon-perf-twr.timer` was already written for. Off the spike,
  same trading day, one pull serves both consumers via the artifact.
- (b) 06:00 ET next morning. Furthest from any spike, but the panel is a day staler in the evening.
- (c) keep 17:00 ET and rely on C1's wider window.
Tradeoff: (a) is the natural fit with C7 (one scheduled pull, both consumers read it) and it retires
the drift-allowlist hold on `radon-perf-twr.timer`, which was held on the theory that it would be a
third consumer. Under the target architecture the timer **is** the fetcher, and holding it buys
nothing while `POST /performance` is live and fetching unbounded.
**Files:** `scripts/monitor_daemon/handlers/cash_flow_sync.py:135-168`,
`cloud/services/radon-flex-pull@.timer`, `cloud/config/drift-allowlist.conf`.
**Test:** `test_cash_flow_sync_cadence.py` window assertions, window-relative dates only.
**Risk:** low.

### C16. Assert on statement shape, and guard currency — UNAMBIGUOUS

**Change:** on every parse, record and assert: number of `<FlexStatement>` elements, `accountId`,
period, presence of `levelOfDetail`, presence of a `<Transfers>` section, count of id-less rows, and
the set of distinct currencies. Any change in shape raises a non-fatal `service_health` warning
naming the field. Reject or convert non-USD rows explicitly (`amountInBase` plus `fxRateToBase` are
in the new query shape) rather than summing mixed currencies.
**Why:** the double-count in A8 is a function of query configuration, not of code. A checkbox change
silently halves or doubles the performance page. Also, the id-less-row skip is a **proxy** for
`levelOfDetail == "SUMMARY"`; if IBKR ever emits a DETAIL row with a blank id it is discarded today
with no counter.
**Files:** `scripts/cash_flow_sync.py:210-248`, `scripts/lib/flex_flows.py:137-195`.
**Test:** parse both fixtures (365-day legacy, YTD DETAIL-only) and assert the recorded shape differs
in exactly the expected fields.
**Risk:** none.

### C17. Chunk the writes and stop laundering a Turso failure into a Flex request — UNAMBIGUOUS

**Change:** `cash_flow_sync.py:340-350` does 264 sequential single-row `upsert_cash_flow` calls, each
a Hrana round trip, with **no try/except around the loop**. One 502 at row 200 aborts the run
non-zero, the handler reads it as a soft failure, and 5 minutes later it spends **another
SendRequest** even though the Flex fetch already succeeded. Chunk into multi-row `INSERT ... ON
CONFLICT` batches per the Hrana I/O bounding rule, wrap the loop, and on a write failure exit with
code 14 (C3) so the handler retries **the write from the artifact**, never the fetch.
**Files:** `scripts/cash_flow_sync.py:335-360`, `scripts/db/writer.py:530-560`.
**Test:** monkeypatch the writer to fail on the Nth chunk; assert the process exits 14 and that a
retry re-reads the artifact and makes zero `urlopen` calls.
**Risk:** low. **Removes:** an amplification path that is live but has not yet fired.

### C18. Make the state file lose less on corruption — UNAMBIGUOUS

**Change:** `daemon.py:413-443` treats any `verified_load` exception as "start blank", which silently
forgets an active 72h embargo and lets the next window re-probe. Once the breaker lives in Turso
(C8), the daemon state file is no longer the system of record for it, which fixes this by
construction. Until then, on a corrupt load, seed `backoff_state` conservatively from the last
`service_health` row's `next_attempt_at` rather than from `initial_state()`.
**Files:** `scripts/monitor_daemon/daemon.py:413-443`,
`scripts/monitor_daemon/handlers/cash_flow_sync.py:114-130`.
**Test:** corrupt the checksum, load, assert the embargo survives.
**Risk:** low.

---

## D. What not to do

1. **Do not "fix" the empty-`transactionID` skip** (`cash_flow_sync.py:254`). It is correct and
   proven: all 65 id-less rows in the 365-day export reconcile exactly against the detail rows that
   share their `(reportDate, type, currency)`. Sum of all 329 elements is -827,215.24; the 65 skipped
   rows sum to -413,607.62 and the 264 parsed rows sum to -413,607.62. The aggregate set is a
   complete second copy of the file. Removing the skip is what produces the 2x double-count in
   `_extract_cash_flows` and `parse_flows`. The only legitimate change is to make the rule explicit
   (`levelOfDetail != "SUMMARY"`) and to count what was skipped (C16).
2. **Do not remove idempotency on `transactionID`.** C12 widens the key so duplicate ids survive; it
   does not make re-running a pull non-idempotent. Re-ingesting the same statement must remain a
   no-op.
3. **Do not remove the `amount == 0` skip.** Zero such rows exist in 554 real rows; the skip changes
   no sum. It is harmless. Just count it.
4. **Do not add internal retries to a throttled request.** This was tried (`25d7321e`, 2026-05-09)
   and it burned ~24h of visibility inside one day, because the sliding window counts failures.
   `b904d184` reverted it deliberately. The current "raise immediately, no internal retry" is correct
   and must survive the refactor.
5. **Do not shorten the cadence to "catch flows sooner".** This was tried (`f82e8c31`, 86400s to
   14400s) and caused the 2026-05-09 incident the next day. IBKR publishes once per day with a ~1 day
   lag; more frequent polling cannot produce data that does not exist yet.
6. **Do not give each consumer its own circuit breaker.** `1018` is token-scoped; N breakers means N
   free first hits. See A3.
7. **Do not merely un-wire `portfolio_performance.py` and call it done.** `b7da28ef` already did
   that, and the on-demand consumer simply moved to `perf_twr_builder` via `server.py:3951`. The
   architecture, not the module, is the problem. Delete the entry points (C5).
8. **Do not keep holding `radon-perf-twr.timer` on the throttle gate.** The reasoning is inverted:
   the timer is the *safe* consumer (one request, once, 20:45 ET, off both windows). The unsafe
   consumer, the same builder fired unbounded by page loads, is already deployed. A scheduled fetch
   is the precondition for the on-demand path to stop fetching at all.
9. **Do not treat `Other` as a benign bucket.** Zero rows have ever landed in it in production, which
   is exactly why it will rot silently. Either alarm on it or stop using it (C11).
10. **Do not hand-edit `data/daemon_state.json`.** It is checksummed; a hand edit discards the whole
    file for every handler. Stop the daemon, `verified_load`, mutate, `atomic_save`, start.
11. **Do not raise the daemon's global `DEFAULT_HANDLER_DEADLINE_SECONDS` to accommodate this one
    handler.** Set `max_runtime_seconds` on `CashFlowSyncHandler` specifically (C1).
12. **Do not "improve" the retry ladder's shape.** 24/48/72/168h works exactly as designed and is
    verifiable in the events log. The problem is the reset condition (C9) and the number of tickets
    spent per day (C1, C7), not the intervals.

---

## E. The alerting question

Ten days of `error` for a failure that cost **one row** of data is the real harm. Replaying the
operator's fixture through the production parse and write path produced exactly one new row
(2026-08-13, -80,000). The outage was loud, expensive in attention, and nearly free in data. That
trains the operator to ignore the alert, which is how the next real outage gets missed.

`service_health` is single-row per service and `service_health_events` only fires on a state
**transition** (`0011:46 WHEN OLD.state != NEW.state`), so today `error -> error` is invisible: every
within-evening soft retry and every repeat-throttle escalation is unrecorded. The 2026-08-12 throttle
that pushed `throttle_count` to 3 has no event row at all. **Recorded failure counts are a lower
bound.**

### Three states, explicitly

**`ok` — the artifact is fresh and the write succeeded.**
```
state:   ok
message: "synced N rows from statement <query_id>@<fetched_at> (period <from>..<to>)"
```
Never page. Heartbeat every cycle (per the heartbeat convention; a missing heartbeat latches).

**`degraded` — we know exactly why, the system is handling it, no action is needed.**
This covers: throttled with an embargo in the future; statement not ready; the daily soft-attempt
budget spent; artifact age within tolerance (below).
```
state:   degraded
message: "Flex throttled (code 1001). Serving statement from <fetched_at> (<age>). Next attempt <next_attempt_at>."
last_error: { "class": "throttle", "code": 1001, "next_attempt_at": "...",
              "artifact_fetched_at": "...", "artifact_age_hours": N,
              "throttle_count": 3, "attempts_today": 1 }
```
Never pages. Renders as amber on the panel. The message must state (1) what happened, (2) that data
is still being served and from when, and (3) when we will try again. `next_attempt_at` must be a
**top-level column**, not buried inside the `last_error` JSON blob, because burying it is what made
the embargo structurally invisible to `incident_watchdog/probes.py` and produced 20 `/incident` runs
on one fingerprint.

**`error` — genuinely broken, a human must act.** Only these:
- config missing (`IB_FLEX_TOKEN` / query id unset) — today this reports `ok`, see C4;
- a permanent Flex application error (auth failure, unknown query id, an error code not on the
  known-transient allowlist);
- parse failure or a statement-shape assertion failure (C16);
- a write failure that persists after retrying from the artifact;
- **staleness**: no successful pull for more than **3 ET trading days**, regardless of cause. This is
  the one that would have fired on 2026-08-10 instead of 2026-08-07, and it is the honest signal:
  after three days the settlement-lag window means we are genuinely missing data.

### Paging policy

| Condition | State | Pages? |
|---|---|---|
| Throttled, embargo in the future, artifact under 3 trading days old | `degraded` | no |
| Statement not ready, soft budget not spent | `degraded` | no |
| Soft budget spent for the day | `degraded` | no |
| Config missing | `error` | **yes, immediately** |
| Permanent Flex app error (auth, bad query id, unknown code) | `error` | **yes, immediately** |
| Parse or shape assertion failure | `error` | **yes, once** |
| No successful pull in > 3 ET trading days, any cause | `error` | **yes, once, then daily** |
| Unknown IBKR type string encountered | `ok`/`degraded` + one-time warning | no, but must be listed |

One page per condition-transition, never per attempt (per the page-storm lesson). The
incident-watchdog must suppress on `degraded` unconditionally and on `error` only while
`next_attempt_at` is in the future **and** the artifact is inside the staleness window.

### Add repeat visibility

`service_health_events` misses `error -> error`. Either extend the trigger to fire on a change of
`last_error.class`, or add an explicit `flex_attempts` append-only table
(`attempted_at, query_id, outcome, code, requests_spent`). Without it there is no way to answer
"how many SendRequests did we actually spend this week", which is the number that governs everything
in section A. Recommend the explicit table: it is the accounting ledger the sliding-window budget
needs, and it costs one small insert per attempt.

---

## F. Acceptance criteria

The overhaul is done when all of the following are demonstrably true.

**Request budget**
1. `grep -rn "gdcdyn" scripts/ web/` matches exactly one non-doc file, `scripts/lib/flex_client.py`,
   and a test enforces this.
2. No FastAPI route, Next.js route, React hook or page load can cause a Flex request. Proven by:
   loading `/performance` and `/orders`, hammering `POST /api/performance` and `POST /api/blotter`
   20 times each, and observing zero rows added to the `flex_attempts` ledger.
3. Steady-state SendRequests per day is **2** (one per query id), with a hard ceiling of 4 enforced
   by the lease.

**Reliability**
4. `handler.max_runtime_seconds > subprocess timeout > script poll budget`, asserted by a test.
5. Thirty consecutive ET trading days with zero `cash_flow_sync timed out` events.
6. `throttle_count` reaches 0 and stays there for 30 days.
7. A simulated Turso write failure produces zero additional Flex requests.
8. A daemon-deadline kill produces a `service_health` row with a top-level `next_attempt_at`.

**Replayability and testability**
9. `python -m scripts.cash_flow_sync --from-file <path> --dry-run` reproduces the exact production
   row set from a saved statement with `urlopen` unreachable, and the full parse path is unit-tested
   with a hard `urlopen.assert_not_called()`.
10. Every failed pull in the last 14 days can be replayed from `flex_statements` without a network
    call.
11. `pytest scripts/tests` and the vitest suite are green, and the parse path has no untested
    branch. Coverage target 95% on `flex_client.py`, `cash_flow_sync.py` and `flex_flows.py`.

**Data correctness**
12. Three rows sharing `transactionID 41191444701` all survive a write; `SUM(amount)` for
    2026-07-06 Interest equals 2382.07, not 2343.89.
13. `cash_flow_sync` and `flex_flows` produce the **same** net external capital flow from the same
    statement, asserted by a test over both fixtures (legacy 365-day and DETAIL-only YTD). Today they
    differ by exactly 2x on one and by the Transfers gap on the other.
14. Every row carries `flex_query_id`, `account_id`, `statement_fetched_at` and a `first_seen_at`
    that does not move on re-ingest.
15. A non-USD row is either converted via `amountInBase`/`fxRateToBase` or refused loudly. It is
    never summed naively.
16. An unrecognised IBKR type string does not silently become `Other` and does not crash the TWR
    build.

**Alerting**
17. A throttle with a future `next_attempt_at` and a fresh artifact produces `degraded` and zero
    pages, and the panel states which statement is being served and when the next attempt is.
18. A missing `IB_FLEX_TOKEN` produces `error` within one cycle and does **not** reset
    `throttle_count`.
19. No successful pull for more than 3 ET trading days produces exactly one page, then one per day.
20. The `flex_attempts` ledger can answer "how many SendRequests were spent in the last 7 days" with
    a single query.

**Operational**
21. `radon-flex-pull@.timer` is installed for both query ids, and the drift-allowlist hold on
    `radon-perf-twr.timer` is either retired or documented as permanent with the new reasoning.
22. The first live probe after the current embargo runs the fixed code, succeeds, and resets the
    ladder. Evidence: the `service_health` row, the `flex_attempts` ledger, and the new
    `flex_statements` artifact row.
