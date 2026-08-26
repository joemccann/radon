# `/portfolio` API and database performance audit

Date: 2026-08-25
Scope: Next.js route handlers, shared Turso helpers, FastAPI, IB synchronization, disk fallbacks, and page-adjacent requests triggered by `/portfolio`.
Method: source trace plus read-only production Turso queries and `EXPLAIN QUERY PLAN`. No production writes or live IB actions were performed.

## Executive findings

1. **The blocking `/api/portfolio` response does three Turso reads, but two are not used by the portfolio page.** The latest portfolio snapshot is followed by two journal-wide JSON scans for `trade_log_dates` and `contract_open_dates` (`web/app/api/portfolio/route.ts:46-62`, `:64-163`, `:166-177`). Those maps are consumed only by the executed-order share-card path on `/orders` (`web/components/WorkspaceSections.tsx:3697-3702`). `/portfolio` waits for them before setting portfolio state.
2. **The journal work grows with all-time trade history.** Production held 1,601 journal rows / 765,022 payload bytes at audit time; 1,184 rows / 570,986 payload bytes matched the option-contract scan. The current query plans are `SCAN journal`, with a temporary B-tree for the per-ticker aggregate. The existing journal indexes do not cover these JSON expressions.
3. **The global shell also fetches and polls orders on `/portfolio`.** `WorkspaceShell` always mounts `useOrders`; it performs an initial GET regardless of market state and polls every 30 seconds during market hours (`web/components/WorkspaceShell.tsx:103-107`, `web/lib/useOrders.ts:149-177`). Each `/api/orders` GET performs two uncached Turso queries serially (`web/lib/orders/readOrdersFromDb.ts:62-81`).
4. **Cache TTLs do not help a single active tab.** Portfolio polls every 30 seconds, while the snapshot cache is 3 seconds and the entry-date caches are 10 seconds (`web/lib/usePortfolio.ts:13-16`, `web/app/api/portfolio/route.ts:25-32`). The autonomous writer runs every 60 seconds during RTH (`cloud/services/radon-portfolio-sync.timer:2-10`). Therefore every steady-state poll from one tab misses all three caches; the caches only coalesce nearly simultaneous tabs.
5. **The live IB sync has a separate serial journal N+1.** For each distinct option ticker it runs `compute_open_basis_for_ticker`, then for each option contract it runs `prior_net_qty_for_contract` (`scripts/ib_sync.py:704-766`, `scripts/clients/journal_basis.py:196-329`). The current snapshot implies 10 unique option tickers and 18 unique option contracts, so the current shape can issue up to 28 repeated journal statements before the other sync reads. Raw IB positions remain the definitive count; they were not queried in this read-only audit.
6. **The latest-snapshot lookup is not the demonstrated problem.** Production `EXPLAIN` uses the `portfolio_snapshots` primary-key index. A separate `COUNT(*)` probe timed out at the 4-second audit bound, but that count is not on the request path and is not evidence that the indexed `ORDER BY taken_at DESC LIMIT 1` read scans every payload.
7. **No `/api/performance` request is triggered by `/portfolio`.** `PerformancePanel` is mounted only in the `performance` switch branch (`web/components/WorkspaceSections.tsx:4142-4155`), and `usePerformance` is owned by that panel (`web/components/PerformancePanel.tsx:417`, `web/lib/usePerformance.ts:8-19`).

## Exact request and data-flow tree

