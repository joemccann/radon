# TradingView integration

Two independent rails. **Rail A** (Phase 1 below) receives alert fires by webhook and
needs no MCP. **Rail B** (Phase 3) is a Radon-owned MCP client for research data.
Cursor's broken `cursor://` OAuth blocks neither: Radon owns its own client.

Operator facts confirmed 2026-09-15: TradingView **Ultimate** (2000 active alerts,
open-ended expiry, MCP included) with TOTP 2FA, which is what unlocks the webhook URL
field on an alert.

## Platform constraints that shape the design

| Constraint | Consequence |
|---|---|
| 3s request timeout | the handler does one insert and returns; no notification or enrichment inline |
| **No retries** on a failed delivery | a 4xx/5xx after auth loses the fire forever, so only auth failures may reject |
| **No request signing** (no HMAC) | the only secrets are what we put in the URL and the body |
| **No event id** in the payload | Clerk-style idempotency is impossible; the ledger is append-only |
| `text/plain` content-type when the message is not valid JSON | the parser must accept both |
| Four fixed sender IPs | `52.89.214.238`, `34.212.75.30`, `54.218.53.128`, `52.32.178.7` (defense in depth, not authentication: TradingView may change them) |
| MCP alerts support **price conditions only** | indicator and Pine alerts stay hand-made in the TradingView UI |
| ~100 MCP requests/min per user, shared with every other client | one cached server-side client, never a per-pageview call |

Repo constraints that shape it just as much:

- `web/middleware.ts` returns before every limiter for a public route, so the endpoint
  has **no rate limit and no body cap** unless we add them.
- `web/lib/boundedShutdown.ts` force-exits 10s after SIGTERM, there is no
  `after()`/`waitUntil` anywhere in `web/app/`, and `spawn()` from Next is banned
  (root `CLAUDE.md`). **Nothing may run after the response.**
- The webhook-directory pin (`web/tests/middleware-share-allowlist.test.ts`) is exact
  match in both directions: any file under `app/api/webhooks/` is published publicly.

---

## Phase 1 — alert ingress (ship first)

**Operator decisions, 2026-09-15:** Phase 1 before any MCP work; destination is the
Radon log **plus a digested Pushover**, never one push per fire; MCP stays read-only.

### Endpoint

`POST /api/webhooks/tradingview/[token]`

- `export const radonCapability = "internal"` (chat must never call it),
  `runtime = "nodejs"`, `dynamic = "force-dynamic"`, every response through
  `setNoStoreResponseHeaders`.
- `[token]` is a 32-byte URL-safe random path secret, `TV_WEBHOOK_PATH_TOKEN`.
- The body carries `TV_WEBHOOK_SECRET`. Both compared in **constant time**.
- Rotation: both env vars accept a comma-separated pair so old and new are valid during
  an overlap window; alerts are re-pointed in bulk later via MCP `update_alert`.

### Handler order (the whole design)

```text
on(POST)
  1. path token or body secret mismatch     -> 401, write nothing      (fail closed)
  2. Content-Length or read length > 16 KiB -> 413, write nothing
  3. INSERT raw body + received_at + source_ip        <- BEFORE any parse
  4. parse application/json, else text/plain
       success -> fill symbol, exchange, price, interval, alert_name, bar_time, sent_at
       failure -> leave parsed columns NULL, keep the raw row
  5. return 200                          (never 4xx/5xx past step 1: TradingView never retries)
```

No Pushover, no FastAPI call, no symbol resolution inside the handler.

### Storage

Migration `scripts/db/migrations/00NN_tv_alert_events.sql`:

```sql
CREATE TABLE IF NOT EXISTS tv_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    source_ip TEXT,
    raw_body TEXT NOT NULL,
    symbol TEXT, exchange TEXT, ticker TEXT,
    price REAL, interval TEXT, alert_name TEXT,
    bar_time TEXT, sent_at TEXT,
    parse_error TEXT,
    processed_at TEXT,
    digest_sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tv_alert_events_unprocessed ON tv_alert_events (processed_at, id);
CREATE INDEX IF NOT EXISTS idx_tv_alert_events_received_desc ON tv_alert_events (received_at DESC);
```

- Append-only. `ticker` is the resolved Radon symbol, NULL when mapping fails.
- **Dedupe is deliberately weak**: only an exact repeat of
  `(alert_name, symbol, interval, bar_time, price)` inside 5s is marked duplicate. A
  genuine second fire in the same bar is indistinguishable from a network echo, and
  dropping it silently is worse than keeping it.
- **Retention**: the drain job prunes rows older than 180 days. Do not copy
  `demo_webhook_events`, which has no prune and grows unbounded.
- Writes go through the `dbExecute` chokepoint
  (`web/tests/db-execute-chokepoint-contract.test.ts`).

### Drain job

`scripts/tv_alerts_drain.py` plus `cloud/services/radon-tv-alerts.{service,timer}`,
every 5 minutes:

1. Read unprocessed rows on an id cursor (Hrana I/O bounding rule).
2. Resolve `NASDAQ:AAPL` and `CME_MINI:ES1!` style symbols to Radon tickers; leave NULL
   and count failures rather than guessing.
3. **One digest Pushover per cycle**: `7 TradingView alerts: NVDA x3, SPX x2, ES1! x2`
   with a link to the inbox. Never one push per fire. Send nothing when the cycle found
   no new rows.
4. `record_service_health("tv-alerts-drain", "ok")` every cycle, so the heartbeat
   belongs to the drain and not the receiver: a webhook with no fires is dormant, not
   down (`feedback_watchdog_dormant_no_row`).
