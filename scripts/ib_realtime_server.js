#!/usr/bin/env node

/**
 * Interactive Brokers Real-Time Price Server (Node.js)
 *
 * This is a direct replacement for the Python websocket server.
 */

import dotenv from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
dotenv.config({ path: resolve(__dirname, "../.env") });
// .env.ib-mode (managed by scripts/ib mode) overlays after .env so its
// IB_GATEWAY_MODE/HOST values win — single switch, no .env rewriting.
dotenv.config({ path: resolve(__dirname, "../.env.ib-mode"), override: true });

import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import net from "node:net";
import { execFile } from "node:child_process";
import { WebSocketServer } from "ws";
import { IBApi, EventName, SecType, OptionType, TickByTickDataType } from "@stoqey/ib";
import { classifyIBConnectionError } from "./ib_connection_status.js";
import {
  createPriceData,
  createFundamentalsData,
  parseFundamentalRatios,
  updatePriceFromTickPrice,
  updatePriceFromTickSize,
} from "./ib_tick_handler.js";
import { LRUCache } from "./lib/lru-cache.js";
import { RateLimiter } from "./lib/rate-limiter.js";
import {
  buildRelayHealthDetail,
  decideHealthWrite,
  farmStateAfterIdleDrain,
  isFarmStateCode,
  nextFarmStateCode,
  summarizeSubscriptionFreshness,
  shouldRequestGatewayRestart,
  STALE_DATA_THRESHOLD_MS,
  STALE_CHECK_INTERVAL_MS,
} from "./lib/staleDataMachine.js";
import { isMarketOpen } from "./lib/marketCalendar.js";
import {
  resolveRelaySecurityConfig,
  resolveUpgradeTarget,
  shouldSkipTicketValidation,
} from "./lib/wsTrust.js";
import { applyDepthOp } from "./lib/depthLadder.js";
import { planDepthAdmission } from "./lib/depthBudget.js";
import { createReconnectGate } from "./lib/reconnectGate.js";
import {
  decideSend,
  HARD_CAP_BYTES,
  settleIncrementalBatch,
} from "./lib/sendBackpressure.js";
import {
  etDateYYYYMMDD,
  isResolvedFutureStale,
  pickNearestExpiry,
  shouldAttemptReresolve,
} from "./lib/futuresRollover.js";
import { createForwardSubRegistry } from "./lib/forwardSubRegistry.js";

const DEFAULT_WS_PORT = 8765;
const DEFAULT_IB_HOST = process.env.IB_GATEWAY_HOST || "127.0.0.1";
const DEFAULT_IB_PORT = parseInt(process.env.IB_GATEWAY_PORT || "4001", 10);
const RECONNECT_MS = 5000;
const SNAPSHOT_TIMEOUT_MS = 5000;
const TICKET_VALIDATION_TIMEOUT_MS = 3000;

/* ─── Keep-Alive Ping/Pong ─────────────────────────────────────────────── */
const PING_INTERVAL_MS = 30_000;
const PONG_TIMEOUT_MS = 65_000; // 30s * 2 + 5s grace

/* ─── systemd watchdog liveness (DUR-17) ───────────────────────────────────
 * On 2026-06-24 the relay served no data for 38 min while ALIVE (NRestarts=0):
 * its single Node event loop wedged, so every in-process self-heal (stale-tick
 * ladder, ping reaper, batch flush, the service_health heartbeat) froze at once
 * and nothing restarted it. An in-process timer cannot rescue an in-process
 * stall — the liveness signal must be external. Under systemd Type=notify +
 * WatchdogSec we emit an UNCONDITIONAL WATCHDOG=1 keep-alive; a wedged loop
 * stops emitting and systemd restarts ONLY this leaf unit (no gateway touch, no
 * 2FA — the same safe action an operator takes). Because the ping proves only
 * "the loop is turning" (never tick-presence), a legitimately idle or farm-down
 * relay keeps pinging and is never killed; farm-down stays owned by the existing
 * lock-held /ib/restart escalation. Node's stdlib cannot open an AF_UNIX
 * SOCK_DGRAM (node:dgram is UDP-only), so the ping is delegated to
 * `systemd-notify` (hence the unit needs NotifyAccess=all — the child PID
 * sends). Absent under scripts/cloud.sh / local dev → the whole feature no-ops. */
const SD_NOTIFY_SOCKET = process.env.NOTIFY_SOCKET || "";
const SD_WATCHDOG_USEC = parseInt(process.env.WATCHDOG_USEC || "0", 10);
const sdWatchdogEnabled = Boolean(SD_NOTIFY_SOCKET && SD_WATCHDOG_USEC > 0);

function sdNotify(state) {
  if (!sdWatchdogEnabled) return;
  try {
    // Fire-and-forget; the callback swallows ENOENT / send errors so a watchdog
    // hiccup can never throw or block the event loop.
    execFile("systemd-notify", [state], () => {});
  } catch {
    /* never propagate into the loop */
  }
}

function parseArgs(argv) {
  const args = {
    port: DEFAULT_WS_PORT,
    ibHost: DEFAULT_IB_HOST,
    ibPort: DEFAULT_IB_PORT,
    ibClientId: 10,  // RELAY_ID_RANGE start (10-19)
    verbose: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") {
      const value = Number.parseInt(argv[i + 1], 10);
      if (Number.isInteger(value)) {
        args.port = value;
      }
      i += 1;
      continue;
    }
    if (arg === "--ib-host") {
      args.ibHost = argv[i + 1] ?? DEFAULT_IB_HOST;
      i += 1;
      continue;
    }
    if (arg === "--ib-port") {
      const value = Number.parseInt(argv[i + 1], 10);
      if (Number.isInteger(value)) {
        args.ibPort = value;
      }
      i += 1;
      continue;
    }
    if (arg === "--client-id") {
      const value = Number.parseInt(argv[i + 1], 10);
      if (Number.isInteger(value)) {
        args.ibClientId = value;
      }
      i += 1;
      continue;
    }
    if (arg === "--verbose" || arg === "-v") {
      args.verbose = true;
    }
  }

  return args;
}

function normalizeSymbols(raw) {
  return raw
    .map((symbol) => String(symbol).trim().toUpperCase())
    .filter((symbol) => symbol.length > 0);
}

/** Build composite key for an option contract: SYMBOL_YYYYMMDD_STRIKE_RIGHT */
function optionKey(c) {
  return `${c.symbol}_${c.expiry}_${c.strike}_${c.right}`;
}

/**
 * Validate and normalize a raw contracts array from client messages.
 * Each contract must have symbol (string), expiry (8-digit string),
 * strike (positive number), and right ("C" or "P").
 */
function normalizeContracts(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((c) => {
      if (typeof c !== "object" || c === null) return null;
      const symbol = typeof c.symbol === "string" ? c.symbol.trim().toUpperCase() : null;
      const expiry = typeof c.expiry === "string" ? c.expiry.trim() : null;
      const strike = typeof c.strike === "number" && Number.isFinite(c.strike) && c.strike > 0 ? c.strike : null;
      const right = c.right === "C" || c.right === "P" ? c.right : null;
      if (!symbol || !expiry || expiry.length !== 8 || !strike || !right) return null;
      return { symbol, expiry, strike, right };
    })
    .filter(Boolean);
}

/**
 * Validate and normalize index contract descriptors from client messages.
 * Each must have symbol (string) and exchange (string, e.g. "CBOE").
 */
function normalizeIndexes(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((c) => {
      if (typeof c !== "object" || c === null) return null;
      const symbol = typeof c.symbol === "string" ? c.symbol.trim().toUpperCase() : null;
      const exchange = typeof c.exchange === "string" ? c.exchange.trim().toUpperCase() : null;
      if (!symbol || !exchange) return null;
      return { symbol, exchange };
    })
    .filter(Boolean);
}

function nowIso() {
  return new Date().toISOString();
}

function parseActionMessage(raw) {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;

  const payload = raw;
  if (typeof payload.action !== "string") {
    return null;
  }

  const action = payload.action.trim().toLowerCase();
  if (!action) {
    return null;
  }

  const symbols = Array.isArray(payload.symbols) ? normalizeSymbols(payload.symbols) : [];
  const contracts = Array.isArray(payload.contracts) ? normalizeContracts(payload.contracts) : [];
  const indexes = Array.isArray(payload.indexes) ? normalizeIndexes(payload.indexes) : [];
  return { action, symbols, contracts, indexes };
}

const cli = parseArgs(process.argv.slice(2));
const relaySecurity = resolveRelaySecurityConfig(process.env);
if (relaySecurity.requireClerk && !relaySecurity.clerkConfigured) {
  console.error("WebSocket relay requires CLERK_JWKS_URL and CLERK_ISSUER");
  process.exit(78);
}
// Loopback is the secure default. Direct network access requires an explicit
// WS_BIND_HOST override and still passes through ticket validation.
const WS_HOST = relaySecurity.bindHost;
const wsUrl = `ws://${WS_HOST}:${cli.port}`;

function verbose(...args) {
  if (cli.verbose) console.log(`\x1b[90m[verbose]\x1b[0m`, ...args);
}

const IB_CLIENT_ID_POOL = [cli.ibClientId, cli.ibClientId + 1, cli.ibClientId + 2];
let activeClientIdIndex = 0;

let ib = createIBClient(IB_CLIENT_ID_POOL[0]);

function createIBClient(clientId) {
  return new IBApi({
    host: cli.ibHost,
    port: cli.ibPort,
    clientId,
  });
}

/* ─── Contract builders ────────────────────────────────────────────────────
 * @stoqey/ib has no ib.contract.* factory helpers — contracts are plain
 * objects. These mirror the field shapes the old `ib` lib produced
 * (expiry → lastTradeDateOrContractMonth; right → OptionType), so every
 * downstream consumer (ensureSymbolState, optionKey, etc.) is unaffected.
 */
function stockContract(symbol, exchange = "SMART", currency = "USD") {
  return { symbol, secType: SecType.STK, exchange, currency };
}

function optionContract(symbol, expiry, strike, right, exchange = "SMART", currency = "USD") {
  return {
    symbol,
    secType: SecType.OPT,
    exchange,
    currency,
    lastTradeDateOrContractMonth: expiry,
    strike,
    right: right === "C" ? OptionType.Call : OptionType.Put,
    multiplier: "100",
  };
}

function indexContract(symbol, currency = "USD", exchange = "CBOE") {
  return { symbol, secType: SecType.IND, exchange, currency };
}

function futureContract(symbol, expiry, currency = "USD", exchange = "ONE") {
  return { symbol, secType: SecType.FUT, currency, exchange, lastTradeDateOrContractMonth: expiry };
}

async function isPortAvailable(host, port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.unref();

    probe.once("error", (error) => {
      if (error?.code === "EADDRINUSE") {
        resolve(false);
        return;
      }
      console.error(`Failed to probe websocket port ${host}:${port}:`, error?.message ?? String(error));
      resolve(false);
    });

    probe.once("listening", () => {
      probe.close(() => resolve(true));
    });

    probe.listen({ host, port, exclusive: true });
  });
}

if (!(await isPortAvailable(WS_HOST, cli.port))) {
  console.log(`WebSocket port already in use at ${wsUrl}; assuming an existing IB realtime server and skipping duplicate startup.`);
  console.log(`IB target ${cli.ibHost}:${cli.ibPort}`);
  process.exit(0);
}

// Create HTTP server for WebSocket upgrade with ticket validation
const TICKET_VALIDATE_URL = process.env.TICKET_VALIDATE_URL || "http://127.0.0.1:8321/ws-ticket/validate";

const httpServer = http.createServer((_req, res) => {
  res.writeHead(426, { "Content-Type": "text/plain" });
  res.end("WebSocket upgrade required");
});

const wss = new WebSocketServer({ noServer: true });

httpServer.on("upgrade", async (req, socket, head) => {
  // Skip ticket validation only for genuine loopback server-to-server calls.
  // Browser handshakes are never locally trusted, and missing Clerk config
  // fails closed unless a non-production process explicitly opts in. Trusting the peer
  // address alone was a full auth bypass: Caddy reverse-proxies /ws* to
  // localhost:8765, so every internet client appears as 127.0.0.1. The
  // forwarding-header check (mirrors scripts/api/auth.py:is_trusted_local_request)
  // forces every proxied/public connection through ticket validation.
  const remoteAddr = socket.remoteAddress || "";
  const skipTicket = shouldSkipTicketValidation({
    clerkConfigured: relaySecurity.clerkConfigured,
    allowUnauthenticatedDev: relaySecurity.allowUnauthenticatedDev,
    remoteAddr,
    headers: req.headers,
  });
  if (skipTicket) {
    wss.handleUpgrade(req, socket, head, (ws) => {
      wss.emit("connection", ws, req);
    });
    return;
  }

  try {
    const url = resolveUpgradeTarget(req);
    const ticket = url.searchParams.get("ticket");
    if (!ticket) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      return;
    }

    const res = await fetch(TICKET_VALIDATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket }),
      signal: AbortSignal.timeout(TICKET_VALIDATION_TIMEOUT_MS),
    });

    if (!res.ok) {
      verbose("WS ticket validation failed: " + res.status);
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      return;
    }

    wss.handleUpgrade(req, socket, head, (ws) => {
      wss.emit("connection", ws, req);
    });
  } catch (err) {
    verbose("WS ticket validation error: " + (err instanceof Error ? err.message : err));
    socket.write("HTTP/1.1 503 Service Unavailable\r\n\r\n");
    socket.destroy();
  }
});

httpServer.listen(cli.port, WS_HOST, () => {
  // Type=notify: tell systemd we're up only once the WS port is actually bound.
  sdNotify("READY=1");
});

// DUR-17: unconditional event-loop liveness heartbeat. Fire at half the systemd
// WatchdogSec deadline so a single missed tick never false-restarts. unref() so
// it never by itself keeps a dying process alive (the WS server holds the loop).
if (sdWatchdogEnabled) {
  const heartbeatMs = Math.max(5_000, Math.floor(SD_WATCHDOG_USEC / 1000 / 2));
  setInterval(() => sdNotify("WATCHDOG=1"), heartbeatMs).unref();
}

const clients = new Set();
const symbolSubscribers = new Map();
const clientSymbols = new Map();
const symbolStates = new Map();
const requestIdToSymbol = new Map();
const snapshotRequests = new Map();
const fundamentalsStore = new LRUCache(500); // symbol → FundamentalsData (LRU-capped)