```text
GET /portfolio
└─ Next page -> <WorkspaceShell section="portfolio">
   ├─ blocking portfolio state: GET /api/portfolio (no-store, 12s browser bound)
   │  ├─ requireRouteAccess + 20/min per-user read limiter
   │  ├─ cachedReadResult("portfolio:snapshot", TTL 3s, stale-on-error 60s)
   │  │  └─ Turso Q1 latest portfolio snapshot
   │  ├─ Promise.all after Q1
   │  │  ├─ cachedRead("portfolio:tradeLogDates", TTL 10s) -> Turso Q2
   │  │  └─ cachedRead("portfolio:contractOpenDates", TTL 10s) -> Turso Q3
   │  │     └─ JS groups and sorts all returned option fills
   │  └─ JSON snapshot + both maps; explicit no-store response
   ├─ parallel shell state: GET /api/orders (no-store, 12s browser bound)
   │  ├─ direct-cloud replica sync hook (no-op by default)
   │  ├─ Turso Q4 open orders
   │  └─ then Turso Q5 executed orders (serial, not Promise.all)
   ├─ after Clerk identity resolves: GET /api/watchlist
   │  └─ Turso Q6 user watchlist; browser module caches for identity lifetime
   ├─ footer: GET /api/service-health every 60s
   │  └─ cachedRead("service_health:rows", TTL 3s) -> Turso Q7
   ├─ footer: GET /api/flex-token
   │  └─ read data/flex_token_config.json
   ├─ realtime status provider
   │  ├─ POST /api/ib/ws-ticket -> FastAPI POST /ws-ticket -> status relay WS
   │  └─ production GET /edge-health/status; local/fault attribution GET /api/admin/health
   ├─ price hook
   │  └─ second POST /api/ib/ws-ticket -> FastAPI POST /ws-ticket -> price relay WS
   └─ after price messages, conditional fallbacks
      ├─ POST /api/previous-close for stock marks missing close
      ├─ GET /api/index-quote for watchlist indexes missing relay marks
      └─ GET /api/futures-quote for ES/NQ/RTY missing relay marks during Globex
```

The portfolio route itself is a pure snapshot read. `GET /api/portfolio` never calls IB (`web/app/api/portfolio/route.ts:195-240`). Live synchronization is a separate manual/stale path:

```text
stale render or Sync Now
└─ POST /api/portfolio (42s browser bound)
   ├─ operator authorization + 4/min per-user rate limit
   ├─ radonFetch POST /portfolio/sync (35s Next bound)
   │  └─ FastAPI sync coordinator
   │     ├─ coalesces same-key in-flight work
   │     ├─ reuses successful result for 30s
   │     └─ ib_sync.py --sync --json-output --db-optional (30s child bound)
   │        ├─ IB managed accounts + account summary
   │        ├─ account P&L subscription
   │        ├─ journal-basis N+1
   │        ├─ IB positions
   │        ├─ all reqPnLSingle + market-data requests issued together
   │        ├─ bounded wait, maximum 2.5s
   │        ├─ journal entry-date read + previous portfolio read
   │        ├─ margin sample + portfolio snapshot write + return-capital reconcile
   │        └─ live JSON payload
   └─ Next loads both entry-date maps again before responding
```

Anchors: `web/lib/useAutoSyncOnStale.ts:77-123`, `web/app/api/portfolio/route.ts:242-287`, `web/lib/radonApi.ts:61-99`, `scripts/api/server.py:192-263`, `scripts/api/server.py:2399-2425`, `scripts/ib_sync.py:1535-1789`.

## Cold page query inventory

A signed-in, cold-cache `/portfolio` load can initiate **seven Turso statements** from the page shell before optional fallbacks: three portfolio statements, two orders statements, one watchlist statement, and one service-health statement. They are not all on the same critical path, but they share the same bounded eight-connection Next.js Turso pool (`web/lib/db.ts:44-62`, `:92-101`).

During RTH, one steady-state tab produces this source-derived cadence:

| Source | Poll cadence | Turso statements per poll | Steady-state statements/minute |
|---|---:|---:|---:|
| `/api/portfolio` | 30s | 3 after TTL expiry | 6 |
| `/api/orders` | 30s | 2 | 4 |
| `/api/service-health` | 60s | 1 after TTL expiry | 1 |
| Total | | | 11 |

This excludes one-time watchlist load, DB keepalive, optional price fallbacks, navigation/visibility refreshes, and any stale-triggered POST. Portfolio and service-health caches single-flight nearly simultaneous tabs; orders currently has no server-side coalescing. Unaligned tabs can therefore multiply the orders workload and share pool capacity with the blocking portfolio read.

## Production SQL and query-plan evidence

The read-only audit queried the configured production Turso database through two clients: a direct-source probe (five runs) and the bounded Hrana HTTP reader from the development Mac (three runs). The latter includes its own network/TLS behavior. These are evidence of query behavior, not VPS route TTFB. The Next.js server uses a warm, bounded Undici pool, so do not transplant either series into a production latency budget.

Audit state: schema migration 57; `data/replica.db` absent; direct-to-cloud remains the default (`web/lib/db.ts:1-29`, `scripts/db/client.py:11-16`).

### Q1: latest portfolio snapshot