5. Stamp `processed_at` and `digest_sent_at`.

### Edge

`cloud/caddy/Caddyfile`, matched on the webhook path, modeled on the existing
`handle /mcp*` block:

- `remote_ip` allowlist of the four TradingView addresses.
- `request_body max_size 16KB`.
- Pinned in `cloud/tests/test_caddyfile.py`. A TradingView IP change shows up as a spike
  in refused requests, which the drain job reports.

### Alert message template

```json
{
  "secret": "…",
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "price": "{{close}}",
  "interval": "{{interval}}",
  "alert": "{{alert_name}}",
  "bar_time": "{{time}}",
  "sent_at": "{{timenow}}"
}
```

Every placeholder is **quoted**: an unquoted `{{close}}` produces invalid JSON on an
empty value, and TradingView then switches the content-type to `text/plain`. Both
timestamps are kept: `bar_time` is what dedupe keys on, `sent_at` measures delivery lag.

### Secrets

`TV_WEBHOOK_PATH_TOKEN` and `TV_WEBHOOK_SECRET` live in `/etc/radon/env` (production,
read by `radon-nextjs.service`) and `web/.env` locally. Never `NEXT_PUBLIC_*`, never the
encrypted credential store (FastAPI exports that; Next cannot read it). Single-quote any
value containing `$`.

### Required pins (CI fails without every one)

- `web/middleware.ts` `PUBLIC_WEBHOOK_API_ROUTES` gains `/api/webhooks/tradingview`
- `web/tests/route-local-authz-matrix.test.ts` `PINNED_ELSEWHERE_ROUTES` gains `webhooks/tradingview`
- `web/tests/assistant-catalog-pin.test.ts` gains `"webhooks/tradingview": "internal"`
- `web/lib/demo/rateTier.ts` needs nothing: the `webhooks` segment is already allowlisted
- `cloud/config/installed-units.sha256`, `cloud/scripts/setup-vps.sh` and
  `cloud/tests/test_systemd_services.py` for the new unit pair
- `web/lib/serviceHealthWindows.ts` and `scripts/watchdog/services.py` for `tv-alerts-drain`
- `docs/operations.md`, `docs/cloud-services.md` and this file for the docs contract

### Definition of done

- Red first, then green: a wire-level test posts to the full path with the correct token
  and asserts the exact row written; paired tests assert **nothing is written** for a
  wrong path token, a wrong body secret, and an oversized body.
- A `text/plain` body and a malformed body both persist as raw rows and return 200.
- Drain test: N rows in, one digest out, `processed_at` stamped, health row written.
- Prune test at the 180-day boundary.
- `cloud/tests/test_caddyfile.py` pins the IP allowlist and the body cap.
- Contract test: the route imports nothing from `web/lib/order/**`. **No order
  placement and no broker routing, ever** — enforced in code, not prose.
- One live smoke alert fired from a chart, end to end, evidence in the PR.

---

## Phase 2 — alert lifecycle

Alerts are created in the TradingView UI against the Phase 1 URL. Ultimate's open-ended
expiry means they do not silently die after two months. Once Rail B exists,
`update_alert` re-points them in bulk (this is what makes secret rotation practical) and
`get_alerts_log` reconciles fires against `tv_alert_events` — the only way to detect a
delivery TradingView dropped, since it never retries.

## Phase 3 — MCP client (read-only)

Copy `scripts/clients/robinhood_client.py`, which already solves this exact problem:
OAuth 2.1 with PKCE over streamable HTTP, a 0600 token file with mandatory refresh, an
enforced read-tool allowlist, and a clean no-op when unconfigured. Python, not Next:
`mcp==1.28.1` is already pinned in `requirements.txt` and `web/package.json` has no MCP
SDK. The OAuth redirect must be on **`app.radon.run`** (Caddy); `radon.run` is the
Vercel marketing site.

Ladder placement, per the operator review of 2026-09-15:

- **TradingView is primary where it fills a gap**: economic and earnings calendars,
  fundamentals and financial history, filings and transcripts, cross-asset screener,
  watchlists, FX and crypto coverage, per-ticker news.
- **TradingView first, Unusual Whales second** for analyst *consensus* and price
  targets. UW's shared daily budget is needed for the flow endpoints only UW can serve,
  and TradingView's limit is its own. UW keeps analyst *rating-change events*
  (`/screener/analysts`: firm, analyst, upgrade or downgrade, target, timestamp), which
  TradingView has no equivalent for.
- **TradingView below the official feeds and above Yahoo** for any price series:
  IB > Robinhood > UW > Cboe/Treasury/FINRA > TradingView > Yahoo.
- **Never** for greeks, implied vol, option chains or open interest, dark pool, sweeps,
  GEX, depth, or execution. TradingView serves none of it, and no Gate 1-3 input may
  come from TradingView.

News: TradingView serves the per-ticker slot Radon has empty today. The Market Ear
remains the curated macro newsfeed. Store ids, links and short snippets only;
TradingView content must never reach the public share routes.

Watchlists: `list_watchlists`, `get_watchlist` and `get_active_watchlist` read the
operator's real lists once the token exists. This is a single-operator integration like
Robinhood, not a per-user "connect your account" flow. Start read-only: the write tools
can delete or overwrite a list, so sync is opt-in per direction.

## Non-goals

Replacing IB, UW, MenthorQ or the newsfeed as primary sources; executing orders from a
webhook; hosting Pine scripts; fixing Cursor's `cursor://` OAuth.
