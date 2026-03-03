#!/usr/bin/env node

/**
 * Interactive Brokers Real-Time Price Server (Node.js)
 *
 * This is a direct replacement for the Python websocket server.
 */

import process from "node:process";
import { WebSocketServer } from "ws";
import IB from "ib";

const DEFAULT_WS_PORT = 8765;
const DEFAULT_IB_HOST = "127.0.0.1";
const DEFAULT_IB_PORT = 4001;
const RECONNECT_MS = 5000;
const SNAPSHOT_TIMEOUT_MS = 5000;

function parseArgs(argv) {
  const args = {
    port: DEFAULT_WS_PORT,
    ibHost: DEFAULT_IB_HOST,
    ibPort: DEFAULT_IB_PORT,
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

function normalizeNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

function nowIso() {
  return new Date().toISOString();
}

function createPriceData(symbol) {
  return {
    symbol,
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    timestamp: nowIso(),
  };
}

function updateDerivedLast(data) {
  if (data.last == null && data.bid != null && data.ask != null) {
    const midpoint = (data.bid + data.ask) / 2;
    data.last = Number.isFinite(midpoint) ? Number(midpoint.toFixed(4)) : null;
    data.lastIsCalculated = true;
  }
}

function updatePriceFromTickPrice(data, tickType, value) {
  const { TICK_TYPE } = IB;
  switch (tickType) {
    case TICK_TYPE.BID:
      data.bid = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.ASK:
      data.ask = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.LAST:
      data.last = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.HIGH:
      data.high = normalizeNumber(value);
      break;
    case TICK_TYPE.LOW:
      data.low = normalizeNumber(value);
      break;
    case TICK_TYPE.OPEN:
      data.open = normalizeNumber(value);
      break;
    case TICK_TYPE.CLOSE:
      data.close = normalizeNumber(value);
      break;
    case TICK_TYPE.VOLUME:
      data.volume = normalizeNumber(value);
      break;
    default:
      break;
  }

  if (data.last == null) {
    updateDerivedLast(data);
  }
  data.timestamp = nowIso();
}

function updatePriceFromTickSize(data, sizeType, value) {
  const { TICK_TYPE } = IB;
  switch (sizeType) {
    case TICK_TYPE.BID_SIZE:
      data.bidSize = normalizeNumber(value);
      break;
    case TICK_TYPE.ASK_SIZE:
      data.askSize = normalizeNumber(value);
      break;
    case TICK_TYPE.VOLUME:
      data.volume = normalizeNumber(value);
      break;
    case TICK_TYPE.LAST_SIZE:
      break;
    default:
      break;
  }

  data.timestamp = nowIso();
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
  return { action, symbols, contracts };
}

const cli = parseArgs(process.argv.slice(2));
const wsUrl = `ws://0.0.0.0:${cli.port}`;

const ib = new IB({
  host: cli.ibHost,
  port: cli.ibPort,
  clientId: 100,
});

const wss = new WebSocketServer({ host: "0.0.0.0", port: cli.port });

const clients = new Set();
const symbolSubscribers = new Map();
const clientSymbols = new Map();
const symbolStates = new Map();
const requestIdToSymbol = new Map();
const snapshotRequests = new Map();

let ibConnected = false;
let shuttingDown = false;
let reconnectTimer = null;
let nextRequestId = 1;
let statusBroadcastTick = null;

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

function startLiveSubscription(key, ibContract) {
  if (!ibConnected) return;

  const existing = symbolStates.get(key);
  const nextTickerId = nextRequestId += 1;
  const state = existing ?? {
    tickerId: null,
    contract: ibContract,
    data: createPriceData(key),
  };

  if (state.tickerId != null) {
    try {
      ib.cancelMktData(state.tickerId);
    } catch {
      // Ignore.
    }
    requestIdToSymbol.delete(state.tickerId);
  }

  try {
    ib.reqMktData(nextTickerId, ibContract, "233", false, false);
    state.tickerId = nextTickerId;
    state.contract = ibContract;
    state.data.timestamp = nowIso();
    symbolStates.set(key, state);
    requestIdToSymbol.set(nextTickerId, key);
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
      ib.reqMktData(requestId, contract, "233", true, false);
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
  sendToSymbolSubscribers(symbol, {
    type: "price",
    symbol,
    data: state.data,
  });
}

function onTickPrice(tickerId, tickType, price) {
  const symbol = requestIdToSymbol.get(tickerId);
  const liveState = symbol ? symbolStates.get(symbol) : null;
  const snapshotState = snapshotRequests.get(tickerId);

  if (liveState) {
    updatePriceFromTickPrice(liveState.data, tickType, price);
    hydrateAndBroadcast(symbol);
  }
  if (snapshotState) {
    updatePriceFromTickPrice(snapshotState.data, tickType, price);
  }
}

function onTickSize(tickerId, sizeType, size) {
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

function restoreSubscriptions() {
  const keys = [...symbolSubscribers.keys()];
  for (const key of keys) {
    const existing = symbolStates.get(key);
    // Use stored contract if available (option contracts), otherwise build stock contract
    const ibContract = existing?.contract ?? ib.contract.stock(key, "SMART", "USD");
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
  switch (message.action) {
    case "subscribe": {
      const subscribed = [];
      // Stock subscriptions (backward compatible)
      for (const symbol of symbols) {
        subscribeClientToSymbol(client, symbol);
        if (ibConnected) {
          const ibContract = ib.contract.stock(symbol, "SMART", "USD");
          startLiveSubscription(symbol, ibContract);
          const state = symbolStates.get(symbol);
          if (state) {
            sendMessage(client, {
              type: "price",
              symbol,
              data: state.data,
            });
          }
          subscribed.push(symbol);
        }
      }
      // Option contract subscriptions
      for (const c of contracts) {
        const key = optionKey(c);
        subscribeClientToSymbol(client, key);
        if (ibConnected) {
          const ibContract = ib.contract.option(c.symbol, c.expiry, c.strike, c.right);
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
    default: {
      sendMessage(client, {
        type: "error",
        message: `Unknown action: ${message.action}`,
      });
    }
  }
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

ib.on("connected", () => {
  ibConnected = true;
  console.log("IB connected");
  reconnectTimer = null;
  cleanupSymbolStateForReconnect();
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

  if (/connection is OK|farm connection is OK/i.test(msg)) {
    console.log(`\x1b[32mIB status: ${msg}\x1b[0m`);
  } else if (code === 354 || /market data is not subscribed/i.test(msg)) {
    // IB account lacks market data subscription for this symbol
    console.warn(`\x1b[33mIB warning: no market data subscription for ${symbol ?? `tickerId:${tickerId}`}\x1b[0m`);
    // Clean up the failed subscription so we stop retrying
    if (symbol) {
      const state = symbolStates.get(symbol);
      if (state && state.tickerId === tickerId) {
        requestIdToSymbol.delete(tickerId);
        state.tickerId = null;
      }
    }
  } else if (/Can't find EId/i.test(msg)) {
    // Cascading error from a rejected subscription — suppress
    console.warn(`\x1b[33mIB warning: ${msg}\x1b[0m`);
  } else {
    console.error(`\x1b[31mIB error: ${msg}${symbol ? ` (${symbol})` : tickerId != null ? ` (tickerId:${tickerId})` : ""}\x1b[0m`);
  }
  broadcastStatus();
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

wss.on("connection", (client) => {
  clients.add(client);
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
    disconnectClient(client);
    clients.delete(client);
  });

  client.on("error", () => {
    disconnectClient(client);
    clients.delete(client);
  });
});

ib.connect();

statusBroadcastTick = setInterval(() => {
  if (ibConnected) return;
  for (const client of clients) {
    sendStatus(client);
  }
}, 5000);

process.on("SIGINT", () => {
  if (shuttingDown) process.exit(0);
  shuttingDown = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
  }
  if (statusBroadcastTick) {
    clearInterval(statusBroadcastTick);
  }
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
