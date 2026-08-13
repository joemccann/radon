# Supplemental security/chat remediation status

## Reconciliation

- Owned findings: **37** (23 HIGH_BUG and 14 BUG rows)
- Fixed: **31**
- Duplicate: **6**
- Deferred: **0**
- Remaining actionable: **0**

## HIGH_BUG findings

| ID | Disposition | Current-code evidence | Regression evidence |
|---|---|---|---|
| HB-046 | fixed | Demo administration uses fail-closed `DEMO_ADMIN_USER_IDS`, independent of the general trial allowlist. | `demo-admin.test.ts` - independent allowlist and default-deny cases. |
| HB-047 | fixed | Blotter rows are parsed independently; malformed rows are quarantined and an all-malformed nonempty journal returns 503 instead of empty success. | `blotter-route-malformed.test.ts`. |
| HB-048 | fixed | Long-range NQ/SPX option risk reversals are date-aligned and published as a typed spread; CTA position spreads remain a separate series. | `internals-skew-series.test.ts::nq_spx_field_has_one_formula_and_unit_across_sources`. |
| HB-049 | fixed | Reconciliation persists a UTC snapshot identity, FastAPI returns that exact ID, and journal sync rejects stale/missing IDs before importing by equality. | `journal-sync-snapshot.test.ts`; `test_ib_reconcile_dual_write.py`. |
| HB-050 | fixed | Replacement is one backend state machine: full limit/what-if preflight precedes cancellation; cancelled targets and replacement orderRef survive partial/504 results. | `test_order_replace_state_machine.py` (2 cases). |
| HB-051 | fixed | Combo legs are limited to 2-8, ratios to integral 1-100, and authoritative effective contracts are capped at `quantity * max(ratio)`. | `test_order_limits.py` parameterized ratio/effective-quantity regressions. |
| HB-052 | fixed | A qualified caller-supplied conId must exact-match symbol, security type, expiry, strike/right, and exchange before placement. | `test_ib_place_order_contract_identity.py`. |
| HB-053 | fixed | Previous-close selection and cache keys share the holiday-aware completed ET session date. | `previous-close-yahoo-daily-array.test.ts::cache_rollover_at_et_midnight_uses_expected_session`. |
| HB-054 | fixed | Ratings GETs coalesce per ticker; the Python cache merges under `flock` and atomically replaces the JSON file. | `test_fetch_analyst_ratings.py::test_concurrent_cache_updates_are_atomic_and_lossless`. |
| HB-055 | fixed | VCG expected session/open state uses the shared US holiday calendar and treats weekends/pre-open as the prior completed session. | `vcg-route-freshness.test.ts`. |
| HB-056 | duplicate -> HB-100 | Global launcher passes portfolio into the same canonical typed proposal and option-preserving placement flow. | HB-100 tests plus `chat-launcher-focus.test.tsx`. |
| HB-057 | duplicate -> HB-101 | Global launcher reaches the same mandatory `OrderRiskGate` as the embedded chat panel. | HB-101 tests plus `chat-launcher-focus.test.tsx`. |
| HB-058 | duplicate -> HB-100 | The confirmation summary and request are derived from one validated stock/option discriminated union. | `chat-order-confirm.test.tsx`; `chat.test.ts`. |
| HB-059 | duplicate -> HB-101 | Confirm is disabled and its handler refuses placement until branded risk state has `okToSubmit`. | `chat-order-confirm.test.tsx`. |
| HB-060 | fixed | Every new user turn synchronously clears the prior proposal and risk state before streaming. | `chat-order-confirm.test.tsx` proposal lifecycle coverage. |
| HB-061 | fixed | Canonical `ib_sync` snapshots carry `risk_budget`; every `OrderRiskGate` renders the correlation status through `useOrderRisk`. | `order-risk-chokepoint.test.tsx`; `test_ib_helpers.py` (high_bug_inventory handoff: 58 Vitest, 177 affected pytest). |
| HB-095 | fixed | Production realtime always obtains Clerk-backed token state; public authless flags no longer create a tokenless provider. | `realtime-socket-auth.test.ts`. |
| HB-096 | fixed | Sibling read calls execute first; a destructive sibling is deferred and must be resubmitted as a freshly runtime-validated proposal. | `assistant-loop-hardening.test.ts::destructive_call_waits_for_required_reads_and_runtime_validation`. |
| HB-097 | duplicate -> HB-100 | Option proposal schema requires complete contract identity and shares HB-100's canonical mapper. | `assistant-tool-loop.test.ts`; `chat.test.ts`. |
| HB-098 | fixed | Prior rows fetch newest-first with a sentinel row and explicit truncation metadata; authoritative realized P&L refuses truncated history. | `assistant-journal-tools.test.ts::realized_pnl_refuses_or_pages_truncated_prior_rows`. |
| HB-100 | fixed | Runtime-validated stock/option union preserves option conId, expiry, strike, right, exchange, and type through `/api/orders/place`. | `chat.test.ts`; `chat-order-confirm.test.tsx`. |
| HB-101 | fixed | Assistant confirmation owns a branded `OrderRiskGate`, receives current portfolio state, and cannot invoke placement without `okToSubmit`. | `chat-order-confirm.test.tsx`; `order-risk-chokepoint.test.tsx`. |
| HB-116 | fixed | User-intent/content keys are principal-scoped, durably persisted, and retained for five minutes across retries/restarts; assistant confirmations mint UUID keys. | `order-place-idempotency-route.test.ts::accepted_order_retry_after_four_seconds_returns_original_result_once`; idempotency suites. |

