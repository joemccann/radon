#!/usr/bin/env node

/**
 * Interactive Brokers Real-Time Price Server (Node.js)
 *
 * This is a direct replacement for the Python websocket server.
 */

import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import { WebSocketServer } from "ws";
import IB from "ib";
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

const DEFAULT_WS_PORT = 8765;
const DEFAULT_IB_HOST = "127.0.0.1";
const DEFAULT_IB_PORT = 4001;
const RECONNECT_MS = 5000;
const SNAPSHOT_TIMEOUT_MS = 5000;

/* ─── Keep-Alive Ping/Pong ─────────────────────────────────────────────── */
const PING_INTERVAL_MS = 30_000;
const PONG_TIMEOUT_MS = 65_000; // 30s * 2 + 5s grace

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
const WS_HOST = "0.0.0.0";
const wsUrl = `ws://${WS_HOST}:${cli.port}`;

function verbose(...args) {
  if (cli.verbose) console.log(`\x1b[90m[verbose]\x1b[0m`, ...args);
}

const IB_CLIENT_ID_POOL = [cli.ibClientId, cli.ibClientId + 1, cli.ibClientId + 2];
let activeClientIdIndex = 0;

let ib = createIBClient(IB_CLIENT_ID_POOL[0]);

function createIBClient(clientId) {
  return new IB({
    host: cli.ibHost,
    port: cli.ibPort,
    clientId,
  });
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

const wss = new WebSocketServer({ host: WS_HOST, port: cli.port });

const clients = new Set();
const symbolSubscribers = new Map();
const clientSymbols = new Map();
const symbolStates = new Map();
const requestIdToSymbol = new Map();
const snapshotRequests = new Map();
const fundamentalsStore = new LRUCache(500); // symbol → FundamentalsData (LRU-capped)

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
let reconnectTimer = null;
let nextRequestId = 1;
let statusBroadcastTick = null;
let ibConnectionIssue = null;

/* ─── Stale Data Detection ─────────────────────────────────────────────────
 * IB Gateway can enter a state where the TCP connection is alive but the
 * data plane stops delivering ticks. Detect this by tracking the last tick
 * timestamp: if we have active subscriptions during market hours but haven't
 * received a tick in STALE_DATA_THRESHOLD_MS, restart IB Gateway.
 */
const STALE_DATA_THRESHOLD_MS = 45_000; // 45s without a tick during market hours
const STALE_CHECK_INTERVAL_MS = 30_000; // Check every 30s
let lastTickTimestamp = Date.now();
let staleCheckTimer = null;
let ibGatewayRestarting = false;

function isUSMarketHours() {
  // Convert to ET and check if within 9:30-16:00 Mon-Fri
  const now = new Date();
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
  const et = new Date(etStr);
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const hours = et.getHours();
  const minutes = et.getMinutes();
  const timeMinutes = hours * 60 + minutes;
  return timeMinutes >= 9 * 60 + 30 && timeMinutes <= 16 * 60;
}

async function restartIBGateway() {
  if (ibGatewayRestarting) return;
  ibGatewayRestarting = true;
  console.log("\x1b[31m[stale-data] No ticks received during market hours — restarting IB Gateway\x1b[0m");
  try {
    const { execSync } = require("child_process");
    const homeDir = require("os").homedir();
    execSync(`${homeDir}/ibc/bin/restart-secure-ibc-service.sh`, {
      timeout: 60_000,
      stdio: "pipe",
    });
    console.log("[stale-data] IB Gateway restart initiated — waiting for reconnect");
  } catch (err) {
    console.error("[stale-data] Failed to restart IB Gateway:", err.message);
  } finally {
    // Allow another restart attempt after 120s cooldown
    setTimeout(() => { ibGatewayRestarting = false; }, 120_000);
  }
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
    buf.clear();
    sendMessage(client, { type: "batch", updates });
  }
}

function startBatchFlush() {
  if (batchFlushTimer) return;
  batchFlushTimer = setInterval(flushBatches, BATCH_INTERVAL_MS);
}

