# L2 Order Book — Implementation Plan (BookTab)

Wire the Radon Terminal L2 montage mockup (`/tmp/radon-l2-montage.html`) into the
product: a depth-of-book surface in `BookTab` for **stocks** (exchange/MPID
montage), **options** (per-exchange BBO montage), and **futures** (centered
price-ladder DOM), plus a show/hide Time & Sales tape.

Grounded in a code-verified workflow sweep (relay / protocol+hook / component /
testing). Line numbers are from the current `main` and should be re-confirmed at
edit time.

**Status: Phase 1 RTH-VERIFIED LIVE ON PROD + FLAG ON (2026-06-01 10:0x ET).**
`RADON_DEPTH_ENABLED=1` set in `/home/radon/radon-cloud/.env` (backup
`.env.bak.predepth`); relay-only restart (no gateway 2FA). Verified via chrome-cdp
on app.radon.run/AAPL Book tab: **DepthMontage populated with live per-exchange L2**
(ARCA/BATS/IEX/MEMX/NSDQ MPID rows, real bid/ask sizes, SMART DEPTH pill, depth-fill
bars, best-quote emphasis, 10 rows), values ticking realtime, no error banner.
- Foundation: depth types + pure helpers + 23 tests (commit df49dbd).
- Relay channel + `ib@0.2.9 → @stoqey/ib@1.5.5` migration unlocking `isSmartDepth`
  (commits 9dc59b9, f69c0d4).
- UI: `OrderBook`/`DepthMontage`/`LadderDOM`/`TimeAndSales` in `BookTab`,
  `usePrices` depth slice, `TickerDetailContext` threading (commit e50f076).
- Full web suite green (2794).

**Phase 2 SHIPPED + verified live (2026-06-01 RTH, commits d40b164 relay /
e549491 web).** Futures ladder + Time & Sales tape, still behind
RADON_DEPTH_ENABLED.
- Futures: `/ES`, `/NQ`, etc. (web/lib/futuresSymbols.ts FUTURES_ROOTS) route to
  `<LadderDOM>`. Relay resolves the front month (reqContractDetails → nearest
  expiry, root→exchange map) and streams `reqMktDepth(10, isSmartDepth=false)`.
  Verified live: ES → ESM6 (conId 649180678), 10 bid/10 ask, kind:"future",
  "CME DEPTH", no marketMaker. CME L2 entitlement confirmed live (ES + NQ).
- Tape: relay opens `reqTickByTickData(AllLast)` alongside depth, 50-print ring,
  `tape-batch` WS message (oldest-first snapshot); usePrices `tape` slice replaces
  (not merges) + threads to TimeAndSales, which reverses for newest-at-top + formats
  the time. Verified live: 50 AAPL prints. Fixes the previously-empty tape panel
  (BookTab had hardcoded `trades=[]`).