/* ─── L2 Depth Channel (flag-gated) ────────────────────────────────────────
 * Entire feature is gated on RADON_DEPTH_ENABLED. When falsy, NO depth
 * handlers are registered, NO reqMktDepth tickets are opened, and the relay
 * behaves byte-for-byte as before. Default OFF.
 *
 * Depth budget: IB allows ~3 concurrent reqMktDepth tickets on a baseline
 * account, so we cap concurrency. Recycling is per-client (lib/depthBudget.js,
 * R-082): a subscribe may LRU-recycle only ladders its OWN session exclusively
 * holds; when other sessions hold the cap it is refused with reason
 * "depth-budget" instead of silently evicting their legs.
 *
 * NOTE on the @stoqey/ib surface: reqMktDepth(reqId, contract, numRows,
 * isSmartDepth) and cancelMktDepth(reqId, isSmartDepth) BOTH carry the
 * smart-depth flag — true for equity/option (SMART aggregation), false for
 * futures (single-venue native depth). The DepthBook also emits isSmartDepth
 * (= !isFutures) to match the web type. updateMktDepth emits
 * (id, position, operation, side, price, size) with NO marketMaker (futures /
 * single-venue); updateMktDepthL2 emits
 * (id, position, marketMaker, operation, side, price, size, isSmartDepth)
 * (equity/SMART) — the trailing isSmartDepth arg is @stoqey-specific.
 *
 * REALTIME: the main connection now requests realtime market data (type 1)
 * unconditionally at the ib "connected" handler (operator decision 2026-06-14)
 * — delayed-frozen (type 4) made the header futures strip and every watchlist
 * quote lag the market by ~15 min. Off-hours, IB holds the last live values so
 * closed-market reads still return last-known prices. A dedicated realtime
 * client per channel is no longer needed for correctness; depth simply shares
 * the already-realtime connection.
 */
const DEPTH_ENABLED = Boolean(process.env.RADON_DEPTH_ENABLED && process.env.RADON_DEPTH_ENABLED !== "0" && process.env.RADON_DEPTH_ENABLED !== "false");
const MAX_CONCURRENT_DEPTH = 3;
const DEPTH_NUM_ROWS_EQUITY = 10;
const DEPTH_NUM_ROWS_FUTURES = 10;
// Symbols treated as futures for depth purposes (single-venue native depth).
// NOTE: VIX futures use IB contract symbol "VIX" (exchange CFE), NOT the CBOE
// product code "VX" — Future(symbol:"VX") returns IB Error 200 (no security definition).
const DEPTH_FUTURES_SYMBOLS = new Set(["ES", "NQ", "RTY", "YM", "CL", "NG", "GC", "SI", "HG", "ZB", "ZN", "ZC", "ZS", "VIX"]);
// Futures root → native exchange. Front-month depth requires a qualified
// contract (conId), which we resolve via reqContractDetails on this exchange.
const FUTURES_ROOT_EXCHANGES = {
  ES: "CME", NQ: "CME", RTY: "CME", YM: "CME",
  CL: "NYMEX", NG: "NYMEX",
  GC: "COMEX", SI: "COMEX", HG: "COMEX",
  ZB: "CBOT", ZN: "CBOT", ZC: "CBOT", ZS: "CBOT",
  VIX: "CFE",
};
function futuresExchangeForRoot(root) {
  return FUTURES_ROOT_EXCHANGES[root] || "CME";
}
// Cash indices whose tradeable depth lives on a CME E-mini future. The Book tab
// subscribes depth under the INDEX symbol (key stays e.g. "SPX") but the relay
// resolves the front-month FUTURE (ES) for reqMktDepth. VIX is NOT here — its
// IB future symbol IS "VIX", so it lives directly in DEPTH_FUTURES_SYMBOLS.
const INDEX_FUTURE_ROOT = { SPX: "ES", NDX: "NQ", RUT: "RTY" };

// Indices whose OPTIONS are priced off the front-month future (the forward),
// not the cash spot — Black-Scholes must use the future as the underlying.
// When such an index L1 is subscribed, the relay also opens an L1 on the
// front-month future and copies its last/mid into prices[index].fwd so the
// client prices options off the tradeable forward, not the after-hours-frozen
// cash index. VIX only (VIX options settle into the VIX future).
const FORWARD_PRICED_INDICES = new Set(["VIX"]);
const FORWARD_KEY_PREFIX = "@fwd:"; // internal symbol-state key; never client-facing
const forwardKeyToIndex = new Map(); // "@fwd:VIX" or "@fwd:VIX:20260618" -> "VIX"
// Curve forward keys ("@fwd:VIX:<futExpiry>") -> Set of OPTION expiries (YYYYMMDD)
// to publish that future's price under in prices[index].fwdCurve. The relay owns
// the option->future matching so the client does a plain fwdCurve[legExpiry].
const fwdKeyToCurveExpiries = new Map();
/* Refcounted holds on the internal forward lines above (lib/forwardSubRegistry).
 * A forward is opened on demand by a client-facing subscription but lives under a
 * key no client ever names, so unsubscribeClientFromSymbol / disconnectClient —
 * both walk client-facing keys — never cancelled it: one leaked IB market-data
 * line per expiry ever subscribed. Holds are keyed by (client, client-facing
 * subject) so two strikes of one expiry, or two clients on one option, each hold
 * the shared line independently and only the last release cancels it. */
const forwardSubs = createForwardSubRegistry();
/* Curve holds ride the same registry under their own namespace, so releasing ONE
 * option expiry prunes just its fwdCurve entry instead of the whole future's set. */
const CURVE_HOLD_PREFIX = "@curve:";
const clientOwnerIds = new Map(); // client → stable id used in registry owner tokens
let nextClientOwnerId = 0;
const FUTURES_RESOLVE_TIMEOUT_MS = 6000;
// key → { depthTickerId, contract, kind, isFutures, ladders:{bid:Map,ask:Map}, focusedAt }
const symbolDepthStates = new Map();
const depthRequestIdToSymbol = new Map(); // depthTickerId → key
// Per-client depth buffer: Map<client, Map<symbol, DepthBook>>
const clientDepthBuffers = new Map();
const depthSubscribers = new Map(); // key → Set<client>
// Resolved front-month futures contracts, cached per root so we don't
// re-resolve on every subscribe. root → qualified Contract (with conId).
const resolvedFuturesContracts = new Map();
// Full non-expired futures chain per root (nearest-expiry first), for the
// per-expiry forward curve. root → Contract[].
const resolvedFuturesChain = new Map();
// Rollover bookkeeping (lib/futuresRollover.js): when the cached resolution was
// written, when we last ATTEMPTED a re-resolve, and which roots are mid-flight.
// Without these the cache lived forever and a rolled contract kept feeding
// prices[root].fwd / fwdCurve after it stopped trading.
const futuresResolvedAt = new Map(); // root → epoch ms of the cached resolution
const futuresResolveAttemptAt = new Map(); // root → epoch ms of the last attempt
const futuresResolveInFlight = new Set(); // roots with a background re-resolve open
// In-flight reqContractDetails resolutions: reqId → { root, resolve, timer, candidates }
const futuresResolveRequests = new Map();

/* ─── Time & Sales tape (flag-gated, rides the focused depth symbol) ─────────
 * On subscribe-depth we also open a reqTickByTickData(AllLast) stream for the
 * same contract; on unsubscribe-depth we cancel it. Trades land in a bounded
 * per-symbol ring buffer and broadcast as {type:"tape-batch"} on the same
 * 100ms flush as depth. tick-by-tick shares the depth line allowance, so the
 * existing focused-symbol-only model + 3-ticket cap keep us inside budget.
 *
 * @stoqey/ib surface (verified against dist/api/api.d.ts):
 *   reqTickByTickData(reqId, contract, tickType, numberOfTicks, ignoreSize)
 *   cancelTickByTickData(reqId)
 *   EventName.tickByTickAllLast: (reqId, tickType, time, price, size,
 *     tickAttribLast, exchange, specialConditions)
 * AllLast is realtime-only (no historical backfill) — start empty, fill forward.
 *
 * ORDERING CONVENTION: the ring buffer (and the emitted Trade[]) is
 * NEWEST-LAST — index 0 is the oldest retained print, the final element is the
 * most recent. classifyTicks consumes an ordered array (prior-tick test) and
 * reads forward, so chronological/newest-last matches the prior-tick semantics.
 */
const TAPE_RING_SIZE = 50;
const tapeRequestIdToSymbol = new Map(); // tapeTickerId → key
// key → { tapeTickerId, trades: Trade[] (newest-last, bounded TAPE_RING_SIZE) }
const symbolTapeStates = new Map();
const clientTapeBuffers = new Map(); // client → Map<symbol, Trade[]>

/* ─── Symbol Search Cache ─────────────────────────────────────────────── */
const SEARCH_CACHE_TTL_MS = 5 * 60 * 1000; // 5 min
const SEARCH_CACHE_MAX = 200;
const searchCache = new Map(); // pattern → { results, ts }
const searchRequestClients = new Map(); // reqId → { client, pattern }

/* ─── Option close price cache ─────────────────────────────────────────────
 * IB sends previous-close for option contracts only during market hours.
 * After hours, delayed-frozen data omits close. We persist close prices to
 * disk so they survive server restarts and are available after hours.
 * File: data/option_close_cache.json  { "AAOI_20260320_105_C": 16.5, ... }
 */
const CLOSE_CACHE_PATH = path.resolve(process.cwd(), "data", "option_close_cache.json");
const optionCloseCache = new Map(); // symbol → close price

function loadCloseCache() {
  try {
    if (fs.existsSync(CLOSE_CACHE_PATH)) {
      const raw = JSON.parse(fs.readFileSync(CLOSE_CACHE_PATH, "utf8"));
      for (const [key, val] of Object.entries(raw)) {
        if (typeof val === "number" && val > 0) optionCloseCache.set(key, val);
      }
      console.log(`Loaded ${optionCloseCache.size} cached option close prices`);
    }
  } catch (err) {
    console.warn("Failed to load option close cache:", err.message);
  }
}

let closeCacheDirty = false;
let closeCacheTimer = null;

function persistCloseCache() {
  if (!closeCacheDirty) return;
  closeCacheDirty = false;
  try {
    const dir = path.dirname(CLOSE_CACHE_PATH);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const obj = Object.fromEntries(optionCloseCache);
    fs.writeFileSync(CLOSE_CACHE_PATH, JSON.stringify(obj), "utf8");
    verbose(`Persisted ${optionCloseCache.size} option close prices`);
  } catch (err) {
    console.warn("Failed to persist option close cache:", err.message);
  }
}

function scheduleCloseCachePersist() {
  closeCacheDirty = true;
  if (closeCacheTimer) return;
  closeCacheTimer = setTimeout(() => {
    closeCacheTimer = null;
    persistCloseCache();
  }, 5000); // batch writes every 5s
}

function updateOptionCloseCache(symbol, closePrice) {
  if (!symbol.includes("_") || closePrice == null || closePrice <= 0) return;
  const existing = optionCloseCache.get(symbol);
  if (existing === closePrice) return;
  optionCloseCache.set(symbol, closePrice);
  scheduleCloseCachePersist();
}

function applyCachedClose(data) {
  if (data.close != null && data.close > 0) return; // already has close
  if (!data.symbol.includes("_")) return; // only for options
  const cached = optionCloseCache.get(data.symbol);
  if (cached != null) {
    data.close = cached;
  }
}

loadCloseCache();

/* ─── Keep-Alive State ──────────────────────────────────────────────────── */
const clientLastPong = new Map(); // client → timestamp (ms)
let pingIntervalTimer = null;

/* ─── Snapshot Rate Limiter ──────────────────────────────────────────────
 * IB allows ~100 snapshot requests/sec. We cap at 50 to leave headroom. */
const snapshotLimiter = new RateLimiter(50);

let ibConnected = false;
let shuttingDown = false;
const reconnectGate = createReconnectGate({ delayMs: RECONNECT_MS });
let ibClientGeneration = 0;
let nextRequestId = 1;
let statusBroadcastTick = null;
let ibConnectionIssue = null;

/* ─── Stale Data Detection ─────────────────────────────────────────────────
 * IB Gateway can enter a state where the TCP connection is alive but the
 * data plane stops delivering ticks. Detect this by tracking the last tick
 * timestamp: if we have active subscriptions during market hours but haven't
 * received a tick in STALE_DATA_THRESHOLD_MS, run the bounded recovery ladder.
 *
 * STALE_DATA_THRESHOLD_MS / STALE_CHECK_INTERVAL_MS / the K-cycle ladder +
 * escalation cooldown all live in lib/staleDataMachine.js — the decision is
 * pure; this layer wires the real IB socket, timer, and service_health write.
 */
let lastTickTimestamp = Date.now();
let staleCheckTimer = null;
let ibGatewayRestarting = false;
// Consecutive reconnect cycles this stale episode (reset when ticks resume).
let staleReconnectCycles = 0;
// Last IB market-data farm info code seen (2103/2104/2105/2106/2108/2158).
let lastFarmStateCode = null;
// Epoch ms of the last escalation alert — rate-limits the escalate action.
let lastEscalationAt = null;
// True once we've written the relay's service_health row to "error" so we
// only clear it (and reset counters) on the recovery edge.
let relayHealthInError = false;
// False until this process has written its first "ok" row. The in-memory
// error flag does NOT survive a restart, so a prior process that escalated
// (then got deploy-restarted before recovering) leaves a stale "error" row
// that recovery-from-error alone would never clear. We write "ok" once on the
// first healthy tick of each process. See feedback_service_health_heartbeat.
let relayHealthInitialized = false;
// Epoch ms of the last RTH tick heartbeat (DUR-16). 0 so the first healthy
// RTH stale-check cycle of each process writes immediately.
let lastTickHeartbeatAt = 0;

const RELAY_HEALTH_SERVICE = "ib-realtime-relay";

// FastAPI's lock-holding Gateway restart. Co-located on the VPS (same host as
// the relay), so loopback. Overridable for tests / non-standard layouts.
const IB_RESTART_URL = process.env.IB_RESTART_URL || "http://127.0.0.1:8321/ib/restart";
// The restart waits on the container coming back; keep the relay's fetch
// generous but bounded so a wedged API can't leak a hung request.
const IB_RESTART_TIMEOUT_MS = 90_000;