## BUG findings

| ID | Disposition | Current-code evidence | Regression evidence |
|---|---|---|---|
| BUG-001 | fixed | Browser discovery accepts only executable regular files; an NVM directory cannot be selected. | `.pi/tests/browser-tools.test.ts::falls back when NVM has no agent-browser executable`. |
| BUG-002 | fixed | Startup market state handles full holidays and US early closes. | `.pi/tests/startup-protocol.test.ts::market state honors holidays and early closes`. |
| BUG-003 | fixed | All detached startup jobs use one helper with timeout, output cap, spawn-error completion, process-group termination, and exactly-once callback. | `.pi/tests/startup-protocol.test.ts::startup jobs use timeout, bounded output, spawn-error, and process-group termination`. |
| BUG-011 | fixed | Newsfeed media names are content-addressed by source identity; changed URLs re-fetch and receive a new immutable path. | `newsfeed-image-url.test.ts`; `newsfeed-media-ssrf.test.ts` (31 tests). |
| BUG-060 | fixed | Workflow confirmation accepts only literal JSON `true`; strings/arrays/objects remain false. | `workflow-run-confirmation.test.ts`. |
| BUG-062 | fixed | Declining order confirmation returns before any run request; both Web and Python validators reject legacy order nodes before any source/effect node executes. | `workflow-composer.test.ts::declined_order_confirmation_executes_no_nodes`; `test_workflow_order_preflight.py`. |
| BUG-063 | fixed | New node IDs use collision-checked `crypto.randomUUID()` allocation against the current loaded node set. | `workflow-composer.test.ts::adding_after_load_never_duplicates_existing_node_id`. |
| BUG-064 | fixed | The structure-only order palette entry is removed, and persisted/direct order graphs fail closed until a complete risk-reviewed contract editor exists. | `workflow-composer.test.ts::palette_order_workflow_builds_valid_executable_contract`; workflow executor tests. |
| BUG-069 | fixed | `DemoWelcomeModal` no longer reads production authless-test switches. | `demo-welcome-modal.test.tsx`. |
| BUG-080 | fixed | Copy/download actions live in the trusted parent; preview iframe stays isolated without script privileges. | `share-report-path.test.ts`. |
| BUG-095 | fixed | `IBStatusContext` no longer fabricates healthy/authenticated state from public authless flags. | `ib-status-context.test.ts`. |
| BUG-099 | duplicate -> BUG-059 | Webhook retry claim and trial-state-preserving upsert are implemented once under BUG-059. | `demo-users.test.ts`; supplemental routes ledger. |
| BUG-108 | fixed | Gemini explicitly rejects tool-bearing requests so the provider chain falls back instead of silently dropping tools. | `llm-provider.test.ts::gemini_tool_request_is_supported_or_explicitly_falls_back`. |
| BUG-129 | fixed | Per-user mutations serialize, roll back only the affected symbol, invalidate pre-mutation reads, and force a fresh canonical reload. | `use-watchlist.test.tsx`; `watchlist-user-isolation.test.tsx`. |

## Verification

- Focused Web Vitest: **21 files, 153 tests passed**.
- Focused Python pytest: **48 tests passed**.
- Workflow safety: **4 Web files, 28 tests passed** and **33 Python tests passed**.
- Boundary checks: **6 browser tests**, **22 startup tests**, and **31 newsfeed/media tests passed**.
- TypeScript: `bunx tsc --noEmit` passed.