```sql
SELECT taken_at, payload
FROM portfolio_snapshots
ORDER BY taken_at DESC
LIMIT 1;
```

Plan:

```text
SCAN portfolio_snapshots USING INDEX sqlite_autoindex_portfolio_snapshots_1
```

Evidence:

- 1 row.
- Stored payload: 11,194 bytes at `2026-08-25T21:50:54.972647Z`.
- Direct-source samples: 24.5-112.4ms across five runs.
- Bounded Hrana development-Mac samples: 339.6ms, 143.6ms, 274.5ms.
- The table primary key is `taken_at` (`scripts/db/migrations/0001_init.sql:78-81`), so SQLite can walk that index in descending order and stop after one row.

### Q2: portfolio `trade_log_dates`

```sql
SELECT
  json_extract(payload, '$.ticker') AS ticker,
  MAX(COALESCE(filled_at, json_extract(payload, '$.date'))) AS date
FROM journal
WHERE json_extract(payload, '$.ticker') IS NOT NULL
GROUP BY json_extract(payload, '$.ticker');
```

Plan:

```text
SCAN journal
USE TEMP B-TREE FOR GROUP BY
```

Evidence:

- 305 result rows; audit result serialization 10,916 bytes.
- Direct-source samples: 33.0-59.4ms across five runs.
- Bounded Hrana development-Mac samples: 158.1ms, 361.3ms, 152.9ms.
- No current index begins with `json_extract(payload, '$.ticker')`. Production journal indexes were the primary key, `idx_journal_filled`, and `idx_journal_effective_at`.

### Q3: portfolio `contract_open_dates`

```sql
SELECT
  json_extract(payload, '$.ticker')    AS ticker,
  json_extract(payload, '$.expiry')    AS expiry,
  json_extract(payload, '$.right')     AS opt_right,
  json_extract(payload, '$.strike')    AS strike,
  json_extract(payload, '$.action')    AS action,
  json_extract(payload, '$.contracts') AS contracts,
  COALESCE(filled_at, json_extract(payload, '$.date')) AS date
FROM journal
WHERE json_extract(payload, '$.right')  IS NOT NULL
  AND json_extract(payload, '$.strike') IS NOT NULL;
```

Plan:

```text
SCAN journal
```

Evidence:

- 1,184 result rows from 1,601 journal rows; audit result serialization 75,036 bytes.
- Matching option journal payloads total 570,986 bytes; all journal payloads total 765,022 bytes.
- Direct-source samples: 49.6-141.4ms across five runs.
- Bounded Hrana development-Mac samples: 384.3ms, 266.2ms, 1,414.2ms.
- JS then groups and sorts fills per contract (`web/lib/entryDates.ts:93-136`). Current output was 149 contract dates.
- The direct-source GET body was 25,565 bytes. The latest stored snapshot payload was 11,194 bytes, so the maps add roughly 14.4KB at the response level; a compact reserialization of the parsed snapshot alone was 10,244 bytes. Exact HTTP framing/compression was not measured in this backend-only pass.
- The predicate matched 74% of current journal rows, so a simple `right/strike` filter index would likely have weak selectivity. Removing the work from `/portfolio`, narrowing it to requested order contracts, or maintaining a derived map is higher leverage than indexing this broad scan alone.

### Q4 and Q5: global orders read

```sql
SELECT payload, updated_at
FROM open_orders
ORDER BY updated_at DESC;

SELECT payload, fill_time
FROM executed_orders
WHERE fill_time >= ?
ORDER BY fill_time DESC;
```

Plans:

```text
open_orders:      SCAN open_orders; USE TEMP B-TREE FOR ORDER BY
executed_orders:  SEARCH executed_orders USING INDEX executed_orders_fill_time_idx (fill_time>?)
```

Evidence from the audit snapshot:

- Open orders: 3 rows / 2,469 serialized audit bytes / 368.9ms sample.
- Executed orders for the audit's fixed lookback: 88 rows / 65,478 serialized audit bytes / 147.8ms sample.
- Production code uses a moving 48-hour cutoff and runs the statements serially (`web/lib/orders/readOrdersFromDb.ts:18-21`, `:62-81`).
- The executed-order plan is appropriate. Adding `open_orders(updated_at DESC)` removes a sort but is low impact at three rows; parallel execution and short server coalescing matter more.