function isUSMarketHours() {
  // Holiday + early-close aware via lib/marketCalendar.js (IBKR cache → static
  // holiday table → weekday/time). A mid-week holiday like Juneteenth is no
  // longer treated as a trading day, so the stale-tick ladder stays idle
  // instead of churning reconnect/resubscribe (and escalating) all day.
  return isMarketOpen();
}

const GATEWAY_MODE = process.env.IB_GATEWAY_MODE || "docker";

/* Best-effort relay health write. The relay must never stall ticks on a DB
 * hiccup, so the writer import + call are dynamic + try/catch'd. The writer
 * is direct-to-cloud by default (embedded replica is opt-in only). */
async function writeRelayHealth(state, error) {
  try {
    const { recordServiceHealth } = await import("./db/writer.js");
    await recordServiceHealth(RELAY_HEALTH_SERVICE, state, error ? { error } : {});
  } catch (err) {
    console.warn(`\x1b[33m[stale-data] relay health write failed: ${String(err?.message ?? err)}\x1b[0m`);
  }
}

// Hand off to FastAPI's lock-holding Gateway restart. The market-data farm is
// dead while the IB API stays responsive (upstream alive), so the api-hang
// watchdog never sees this — the relay is the only detector. POST /ib/restart
// owns the 2FA push lock + restart backoff, so if a push is already in flight
// (e.g. a concurrent watchdog restart) or we're inside the backoff window the
// call no-ops rather than stacking a second 2FA push. Best-effort: never throw
// into the stale-check timer; fire-and-forget so the relay doesn't block.
async function requestGatewayRestart() {
  try {
    const res = await fetch(IB_RESTART_URL, {
      method: "POST",
      signal: AbortSignal.timeout(IB_RESTART_TIMEOUT_MS),
    });
    const body = await res.json().catch(() => ({}));
    console.log(
      `[stale-data] requested lock-held Gateway restart (HTTP ${res.status}): ` +
        `restarted=${body?.restarted ?? "?"} auth_state=${body?.auth_state ?? "?"}`,
    );
  } catch (err) {
    console.warn(
      `\x1b[33m[stale-data] Gateway restart request failed: ${String(err?.message ?? err)}\x1b[0m`,
    );
  }
}

// Re-issue reqMktData for every desired symbol without bouncing the socket.
// Used when a farm-OK signal says the connection is healthy but ticks lapsed.
function resubscribeAll() {
  console.log("[stale-data] farm connection OK — resubscribing market data without socket bounce");
  // Consume the farm-OK hint. If this resubscribe doesn't restore ticks, the
  // next stale cycle sees farmState=null and falls through to the bounded
  // reconnect ladder → escalate, rather than resubscribing forever in silence.
  lastFarmStateCode = null;
  cleanupSymbolStateForReconnect();
  if (DEPTH_ENABLED) cleanupDepthForReconnect();
  restoreSubscriptions();
  if (DEPTH_ENABLED) restoreDepthSubscriptions();
}

// Bounce the IB socket; the connected handler restores fresh subscriptions.
function reconnectIBSocket() {
  staleReconnectCycles += 1;
  if (GATEWAY_MODE === "cloud" || GATEWAY_MODE === "docker") {
    console.log(`[stale-data] ${GATEWAY_MODE} mode — disconnecting and scheduling reconnect (cycle ${staleReconnectCycles})`);
  } else {
    // Local launchd mode — do NOT restart IBC from the relay. Repeated local
    // restarts thrash the auth session and can trigger more 2FA prompts.
    console.log(`[stale-data] launchd mode — reconnecting IB socket only (cycle ${staleReconnectCycles}); manual IBC restart required if Gateway stays down`);
  }
  try { ib.disconnect(); } catch { /* ignore */ }
  scheduleReconnect();
}

// K reconnect cycles failed during RTH: the market-data farm is dead while the
// IB API stays responsive. Write the relay's service_health error row (so the
// outage is finally VISIBLE on the status surface) AND, for container-owned
// gateways, hand off to the lock-holding /ib/restart — the only path that can
// re-establish a dead farm, and the only actor that detects this case at all
// (the api-hang watchdog sees the API as healthy here). escalation is
// rate-limited to once per ESCALATION_COOLDOWN_MS and the restart endpoint
// self-gates on the 2FA lock + backoff, so this never stacks a push.
function escalateStaleData(elapsedMs, activeSubscriptions) {
  if (ibGatewayRestarting) return;
  ibGatewayRestarting = true;
  lastEscalationAt = Date.now();
  relayHealthInError = true;
  console.log("\x1b[31m[stale-data] escalating — relay market data dead through bounded reconnect ladder during RTH\x1b[0m");
  void writeRelayHealth("error", {
    message: `No ticks for ${Math.round(elapsedMs / 1000)}s with ${activeSubscriptions} active subscriptions after ${staleReconnectCycles} reconnect cycles during market hours`,
    farm_state: lastFarmStateCode,
  });
  if (shouldRequestGatewayRestart(GATEWAY_MODE)) {
    void requestGatewayRestart();
  }
  // Allow another reconnect ladder after the cooldown; the escalation itself
  // is independently rate-limited by lastEscalationAt in the decision core.
  setTimeout(() => { ibGatewayRestarting = false; }, 120_000);
}

// Ticks resumed — clear the error row + reset the ladder so a future episode
// starts fresh. Only writes "ok" if we had previously latched an error.
function onTicksRecovered() {
  if (staleReconnectCycles > 0 || relayHealthInError) {
    staleReconnectCycles = 0;
    if (relayHealthInError) {
      relayHealthInError = false;
      console.log("\x1b[32m[stale-data] ticks resumed — clearing relay error state\x1b[0m");
      void writeRelayHealth("ok");
    }
  }
}

// Single chokepoint for every inbound tick: refresh the staleness clock and
// run the recovery-reset edge. All tick/depth/tape handlers call this.
function markTick(symbol) {
  lastTickTimestamp = Date.now();
  const state = symbol ? symbolStates.get(symbol) : null;
  if (state) state.lastTickAt = lastTickTimestamp;
  // First healthy tick of this process: write "ok" once to clear any stale
  // "error" row a prior (escalated, then restarted) process may have latched.
  if (!relayHealthInitialized) {
    relayHealthInitialized = true;
    void writeRelayHealth("ok");
  }
  onTicksRecovered();
}

/* ─── Batched Price Relay ──────────────────────────────────────────────────
 * Buffers price ticks per symbol (last-write-wins) and flushes to each
 * subscribed client as a single {"type": "batch", "updates": {...}} message
 * every BATCH_INTERVAL_MS. Reduces React re-renders from N per-tick updates
 * to 1 batched update per interval.
 */
const BATCH_INTERVAL_MS = 100;

// Per-client batch buffer: Map<client, Map<symbol, PriceData>>
const clientBatchBuffers = new Map();
const BATCH_THRESHOLD = 50; // Adaptive: flush early when any client has this many buffered symbols
let lastFlushTime = 0;

let batchFlushTimer = null;

function bufferPriceForClient(client, symbol, data) {
  let buf = clientBatchBuffers.get(client);
  if (!buf) {
    buf = new Map();
    clientBatchBuffers.set(client, buf);
  }
  buf.set(symbol, data);

  // Adaptive flush: trigger early when buffer exceeds threshold and min interval elapsed
  if (buf.size >= BATCH_THRESHOLD && Date.now() - lastFlushTime >= BATCH_INTERVAL_MS) {
    flushBatches();
  }
}

function flushBatches() {
  lastFlushTime = Date.now();
  for (const [client, buf] of clientBatchBuffers) {
    if (buf.size === 0) continue;
    const updates = Object.fromEntries(buf);
    const decision = sendMessage(client, { type: "batch", updates });
    settleIncrementalBatch(buf, updates, decision);
  }
}

function bufferDepthForClient(client, symbol, book) {
  let buf = clientDepthBuffers.get(client);
  if (!buf) {
    buf = new Map();
    clientDepthBuffers.set(client, buf);
  }
  buf.set(symbol, book);
}

function flushDepthBatches() {
  for (const [client, buf] of clientDepthBuffers) {
    if (buf.size === 0) continue;
    const updates = Object.fromEntries(buf);
    const decision = sendMessage(client, { type: "depth-batch", updates });
    settleIncrementalBatch(buf, updates, decision);
  }
}

function bufferTapeForClient(client, symbol, trades) {
  let buf = clientTapeBuffers.get(client);
  if (!buf) {
    buf = new Map();
    clientTapeBuffers.set(client, buf);
  }
  buf.set(symbol, trades);
}

function flushTapeBatches() {
  for (const [client, buf] of clientTapeBuffers) {
    if (buf.size === 0) continue;
    const updates = Object.fromEntries(buf);
    const decision = sendMessage(client, { type: "tape-batch", updates });
    settleIncrementalBatch(buf, updates, decision);
  }
}

function startBatchFlush() {
  if (batchFlushTimer) return;
  batchFlushTimer = setInterval(() => {
    flushBatches();
    if (DEPTH_ENABLED) {
      flushDepthBatches();
      flushTapeBatches();
    }
  }, BATCH_INTERVAL_MS);
}

function stopBatchFlush() {
  if (batchFlushTimer) {
    clearInterval(batchFlushTimer);
    batchFlushTimer = null;
  }
}

function removeBatchBuffer(client) {
  clientBatchBuffers.delete(client);
  clientDepthBuffers.delete(client);
  clientTapeBuffers.delete(client);
}

/* ─── Per-client backpressure ──────────────────────────────────────────────
 * readyState OPEN says nothing about whether the peer is DRAINING. A stalled
 * client keeps the socket open (and keeps ponging) while ws queues every frame
 * on the relay heap. lib/sendBackpressure.js owns the policy: shed coalescable
 * snapshot batches past the soft mark, tear the socket down at the hard cap.
 * Drops are counted per client and reported on disconnect. */
const clientDroppedFrames = new Map(); // client → frames shed by backpressure

function countDroppedFrame(client) {
  clientDroppedFrames.set(client, (clientDroppedFrames.get(client) ?? 0) + 1);
}

function sendMessage(client, payload) {
  try {
    if (client.readyState !== client.OPEN) return "close";
    const decision = decideSend(client.bufferedAmount, payload?.type);
    if (decision === "drop") {
      countDroppedFrame(client);
      return decision;
    }
    if (decision === "close") {
      countDroppedFrame(client);
      console.warn(
        `\x1b[33m[backpressure] closing client: ${client.bufferedAmount} buffered bytes ` +
          `≥ hard cap ${HARD_CAP_BYTES}\x1b[0m`,
      );
      // A close handshake would queue behind the same undrained backlog, so the
      // only action that reclaims the heap is destroying the socket.
      try { client.terminate(); } catch { try { client.close(); } catch { /* ignore */ } }
      return decision;
    }
    client.send(JSON.stringify(payload));
    return decision;
  } catch {
    // Ignore send failures.
    return "close";
  }
}

function sendToSymbolSubscribers(symbol, payload) {
  const subscribers = symbolSubscribers.get(symbol);
  if (!subscribers || subscribers.size === 0) return;
  for (const client of subscribers) {
    sendMessage(client, payload);
  }
}

function sendStatus(client) {
  const subscriptions = Array.from(symbolSubscribers.keys()).filter((symbol) => symbolSubscribers.get(symbol)?.size);
  sendMessage(client, {
    type: "status",
    ib_connected: ibConnected,
    ib_issue: ibConnectionIssue?.code ?? null,
    ib_status_message: ibConnectionIssue?.operatorMessage ?? null,
    subscriptions,
  });
}

function broadcastStatus() {
  for (const client of clients) {
    sendStatus(client);
  }
}

function clearSnapshot(requestId) {
  const req = snapshotRequests.get(requestId);
  if (!req) return;
  clearTimeout(req.timer);
  snapshotRequests.delete(requestId);
  requestIdToSymbol.delete(requestId);
}

function completeSnapshot(symbol, requestId) {
  const req = snapshotRequests.get(requestId);
  if (!req) return;
  sendMessage(req.client, {
    type: "snapshot",
    symbol,
    data: req.data,
  });
  clearSnapshot(requestId);
  try {
    ib.cancelMktData(requestId);
  } catch {
    // Ignore cleanup failures.
  }
}

function ensureSymbolState(key, ibContract) {
  const existing = symbolStates.get(key);
  if (existing) {
    existing.contract = ibContract;
    return existing;
  }

  const state = {
    tickerId: null,
    contract: ibContract,
    data: createPriceData(key),
    lastTickAt: Date.now(),
  };
  symbolStates.set(key, state);
  return state;
}

function startLiveSubscription(key, ibContract) {
  if (!ibConnected) return;

  const existing = ensureSymbolState(key, ibContract);
  const nextTickerId = nextRequestId += 1;
  const state = existing;

  if (state.tickerId != null) {
    try {
      ib.cancelMktData(state.tickerId);
    } catch {
      // Ignore.
    }
    requestIdToSymbol.delete(state.tickerId);
  }

  try {
    ib.reqMktData(nextTickerId, ibContract, "233,165", false, false);
    state.tickerId = nextTickerId;
    state.contract = ibContract;
    state.data.timestamp = nowIso();
    state.lastTickAt = Date.now();
    symbolStates.set(key, state);
    requestIdToSymbol.set(nextTickerId, key);

    // Request fundamentals data for stocks (one-shot, cached)
    requestFundamentals(key, ibContract);
  } catch (error) {
    console.error(`Failed to subscribe ${key}:`, error);
  }
}

// Open (or re-open after a reconnect) an L1 subscription on the front-month
// future for a forward-priced index, under an internal "@fwd:<index>" key. Its
// ticks feed prices[index].fwd via publishForwardFromFutureTick. Resolution can
// fail during a data-farm flap (returns null) — we just skip and the client
// falls back to the cash spot until the next tick re-arms it.
async function ensureForwardSubscription(indexKey, client) {
  if (!FORWARD_PRICED_INDICES.has(indexKey) || !ibConnected) return;
  const fwdKey = FORWARD_KEY_PREFIX + indexKey;
  const owner = activeForwardOwner(client, indexKey);
  if (owner) forwardSubs.acquire(fwdKey, owner);
  const existing = symbolStates.get(fwdKey);
  if (existing && existing.tickerId != null) return; // already streaming
  const contract = await resolveFuturesFrontMonth(indexKey);
  // The holder can unsubscribe while the resolve is in flight — opening a line
  // nobody holds leaks it, because no release is left to cancel it.
  if (!contract || forwardSubs.holderCount(fwdKey) === 0) return;
  forwardKeyToIndex.set(fwdKey, indexKey);
  startLiveSubscription(fwdKey, contract);
}

