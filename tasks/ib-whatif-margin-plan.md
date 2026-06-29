# IB whatIfOrder Margin Backend — Implementation Plan (Phase 2)

Replaces the static "UNAVAILABLE — IB what-if required" text in `OrderConfirmSummary`
with a real IB `initMarginChange` for multi-leg undefined-risk option combos, sourced
from `ib_insync.IB.whatIfOrder()`. Produced by the `ib-whatif-margin-backend` design
workflow (2026-06-29).

## Architecture (one debounced round-trip, fired at the confirm step)

```
OrderConfirmSummary  ◄─ merged summary + marginWhatIf={status}
OrderRiskGate.tsx    ── useOrderRisk (pure useMemo, UNCHANGED) ─► requirement === null
   └─ useWhatIfMargin(input, portfolio, state)  [NEW: gate + ~400ms debounce + abort]
        POST /api/orders/whatif  (structural key, NO limit price)
   web/app/api/orders/whatif/route.ts  [NEW nodejs, AUTHENTICATED, no-store]
        radonFetch('/orders/whatif', timeout 15s)
   scripts/api/server.py  POST /orders/whatif  [NEW, NOT auth-exempt]
        _run_ib_script_with_recovery('ib_place_order.py', ['--json', body, '--whatif'], timeout=12)
   scripts/ib_place_order.py --whatif  [build SAME contract+order, branch BEFORE place]
        asyncio.wait_for(client._ib.whatIfOrderAsync(contract, order), timeout=8)
   IB pre-trade risk engine ─► OrderState(initMarginChange, maintMarginChange, ...)
```

Invariants: what-if never transmits (no permId, skip the 6-12s confirm-poll); `useOrderRisk`
stays pure/sync (async lives only in `OrderRiskGate`); the margin is INFORMATIONAL — it never
flips `okToSubmit` (Gate 1 / undefined risk still governs submit).

## Ordered checklist
1. `scripts/ib_place_order.py`: `what_if` param + `--whatif` flag in `main()`; `_margin()`
   sentinel/`''`→None parser; bounded `await_what_if` (`asyncio.wait_for` timeout=8, per
   `cri_scan.py:_fetch_ib`); branch BEFORE `place_order` returning stdout JSON; progress→stderr.
2. `scripts/tests/test_ib_whatif_margin.py`: mocked `whatIfOrderAsync`; sentinel/empty→None;
   After-Before fallback; no-place/no-poll; combo BAG `ComboLeg.action`=structure + index exchange.
3. `scripts/api/server.py`: `@app.post('/orders/whatif')` (test_mode stub + recovery wrapper,
   timeout=12). Confirm NOT in `AUTH_EXEMPT_PATHS` (keeps the two test pins unchanged).
4. `scripts/api/tests/test_route_authz_matrix.py`: add 401-anon / 200-trusted-local row.
5. Types — add `'ib-whatif'` to BOTH unions: `marginEstimate.ts:24` (`MarginEstimateSource`)
   and `web/lib/order/types.ts:~109` (`marginImpact.source`).
6. `web/app/api/orders/whatif/route.ts`: new nodejs route (reuse place validation +
   orderPayload builder; demo user → `{source:'unavailable'}`, NOT /paper/place; no-store).
7. `web/tests/api-orders-whatif.route.test.ts`.
8. `web/lib/order/risk/internal/whatIfKey.ts` (structural key, EXCLUDES limit price),
   `useWhatIfMargin.ts` (gate predicate + debounce + abort + useRef cache),
   `mergeWhatIfMargin.ts` (brand-preserving spread merge); export merge from `index.ts`.
9. `web/lib/order/risk/OrderRiskGate.tsx`: wire hook + merge + pass `marginWhatIf` prop.
10. `web/lib/order/components/OrderConfirmSummary.tsx`: loading ("Calculating IB margin…")
    + error (revert to UNAVAILABLE) branches; "IB margin" tag for `source==='ib-whatif'`.
11. TS tests: use-whatif-margin, mergeWhatIfMargin, order-risk-gate-whatif, order-confirm-summary-whatif.
12. `web/CLAUDE.md`: document the Phase-2 async layer.
13. Full suites green (`bun test` + `pytest`). **Ship behind `NEXT_PUBLIC_WHATIF_MARGIN_ENABLED` (OFF).**
14. Live-verify on an authenticated gateway (market hours): debit-spread magnitude;
    **bearish-RR returns non-empty margin**; off-hours sentinel → UNAVAILABLE; chrome-cdp confirm
    of the rendered number + "IB margin" tag. Then flip the flag on.

## Gate predicate for useWhatIfMargin (ALL must hold)
`coverageStatus==='resolved'` AND `marginImpact!=null` AND `marginImpact.requirement===null`
AND `input.type!=='linear'` AND `input.closeOut==null` AND `input.chainLegs.length>1`.

## Top risks (mitigations baked in)
1. **Awaiting-2FA hang** — `whatIfOrderAsync` blocks forever on a logged-in-but-awaiting-2FA
   gateway. The inner `asyncio.wait_for(timeout=8)` is the single most important detail; do NOT skip.
2. **String/sentinel margins** — every field through `_margin()`; `1.79e308`/`''`→None (never 0).
3. **Bearish-RR combo-router caveat** — reasoned NOT to apply to what-if (pre-trade engine, no
   routing), but the one empirically-unverified case → confirm live once.
4. **Connection hammering** — structural debounce key (excludes price) + ~400ms + abort + useRef
   cache → one round-trip per confirm, not per keystroke.
5. **Brand integrity** — success merge MUST go through `mergeWhatIfMargin` (never an `as`-cast).

## Verification gap (read before shipping)
Real margin magnitudes, the bearish-RR confirmation, and off-hours sentinel behavior CANNOT be
unit-tested — they need an authenticated IB gateway, ideally during/after market hours. The
feature-flag rollout exists precisely so the backend can ship + bake inert, then be enabled after
this live verification.

## Key file references
- `scripts/ib_place_order.py` (contract builder ~145-248, LimitOrder ~265-271, place ~300, main ~412)
- `scripts/api/server.py` (`orders_place` 1803, `AUTH_EXEMPT_PATHS` 385, `_run_ib_script_with_recovery` 2997)
- `scripts/cri_scan.py` (`_fetch_ib` bounding pattern)
- `web/lib/order/risk/{useOrderRisk.ts,OrderRiskGate.tsx,index.ts}`,
  `internal/marginEstimate.ts:24`, `web/lib/order/types.ts:~109`,
  `web/lib/order/components/OrderConfirmSummary.tsx` (100, 129-142)
- clone target: `web/app/api/orders/place/route.ts`