function stopBatchFlush() {
  if (batchFlushTimer) {
    clearInterval(batchFlushTimer);
    batchFlushTimer = null;
  }
}

function removeBatchBuffer(client) {
  clientBatchBuffers.delete(client);
}

function sendMessage(client, payload) {
  try {
    if (client.readyState === client.OPEN) {
      client.send(JSON.stringify(payload));
    }
  } catch {
    // Ignore send failures.
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
    symbolStates.set(key, state);
    requestIdToSymbol.set(nextTickerId, key);

    // Request fundamentals data for stocks (one-shot, cached)
    requestFundamentals(key, ibContract);
  } catch (error) {
    console.error(`Failed to subscribe ${key}:`, error);
  }
}

function stopLiveSubscription(symbol) {
  const state = symbolStates.get(symbol);
  if (!state || state.tickerId == null) return;
  try {
    ib.cancelMktData(state.tickerId);
  } catch {
    // Ignore.
  }
  requestIdToSymbol.delete(state.tickerId);
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

  return unsubscribed;
}

function disconnectClient(client) {
  removeBatchBuffer(client);
  clientLastPong.delete(client);
  const clientSet = clientSymbols.get(client);
  if (!clientSet) {
    return;
  }

  for (const symbol of clientSet) {
    const subscribers = symbolSubscribers.get(symbol);
    if (!subscribers) continue;

    subscribers.delete(client);
    if (subscribers.size === 0) {
      symbolSubscribers.delete(symbol);
      stopLiveSubscription(symbol);
    }
  }

  clientSymbols.delete(client);
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
    const contract = ib.contract.stock(symbol, "SMART", "USD");
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
        ib.reqMktData(requestId, contract, "233,165", true, false);
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
  lastTickTimestamp = Date.now();
  const symbol = requestIdToSymbol.get(tickerId);
  const liveState = symbol ? symbolStates.get(symbol) : null;
  const snapshotState = snapshotRequests.get(tickerId);

  if (liveState) {
    updatePriceFromTickPrice(liveState.data, tickType, price);
    // Cache option close prices to disk for after-hours availability
    updateOptionCloseCache(symbol, liveState.data.close);
    verbose(`tick ${symbol} type=${tickType} price=${price}`);
    hydrateAndBroadcast(symbol);
  }
  if (snapshotState) {
    updatePriceFromTickPrice(snapshotState.data, tickType, price);
  }
}

function onTickSize(tickerId, sizeType, size) {
  lastTickTimestamp = Date.now();
  const symbol = requestIdToSymbol.get(tickerId);
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
    const state = symbolStates.get(key);
    if (state) {
      sendToSymbolSubscribers(key, {
        type: "price",
        symbol: key,
        data: state.data,
      });
    }
  }
}

async function handleClientMessage(client, data) {
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
        const ibContract = ib.contract.stock(symbol, "SMART", "USD");
        ensureSymbolState(symbol, ibContract);
        if (ibConnected) {
          startLiveSubscription(symbol, ibContract);
          const state = symbolStates.get(symbol);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol,
              data: state.data,
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
        const ibContract = ib.contract.option(c.symbol, c.expiry, c.strike, c.right);
        ensureSymbolState(key, ibContract);
        if (ibConnected) {
          startLiveSubscription(key, ibContract);
          const state = symbolStates.get(key);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol: key,
              data: state.data,
            });
          }
          subscribed.push(key);
        }
      }
      // Index subscriptions (e.g. VIX, VVIX on CBOE)
      for (const idx of indexes) {
        const key = idx.symbol;
        subscribeClientToSymbol(client, key);
        const ibContract = ib.contract.index(idx.symbol, "USD", idx.exchange);
        ensureSymbolState(key, ibContract);
        if (ibConnected) {
          startLiveSubscription(key, ibContract);
          const state = symbolStates.get(key);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol: key,
              data: state.data,
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
        sendMessage(client, { type: "searchResults", pattern, results: [] });
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
  // Tear down old instance
  try { ib.disconnect(); } catch { /* ignore */ }
  try { ib.removeAllListeners(); } catch { /* ignore */ }

  // Create new instance and rewire all events
  ib = createIBClient(newClientId);
  wireIBEvents();

  // Reconnect after a short delay
  setTimeout(() => {
    try { ib.connect(); } catch { /* ignore — scheduleReconnect will retry */ }
  }, 2000);
}

function scheduleReconnect() {
  if (shuttingDown || reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    console.log(`Attempting IB reconnect to ${cli.ibHost}:${cli.ibPort}...`);
    try {
      ib.disconnect();
    } catch {
      // Ignore.
    }
    try {
      ib.connect();
    } catch {
      // Ignore.
      ibConnected = false;
      broadcastStatus();
      scheduleReconnect();
    }
  }, RECONNECT_MS);
}