// Per-expiry forward: match a held VIX OPTION expiry to its nearest VIX future,
// subscribe that future's L1 (deduped by future expiry), and publish its price
// in prices[index].fwdCurve under the OPTION expiry so each leg prices off the
// future of its own expiry. Degrades silently (falls back to front-month `fwd`
// then cash) when the chain can't resolve during a data-farm flap.
async function ensureForwardForOptionExpiry(indexKey, optExpiryRaw, client, subjectKey) {
  if (!FORWARD_PRICED_INDICES.has(indexKey) || !ibConnected) return;
  const optExp = String(optExpiryRaw || "").replace(/\D/g, "").slice(0, 8);
  if (optExp.length !== 8) return;
  const chain = await resolveFuturesChain(indexKey);
  const fut = pickNearestExpiry(chain, optExp);
  if (!fut) return;
  const futExp = String(fut.lastTradeDateOrContractMonth || "").slice(0, 8);
  const fwdKey = `${FORWARD_KEY_PREFIX}${indexKey}:${futExp}`;
  // The forward key is only known after the async chain resolve, so the hold is
  // taken here — and only if the subject survived the resolve.
  const owner = activeForwardOwner(client, subjectKey);
  if (owner) {
    forwardSubs.acquire(fwdKey, owner);
    forwardSubs.acquire(curveHoldKey(fwdKey, optExp), owner);
  }
  if (forwardSubs.holderCount(fwdKey) === 0) return;
  forwardKeyToIndex.set(fwdKey, indexKey);
  let expiries = fwdKeyToCurveExpiries.get(fwdKey);
  if (!expiries) { expiries = new Set(); fwdKeyToCurveExpiries.set(fwdKey, expiries); }
  expiries.add(optExp);
  const existing = symbolStates.get(fwdKey);
  if (!existing || existing.tickerId == null) startLiveSubscription(fwdKey, fut);
}

/* Registry owner token: one client's hold on ONE client-facing subject. Two
 * subjects of the same client hold a shared forward independently, so dropping
 * one leaves the line up for the other. */
function forwardOwner(client, subjectKey) {
  let id = clientOwnerIds.get(client);
  if (id === undefined) {
    id = nextClientOwnerId += 1;
    clientOwnerIds.set(client, id);
  }
  return `${id}|${subjectKey}`;
}

/* Owner token for an ACQUIRE, or null when the client already dropped the
 * subject. Forward resolution is async, so a hold taken after its subject is
 * gone could never be released — exactly the leak this registry closes. */
function activeForwardOwner(client, subjectKey) {
  if (!client || !clientSymbols.get(client)?.has(subjectKey)) return null;
  return forwardOwner(client, subjectKey);
}

function curveHoldKey(fwdKey, optExp) {
  return `${CURVE_HOLD_PREFIX}${fwdKey}|${optExp}`;
}

/* Drop this client's holds on the forwards behind ONE client-facing subject.
 * The internal forward keys are unreachable from clientSymbols, so the registry
 * is the only thing that can tell us when a line has no requirements left. */
function releaseForwardsForSubject(client, subjectKey) {
  if (!clientOwnerIds.has(client)) return;
  const retired = forwardSubs.releaseAllForOwner(forwardOwner(client, subjectKey));
  // Curve entries first: they resolve their index THROUGH forwardKeyToIndex, which
  // retiring the forward line deletes.
  for (const key of retired) {
    if (key.startsWith(CURVE_HOLD_PREFIX)) dropCurveExpiry(key);
  }
  for (const key of retired) {
    if (!key.startsWith(CURVE_HOLD_PREFIX)) stopForwardSubscription(key);
  }
}

/* Last holder of one option expiry on a shared future: stop publishing that
 * expiry in prices[index].fwdCurve. Other expiries on the same future stay. */
function dropCurveExpiry(holdKey) {
  const body = holdKey.slice(CURVE_HOLD_PREFIX.length);
  const separator = body.lastIndexOf("|");
  if (separator < 0) return;
  const fwdKey = body.slice(0, separator);
  const optExp = body.slice(separator + 1);
  const idxState = symbolStates.get(forwardKeyToIndex.get(fwdKey));
  if (idxState?.data?.fwdCurve) delete idxState.data.fwdCurve[optExp];
  const expiries = fwdKeyToCurveExpiries.get(fwdKey);
  if (!expiries) return;
  expiries.delete(optExp);
  if (expiries.size === 0) fwdKeyToCurveExpiries.delete(fwdKey);
}

/* Last holder of an internal forward line: cancel the IB market-data ticket and
 * drop every map keyed by it, including any curve entries it still publishes
 * (a re-mapped expiry after a roll can outlive its own curve hold). */
function stopForwardSubscription(fwdKey) {
  const idxState = symbolStates.get(forwardKeyToIndex.get(fwdKey));
  const expiries = fwdKeyToCurveExpiries.get(fwdKey);
  if (idxState?.data?.fwdCurve && expiries) {
    for (const optExp of expiries) delete idxState.data.fwdCurve[optExp];
  }
  stopLiveSubscription(fwdKey);
  forwardKeyToIndex.delete(fwdKey);
  fwdKeyToCurveExpiries.delete(fwdKey);
}

// Re-open the internal forward L1(s) a client-facing subject needs (index →
// front month, forward-priced index OPTION → the future of its own expiry).
// Both are internal keys, so nothing that walks client-facing subscriptions
// restores them. Re-arms per SUBSCRIBER so the hold is re-acquired too: after a
// roll the option can match a DIFFERENT future, and an un-held line would leak.
function rearmForwardsForSubject(key) {
  const subscribers = symbolSubscribers.get(key);
  if (!subscribers || subscribers.size === 0) return;
  if (FORWARD_PRICED_INDICES.has(key)) {
    for (const client of subscribers) void ensureForwardSubscription(key, client);
    return;
  }
  const parts = key.split("_");
  if (parts.length >= 4 && FORWARD_PRICED_INDICES.has(parts[0])) {
    for (const client of subscribers) {
      void ensureForwardForOptionExpiry(parts[0], parts[1], client, key);
    }
  }
}

// A re-resolve replaced the cached chain (rolled expiry, or a newly listed one):
// re-arm the forwards so the index stops pricing off a contract that no longer
// trades. Refreshing the CACHE alone would leave the live L1 on the dead conId.
function rearmForwardsAfterRoll(root) {
  if (!FORWARD_PRICED_INDICES.has(root)) return;
  const fwdKey = FORWARD_KEY_PREFIX + root;
  const front = resolvedFuturesContracts.get(root);
  const live = symbolStates.get(fwdKey);
  if (front && live && live.contract?.conId !== front.conId) stopLiveSubscription(fwdKey);
  for (const key of [...symbolSubscribers.keys()]) {
    if (key === root || key.startsWith(`${root}_`)) rearmForwardsForSubject(key);
  }
}

function isFinitePositive(n) {
  return typeof n === "number" && Number.isFinite(n) && n > 0;
}

function forwardFromState(fwdState) {
  const d = fwdState.data;
  if (isFinitePositive(d.bid) && isFinitePositive(d.ask)) return (d.bid + d.ask) / 2;
  if (isFinitePositive(d.last)) return d.last;
  if (isFinitePositive(d.close)) return d.close;
  return null;
}

// Route a future tick into the index price: a curve key ("@fwd:VIX:<futExp>")
// updates prices[index].fwdCurve under every OPTION expiry mapped to it; the
// front-month key ("@fwd:VIX") updates prices[index].fwd. Rebroadcasts only on
// a real change.
function publishForwardFromFutureTick(fwdKey, fwdState) {
  const indexKey = forwardKeyToIndex.get(fwdKey);
  if (!indexKey) return;
  const fwd = forwardFromState(fwdState);
  if (fwd == null) return;
  const idxState = symbolStates.get(indexKey);
  if (!idxState) return;

  const curveExpiries = fwdKeyToCurveExpiries.get(fwdKey);
  if (curveExpiries && curveExpiries.size) {
    const curve = idxState.data.fwdCurve || (idxState.data.fwdCurve = {});
    let changed = false;
    for (const optExp of curveExpiries) {
      if (curve[optExp] !== fwd) { curve[optExp] = fwd; changed = true; }
    }
    if (changed) hydrateAndBroadcast(indexKey);
    return;
  }

  if (idxState.data.fwd === fwd) return; // no change — skip rebroadcast
  idxState.data.fwd = fwd;
  hydrateAndBroadcast(indexKey);
}

function stopLiveSubscription(symbol) {
  const state = symbolStates.get(symbol);
  if (!state) return;
  // A null tickerId means the ticket was already cancelled (reconnect cleanup,
  // IB error 200/354) — the STATE still has to go. Returning early on it leaked
  // one symbolStates entry per such symbol for the life of the process.
  if (state.tickerId != null) {
    try {
      ib.cancelMktData(state.tickerId);
    } catch {
      // Ignore.
    }
    requestIdToSymbol.delete(state.tickerId);
  }
  symbolStates.delete(symbol);
}

function cleanupSymbolStateForReconnect() {
  for (const state of symbolStates.values()) {
    if (state.tickerId != null) {
      try {
        ib.cancelMktData(state.tickerId);
      } catch {
        // Ignore.
      }
      requestIdToSymbol.delete(state.tickerId);
      state.tickerId = null;
    }
  }
}

/* ─── L2 Depth lifecycle + ladder maintenance ──────────────────────────────
 * All functions in this block are no-ops on the request side unless
 * DEPTH_ENABLED; callers also guard, but the cap/LRU logic lives here.
 */

function depthFeedLabel(kind, isFutures, exchange) {
  // Futures: native single-venue depth — label with the venue when known
  // (e.g. "CME DEPTH"), else generic "NATIVE DEPTH".
  if (isFutures) return exchange ? `${exchange} DEPTH` : "NATIVE DEPTH";
  if (kind === "option") return "OPRA BBO";
  return "SMART DEPTH";
}

function emitDepthUnavailable(symbol, reason, code) {
  const subscribers = depthSubscribers.get(symbol);
  const payload = { type: "depth-unavailable", symbol, reason };
  if (code != null) payload.code = code;
  if (subscribers) {
    for (const client of subscribers) sendMessage(client, payload);
  }
}

function stopDepthSubscription(key, { keepState = false } = {}) {
  const state = symbolDepthStates.get(key);
  if (!state) return;
  if (state.depthTickerId != null) {
    try {
      ib.cancelMktDepth(state.depthTickerId, !state.isFutures);
    } catch {
      // Ignore.
    }
    depthRequestIdToSymbol.delete(state.depthTickerId);
    state.depthTickerId = null;
  }
  if (!keepState) symbolDepthStates.delete(key);
}

// Live tickets with their subscriber sets, for the per-client budget planner.
function collectActiveDepthTickets() {
  const tickets = [];
  for (const [key, state] of symbolDepthStates) {
    if (state.depthTickerId == null) continue;
    tickets.push({
      key,
      focusedAt: state.focusedAt,
      subscribers: depthSubscribers.get(key) ?? new Set(),
    });
  }
  return tickets;
}

/* Resolve a futures root (e.g. "ES") to its qualified front-month contract.
 * @stoqey/ib has no ContFuture auto-resolution, so reqMktDepth on a bare root
 * fails — we need a conId. reqContractDetails on the native exchange returns
 * every listed expiry; we pick the NEAREST non-expired one. Cached per root.
 * Bounded await: resolves null on timeout/error so the caller never hangs. */
function resolveFuturesFrontMonth(root) {
  const cached = resolvedFuturesContracts.get(root);
  if (cached) {
    // Serve the cached contract even when it is stale and re-resolve behind it:
    // blanking prices[root].fwd / fwdCurve while reqContractDetails is in flight
    // would drop every VIX option back to cash spot for seconds at a time.
    if (isCachedFuturesResolutionStale(root)) refreshFuturesResolution(root);
    return Promise.resolve(cached);
  }
  if (!ibConnected) return Promise.resolve(null);
  return startFuturesResolve(root);
}

/* Has the cached resolution for `root` rolled (expiry behind ET today) or gone
 * unverified past the re-check TTL? An absent contract (including a chain that
 * resolved empty) counts as stale — there is nothing to serve, so re-resolving
 * can only help. Rate-limiting lives in refreshFuturesResolution. */
function isCachedFuturesResolutionStale(root, now = Date.now()) {
  const cached = resolvedFuturesContracts.get(root);
  if (!cached) return true;
  return isResolvedFutureStale(
    cached.lastTradeDateOrContractMonth,
    futuresResolvedAt.get(root),
    now,
    etDateYYYYMMDD(now),
  );
}

/* Background re-resolve for a stale root. One request per root at a time and at
 * most one attempt per FUTURES_RERESOLVE_MIN_GAP_MS, so a root whose re-resolve
 * keeps failing (IB down, no non-expired expiry) cannot storm reqContractDetails
 * from every subscribe. On success the internal forward L1s re-arm onto the new
 * contract — a fresh CACHE alone would leave the live line on the dead one. */
function refreshFuturesResolution(root) {
  if (!ibConnected || futuresResolveInFlight.has(root)) return;
  const now = Date.now();
  if (!shouldAttemptReresolve(futuresResolveAttemptAt.get(root), now)) return;
  futuresResolveAttemptAt.set(root, now);
  futuresResolveInFlight.add(root);
  void startFuturesResolve(root)
    .then((front) => {
      if (front) rearmForwardsAfterRoll(root);
    })
    .finally(() => futuresResolveInFlight.delete(root));
}

