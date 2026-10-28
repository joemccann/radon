# UW REST admission

`UWClient.get()` and all existing UWClient wrappers reserve capacity before each real HTTP attempt. Profile key validation and the ivrank collector use the same admission coordinator. The Next.js admitted proxy calls the client rather than mirroring usage after an independent provider request.

## Scope and defaults

One account-wide SQLite coordinator is stored next to `data/uw_budget.json`, or at `RADON_UW_GOVERNOR_PATH`. Mount that volume into every same-host consumer. Tokens share the allowance; rotating a key does not create another request bucket. This is **not** a cross-host coordinator: route other hosts through the admitted API, do not give each host an independent ledger.

Default operating policy is 120 requests/minute and 40,000/day. This conservative policy is not a statement about the current Advanced entitlement. Root `RADON_UW_LIMITS_VERIFIED=1` is required before a larger RPM/daily limit or `RADON_UW_DAILY_LIMIT=unlimited` can take effect. `RADON_UW_QUOTA_PERIOD=daily|monthly|unknown` defaults to unknown; a numeric provider quota header alone does not establish the period because Starter uses a monthly allowance.

The client records only allowlisted numeric cap/count headers, a receipt timestamp, coarse auth status and a one-way token fingerprint. Missing/malformed headers cannot establish unlimited access. Unknown-period provider counts do not reset at a guessed daily boundary; a verified daily policy may accept a next-day receipt. Successful local probes without headers establish endpoint access only, not a higher rate.

## Guarantees and limits

- SQLite `BEGIN IMMEDIATE` atomically reserves each attempt across processes. Initial activation preserves the current legacy spend. Connection failures conservatively retain their reservation.
- Standard/background requests stop at 80% of minute/daily policy; interactive calls can use the remaining headroom. Admission defaults to five seconds and releases the DB lock while waiting. Explicit scanner waits are capped at 120 seconds. LEAP uses `UWClient(admission_timeout=120)` so a shared minute window or short Retry-After does not discard the rest of its universe. `scripts/strength_confirmation_scanner.py` and `scripts/theta_harvester_scanner.py` set `admission_timeout` from `UW_ADMISSION_WAIT_S` (120 seconds) on every client they build. Their FastAPI scan budgets are 480 seconds and 420 seconds. `scripts/fetch_flow.py` builds its transient-retry client with `FLOW_ADMISSION_WAIT_S` (120 seconds) only when no history budget is set. `POST /flow-analysis/{ticker}` passes `--deadline-epoch` (`FLOW_REPORT_TOTAL_DEADLINE_SECS` minus `FLOW_REPORT_FINISH_MARGIN_SECS` in `scripts/api/server.py`), and that budgeted walk uses `FLOW_BUDGETED_ADMISSION_WAIT_S` (60 seconds) per page inside the 300-second subprocess timeout. A relative budget is not used: capacity-shed retries re-run the same argv, so the epoch is what stops paging. Closed days already walked stay in the per-day cache; skipped sessions are `history_backfill_skipped` and the report can still be written. Its fail-fast scanner/discover sampling client keeps the five-second default. Ordinary clients and explicit per-request deadlines keep their fast refusal. Daily/provider exhaustion and embargoes longer than the deadline still refuse before HTTP.
- HTTP429 receipts create a shared embargo from Retry-After (seconds or HTTP date); a daily-exhausted response waits for the ET reset. Missing Retry-After uses a conservative minute delay. Requests that cannot wait fail with `UWRateLimitError` before provider HTTP.
- Existing attributed `uw_budget` telemetry remains post-response and separate from admission. It is not presented as exact provider-billed usage; provider allowance counting semantics vary.
- Per-key caching uses 256 stable cross-process lock stripes, rechecks cache after lock admission and unique temp files. Cache hits do not reserve an HTTP request. Locks never have their inode unlinked while an owner may exist. Explicit scanner waits use the same 120-second ceiling for cache contention; normal per-request deadlines remain short.
- Corrupt/unavailable admission state refuses new requests rather than resetting usage. Recover storage and retain the ledger; do not delete it to bypass a quota.
- No key, provider URL, response body or arbitrary header is persisted in the governor. Provider errors returned by UWClient have token text removed and bounded messages.

## Verification

Synthetic CI fixtures cover parallel workers, token rotation, interactive headroom, invalid policy/state, quota rollover, out-of-order receipts, Retry-After variants, first activation, safe errors and refusal before HTTP. No local test suites run under the operator policy.