- Integration-seam bugs caught + fixed at integration: ordering contract mismatch
  (relay oldest-first vs web's assumed newest-first), snapshot-replace-not-merge
  (merging duplicated rows every 100ms flush), raw-epoch time rendering.
- Futures L1 quote-bar fix (commit af8e208): a bare futures root subscribed L1 as
  a stock, so /ES showed the equity "ES" (Eversource ~$67) in the quote bar while
  the ladder showed the future (~7595). L1 now resolves the front-month future
  (same path as depth). Verified live: /ES quote bar 7595.00 == ladder head 7595.00.

**Remaining: Phase 3 — options per-exchange BBO montage refinement + any tape/depth
polish. (Options already get depth via the stock montage path + tape; Phase 3 is the
dedicated per-exchange BBO treatment + NBBO edge-marking live-verification.)**

---

## 0. Key findings that shape the plan

1. **The relay uses the SYNCHROUS `ib` npm library, not `ib_insync`.** The earlier
   Python research (`reqMktDepth`, `ContFuture`, `domBids/domAsks`) describes the
   *Python* surface. In `scripts/ib_realtime_server.js` the API is:
   - `ib.reqMktDepth(tickerId, contract, numRows, isSmartDepth)` / `ib.cancelMktDepth(tickerId)`
   - events `ib.on("updateMktDepth", (id, position, marketMaker, operation, side, price, size))`
     and `ib.on("updateMktDepthL2", (id, position, marketMaker, operation, side, price, size))`
   - **No async contract qualification** — futures can't be `qualifyContracts`'d in the relay;
     build `ib.contract.future(base, "USD", exchange)` and let IB reject (error 200) if invalid.
2. **Depth is scarce: ~3 concurrent `reqMktDepth` tickets** on a baseline account
   (vs. the generous L1 line budget). Phase 1 subscribes depth for the **focused
   symbol only**; an explicit cap + LRU recycle protects the budget.
3. **No depth code exists today** — `PriceData` carries only L1 scalars, `BookTab`
   renders only `<L1OrderBook>`. This is greenfield plumbing end-to-end; `<L1OrderBook>`
   becomes the degraded/no-entitlement fallback.
4. **Cache contract: not affected.** Depth is WS-only (relay `:8765`), no Next.js
   route, no `data/*.json`, so the `force-dynamic`/`no-store` contract and its test
   are untouched (confirm-only). Mobile SW already bypasses `/ws`.
5. **Realtime + entitlements — CONFIRMED LIVE (2026-05-31, probe vs production gateway).**
   Requesting `reqMarketDataType(1)` returned **marketDataType=REALTIME** for AAPL (account
   is realtime-entitled), `reqMktDepthExchanges()` returned **307 venues**, and IB msg
   **2152** listed depth on **NASDAQ, BATS, ARCA, BEX, NYSE, IEX** (top-of-book on BYX,
   AMEX, PEARL, MEMX...). No 354 / 10089 errors. Depth rows were 0 only because the probe
   ran on a closed Saturday. **So L2 depth + realtime are entitled; this is not a blocker.**
   BUT the relay requests `reqMarketDataType(4)` (delayed-frozen) at `:1113` by design.
   `reqMarketDataType` is **per-connection/global**, and depth + tick-by-tick require
   **realtime (type 1)**. Therefore the depth path must run realtime. **Recommended:
   a dedicated realtime depth IB client** (its own clientId in the 10-19 relay range, set to
   `reqMarketDataType(1)`) so the watchlist L1 stays delayed-frozen for closed-market last
   prices while the focused-symbol depth + tape stream live. Re-verify futures (ES) depth
   during RTH — the probe saw the usfuture farm still connecting.
6. **Per-instrument depth reality** (from API research):
   - Stock → real L2 via `isSmartDepth=true`; `marketMaker` = exchange (SMART) or MPID (TotalView). Needs TotalView/ArcaBook/Cboe entitlement.
   - Option → OPRA is L1; "depth" is a **per-exchange BBO montage**, not stacked levels. Needs OPRA.
   - Future → native single-venue depth via `isSmartDepth=false`, arrives on `updateMktDepth` (no MM); render as centered ladder. Needs CME (NP,L2) depth add-on.

---

## 1. Canonical data model (reconciliation)

Two shapes were proposed (a discriminated `kind: "montage"|"ladder"` with `rows`/`levels`,
vs. a flat `bid[]`/`ask[]` with `kind: "stock"|"option"|"future"`). **Decision: the
flat two-sided shape** — it matches the relay's natural `bidLadder`/`askLadder`
serialization and the mockup renderers 1:1, and keeps a single `case "depth"` handler.
Futures simply leave `marketMaker`/`exchange` null. Add to `web/lib/pricesProtocol.ts`:

```typescript
export type DepthSide = "bid" | "ask";

/** One book row. marketMaker/exchange null for futures (no venue attribution). */
export type DepthLevel = {
  price: number;
  size: number;
  marketMaker: string | null;   // MPID (NASDAQ TotalView) — equities direct
  exchange: string | null;      // venue code (SMART equities, options BBO)
};

export type DepthBook = {
  symbol: string;                       // same keyspace as PriceData.symbol (ticker | optionKey | future)
  kind: "stock" | "option" | "future";
  bid: DepthLevel[];                    // index 0 = inside/best
  ask: DepthLevel[];                    // index 0 = inside/best
  isSmartDepth: boolean;                // true equities/options, false futures
  feed: string | null;                  // head-pill label, e.g. "SMART DEPTH · TOTALVIEW"
  entitled: boolean;                    // false → render L1 fallback
  timestamp: string;
};

export type Trade = { price: number; size: number; exchange: string | null; time: string }; // T&S tape row

export type WSDepthMessage = { type: "depth"; symbol: string; data: DepthBook };
export type WSDepthBatchMessage = { type: "depth-batch"; updates: Record<string, DepthBook> };
export type WSDepthUnavailableMessage = { type: "depth-unavailable"; symbol: string; reason: "no-entitlement" | "futures-no-depth" | "recycled"; code?: number };
```

Add the three messages to the `WSMessage` union (`pricesProtocol.ts:95-105`). A
later refinement can add a `Trade[]` tape stream (`type:"tape"`); Phase 1 may seed
the tape from the existing RTVolume last-trade the relay already receives.

---

## 2. Relay — `scripts/ib_realtime_server.js`

**Current anchors (verify):** subscribe `920-989`, unsubscribe `990-1005`,
`startLiveSubscription` `549-578` (`reqMktData` at `566`), `stopLiveSubscription`
`580-590`, `clientBatchBuffers` `428` / `bufferPriceForClient` `434-446` /
`flushBatches` `448-456` (`BATCH_INTERVAL_MS=100` at `425`), `nextRequestId` `367`,
`requestIdToSymbol` `278`, reconnect/`restoreSubscriptions` `890-906`+`1106-1118`,
error handler `1129-1188` (200 at `1141`, 354 at `1152`).

**Additions (~250 LOC):**
1. **State (after ~280):** `MAX_CONCURRENT_DEPTH=3`, `symbolDepthStates` Map,
   `depthRequestIdToSymbol` Map, `clientDepthBuffers` Map, `depthLRU`, per-symbol
   `{ depthTickerId, contract, ladders:{bid:Map,ask:Map}, focusedAt }`.
2. **`startDepthSubscription(key, contract, isFutures)` / `stopDepthSubscription(key)`
   (after ~590):** cap-check + LRU-evict oldest non-focused before subscribing;
   `numRows = isFutures ? 10 : 5`, `isSmartDepth = !isFutures`; `ib.reqMktDepth(...)`;
   cleanup via `ib.cancelMktDepth`.
3. **Handlers (after ~1230):** `ib.on("updateMktDepth"...)` (futures, MM null) and
   `ib.on("updateMktDepthL2"...)` (equity/SMART, MM=exchange). Maintain the ladder by
   `(side, position)` and `operation` (0 insert / 1 update / 2 delete), `side` 1=bid 0=ask,
   then `hydrateAndBroadcastDepth(symbol)`.
4. **Broadcast/batch:** `hydrateAndBroadcastDepth` serializes each ladder Map →
   position-sorted array; `bufferDepthForClient`; `flushDepthBatches()` emits
   `{type:"depth-batch", updates}`. Call it from `flushBatches()` (reuse the 100ms tick);
   add `clientDepthBuffers.delete(client)` to `removeBatchBuffer()` (`470`).
5. **Subscribe/unsubscribe routing:** handle NEW actions `subscribe-depth` /
   `unsubscribe-depth` (single `symbol`) — distinct from the array `subscribe` so the
   scarce resource is routed separately. Determine instrument: option (has expiry/strike
   payload), future (symbol/exchange in a futures set), else stock.
6. **Reconnect (`1106`):** `cancelMktDepth` all tickets on drop, `restoreDepthSubscriptions()`
   for the focused symbol after `restoreSubscriptions()`.
7. **Entitlement (error handler, ~1152):** code **10089** (or `/depth.*not (allowed|eligible)/i`)
   → cancel the ticket, emit `depth-unavailable {reason:"no-entitlement", code:10089}`, do
   NOT latch a fault. For futures with no depth → `reason:"futures-no-depth"`.

**REALTIME (required):** the main relay connection runs `reqMarketDataType(4)` (delayed)
by design. Depth + tick-by-tick need realtime. **Open a dedicated depth IB client**
(separate clientId in 10-19) that calls `reqMarketDataType(1)` and owns all `reqMktDepth`
+ tick-by-tick tickets, so the watchlist L1 stays delayed-frozen. Gate the whole thing
behind `RADON_DEPTH_ENABLED` (no second client / no realtime lines when off).

**Friction flagged:** shared `nextRequestId` counter (add a leak audit), no async
futures qualification (rely on IB error 200), `reqMarketDataType` is per-connection (hence
the dedicated realtime depth client above), and entitlements are now confirmed (§0.5).

---

## 3. Protocol + hook — `web/lib/pricesProtocol.ts`, `web/lib/usePrices.ts`

**Current anchors (verify):** `WSMessage` union `pricesProtocol.ts:95-105`;
`usePrices` diff-based `syncSubscriptions` `197-300`, `desiredRef` `112-116`, hashes
`125-130`/`183-194`, state machine `ConnState` `62`/`connStateRef` `105`/`socketGenRef`
`106`, onopen full-resend `368-371`, onmessage switch `389-440`, staleness
`15s/60s` `69-70`/`375-380`, teardown `594-606`, return shape `41-60`/`620-630`.

**Changes:**
1. Add `depths: Record<string, DepthBook>` state slice + to `UsePricesReturn`.
2. **Separate** focused-depth tracking: `desiredDepthRef`/`lastSentDepthRef` (a single
   symbol or null) — NOT folded into `desiredRef`. New `depthSymbol?: string|null`
   option; sync the ref during render.
3. `syncDepth(ws)` sibling of `syncSubscriptions`: diffs the one focused symbol, sends
   `{action:"subscribe-depth", symbol}` / `{action:"unsubscribe-depth", symbol}`, evicts
   the old key from `depths` on change.
4. onmessage cases `"depth"` (merge one) / `"depth-batch"` (merge map) / `"depth-unavailable"`
   (mark `depths[symbol] = {...entitled:false}` or drop with a reason flag).
5. On open: after the existing full-resend, `lastSentDepthRef.current=null; syncDepth(ws)`;
   reset it in onclose + teardown branches (symmetry with `lastSentHashRef`).
6. A diff effect keyed on `depthSymbol`. Do **not** let `depthSymbol` alone force a connect
   (`hasSubscriptions` untouched) — the focused symbol is already in `symbols`.
7. Staleness thresholds unchanged. Update `web/CLAUDE.md` WebSocket section to document the
   two new inbound types + two new scarce-resource actions.

---

## 4. Component — `web/components/ticker-detail/BookTab.tsx` (+ `TickerDetailContent.tsx`)

**Current anchors (verify):** `BookTabProps` `16-26`, `<L1OrderBook>` `30-93`,
`BookTab` main `342-399` (replace the `<L1OrderBook>` call `361-369`; everything below —
`PositionSummary`, order forms, `OpenOrdersList` — untouched). `TickerDetailContent`
props `108-118`, `resolveTickerQuoteTelemetry` `69-106` (gives `priceKey` for single-leg
options), `BookTab` call `217-224`, `usePrices` consumed via `prices` prop `113`.

**Threading:** add `depths` (and later `tape`) to `TickerDetailContentProps` from the
same `usePrices` call, forward to `BookTab`. Pass `bookKey = chartPriceKey ?? ticker`
(reusing the option `priceKey`) and **set `usePrices({ depthSymbol: bookKey })`** so depth
subscribes for exactly the focused subject.

**Instrument kind** (no new round-trip): `depth.kind` wins when present; else
`isIndexSymbol(ticker) && hasFuturesSupport(ticker)` → future (e.g. VIX); else single-leg
non-stock position → option; else stock. (`indexSymbols.ts:42,62`, `types.ts:64,77`.)

**New tree** (polymorphic dispatch, not nested ifs):
```
BookTab
└─ <OrderBook depth tape l1 kind symbolLabel feed>
   ├─ window head (sym + kind + LAST/BID/ASK/SPRD + feed pill)
   ├─ <TapeToggle> (persist to localStorage `radon:book:tape`)
   └─ body-grid (.tape-hidden → 1fr 0fr reflow)
      ├─ left:  !depth?.entitled → <L1OrderBook> (EXISTING fallback)
      │         kind==="future" → <LadderDOM>
      │         else            → <DepthMontage kind>   (stock + option, one component)
      └─ right: <TimeAndSales trades visible>
```
`<DepthMontage>` covers stock L2 and option BBO (differ only by best-rule: `i===0` vs
`nbbo`, and the price-level edge marker suppressed for options). `<LadderDOM>` is the
futures centered ladder with cumulative-depth fans. `<L1OrderBook>` is the no-entitlement
fallback.

**CSS:** port the mockup `<style>` into `web/app/globals.css` after the existing Order Book
block, namespaced `.book-*`. Drop the mockup's `:root` token redefs (keep only the derived
`--hairline`/`--depth-*`/`--ladder-*` `color-mix` helpers). Drive the reflow off a React
`.tape-hidden` class (BookTab can't own `<html data-tape>`). Replace the `rgba()` box-shadow
with a `color-mix` token or drop it. 4px max radius, brand tokens only, no em dashes; do not
port the `.legend` (dev copy).

**Pure helpers → `web/lib/book/depthDerivations.ts` (testable):** `groupPriceLevels(rows)`
(montage edge markers), `buildLadderRows({bid,ask})` (cumulative + ordering + fill ratio +
best), `montageFill(level, maxSize)`, `classifyTicks(trades)` (uptick/downtick tick-test),
`isBestLevel(level, index, kind)`. Reuse `fmtPrice` from `positionUtils` (confirm 2-dp
future/index precision; else add `fmtDepthPrice`).

**Mobile** (`useViewport` + `body[data-mobile]`): stack the reflow (tape below or in a
`<BottomSheet>`), drop the `mkt` column ≤640px, cap to ~8 levels/side; the futures ladder is
already narrow. Add mobile E2E at 393×852.

---

## 5. Testing (TDD, red first)

- **Pure logic** (`web/tests/depth-*.test.ts`, Node env): price-level grouping
  (equal/adjacent prices, conservation), cumulative ladder (monotonic, truncation,
  crossed/locked flagged not thrown), tick-test (up/down/zero-tick/first-tick), best-quote
  (one-sided/empty, never fabricate a mid). Optional `fast-check` fuzz (seed 42): cumulative
  monotonicity + grouping conservation.
- **Relay** (`scripts/tests/test_depth_relay.py`, asyncio mock_send): insert/update/delete →
  correct ladders + positions + MM; side routing; same-interval last-write-wins collapse to
  `depth-batch`; empty buffer no-emit; 10089 → cancel + `depth-unavailable`, no fault latch.
  Source-assertion test (`web/tests/ib-depth-stream-contracts.test.ts`) like the index one.
- **Hook** (`web/tests/use-depth-*.test.ts`): depth subscribes only for focused symbol;
  focus-switch unsub→sub; depth-batch merge; coexists with L1; resubscribe on reconnect.
- **Component** (`web/tests/depth-book-*.test.tsx`, jsdom): all 3 modes render + switch
  without unmount; tape toggle reflow; tokens only (no raw hex / `green-500`); `---` on
  empty/one-sided/crossed; degradation note on no entitlement; 4px radius.
- **E2E/chrome-cdp** (market-hours-gated): liquid stock ladder populates, cumulative
  increases, tick colors flip; focus-switch releases prior ticket (≤budget); WS frames show
  ~10/s `depth-batch`. Ignore extension console noise.

---

## 6. Degradation, flag, phased rollout

- **No L2 entitlement (10089):** soft, expected → cancel ticket, `depth-unavailable`,
  panel flips to `<L1OrderBook>` + calm `--text-muted` note "Level 2 depth not entitled —
  showing top of book." (reworded without em dash). No toast/banner.
- **3-ticket budget:** focused-symbol-only (1 active) in Phase 1; LRU recycle with a calm
  "Depth recycled to <FOCUSED>" note; never exceed the cap (cancel-before-subscribe).
- **Futures no-depth:** graceful `depth-unavailable {reason:"futures-no-depth"}`, no hang
  (bound any await), L1 BBO ladder fallback.
- **Flag:** `NEXT_PUBLIC_RADON_L2_DEPTH` (panel mount) + relay `RADON_DEPTH_ENABLED` (no
  tickets when off). Default OFF → zero behavior change.
- **Phases:** **P1** stock montage + tape toggle + all degradation wired; **P2** futures
  ladder (click-to-trade via existing `<OrderRiskGate>` `LinearOrderRiskInput`, never a new
  order seam); **P3** options BBO + `Trade[]` tape stream (3-ticket LRU exercised here).
- **Per-phase live checklist:** market open (`TZ=America/New_York date`), `/health`
  authenticated before expecting depth, cdp ladder check, WS cadence, ticket release on
  focus-switch, entitlement-absent path stated honestly, full Vitest+pytest green, flag OFF
  until the phase's live checklist passes.

---

## 7. Open questions / risks
- Does the production IBKR account hold TotalView / CME-depth / OPRA entitlements? If not,
  P1 ships permanently in L1-fallback (confirm before spending E2E budget).
- `reqMktDepth` is heavier than `reqMktData`; debounce focus-driven (re)subscription (~250ms),
  reuse one connection, never connect-disconnect per focus (avoids pacing 162/366 + the
  2FA-renewal-under-load failure mode).
- Full TotalView montage re-render perf: memoize rows keyed `(side, position)`; fixed
  `depthN` (10) likely sufficient, virtualization only if needed.
- Mobile density: reduced BBO-only on the narrowest tier vs. full montage.
- Crossed/locked books: render flagged, never throw/hide (pinned in unit tests + a live check).

---

## File map
- **New:** `web/lib/book/depthDerivations.ts` (+ `.test.ts`); `web/components/ticker-detail/{OrderBook,DepthMontage,LadderDOM,TimeAndSales}.tsx`; depth types in `web/lib/pricesProtocol.ts`; relay/hook/component tests listed in §5.
- **Modified:** `scripts/ib_realtime_server.js` (~250 LOC); `web/lib/usePrices.ts`; `web/components/ticker-detail/BookTab.tsx`; `web/components/TickerDetailContent.tsx`; `web/app/globals.css`; `web/CLAUDE.md` (WebSocket section).
- **Confirm-only:** `web/tests/api-routes-no-cache-contract.test.ts` (depth is WS-only, no change).
- **Reference:** mockup `/tmp/radon-l2-montage.html`.

---

## RTH go-live runbook (run Monday during market hours)

The depth feature is deployed but flag-gated OFF. Enabling it requires the prod
host (SSH) + the live app (browser), so it must be run from a local Claude Code
session during RTH (09:30-16:00 ET, Mon-Fri). Trigger: open a session and say
"run the L2 RTH go-live" (memory: project_l2_orderbook_2026_05_31).

1. **Gate:** `TZ=America/New_York date` (weekday, 09:30-16:00 ET) and
   `ssh ib-gateway 'curl -s localhost:8321/health'` → `auth_state: authenticated`.
   Not RTH or not authenticated → STOP, do not flip.
2. **Enable:** set `RADON_DEPTH_ENABLED=1` for the relay (`/home/radon/radon-cloud/.env`
   or the radon-relay systemd Environment). Restart: `ssh ib-gateway 'systemctl restart
   radon-relay.service'` (fallback `ssh ib-gateway radon restart`). Confirm journalctl
   "IB connected" + WS 8765 listening + realtime `reqMarketDataType(1)`.
3. **Verify (chrome-cdp on app.radon.run):** AAPL Book tab → DepthMontage populates with
   live exchange/MPID rows; VIX or an ES future → LadderDOM with cumulative fans; no
   console errors; depth-batch WS frames ~10/s.
4. **Success →** screenshot, mark this plan RTH-verified + update the memory, flag stays ON.
5. **Failure (no rows / 10089 / price disruption) →** set `RADON_DEPTH_ENABLED` off,
   restart radon-relay, confirm L1 prices resume, report the relay log + IB error codes.
   Never leave a broken relay.