function startFuturesResolve(root) {
  const exchange = futuresExchangeForRoot(root);
  const probe = {
    symbol: root,
    secType: SecType.FUT,
    exchange,
    currency: "USD",
    includeExpired: false,
  };
  const reqId = nextRequestId += 1;

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      futuresResolveRequests.delete(reqId);
      resolve(value);
    };

    const timer = setTimeout(() => {
      verbose(`futures resolve ${root} timed out`);
      finish(null);
    }, FUTURES_RESOLVE_TIMEOUT_MS);

    futuresResolveRequests.set(reqId, { root, finish, candidates: [] });

    try {
      ib.reqContractDetails(reqId, probe);
    } catch (error) {
      verbose(`reqContractDetails(${root}) failed: ${error}`);
      finish(null);
    }
  });
}

// Pick the nearest non-expired expiry from the collected contractDetails.
// Non-expired contracts sorted nearest-expiry first. Shared by front-month
// (chain[0]) and the per-expiry forward curve (nearest match).
function sortedNonExpiredChain(candidates) {
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, ""); // YYYYMMDD
  return candidates
    .map((c) => ({ contract: c, expiry: String(c.lastTradeDateOrContractMonth || "").slice(0, 8) }))
    .filter((e) => e.expiry.length === 8 && e.expiry >= today)
    .sort((a, b) => a.expiry.localeCompare(b.expiry))
    .map((e) => e.contract);
}

function pickFrontMonth(candidates) {
  const chain = sortedNonExpiredChain(candidates);
  return chain.length ? chain[0] : null;
}

// Option expiry → the future it prices off: lib/futuresRollover.js:pickNearestExpiry
// (a future expiring BEFORE the option can never be its forward).

function onContractDetails(reqId, contractDetails) {
  const req = futuresResolveRequests.get(reqId);
  if (!req || !contractDetails?.contract) return;
  req.candidates.push(contractDetails.contract);
}

function onContractDetailsEnd(reqId) {
  const req = futuresResolveRequests.get(reqId);
  if (!req) return;
  const chain = sortedNonExpiredChain(req.candidates);
  resolvedFuturesChain.set(req.root, chain); // full curve for per-expiry matching
  const front = chain.length ? chain[0] : null;
  if (front) {
    resolvedFuturesContracts.set(req.root, front);
    // Stamp the resolution so the rollover re-check has an age to measure. A
    // failed resolve deliberately leaves the previous stamp (and contract) in
    // place — the cache keeps serving and refreshFuturesResolution retries.
    futuresResolvedAt.set(req.root, Date.now());
    verbose(`futures resolve ${req.root} → ${front.localSymbol || front.lastTradeDateOrContractMonth} (conId ${front.conId}), ${chain.length} listed`);
    req.finish(front);
  } else {
    verbose(`futures resolve ${req.root}: no non-expired expiry`);
    req.finish(null);
  }
}

// Full non-expired chain for a root, resolving (and caching) on first use by
// piggybacking on the front-month reqContractDetails flow.
async function resolveFuturesChain(root) {
  if (resolvedFuturesChain.has(root)) {
    // Same rollover contract as the front-month lookup: keep serving the cached
    // curve, re-resolve behind it when it has rolled or aged out.
    if (isCachedFuturesResolutionStale(root)) refreshFuturesResolution(root);
    return resolvedFuturesChain.get(root);
  }
  await resolveFuturesFrontMonth(root); // populates resolvedFuturesChain as a side effect
  return resolvedFuturesChain.get(root) || [];
}

/* ─── Time & Sales tape lifecycle ────────────────────────────────────────── */
function startTapeSubscription(key, contract) {
  if (!DEPTH_ENABLED || !ibConnected) return;
  let state = symbolTapeStates.get(key);
  if (!state) {
    state = { tapeTickerId: null, trades: [] };
    symbolTapeStates.set(key, state);
  }
  if (state.tapeTickerId != null) return; // already streaming

  const tapeTickerId = nextRequestId += 1;
  try {
    // AllLast = every last-trade print (incl. combos/odd-lots). numberOfTicks=0
    // → continuous stream; ignoreSize=false keeps trade sizes.
    ib.reqTickByTickData(tapeTickerId, contract, TickByTickDataType.AllLast, 0, false);
    state.tapeTickerId = tapeTickerId;
    tapeRequestIdToSymbol.set(tapeTickerId, key);
    verbose(`tape subscribe ${key} ticket=${tapeTickerId}`);
  } catch (error) {
    console.error(`Failed to subscribe tape ${key}:`, error);
  }
}

function stopTapeSubscription(key) {
  const state = symbolTapeStates.get(key);
  if (!state) return;
  if (state.tapeTickerId != null) {
    try {
      ib.cancelTickByTickData(state.tapeTickerId);
    } catch {
      // Ignore.
    }
    tapeRequestIdToSymbol.delete(state.tapeTickerId);
    state.tapeTickerId = null;
  }
  symbolTapeStates.delete(key);
}

// Append a print to the bounded ring (newest-last) and buffer for broadcast.
function applyTrade(key, price, size, exchange, time) {
  const state = symbolTapeStates.get(key);
  if (!state) return;
  state.trades.push({ price, size, exchange: exchange || null, time });
  if (state.trades.length > TAPE_RING_SIZE) {
    state.trades.splice(0, state.trades.length - TAPE_RING_SIZE);
  }
  const subscribers = depthSubscribers.get(key);
  if (!subscribers || subscribers.size === 0) return;
  const snapshot = state.trades.slice();
  for (const client of subscribers) {
    bufferTapeForClient(client, key, snapshot);
  }
}

function cleanupTapeForReconnect() {
  for (const [, state] of symbolTapeStates) {
    if (state.tapeTickerId != null) {
      try {
        ib.cancelTickByTickData(state.tapeTickerId);
      } catch {
        // Ignore.
      }
      tapeRequestIdToSymbol.delete(state.tapeTickerId);
      state.tapeTickerId = null;
    }
  }
}

function startDepthSubscription(key, contract, { kind, isFutures, requestingClient = null }) {
  if (!DEPTH_ENABLED || !ibConnected) return;

  let state = symbolDepthStates.get(key);
  if (!state) {
    state = { depthTickerId: null, contract, kind, isFutures, ladders: { bid: [], ask: [] }, focusedAt: Date.now() };
    symbolDepthStates.set(key, state);
  } else {
    state.contract = contract;
    state.kind = kind;
    state.isFutures = isFutures;
    state.focusedAt = Date.now();
  }

  if (state.depthTickerId != null) return; // already streaming

  // Cap-check via the per-client planner: only the requesting client's own
  // exclusive tickets may be recycled — another session's legs are never
  // evicted; the newcomer is refused with an explicit depth-budget signal.
  const admission = planDepthAdmission({
    activeTickets: collectActiveDepthTickets(),
    requestingClient,
    exceptKey: key,
    maxConcurrent: MAX_CONCURRENT_DEPTH,
  });
  if (!admission.admit) {
    emitDepthUnavailable(key, "depth-budget");
    return;
  }
  for (const evictKey of admission.evictKeys) {
    stopDepthSubscription(evictKey);
    emitDepthUnavailable(evictKey, "recycled");
  }

  const numRows = isFutures ? DEPTH_NUM_ROWS_FUTURES : DEPTH_NUM_ROWS_EQUITY;
  const depthTickerId = nextRequestId += 1;
  const isSmartDepth = !isFutures;
  try {
    // @stoqey/ib: reqMktDepth(reqId, contract, numRows, isSmartDepth). Smart
    // depth (true) for equity/option SMART aggregation, native single-venue
    // depth (false) for futures. The same isSmartDepth is required on cancel.
    ib.reqMktDepth(depthTickerId, contract, numRows, isSmartDepth);
    state.depthTickerId = depthTickerId;
    state.ladders.bid.length = 0;
    state.ladders.ask.length = 0;
    depthRequestIdToSymbol.set(depthTickerId, key);
    verbose(`depth subscribe ${key} kind=${kind} rows=${numRows} ticket=${depthTickerId}`);
  } catch (error) {
    console.error(`Failed to subscribe depth ${key}:`, error);
  }
}

function applyDepthDelta(key, position, marketMaker, operation, side, price, size) {
  const state = symbolDepthStates.get(key);
  if (!state) return;
  const ladder = side === 1 ? state.ladders.bid : state.ladders.ask;
  const maxRows = state.isFutures ? DEPTH_NUM_ROWS_FUTURES : DEPTH_NUM_ROWS_EQUITY;
  // Pure position-SHIFT reducer (scripts/lib/depthLadder.js, T-041): OOB
  // ops mean the ladder desynced from IB — reject rather than corrupt
  // every later position-addressed update, and never exceed the row budget.
  const result = applyDepthOp(
    ladder,
    { operation, position, price, size, marketMaker: marketMaker || null },
    maxRows,
  );
  if (!result.applied) {
    state.desyncCount = (state.desyncCount || 0) + 1;
    if (state.desyncCount === 1 || state.desyncCount % 50 === 0) {
      console.warn(
        `[depth] rejected ${result.reason} for ${key} (pos=${position}, op=${operation}, ` +
        `desyncs=${state.desyncCount}) — ladder kept intact`,
      );
    }
    return;
  }
  hydrateAndBroadcastDepth(key);
}

function serializeLadder(ladder, isFutures, kind, side) {
  const rows = ladder.map((lvl, i) => [i, lvl]);
  // OPRA options: each row is a venue's top-of-book BBO (no stacked depth). The
  // NBBO is the best bid (max price) / best ask (min price) across the venue
  // rows; ALL venues tied at that inside price are flagged nbbo=true.
  const isOption = kind === "option";
  const nbboPrice = nbboPriceForOptionLadder(rows, side, isOption);
  return rows.map(([, lvl]) => {
    // Equity/option (L2): the venue/MPID code arrives as marketMaker. For
    // options the marketMaker IS the venue, so populate BOTH fields so the web
    // montage (level.marketMaker ?? level.exchange) labels the Market column
    // consistently with stocks. Equities keep marketMaker null (MPID exposed
    // as exchange). Futures (single-venue): no attribution, both null.
    const venue = isFutures ? null : (lvl.marketMaker || null);
    const marketMaker = isOption ? venue : null;
    const exchange = venue;
    const level = { price: lvl.price, size: lvl.size, marketMaker, exchange };
    if (isOption) level.nbbo = nbboPrice != null && lvl.price === nbboPrice;
    return level;
  });
}

// Inside price across an option venue montage: max bid / min ask. Returns null
// for non-option ladders or empty rows (no flag emitted).
function nbboPriceForOptionLadder(rows, side, isOption) {
  if (!isOption || rows.length === 0) return null;
  const prices = rows.map(([, lvl]) => lvl.price).filter((p) => typeof p === "number");
  if (prices.length === 0) return null;
  return side === "bid" ? Math.max(...prices) : Math.min(...prices);
}

// Cross-venue NBBO summary for an option montage: best bid / best ask / mid /
// total displayed size across the venue rows at the inside.
function summarizeOptionNbbo(bid, ask) {
  const bestBid = bid.length ? Math.max(...bid.map((l) => l.price)) : null;
  const bestAsk = ask.length ? Math.min(...ask.map((l) => l.price)) : null;
  const mid = bestBid != null && bestAsk != null ? (bestBid + bestAsk) / 2 : null;
  const sumSize = (rows) => rows.reduce((acc, l) => acc + (typeof l.size === "number" ? l.size : 0), 0);
  return { bestBid, bestAsk, mid, bidSize: sumSize(bid), askSize: sumSize(ask) };
}

function hydrateAndBroadcastDepth(key) {
  const state = symbolDepthStates.get(key);
  if (!state) return;
  const subscribers = depthSubscribers.get(key);
  if (!subscribers || subscribers.size === 0) return;

  const bid = serializeLadder(state.ladders.bid, state.isFutures, state.kind, "bid");
  const ask = serializeLadder(state.ladders.ask, state.isFutures, state.kind, "ask");
  const book = {
    symbol: key,
    kind: state.kind,
    bid,
    ask,
    isSmartDepth: !state.isFutures,
    feed: depthFeedLabel(state.kind, state.isFutures, state.isFutures ? state.contract?.exchange : null),
    entitled: true,
    timestamp: nowIso(),
  };
  // Options: surface the cross-venue NBBO summary (cheap — derived from the
  // already-flagged inside rows). Honest framing: this is top-of-book per
  // venue, not stacked depth.
  if (state.kind === "option") book.nbbo = summarizeOptionNbbo(bid, ask);
  for (const client of subscribers) {
    bufferDepthForClient(client, key, book);
  }
}

function subscribeClientToDepth(client, key) {
  let subscribers = depthSubscribers.get(key);
  if (!subscribers) {
    subscribers = new Set();
    depthSubscribers.set(key, subscribers);
  }
  subscribers.add(client);
}

function unsubscribeClientFromDepth(client, key) {
  const subscribers = depthSubscribers.get(key);
  if (!subscribers) return;
  subscribers.delete(client);
  if (subscribers.size === 0) {
    depthSubscribers.delete(key);
    stopDepthSubscription(key);
    stopTapeSubscription(key); // tape rides the focused depth symbol
  }
}

function cleanupDepthForReconnect() {
  for (const [, state] of symbolDepthStates) {
    if (state.depthTickerId != null) {
      try {
        ib.cancelMktDepth(state.depthTickerId, !state.isFutures);
      } catch {
        // Ignore.
      }
      depthRequestIdToSymbol.delete(state.depthTickerId);
      state.depthTickerId = null;
    }
    state.ladders.bid.length = 0;
    state.ladders.ask.length = 0;
  }
  cleanupTapeForReconnect();
  // Defensively drop any reverse-map entries that outlived their state (a
  // depth/tape ticker that was cancelled without a matching state row would
  // otherwise route post-reconnect depth/tape events to a dead tickerId,
  // producing the "Can't find EId" / stale-ladder signature). The per-state
  // loops above already cleared live mappings; this clears orphans.
  depthRequestIdToSymbol.clear();
  tapeRequestIdToSymbol.clear();
}

// Restore the focused depth ticket(s) + tape after a reconnect.
function restoreDepthSubscriptions() {
  if (!DEPTH_ENABLED) return;
  for (const key of depthSubscribers.keys()) {
    const state = symbolDepthStates.get(key);
    if (!state || !state.contract) continue;
    startDepthSubscription(key, state.contract, { kind: state.kind, isFutures: state.isFutures });
    startTapeSubscription(key, state.contract);
  }
}