### Q6: user watchlist

```sql
SELECT id, symbol, sector, added_at
FROM user_watchlist
WHERE user_id = ?
ORDER BY added_at DESC;
```

Plan:

```text
SEARCH user_watchlist USING INDEX user_watchlist_user_date (user_id=?)
```

The index is correct (`scripts/db/migrations/0010_user_profiles_bookmarks_watchlist.sql:28-36`). The browser hook loads once per authenticated identity and retains the module-level result (`web/lib/useWatchlist.ts:20-35`, `:50-99`). This is not a primary optimization target.

### Q7: service health

```sql
SELECT service, state, last_attempt_started_at, last_attempt_finished_at,
       last_error, updated_at
FROM service_health
ORDER BY updated_at DESC;
```

Plan:

```text
SCAN service_health
USE TEMP B-TREE FOR ORDER BY
```

Evidence: 73 rows / 10,402 serialized audit bytes / 225.4ms sample. The route already has a three-second single-flight cache (`web/app/api/service-health/route.ts:27-49`, `:157-161`). An `updated_at` index would remove the small sort, but query frequency and table size make it lower priority than the portfolio and orders paths.

### IB-sync journal basis statement

Both `compute_open_basis_for_ticker` and `prior_net_qty_for_contract` execute this statement; the latter reruns it for each contract and filters the contract in Python:

```sql
SELECT payload, filled_at, written_at
FROM journal
WHERE UPPER(COALESCE(
  json_extract(payload, '$.ticker'),
  json_extract(payload, '$.symbol'),
  ''
)) = ?
ORDER BY COALESCE(filled_at, written_at) ASC, written_at ASC;
```

Production plan and one current-ticker audit sample:

```text
SCAN journal USING INDEX idx_journal_effective_at
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
6 result rows; 115.9ms audit sample
```

`idx_journal_effective_at` begins with `COALESCE(filled_at, written_at)`, so it helps ordering but cannot seek on the JSON ticker expression (`scripts/db/migrations/0025_journal_effective_at_index.sql:1-11`). The Python loop makes the repeated transport and repeated journal walk the concern, not the six returned rows.

## Cache, timeout, and fallback matrix

| Path | Authority | Current cache/coalescing | Bounds | Required cache posture |
|---|---|---|---|---|
| `GET /api/portfolio` | Latest Turso snapshot + journal-derived maps | snapshot 3s TTL / 60s stale-on-error; maps 10s; per-key single-flight | DB transport 2.75s, DB caller 3s, browser 12s | Keep `force-dynamic`, client `no-store`, response `no-store`; in-process TTL is safe when bounded and warning provenance is preserved. |
| `POST /api/portfolio` | Live IB payload, Turso fallback | FastAPI same-key single-flight + 30s success reuse | child 30s < Next 35s < browser 42s | Never HTTP-cache. Existing deadline ordering is correct. |
| `GET /api/orders` | Turso `open_orders` + `executed_orders` | none server-side | each DB statement 2.75/3s; browser 12s | Keep dynamic/no-store. A 1-3s in-process single-flight cache is safe for read coalescing if writes/POST invalidate it. |
| `GET /api/watchlist` | Per-user Turso state | browser module cache by user | DB 2.75/3s | Keep dynamic/no-store; any server cache must key by user and invalidate on mutations. Not needed first. |
| `GET /api/service-health` | Live Turso health rows | 3s single-flight | DB 2.75/3s; browser 10s | Keep dynamic/no-store; current server cache is appropriate. |
| `GET /api/flex-token` | `data/flex_token_config.json` | framework behavior only | browser 10s | Disk-backed live route must add `force-dynamic`; client must use `cache: "no-store"`. A tiny in-process TTL can still avoid repeated file parsing without enabling HTTP/framework caching. |
| `POST /api/previous-close` | IB snapshot, then UW, then Yahoo | per-session module cache for successful closes | ticket 750ms; IB snapshot 3s; UW/Yahoo 5s each | Daily successful-result cache is appropriate. Do not cache failures for the day; they retry. Not an initial portfolio-data blocker. |
| `/api/ib/ws-ticket` | Single-use, 30s FastAPI memory ticket | none | client ticket request 8s | Never cache or reuse: tickets are single-use (`scripts/api/ws_ticket.py:17-45`). |
| `/edge-health/status`, `/api/admin/health` | Live daemon/FastAPI state | none | status poll request bound in provider; Next health proxy 10s | Keep no-store. |
| `/api/performance` | Turso/disk performance snapshot | not requested by `/portfolio` | n/a here | No change for portfolio load. |