function wireIBEvents() {
  ib.on("connected", () => {
    ibConnected = true;
    ibConnectionIssue = null;
    console.log(`IB connected (clientId ${IB_CLIENT_ID_POOL[activeClientIdIndex]})`);
    reconnectTimer = null;
    // Request Delayed-Frozen data so closed-market queries return last known prices
    // Type 4 cascades: Live → Delayed → Frozen → Delayed-Frozen
    ib.reqMarketDataType(4);
    cleanupSymbolStateForReconnect();
    searchCache.clear(); // Invalidate stale search results from IB-down period
    restoreSubscriptions();
    broadcastStatus();
  });

  ib.on("disconnected", () => {
    if (ibConnected) {
      console.log("IB disconnected");
    }
    ibConnected = false;
    broadcastStatus();
    scheduleReconnect();
  });

  ib.on("error", (error, data) => {
    const msg = String(error?.message ?? error);
    const tickerId = data?.id;
    const code = data?.code;
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

  ib.on("tickPrice", (tickerId, tickType, price) => {
    onTickPrice(tickerId, tickType, price);
  });

  ib.on("tickSize", (tickerId, sizeType, size) => {
    onTickSize(tickerId, sizeType, size);
  });

  ib.on("tickSnapshotEnd", (tickerId) => {
    onTickSnapshotEnd(tickerId);
  });

  ib.on("fundamentalData", (reqId, data) => {
    onFundamentalData(reqId, data);
  });

  ib.on("tickOptionComputation", (tickerId, tickType, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice) => {
    const symbol = requestIdToSymbol.get(tickerId);
    const liveState = symbol ? symbolStates.get(symbol) : null;
    if (!liveState) return;

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

  ib.on("symbolSamples", (reqId, contracts) => {
    const req = searchRequestClients.get(reqId);
    if (!req) return;
    searchRequestClients.delete(reqId);

    const results = contracts.map((c) => ({
      conId: c.conId,
      symbol: c.symbol,
      secType: c.secType,
      primaryExchange: c.primaryExchange,
      currency: c.currency,
      derivativeSecTypes: c.derivativeSecTypes || [],
    }));

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
    verbose(`WS client disconnected (remaining: ${clients.size - 1})`);
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
 * If connected to IB with active subscriptions during market hours but no
 * ticks received in STALE_DATA_THRESHOLD_MS, auto-restart IB Gateway.
 */
staleCheckTimer = setInterval(() => {
  if (!ibConnected || shuttingDown || ibGatewayRestarting) return;
  if (!isUSMarketHours()) return;

  const activeSubscriptions = symbolSubscribers.size;
  if (activeSubscriptions === 0) return;

  const elapsed = Date.now() - lastTickTimestamp;
  if (elapsed > STALE_DATA_THRESHOLD_MS) {
    console.warn(
      `\x1b[33m[stale-data] No ticks for ${Math.round(elapsed / 1000)}s with ${activeSubscriptions} active subscriptions during market hours\x1b[0m`,
    );
    restartIBGateway();
  }
}, STALE_CHECK_INTERVAL_MS);

process.on("SIGINT", () => {
  if (shuttingDown) process.exit(0);
  shuttingDown = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
  }
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

console.log(`IB realtime server listening on ${wsUrl}`);
console.log(`IB target ${cli.ibHost}:${cli.ibPort}`);