function subscribeClientToSymbol(client, symbol) {
  let subscribers = symbolSubscribers.get(symbol);
  if (!subscribers) {
    subscribers = new Set();
    symbolSubscribers.set(symbol, subscribers);
  }
  subscribers.add(client);

  let clientSet = clientSymbols.get(client);
  if (!clientSet) {
    clientSet = new Set();
    clientSymbols.set(client, clientSet);
  }
  clientSet.add(symbol);
}

function unsubscribeClientFromSymbol(client, symbol) {
  const subscribers = symbolSubscribers.get(symbol);
  let unsubscribed = false;

  if (subscribers) {
    subscribers.delete(client);
    if (subscribers.size === 0) {
      symbolSubscribers.delete(symbol);
      stopLiveSubscription(symbol);
      unsubscribed = true;
    } else {
      unsubscribed = true;
    }
  }

  const clientSet = clientSymbols.get(client);
  if (clientSet) {
    clientSet.delete(symbol);
  }

  // The internal forward line(s) this subject required are not client-facing
  // keys, so only the refcount registry can retire them.
  releaseForwardsForSubject(client, symbol);

  if (symbolSubscribers.size === 0) {
    lastFarmStateCode = farmStateAfterIdleDrain();
  }

  return unsubscribed;
}

function disconnectClient(client) {
  removeBatchBuffer(client);
  clientLastPong.delete(client);
  clientDroppedFrames.delete(client);
  if (DEPTH_ENABLED) {
    for (const key of [...depthSubscribers.keys()]) {
      unsubscribeClientFromDepth(client, key);
    }
  }
  const clientSet = clientSymbols.get(client);
  if (!clientSet) {
    clientOwnerIds.delete(client);
    return;
  }

  for (const symbol of clientSet) {
    // Retire this client's forward holds before the subject goes away — a
    // disconnect is just a bulk unsubscribe as far as the registry is concerned.
    releaseForwardsForSubject(client, symbol);
    const subscribers = symbolSubscribers.get(symbol);
    if (!subscribers) continue;

    subscribers.delete(client);
    if (subscribers.size === 0) {
      symbolSubscribers.delete(symbol);
      stopLiveSubscription(symbol);
    }
  }

  clientSymbols.delete(client);
  clientOwnerIds.delete(client);
  if (symbolSubscribers.size === 0) {
    lastFarmStateCode = farmStateAfterIdleDrain();
  }
}

function sendSubscribedConfirmation(client, symbols) {
  sendMessage(client, {
    type: "subscribed",
    symbols,
  });
}

function sendUnsubscribedConfirmation(client, symbols) {
  sendMessage(client, {
    type: "unsubscribed",
    symbols,
  });
}

async function handleSnapshotRequest(client, symbols) {
  for (const symbol of symbols) {
    if (!ibConnected) {
      sendMessage(client, {
        type: "error",
        message: "IB not connected",
      });
      continue;
    }

    const requestId = nextRequestId += 1;
    const contract = stockContract(symbol, "SMART", "USD");
    const requestState = {
      symbol,
      client,
      timer: setTimeout(() => {
        sendMessage(client, {
          type: "error",
          message: `Timeout waiting for snapshot: ${symbol}`,
        });
        clearSnapshot(requestId);
        try {
          ib.cancelMktData(requestId);
        } catch {
          // Ignore.
        }
      }, SNAPSHOT_TIMEOUT_MS),
      data: createPriceData(symbol),
    };

    snapshotRequests.set(requestId, requestState);
    requestIdToSymbol.set(requestId, symbol);

    try {
      await snapshotLimiter.submit(() => {
        // IB rejects snapshot=true paired with generic ticks (233=RTVolume,
        // 165=Misc Stats) — both are streaming-only. Snapshot still returns
        // bid/ask/last/close/volume/high/low/open via default tick types,
        // which is what the snapshot consumer needs. The streaming path at
        // line 566 keeps "233,165" because snapshot=false there.
        ib.reqMktData(requestId, contract, "", true, false);
      });
    } catch (error) {
      clearSnapshot(requestId);
      try {
        ib.cancelMktData(requestId);
      } catch {
        // Ignore.
      }
      sendMessage(client, {
        type: "error",
        message: `Failed to request snapshot for ${symbol}: ${String(error)}`,
      });
    }
  }
}

/** Maximum age (ms) for cached bid/ask before we consider it stale.
 * Option quotes older than 8 hours come from a prior session and should
 * not be broadcast to new subscribers — they will be replaced once the
 * fresh reqMktData ticks arrive from IB. */
const QUOTE_STALE_MS = 8 * 60 * 60 * 1000; // 8 hours

/**
 * Return a copy of PriceData safe to send as an initial snapshot.
 * If the cached bid/ask timestamps are older than QUOTE_STALE_MS
 * (i.e. from a prior trading session), null them out so the UI shows
 * "---" instead of stale prices until fresh ticks arrive.
 */
function safeInitialState(data) {
  if (!data.timestamp) return data;
  const age = Date.now() - new Date(data.timestamp).getTime();
  if (age > QUOTE_STALE_MS) {
    return { ...data, bid: null, ask: null, bidSize: null, askSize: null, lastIsCalculated: false };
  }
  return data;
}

/**
 * Build the initial `price` message for a subscription restored after an IB
 * reconnect. cleanupSymbolStateForReconnect() only cancels tickers — every
 * symbol's cached PriceData survives with its prior-session timestamp — so this
 * broadcast is an INITIAL snapshot in exactly the same sense as the three
 * subscribe paths and must clear stale quotes the same way.
 */
function buildRestorePriceMessage(key, state) {
  return { type: "price", symbol: key, data: safeInitialState(state.data) };
}

function hydrateAndBroadcast(symbol) {
  const state = symbolStates.get(symbol);
  if (!state) return;
  // Backfill close from cache for options that IB hasn't sent close for
  applyCachedClose(state.data);

  // Buffer the tick for batched delivery instead of sending immediately.
  // Each subscribed client gets the latest PriceData snapshot for this symbol
  // in their per-client buffer. The batch flush timer sends all buffered
  // updates as a single {"type": "batch", "updates": {...}} message.
  const subscribers = symbolSubscribers.get(symbol);
  if (!subscribers || subscribers.size === 0) return;
  const dataSnapshot = { ...state.data };
  for (const client of subscribers) {
    bufferPriceForClient(client, symbol, dataSnapshot);
  }
}

function onTickPrice(tickerId, tickType, price) {
  const symbol = requestIdToSymbol.get(tickerId);
  markTick(symbol);
  const liveState = symbol ? symbolStates.get(symbol) : null;
  const snapshotState = snapshotRequests.get(tickerId);

  if (liveState) {
    updatePriceFromTickPrice(liveState.data, tickType, price);
    // Cache option close prices to disk for after-hours availability
    updateOptionCloseCache(symbol, liveState.data.close);
    verbose(`tick ${symbol} type=${tickType} price=${price}`);
    hydrateAndBroadcast(symbol);
    // Forward-priced index: route the front-month future tick into index.fwd.
    if (forwardKeyToIndex.has(symbol)) publishForwardFromFutureTick(symbol, liveState);
  }
  if (snapshotState) {
    updatePriceFromTickPrice(snapshotState.data, tickType, price);
  }
}

function onTickSize(tickerId, sizeType, size) {
  const symbol = requestIdToSymbol.get(tickerId);
  markTick(symbol);
  const liveState = symbol ? symbolStates.get(symbol) : null;
  const snapshotState = snapshotRequests.get(tickerId);

  if (liveState) {
    updatePriceFromTickSize(liveState.data, sizeType, size);
  }
  if (snapshotState) {
    updatePriceFromTickSize(snapshotState.data, sizeType, size);
  }
}

function onTickSnapshotEnd(tickerId) {
  const symbol = requestIdToSymbol.get(tickerId);
  if (!symbol) return;
  completeSnapshot(symbol, tickerId);
}

/* ─── Fundamentals via reqFundamentalData (IBIS subscription) ─── */

const fundamentalsRequestIds = new Map(); // reqId → symbol
const fundamentalsPending = new Set(); // symbols currently being fetched

function requestFundamentals(symbol, ibContract) {
  // Only for stock symbols, not options
  if (symbol.includes("_")) return;
  // Not for futures roots — reqFundamentalData(ReportSnapshot) is equities-only;
  // a FUT contract returns nothing useful and just logs noise.
  if (ibContract && ibContract.secType === SecType.FUT) return;
  // Already have data or request in-flight
  if (fundamentalsStore.has(symbol) || fundamentalsPending.has(symbol)) return;
  if (!ibConnected) return;

  const reqId = nextRequestId += 1;
  fundamentalsRequestIds.set(reqId, symbol);
  fundamentalsPending.add(symbol);

  try {
    ib.reqFundamentalData(reqId, ibContract, "ReportSnapshot");
    verbose(`reqFundamentalData ${symbol} reqId=${reqId}`);
  } catch (error) {
    verbose(`reqFundamentalData failed for ${symbol}: ${error}`);
    fundamentalsRequestIds.delete(reqId);
    fundamentalsPending.delete(symbol);
  }
}

function onFundamentalData(reqId, xmlData) {
  const symbol = fundamentalsRequestIds.get(reqId);
  if (!symbol) return;
  fundamentalsRequestIds.delete(reqId);
  fundamentalsPending.delete(symbol);

  const fundData = createFundamentalsData(symbol);

  // Parse XML ratio data — extract key financial ratios
  const extractRatio = (tag) => {
    const match = xmlData.match(new RegExp(`<${tag}[^>]*>([^<]+)</${tag}>`));
    if (!match) return null;
    const val = parseFloat(match[1]);
    return Number.isFinite(val) ? val : null;
  };

  // IB ReportSnapshot XML has Ratio tags like:
  //   <Ratio FieldName="PEEXCLXOR">25.3</Ratio>
  const extractNamedRatio = (fieldName) => {
    const re = new RegExp(`<Ratio[^>]*FieldName="${fieldName}"[^>]*>([^<]+)</Ratio>`);
    const match = xmlData.match(re);
    if (!match) return null;
    const val = parseFloat(match[1]);
    return Number.isFinite(val) ? val : null;
  };

  fundData.peRatio = extractNamedRatio("PEEXCLXOR") ?? extractNamedRatio("APENORM");
  fundData.eps = extractNamedRatio("TTMEPSXCLX") ?? extractNamedRatio("AEPSNORM");
  fundData.dividendYield = extractNamedRatio("YIELD") ?? extractNamedRatio("TTMDIVSHR");
  fundData.week52High = extractNamedRatio("NHIG") ?? extractNamedRatio("NPRICE");
  fundData.week52Low = extractNamedRatio("NLOW");
  fundData.priceBookRatio = extractNamedRatio("PRICE2BK");
  fundData.roe = extractNamedRatio("TTMROEPCT");
  fundData.revenue = extractNamedRatio("TTMREV");

  const hasData = Object.entries(fundData).some(([k, v]) => k !== "symbol" && k !== "timestamp" && v !== null);
  if (hasData) {
    fundamentalsStore.set(symbol, fundData);
    verbose(`fundamentals ${symbol}: PE=${fundData.peRatio} EPS=${fundData.eps} DY=${fundData.dividendYield} 52H=${fundData.week52High} 52L=${fundData.week52Low}`);
    sendToSymbolSubscribers(symbol, {
      type: "fundamentals",
      symbol,
      data: fundData,
    });
  } else {
    verbose(`fundamentals ${symbol}: no usable data in XML (${xmlData.length} chars)`);
  }
}

function restoreSubscriptions() {
  const keys = [...symbolSubscribers.keys()];
  for (const key of keys) {
    const existing = symbolStates.get(key);
    const ibContract = existing?.contract;
    if (!ibContract) continue;
    startLiveSubscription(key, ibContract);
    // Re-arm the internal forward L1(s) behind this subject: their tickers were
    // cancelled by cleanupSymbolStateForReconnect and they are not client-facing
    // subscriptions, so the loop above would otherwise skip them.
    rearmForwardsForSubject(key);
    const state = symbolStates.get(key);
    if (state) {
      sendToSymbolSubscribers(key, buildRestorePriceMessage(key, state));
    }
  }
}

/* Resolve the IB contract + instrument kind for a single depth subject from
 * the raw client payload. Option → has expiry/strike; future → symbol in the
 * futures set; else stock. Returns null when the payload is unusable. */
function resolveDepthSubject(payload) {
  const rawSymbol = typeof payload.symbol === "string" ? payload.symbol.trim().toUpperCase() : null;
  if (!rawSymbol) return null;

  const hasOptionFields = typeof payload.expiry === "string" && typeof payload.strike === "number" && (payload.right === "C" || payload.right === "P");
  if (hasOptionFields) {
    const c = { symbol: rawSymbol, expiry: payload.expiry.trim(), strike: payload.strike, right: payload.right };
    return { key: optionKey(c), contract: optionContract(c.symbol, c.expiry, c.strike, c.right), kind: "option", isFutures: false };
  }

  const mappedRoot = INDEX_FUTURE_ROOT[rawSymbol];
  const isFuture = DEPTH_FUTURES_SYMBOLS.has(rawSymbol) || Boolean(mappedRoot) || payload.instrument === "future" || payload.secType === "FUT";
  if (isFuture) {
    // Depth on a bare futures root needs a QUALIFIED front-month contract
    // (conId), resolved via reqContractDetails in the handler. We carry the
    // root + a fallback bare-root contract; the handler swaps in the resolved
    // front-month before calling reqMktDepth. For an index→future mapping
    // (SPX→ES) the native future exchange wins over any index exchange the
    // client sent; the key stays the inbound symbol so depths[key] still matches.
    const root = mappedRoot || rawSymbol;
    const exchange = mappedRoot
      ? futuresExchangeForRoot(root)
      : (typeof payload.exchange === "string" ? payload.exchange.trim().toUpperCase() : futuresExchangeForRoot(root));
    // Carry the order-ticket selected expiry (YYYYMMDD/YYYYMM digits) so the
    // handler resolves THAT listed future under the key instead of the front
    // month. Absent → front-month fallback. Key stays the inbound symbol so
    // depths[key] still matches regardless of which expiry is selected.
    const expiry = typeof payload.expiry === "string" ? payload.expiry.replace(/-/g, "").trim() : null;
    return { key: rawSymbol, root, expiry: expiry || null, contract: futureContract(root, "", "USD", exchange), kind: "future", isFutures: true };
  }

  return { key: rawSymbol, contract: stockContract(rawSymbol, "SMART", "USD"), kind: "stock", isFutures: false };
}