The database timeout stack is already ordered correctly: hard Undici transport abort at 2,750ms precedes the 3,000ms `dbExecute` Promise bound (`web/lib/db.ts:60-62`, `:153-188`, `web/lib/dbExecute.ts:25-60`). This releases the occupied socket before the caller gives up. Do not raise these deadlines to mask query work, and do not attribute the current scans to pool reset behavior without correlated pool statistics.

## Prioritized issues and recommendations

### Priority 1: remove orders-only journal maps from the default portfolio payload

**Evidence:** `trade_log_dates` and `contract_open_dates` are created on every portfolio response (`web/app/api/portfolio/route.ts:166-177`, also POST at `:251-276`) but their only production consumer is `/orders` (`web/components/WorkspaceSections.tsx:3697-3702`). The two scans return 305 + 1,184 rows and block `usePortfolio` from receiving its snapshot.

Recommended design:

1. Make `GET /api/portfolio` snapshot-only by default.
2. Move order-share entry dates to a dedicated authenticated endpoint or an explicit `include=entry-dates` request used only by `OrdersSections`.
3. Narrow contract reconstruction to the executed contracts for the rendered session/page, rather than every option row in the journal.
4. Cache that derived result for 60 seconds or key it by a journal revision/watermark; it is not live mark data.
5. Preserve the default portfolio route's `force-dynamic` and client/response `no-store` contracts. The optimization is server work elimination, not stale HTTP caching.

Acceptance evidence for an implementation: a cold `/api/portfolio` request issues exactly Q1, returns the snapshot contract, and does not reference `FROM journal`; `/orders` share-card regressions continue to pass from the lazy entry-date path.

### Priority 2: coalesce and parallelize the global orders read

**Evidence:** `/portfolio` mounts `useOrders` and performs the initial read even outside market hours. During RTH it polls every 30 seconds. The two Turso statements are independent but serial and have no cross-request cache.

Recommended design:

1. Run `open_orders` and `executed_orders` with `Promise.all` after the direct-cloud `syncDb()` no-op.
2. Wrap the combined result in a 2-second `cachedReadResult` single-flight cache. Invalidate it after orders POST/cancel/modify/place transitions handled by the same Next process. A two-second cap preserves operational freshness while collapsing simultaneous tabs.
3. Consider a page-scoped orders mode for `/portfolio`: open-order symbols plus today's realized P&L are the shell consumers (`web/components/WorkspaceShell.tsx:109-143`, `:324-329`). The full orders payload and UI-only history can remain for `/orders`.
4. If the table is expected to grow beyond a handful of working orders, add `CREATE INDEX ... ON open_orders(updated_at DESC)` and verify the temp B-tree disappears. At the current three rows, this is not the first change.

Acceptance evidence: simultaneous `/api/orders` GETs share one pair of DB statements; the two statements overlap; existing ET-day and stale-DAY filtering tests stay green.

### Priority 3: align portfolio caching with the 60-second writer

**Evidence:** 30-second client polling always misses 3-second/10-second caches in a single tab, while the authoritative portfolio producer fires every 60 seconds RTH.

Recommended design after Priority 1:

- Raise `portfolio:snapshot` from 3 seconds to 15 seconds, retaining 60-second stale-on-error and warning headers. This bounds added in-process staleness to 15 seconds while coalescing unaligned tabs better.
- Keep the browser poll at 30 seconds initially so it still observes a 60-second writer quickly. Measure before changing cadence.
- If entry-date derivation remains request-time, use at least a 60-second TTL and restrict it to `/orders`; a 10-second TTL has no benefit against a 30-second poll.
- Add a response `Server-Timing` field for snapshot DB, entry-date DB, and JSON assembly before choosing a larger cache window.

### Priority 4: batch IB-sync journal reads and add a seekable ticker index

**Evidence:** the sync loops serially over tickers and contracts, repeating the same ticker-filtered statement. The current plan scans the effective-time index and still uses a temp B-tree for the last order term.

Recommended design:

1. Fetch journal rows for all current option tickers once, then compute open basis and per-contract net quantity in one in-memory pass. At current scale, one bounded read is preferable to up to 28 repeated reads.
2. If per-ticker reads remain, add and validate an expression index shaped to the actual predicate and full order:

   ```sql
   CREATE INDEX IF NOT EXISTS idx_journal_ticker_effective_v2
   ON journal (
     UPPER(COALESCE(
       json_extract(payload, '$.ticker'),
       json_extract(payload, '$.symbol'),
       ''
     )),
     COALESCE(filled_at, written_at),
     written_at
   );
   ```

3. Run production `EXPLAIN QUERY PLAN` before and after the migration; require a ticker seek and no temp B-tree. Do not add the index based only on theory.
4. Longer term, normalize ticker/contract/effective-time columns or maintain a derived current-contract state table at the journal writer. That removes repeated JSON extraction, but it is a schema/data-contract project and should follow the surgical batching fix.

This sync work does not block the default GET, but it matters when a stale render fires an automatic POST and for the every-minute producer. Existing FastAPI single-flight/30-second reuse should remain (`scripts/api/server.py:192-242`).

### Priority 5: stop optional shell telemetry from competing with first portfolio data

- Keep `/api/service-health` and `/api/flex-token` non-blocking in the footer; they already do not gate `PortfolioSections`.
- Correct `/api/flex-token` to the disk-backed route contract (`dynamic = "force-dynamic"`, client `cache: "no-store"`). This is a freshness correction, not a latency claim.
- The two relay sockets each mint a ticket because tickets are single-use. Consolidating status and price subscriptions onto one browser relay connection could remove one Next-to-FastAPI ticket round trip and one socket, but that is an architectural follow-up, not the first portfolio fix.
- Add a browser abort to `/api/previous-close` consistent with its server fallback budget. It can wait for the 3-second IB snapshot and then 5-second fallbacks, but it begins only after a live price lacks a close and should never gate the base snapshot.

## Existing safeguards to preserve

- `GET /api/portfolio` must remain a snapshot read and must never call IB; after Priority 1, "snapshot-only" should also mean no journal-derived maps on its default path.
- Direct-to-cloud is the default; `data/replica.db` was absent. Do not reintroduce the retired replica as a performance fix.
- The Next DB pool is capped at eight connections, reaps idle sockets, keeps one connection warm in production, and aborts transport before the route's caller timeout (`web/lib/db.ts:44-83`, `web/lib/dbKeepAlive.ts:3-9`, `web/instrumentation.ts:1-15`).
- Snapshot stale-on-error is bounded and surfaces `X-Sync-Warning`; do not turn stale data into silent success (`web/app/api/portfolio/route.ts:200-227`).
- IB sync deadline order is sound: child 30s, Next/FastAPI call 35s, browser 42s. The autonomous wrapper also uses a 35-second curl bound (`scripts/run_portfolio_refresh.sh:130-151`).
- The IB sync coordinator shields shared work from disconnected callers and coalesces concurrent requests (`scripts/api/server.py:192-236`).
- `force-dynamic`, `cache: "no-store"`, and explicit no-store response headers remain required for live DB/disk routes. In-process coalescing does not relax that network cache contract.

## Verification and measurement gaps

- Read-only production `EXPLAIN QUERY PLAN`, row counts, payload sizes, and repeated query samples are included above.
- Chrome DevTools MCP was unavailable in this agent session, so no browser waterfall or Core Web Vitals numbers are claimed here. The parallel browser trace should supply request start/end timing, TTFB, and render milestones.
- No production VPS journal was available locally. `PhaseTimer` already emits `ib_hot_path_timing` with connect/qualify/sleep/done marks (`scripts/clients/ib_timing.py:16-53`); collect those logs before assigning wall time among IB, journal basis, and post-sync writes.
- No local server was started, avoiding build/cache mutations. Exact compressed HTTP payload bytes and route TTFB should come from the browser/network pass.

## Implementation order for the parent report

1. Split entry-date maps out of the default portfolio response.
2. Parallelize and 2-second single-flight cache `/api/orders` reads.
3. Raise portfolio snapshot cache to 15 seconds and measure `Server-Timing`.
4. Batch IB-sync journal basis reads; then verify whether the expression index is still needed.
5. Re-profile `/portfolio` with one tab and five staggered tabs, during RTH and closed market, before considering broader pool or timeout changes.
