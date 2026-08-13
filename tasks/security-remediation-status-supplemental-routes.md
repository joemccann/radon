# Supplemental routes BUG remediation status

## Reconciliation

- Owned findings: **23** (`BUG-038` through `BUG-059`, plus `BUG-061`; `BUG-060` excluded)
- Fixed: **23**
- Duplicate or evidence-backed non-actionable: **0**
- Remaining actionable: **0**

## Findings

| ID | Disposition | Current-code evidence | Regression evidence |
|---|---|---|---|
| BUG-038 | fixed | Reliability history selects `MAX_EVENT_ROWS + 1` newest rows, restores chronological order, and reports truncation. | `security-remediation-supplemental-routes.test.ts` - newest-row query and truncation contract. |
| BUG-039 | fixed | The proxy budget is 190 seconds and propagates request abort; FastAPI detects disconnects, cancels the script task, and the subprocess runner kills and reaps on cancellation. | `backtest-route-security.test.ts`; `test_route_abuse_controls.py::test_run_script_cancellation_kills_and_reaps_child`. |
| BUG-040 | fixed | Bookmark deletion trims the already-decoded route parameter without a second decode and bounds its length. | `profile-bookmarks-watchlist-api.test.ts` - literal percent sequences are preserved. |
| BUG-041 | fixed | The GEX route delegates expected-session selection to `isGexDataStale` instead of passing the ET calendar date. | `gex-staleness.test.ts`; `security-remediation-supplemental-routes.test.ts`. |
| BUG-042 | fixed | The validated GEX share basename is fetched from the generator host through the authenticated FastAPI `/share/content` endpoint. | `share-report-path.test.ts`; `security-remediation-supplemental-routes.test.ts`. |
| BUG-043 | fixed | Demo WebSocket tickets use tier E's minute bucket plus tier F's daily ceiling. | `demo-rate-tier.test.ts`; `demo-gate.test.ts`. |
| BUG-044 | fixed | Internals cache candidates are accepted only when metadata matches the default NDX/SPX, 5Y, delta-25 query identity. | `internals-skew-route-staleness.test.ts` - newer non-default variants are rejected. |
| BUG-045 | fixed | Internals generation passes `--card-type internals` and writes an allowlisted `tweet-internals-*` filename. | `share-report-path.test.ts`; `security-remediation-supplemental-routes.test.ts`. |
| BUG-046 | fixed | The internals content route proxies validated HTML from the remote FastAPI content endpoint. | `share-report-path.test.ts`; `security-remediation-supplemental-routes.test.ts`. |
| BUG-047 | fixed | CTA expected-date selection uses the shared US trading-day calendar. | `menthorq-cta-route.test.ts` - July 3 holiday resolves to July 2 without a sync storm. |
| BUG-048 | fixed | WebSocket ticket acquisition has its own 750 ms abort deadline before socket setup. | `security-remediation-supplemental-routes.test.ts` - ticket deadline contract. |
| BUG-049 | fixed | Profile PUT issues field-specific atomic updates instead of read/merge/full-row overwrite. | `profile-bookmarks-watchlist-api.test.ts` - concurrent disjoint updates preserve both fields. |
| BUG-050 | fixed | Generated previews contain neither inline control scripts nor inline click handlers blocked by CSP. | `regime-share.test.ts`; `security-remediation-supplemental-routes.test.ts`. |
| BUG-051 | fixed | FRED observations older than the allowed market-calendar window return an explicitly stale, private fallback. | `security-remediation-supplemental-routes.test.ts` - stale numeric observation is not current or public-cacheable. |
| BUG-052 | fixed | Strength cache metadata is derived from the selected DB result; disk mtime is used only for a disk result. | `strength-confirmation-route.test.ts` - fresher Turso selection carries database freshness. |
| BUG-053 | fixed | Strength scan failures preserve failure status and expose `scan_succeeded:false`; cache fallback is identity-matched only. | `strength-confirmation-route.test.ts` - failed preset/targeted scans cannot return arbitrary cached success. |
| BUG-054 | fixed | Theta scan failures preserve non-OK status, expose `scan_succeeded:false`, and accept fallback only for matching request identity, so the existing `response.ok` client check fails closed. | `theta-harvester-route.test.ts` - matching cache cannot convert upstream failure into success. |
| BUG-055 | fixed | The skew route returns data only when the selected snapshot is fresh. | `skew-api.test.ts` - expired snapshot is not returned as current. |
| BUG-056 | fixed | The two-day skew route applies the same fresh-only response contract. | `skew2d-api.test.ts` - expired snapshot is not returned as current. |
| BUG-057 | fixed | Every UW, Exa, and Yahoo ticker-info request has a bounded provider deadline while existing cache selection retains last-known-good data. | `ticker-info-cache.test.ts`; `security-remediation-supplemental-routes.test.ts` - all five fetches carry deadlines. |
| BUG-058 | fixed | UW and both Yahoo news requests have bounded provider deadlines and existing provider fallthrough remains intact. | `security-remediation-supplemental-routes.test.ts` - all three fetches carry deadlines. |
| BUG-059 | fixed | Signed webhook IDs are atomically claimed before provisioning; retries are no-ops, failed handling releases the claim, and provisioning conflicts preserve trial state. | `demo-users.test.ts` - original clock/revocation preservation and one-time event claim; migration-order regression in `security-remediation-supplemental-routes.test.ts`. |
| BUG-061 | fixed | Yield-curve refreshes use three-second leg deadlines, one shared in-flight promise, a short negative cache, and stale last-known-good fallback. | `yield-curve-live-api.test.ts` - concurrent misses coalesce into one two-leg refresh. |

## Dependencies and overlaps

- `T7` source work completed after the relevant `T2`/`T3` route and backend surfaces were present.
- Shared `scripts/api/server.py` edits are limited to imports, backtest cancellation, and GEX/internals share content; `/journal/reconcile` is untouched.
- `web/components/WorkspaceSections.tsx` is untouched; BUG-054 fails closed through the route response contract and the existing client `response.ok` check.

## Verification

- Focused Web Vitest: **17 files, 155 tests passed**.
- Focused Python pytest: **9 tests passed**.
- Python compile: `scripts/api/server.py`, `scripts/api/subprocess.py`, and `scripts/generate_regime_share.py` passed.
- TypeScript: `npm run typecheck` passed.
- Patch hygiene: `git diff --check` passed.