async function handleClientMessage(client, data) {
  // Depth actions carry a single `symbol` (+ instrument fields), distinct from
  // the array subscribe. Route them off the raw payload before normalization.
  if (DEPTH_ENABLED && data && typeof data === "object" && typeof data.action === "string") {
    const depthAction = data.action.trim().toLowerCase();
    if (depthAction === "subscribe-depth" || depthAction === "unsubscribe-depth") {
      const subject = resolveDepthSubject(data);
      if (!subject) {
        sendMessage(client, { type: "error", message: "Invalid depth subscription" });
        return;
      }
      if (depthAction === "subscribe-depth") {
        subscribeClientToDepth(client, subject.key);
        let contract = subject.contract;
        if (subject.isFutures) {
          // Qualify a real conId before reqMktDepth — bounded await so a
          // slow/failed resolution can't hang the relay. No depth without a
          // real contract: emit futures-no-depth and bail.
          //
          // If the client sent a selected expiry, resolve THAT specific listed
          // future from the full curve; otherwise fall back to the front month.
          // The depth path owns its own resolveFuturesChain — independent of
          // /api/futures/chain — so depth resolves even when the order-form
          // chain fetch is slow.
          let resolved = null;
          if (subject.expiry) {
            const chain = await resolveFuturesChain(subject.root);
            resolved = pickNearestExpiry(chain, subject.expiry);
          }
          if (!resolved) {
            resolved = await resolveFuturesFrontMonth(subject.root);
          }
          if (!resolved) {
            emitDepthUnavailable(subject.key, "futures-no-depth");
            return;
          }
          contract = resolved;
        }
        // Contract swap under an existing key (e.g. user picked a different
        // VIX expiry): cancel the live ticket so startDepthSubscription re-reqs
        // on the new conId. Without this it short-circuits ("already streaming")
        // and the book stays pinned to the prior contract.
        const existing = symbolDepthStates.get(subject.key);
        if (existing && existing.depthTickerId != null && existing.contract && contract && existing.contract.conId !== contract.conId) {
          stopDepthSubscription(subject.key, { keepState: true });
          stopTapeSubscription(subject.key);
        }
        startDepthSubscription(subject.key, contract, { kind: subject.kind, isFutures: subject.isFutures, requestingClient: client });
        startTapeSubscription(subject.key, contract);
      } else {
        unsubscribeClientFromDepth(client, subject.key);
      }
      return;
    }
  }

  const message = parseActionMessage(data);
  if (!message) {
    sendMessage(client, { type: "error", message: "Invalid JSON" });
    return;
  }

  const symbols = message.symbols;
  const contracts = message.contracts;
  const indexes = message.indexes;
  verbose(`action=${message.action} symbols=[${symbols.join(",")}] contracts=${contracts.length} indexes=${indexes.length}`);
  switch (message.action) {
    case "subscribe": {
      const subscribed = [];
      // Stock subscriptions (backward compatible)
      for (const symbol of symbols) {
        subscribeClientToSymbol(client, symbol);
        // A bare futures ROOT (e.g. "ES") must subscribe L1 against the resolved
        // front-month FUTURE, not a stock — otherwise IB resolves the equity
        // ticker of the same name (ES = Eversource Energy ~$67) and the quote bar
        // shows the wrong instrument while the depth ladder shows the future.
        // Front-month L1 is decoupled from DEPTH_ENABLED so the header futures
        // strip (ES/NQ/RTY last + prior close) streams without the depth flag;
        // the depth LADDER (reqMktDepth) stays gated on DEPTH_ENABLED elsewhere.
        let ibContract;
        if (DEPTH_FUTURES_SYMBOLS.has(symbol)) {
          const resolvedFut = await resolveFuturesFrontMonth(symbol);
          ibContract = resolvedFut || stockContract(symbol, "SMART", "USD");
        } else {
          ibContract = stockContract(symbol, "SMART", "USD");
        }
        ensureSymbolState(symbol, ibContract);
        if (ibConnected) {
          startLiveSubscription(symbol, ibContract);
          const state = symbolStates.get(symbol);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol,
              data: safeInitialState(state.data),
            });
          }
          // Send cached fundamentals if available
          const fund = fundamentalsStore.get(symbol);
          if (fund) {
            sendMessage(client, {
              type: "fundamentals",
              symbol,
              data: fund,
            });
          }
          subscribed.push(symbol);
        }
      }
      // Option contract subscriptions
      for (const c of contracts) {
        const key = optionKey(c);
        subscribeClientToSymbol(client, key);
        const ibContract = optionContract(c.symbol, c.expiry, c.strike, c.right);
        ensureSymbolState(key, ibContract);
        if (ibConnected) {
          startLiveSubscription(key, ibContract);
          // Forward-priced index option (VIX): subscribe the future of THIS
          // option's expiry so it prices off its own forward (prices[idx].fwdCurve).
          if (FORWARD_PRICED_INDICES.has(String(c.symbol).toUpperCase())) {
            void ensureForwardForOptionExpiry(String(c.symbol).toUpperCase(), c.expiry, client, key);
          }
          const state = symbolStates.get(key);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol: key,
              data: safeInitialState(state.data),
            });
          }
          subscribed.push(key);
        }
      }
      // Index subscriptions (e.g. VIX, VVIX on CBOE)
      for (const idx of indexes) {
        const key = idx.symbol;
        subscribeClientToSymbol(client, key);
        const ibContract = indexContract(idx.symbol, "USD", idx.exchange);
        ensureSymbolState(key, ibContract);
        if (ibConnected) {
          startLiveSubscription(key, ibContract);
          // Forward-priced index (VIX): also open the front-month future L1 so
          // prices[key].fwd carries the forward options are priced against.
          if (FORWARD_PRICED_INDICES.has(key)) void ensureForwardSubscription(key, client);
          const state = symbolStates.get(key);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol: key,
              data: safeInitialState(state.data),
            });
          }
          subscribed.push(key);
        }
      }
      sendSubscribedConfirmation(client, subscribed);
      return;
    }
    case "unsubscribe": {
      const unsubscribed = [];
      for (const symbol of symbols) {
        if (unsubscribeClientFromSymbol(client, symbol)) {
          unsubscribed.push(symbol);
        }
      }
      for (const idx of indexes) {
        if (unsubscribeClientFromSymbol(client, idx.symbol)) {
          if (!unsubscribed.includes(idx.symbol)) {
            unsubscribed.push(idx.symbol);
          }
        }
      }
      sendUnsubscribedConfirmation(client, unsubscribed);
      return;
    }
    case "snapshot": {
      await handleSnapshotRequest(client, symbols);
      return;
    }
    case "ping": {
      sendMessage(client, { type: "pong" });
      return;
    }
    case "pong": {
      clientLastPong.set(client, Date.now());
      return;
    }
    case "search": {
      const pattern = typeof data.pattern === "string" ? data.pattern.trim() : "";
      if (!pattern || pattern.length < 1) {
        sendMessage(client, { type: "error", message: "Search requires a non-empty pattern" });
        return;
      }
      if (!ibConnected) {
        sendMessage(client, {
          type: "searchResults",
          pattern,
          results: [],
          disconnected: true,
        });
        return;
      }

      // Check cache
      const cached = searchCache.get(pattern.toUpperCase());
      if (cached && (Date.now() - cached.ts) < SEARCH_CACHE_TTL_MS) {
        sendMessage(client, { type: "searchResults", pattern, results: cached.results });
        return;
      }

      const reqId = nextRequestId += 1;
      searchRequestClients.set(reqId, { client, pattern: pattern.toUpperCase() });
      try {
        ib.reqMatchingSymbols(reqId, pattern);
      } catch (error) {
        searchRequestClients.delete(reqId);
        sendMessage(client, { type: "searchResults", pattern, results: [] });
      }

      // Timeout: if IB doesn't respond in 5s, return empty
      setTimeout(() => {
        if (searchRequestClients.has(reqId)) {
          searchRequestClients.delete(reqId);
          sendMessage(client, { type: "searchResults", pattern, results: [] });
        }
      }, SNAPSHOT_TIMEOUT_MS);
      return;
    }
    default: {
      sendMessage(client, {
        type: "error",
        message: `Unknown action: ${message.action}`,
      });
    }
  }
}

function rotateIBClient(newClientId) {
  reconnectGate.invalidate();
  ibClientGeneration += 1;

  // Remove callbacks before the deliberate disconnect so the old client's
  // disconnected event cannot enqueue work for the replacement generation.
  const oldClient = ib;
  try { oldClient.removeAllListeners(); } catch { /* ignore */ }
  try { oldClient.disconnect(); } catch { /* ignore */ }

  // Create new instance and rewire all events
  ib = createIBClient(newClientId);
  wireIBEvents();
  const nextClient = ib;
  const nextGeneration = ibClientGeneration;

  // Reconnect after a short delay through the same single-flight gate used by
  // disconnect recovery. Rotation and shutdown can now cancel this work too.
  reconnectGate.schedule(() => {
    if (nextClient !== ib || nextGeneration !== ibClientGeneration) return;
    try {
      nextClient.connect();
    } catch {
      ibConnected = false;
      broadcastStatus();
      scheduleReconnect();
    }
  }, 2000);
}

function scheduleReconnect() {
  if (shuttingDown) return;
  const scheduledClient = ib;
  const scheduledGeneration = ibClientGeneration;
  reconnectGate.schedule(() => {
    if (scheduledClient !== ib || scheduledGeneration !== ibClientGeneration) return;
    console.log(`Attempting IB reconnect to ${cli.ibHost}:${cli.ibPort}...`);
    try {
      scheduledClient.disconnect();
    } catch {
      // Ignore.
    }
    try {
      scheduledClient.connect();
    } catch {
      // Ignore.
      ibConnected = false;
      broadcastStatus();
      scheduleReconnect();
    }
  });
}

function wireIBEvents() {
  const wiredClient = ib;
  const wiredGeneration = ibClientGeneration;
  const isCurrentClient = () => wiredClient === ib && wiredGeneration === ibClientGeneration;

  ib.on(EventName.connected, () => {
    if (!isCurrentClient()) return;
    ibConnected = true;
    ibConnectionIssue = null;
    console.log(`IB connected (clientId ${IB_CLIENT_ID_POOL[activeClientIdIndex]})`);
    reconnectGate.invalidate();
    // Real-time market data (type 1) for ALL L1 — a trading terminal must not
    // show ~15-min delayed quotes. Was delayed-frozen (type 4) when depth was
    // off, which made the header futures strip (and every watchlist quote) lag
    // the real market; flipped to always-realtime per operator decision
    // 2026-06-14. Off-hours, IB holds the last live values (frozen) so
    // closed-market reads still return last-known prices; explicit close ticks
    // (type 9) continue to populate PriceData.close for % -change math.
    ib.reqMarketDataType(1);
    cleanupSymbolStateForReconnect();
    if (DEPTH_ENABLED) cleanupDepthForReconnect();
    searchCache.clear(); // Invalidate stale search results from IB-down period
    restoreSubscriptions();
    if (DEPTH_ENABLED) restoreDepthSubscriptions();
    broadcastStatus();
  });

  ib.on(EventName.disconnected, () => {
    if (!isCurrentClient()) return;
    if (ibConnected) {
      console.log("IB disconnected");
    }
    ibConnected = false;
    broadcastStatus();
    scheduleReconnect();
  });

  // @stoqey/ib error signature: (error, code, reqId). reqId is the request
  // (ticker) id the error pertains to, or -1 when not request-scoped. The old
  // `ib` lib delivered (error, { id, code }); we map reqId → tickerId, code → code.
  ib.on(EventName.error, (error, code, reqId) => {
    const msg = String(error?.message ?? error);
    const tickerId = reqId != null && reqId >= 0 ? reqId : undefined;
    const symbol = tickerId != null ? requestIdToSymbol.get(tickerId) : null;
    const connectionIssue = classifyIBConnectionError(msg, {
      ibHost: cli.ibHost,
      ibPort: cli.ibPort,
    });

    if (/connection is OK|farm connection is OK/i.test(msg)) {
      console.log(`\x1b[32mIB status: ${msg}\x1b[0m`);
    } else if (code === 200 || /No security definition has been found/i.test(msg)) {
      verbose(`no security def for ${symbol ?? `tickerId:${tickerId}`}`);
      if (symbol) {
        const state = symbolStates.get(symbol);
        if (state && state.tickerId === tickerId) {
          requestIdToSymbol.delete(tickerId);
          state.tickerId = null;
        }
      } else if (tickerId != null) {
        requestIdToSymbol.delete(tickerId);
      }
    } else if (code === 354 || /market data is not subscribed/i.test(msg)) {
      console.warn(`\x1b[33mIB warning: no market data subscription for ${symbol ?? `tickerId:${tickerId}`}\x1b[0m`);
      if (symbol) {
        const state = symbolStates.get(symbol);
        if (state && state.tickerId === tickerId) {
          requestIdToSymbol.delete(tickerId);
          state.tickerId = null;
        }
      }
    } else if (DEPTH_ENABLED && (code === 10089 || code === 10092 || /depth.*not (allowed|eligible)|not supported for this combination/i.test(msg)) && tickerId != null && depthRequestIdToSymbol.has(tickerId)) {
      // No L2 entitlement (10089) or deep depth unsupported for this
      // secType/exchange (10092, e.g. index options on CBOE) — soft, expected.
      // Cancel the ticket and tell the client; never latch a connection fault.
      // (Index options still serve a single CBOE BBO row via smart depth; this
      // only fires when even that is unavailable.)
      const depthSymbol = depthRequestIdToSymbol.get(tickerId);
      console.warn(`\x1b[33mIB warning: depth unavailable for ${depthSymbol} (code ${code})\x1b[0m`);
      stopDepthSubscription(depthSymbol, { keepState: true });
      emitDepthUnavailable(depthSymbol, "no-entitlement", code);
    } else if (/Fundamentals data is not allowed/i.test(msg)) {
      verbose(`fundamentals not allowed for tickerId:${tickerId} — IBIS subscription may be inactive`);
      if (tickerId != null) {
        const fundSymbol = fundamentalsRequestIds.get(tickerId);
        if (fundSymbol) {
          fundamentalsRequestIds.delete(tickerId);
          fundamentalsPending.delete(fundSymbol);
        }
      }
    } else if (/Can't find EId/i.test(msg)) {
      console.warn(`\x1b[33mIB warning: ${msg}\x1b[0m`);
    } else if (/client id is already in use/i.test(msg)) {
      activeClientIdIndex = (activeClientIdIndex + 1) % IB_CLIENT_ID_POOL.length;
      const nextId = IB_CLIENT_ID_POOL[activeClientIdIndex];
      console.warn(`\x1b[33mIB warning: client ID ${IB_CLIENT_ID_POOL[(activeClientIdIndex - 1 + IB_CLIENT_ID_POOL.length) % IB_CLIENT_ID_POOL.length]} in use, rotating to ${nextId}\x1b[0m`);
      rotateIBClient(nextId);
      return;
    } else {
      console.error(`\x1b[31mIB error: ${msg}${symbol ? ` (${symbol})` : tickerId != null ? ` (tickerId:${tickerId})` : ""}\x1b[0m`);
    }

    if (connectionIssue) {
      ibConnected = false;
      ibConnectionIssue = connectionIssue;
      broadcastStatus();
      scheduleReconnect();
    }
  });

  // Market-data farm status info codes (2103/2104/2105/2106/2108/2158).
  // @stoqey/ib delivers these as EventName.info (message, code) rather than
  // errors. Feed the latest farm code into the stale-data decision core so a
  // farm-OK signal steers recovery toward a fresh resubscribe over a socket
  // bounce. Non-farm info codes are ignored here.
  ib.on(EventName.info, (message, code) => {
    if (typeof code === "number" && isFarmStateCode(code)) {
      lastFarmStateCode = nextFarmStateCode(lastFarmStateCode, code, symbolSubscribers.size);
      verbose(`IB farm info ${code}: ${message}`);
    }
  });

  ib.on(EventName.tickPrice, (tickerId, tickType, price) => {
    onTickPrice(tickerId, tickType, price);
  });

  ib.on(EventName.tickSize, (tickerId, sizeType, size) => {
    onTickSize(tickerId, sizeType, size);
  });

  ib.on(EventName.tickSnapshotEnd, (tickerId) => {
    onTickSnapshotEnd(tickerId);
  });

  ib.on(EventName.fundamentalData, (reqId, data) => {
    onFundamentalData(reqId, data);
  });

  ib.on(EventName.tickOptionComputation, (tickerId, tickType, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice) => {
    const symbol = requestIdToSymbol.get(tickerId);
    const liveState = symbol ? symbolStates.get(symbol) : null;
    if (!liveState) return;
    markTick(symbol);

    const validTickTypes = [13, 83, 12, 82];
    if (!validTickTypes.includes(tickType)) return;

    const pd = liveState.data;
    const isModel = tickType === 13 || tickType === 83;
    if (!isModel && pd.delta !== null) return;

    const valid = (v) => v !== undefined && v !== -2 && v !== -1 && Number.isFinite(v);

    if (valid(delta)) pd.delta = delta;
    if (valid(gamma)) pd.gamma = gamma;
    if (valid(theta)) pd.theta = theta;
    if (valid(vega)) pd.vega = vega;
    if (valid(impliedVol)) pd.impliedVol = impliedVol;
    if (valid(undPrice)) pd.undPrice = undPrice;

    pd.timestamp = nowIso();
    verbose(`greeks ${symbol} tickType=${tickType} delta=${delta} iv=${impliedVol}`);
    hydrateAndBroadcast(symbol);
  });

  ib.on(EventName.symbolSamples, (reqId, contracts) => {
    const req = searchRequestClients.get(reqId);
    if (!req) return;
    searchRequestClients.delete(reqId);

    // @stoqey/ib delivers ContractDescription[] where the contract fields are
    // NESTED under `c.contract` (and primaryExchange is renamed `primaryExch`),
    // unlike the dead ib@0.2.9 lib which had them flat on `c`. Read from
    // `c.contract` (falling back to `c` for the old shape). Without this the
    // wire payload had secType:undefined, so TickerSearch's ALLOWED_SEC_TYPES
    // filter dropped every result → "No results" (regression from f69c0d4).
    const results = contracts.map((c) => {
      const k = c.contract ?? c;
      return {
        conId: k.conId,
        symbol: k.symbol,
        secType: k.secType,
        primaryExchange: k.primaryExch ?? k.primaryExchange,
        currency: k.currency,
        derivativeSecTypes: c.derivativeSecTypes || [],
      };
    });

    if (searchCache.size >= SEARCH_CACHE_MAX) {
      const oldest = searchCache.keys().next().value;
      searchCache.delete(oldest);
    }
    searchCache.set(req.pattern, { results, ts: Date.now() });

    sendMessage(req.client, {
      type: "searchResults",
      pattern: req.pattern,
      results,
    });

    verbose(`search "${req.pattern}" → ${results.length} results`);
  });

  if (DEPTH_ENABLED) {
    // Futures / single-venue depth — no market maker (arg order: id, position,
    // operation, side, price, size in the installed `ib` lib).
    ib.on(EventName.updateMktDepth, (id, position, operation, side, price, size) => {
      const key = depthRequestIdToSymbol.get(id);
      if (!key) return;
      markTick();
      applyDepthDelta(key, position, null, operation, side, price, size);
    });

    // Equity / SMART L2 — marketMaker = exchange/MPID code. @stoqey/ib appends
    // a trailing isSmartDepth arg after size; we accept it but don't re-derive
    // from it (the ladder's smart-depth label comes from state.isFutures).
    ib.on(EventName.updateMktDepthL2, (id, position, marketMaker, operation, side, price, size, _isSmartDepth) => {
      const key = depthRequestIdToSymbol.get(id);
      if (!key) return;
      markTick();
      applyDepthDelta(key, position, marketMaker, operation, side, price, size);
    });

    // Time & Sales tape — reqTickByTickData(AllLast). @stoqey/ib arity:
    // (reqId, tickType, time, price, size, tickAttribLast, exchange,
    // specialConditions). `time` is unix seconds (string); we emit ISO.
    ib.on(EventName.tickByTickAllLast, (reqId, _tickType, time, price, size, _tickAttribLast, exchange, _specialConditions) => {
      const key = tapeRequestIdToSymbol.get(reqId);
      if (!key) return;
      markTick();
      const seconds = Number(time);
      const iso = Number.isFinite(seconds) ? new Date(seconds * 1000).toISOString() : nowIso();
      applyTrade(key, price, size, exchange, iso);
    });

    // Front-month futures contract resolution (reqContractDetails).
    ib.on(EventName.contractDetails, (reqId, contractDetails) => {
      onContractDetails(reqId, contractDetails);
    });
    ib.on(EventName.contractDetailsEnd, (reqId) => {
      onContractDetailsEnd(reqId);
    });
  }
}

// Wire events on initial instance
wireIBEvents();

wss.on("connection", (client) => {
  clients.add(client);
  clientLastPong.set(client, Date.now());
  verbose(`WS client connected (total: ${clients.size})`);
  sendStatus(client);

  client.on("message", (raw) => {
    const payload = (() => {
      if (typeof raw === "string") return raw;
      if (raw instanceof Buffer) return raw.toString("utf8");
      if (raw instanceof ArrayBuffer) return new TextDecoder().decode(raw);
      return "";
    })();

    if (!payload) return;

    try {
      const data = JSON.parse(payload);
      void handleClientMessage(client, data);
    } catch {
      sendMessage(client, { type: "error", message: "Invalid JSON" });
    }
  });

  client.on("close", () => {
    const dropped = clientDroppedFrames.get(client) ?? 0;
    verbose(`WS client disconnected (remaining: ${clients.size - 1}, dropped_frames: ${dropped})`);
    disconnectClient(client);
    clients.delete(client);
  });

  client.on("error", () => {
    disconnectClient(client);
    clients.delete(client);
  });
});

ib.connect();
startBatchFlush();

/* ─── Keep-Alive Ping Interval ─────────────────────────────────────────── */
pingIntervalTimer = setInterval(() => {
  const now = Date.now();
  for (const client of clients) {
    sendMessage(client, { type: "ping" });
    const lastPong = clientLastPong.get(client);
    if (lastPong !== undefined && now - lastPong > PONG_TIMEOUT_MS) {
      console.log(`Closing unresponsive client (no pong for ${Math.round((now - lastPong) / 1000)}s)`);
      clientLastPong.delete(client);
      try { client.close(); } catch { /* ignore */ }
    }
  }
}, PING_INTERVAL_MS);

statusBroadcastTick = setInterval(() => {
  if (ibConnected) return;
  for (const client of clients) {
    sendStatus(client);
  }
}, 5000);

/* ─── Stale Data Health Check ──────────────────────────────────────────────
 * Delegate the decision to the pure machine; this layer only supplies the
 * live snapshot + executes the chosen action against the real IB socket /
 * subscriptions / service_health row. The bounded ladder (K reconnects →
 * escalate-and-alert) and the escalation cooldown live in the machine.
 */
staleCheckTimer = setInterval(() => {
  if (shuttingDown || ibGatewayRestarting) return;

  const now = Date.now();
  const marketHours = isUSMarketHours();
  const freshness = summarizeSubscriptionFreshness(
    [...symbolSubscribers.keys()].map((symbol) => {
      const state = symbolStates.get(symbol);
      return {
        active: Boolean(state?.tickerId != null),
        lastTickAt: state?.lastTickAt,
      };
    }),
    now,
  );
  const { activeSubscriptions, subscribedSymbols } = freshness;
  const elapsed = now - freshness.lastTickAt;

  // DUR-16: RTH tick heartbeat for /api/probe/freshness + the recovery action
  // are decided together (decideHealthWrite) so they can never race. The
  // heartbeat fires ONLY on a healthy cycle (action "none"); whenever the
  // ladder is acting it owns the service_health row, so an "ok" heartbeat can
  // no longer land last and clobber the escalation's "error" row — the
  // 2026-06-18 invisibility bug where a dead relay still read state=ok.
  const { action, heartbeat, clearError, degraded } = decideHealthWrite({
    now,
    lastTickAt: freshness.lastTickAt,
    ibConnected,
    isMarketHours: marketHours,
    activeSubscriptions,
    subscribedSymbols,
    reconnectCycles: staleReconnectCycles,
    farmState: lastFarmStateCode,
    lastEscalationAt,
    inError: relayHealthInError,
    lastHeartbeatAt: lastTickHeartbeatAt,
  });

  // The tick edge (onTicksRecovered) can never fire with zero subscriptions,
  // so a relay that escalated and then went idle would stay latched at "error"
  // indefinitely. Clear it here on the writer-state edge instead.
  if (clearError) {
    staleReconnectCycles = 0;
    relayHealthInError = false;
    console.log("\x1b[32m[stale-data] data plane healthy with no outstanding demand — clearing relay error state\x1b[0m");
  }

  if (heartbeat || clearError) {
    lastTickHeartbeatAt = now;
    // last_tick_at and tick_age_secs both derive from lastTickTimestamp — the
    // freshness summary's idle-substituted lastTickAt once made them
    // contradict each other (R-061).
    void writeRelayHealth("ok", {
      heartbeat: "tick",
      ...buildRelayHealthDetail(now, lastTickTimestamp, freshness),
    });
  }

  // R-061: IB nulled every subscription (error 200/354) while symbols remain
  // subscribed — a blackout, not idle. The ladder has nothing actionable
  // (reconnect/resubscribe cannot restore a revoked entitlement), so honesty
  // is the job here: latch + write a non-ok row every cycle so the freshness
  // probe and status surface see frozen prices instead of green health.
  // Recovery edges: markTick→onTicksRecovered when ticks resume, or the
  // clearError writer-state edge if demand fully drains.
  if (degraded) {
    if (!relayHealthInError) {
      console.warn(
        `\x1b[33m[stale-data] IB nulled all ${subscribedSymbols} subscriptions (0 active tickets) with no ticks for ${Math.round(elapsed / 1000)}s during market hours — writing degraded health row\x1b[0m`,
      );
    }
    relayHealthInError = true;
    void writeRelayHealth("error", {
      message: `IB nulled all market-data subscriptions (${subscribedSymbols} symbols subscribed, 0 active) with no ticks for ${Math.round(elapsed / 1000)}s during market hours`,
      reason: "subscriptions_nulled",
      ...buildRelayHealthDetail(now, lastTickTimestamp, freshness),
    });
  }

  if (action === "none") return;

  console.warn(
    `\x1b[33m[stale-data] No ticks for ${Math.round(elapsed / 1000)}s with ${activeSubscriptions} active subscriptions during market hours → ${action}\x1b[0m`,
  );

  if (action === "resubscribe") {
    resubscribeAll();
  } else if (action === "reconnect") {
    reconnectIBSocket();
  } else if (action === "escalate") {
    escalateStaleData(elapsed, activeSubscriptions);
  }
}, STALE_CHECK_INTERVAL_MS);

process.on("SIGINT", () => {
  if (shuttingDown) process.exit(0);
  shuttingDown = true;
  reconnectGate.invalidate();
  if (statusBroadcastTick) {
    clearInterval(statusBroadcastTick);
  }
  stopBatchFlush();
  if (pingIntervalTimer) {
    clearInterval(pingIntervalTimer);
    pingIntervalTimer = null;
  }
  if (staleCheckTimer) {
    clearInterval(staleCheckTimer);
    staleCheckTimer = null;
  }
  snapshotLimiter.clear();
  for (const client of clients) {
    try {
      client.close();
    } catch {
      // Ignore.
    }
  }
  for (const [requestId] of snapshotRequests) {
    clearSnapshot(requestId);
  }
  for (const state of symbolStates.values()) {
    if (state.tickerId != null) {
      try {
        ib.cancelMktData(state.tickerId);
      } catch {
        // Ignore.
      }
    }
  }
  // Flush option close cache before exit
  persistCloseCache();
  try {
    wss.close();
    ib.disconnect();
  } catch {
    // Ignore.
  }
  process.exit(0);
});

process.on("SIGTERM", () => {
  process.emit("SIGINT");
});

httpServer.on("listening", () => {
  console.log(`WebSocket server listening on ${WS_HOST}:${cli.port}`);
});
console.log(`IB realtime server listening on ${wsUrl}`);
console.log(`IB target ${cli.ibHost}:${cli.ibPort}`);
