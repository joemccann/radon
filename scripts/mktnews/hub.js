import http from "node:http";
import { WebSocketServer } from "ws";

import { reconnectDelayMs } from "../lib/reconnectGate.js";
import {
  resolveRelaySecurityConfig,
  resolveUpgradeTarget,
  shouldSkipTicketValidation,
} from "../lib/wsTrust.js";
import { connectMktnews, fetchFlashHistory } from "./client.js";
import {
  MAX_FRAME_BYTES,
  RING_SIZE,
  containsUpstreamHost,
  toHeadline,
} from "./normalize.js";
import { parseFrame } from "./protocol.js";

export const HEADLINES_PATH = "/ws-headlines";
export const DEFAULT_LISTEN_PORT = 8766;
export const MAX_CLIENTS = 32;
// R-459: the serve path dropped the client's `reconnect` / `error` events and
// wrote no service_health row, so a refused or silent upstream left only a
// status pill on whichever dashboard happened to be open. The hub now
// journals every upstream event and keeps a `mktnews-hub` row: `error` after
// FAILURE_THRESHOLD consecutive failed dials or SILENCE_MS without any frame
// (time heartbeats included), `ok` at most every SILENCE_MS while frames flow.
export const FAILURE_THRESHOLD = 3;
export const SILENCE_MS = 5 * 60_000;
const HEALTH_TICK_MS = 60_000;
const TICKET_VALIDATION_TIMEOUT_MS = 3000;
const LOOPBACK_LISTEN_HOSTS = new Set(["127.0.0.1", "::1"]);

function isOriginFormPath(requestUrl, path) {
  const raw = String(requestUrl ?? "");
  return raw === path || raw.startsWith(`${path}?`);
}

function resolveHubListenHost(listenHost, bindHost) {
  const candidate = listenHost || bindHost || "127.0.0.1";
  return LOOPBACK_LISTEN_HOSTS.has(candidate) ? candidate : "127.0.0.1";
}

function frameByteLength(parsed) {
  if (parsed && typeof parsed.raw === "string") return Buffer.byteLength(parsed.raw);
  try {
    return Buffer.byteLength(JSON.stringify(parsed ?? ""));
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

function envelopeLeaksUpstream(payload) {
  if (!payload || typeof payload !== "object") return containsUpstreamHost(payload);
  if (payload.type === "snapshot") {
    return (payload.items ?? []).some((item) => envelopeLeaksUpstream({ type: "headline", item }));
  }
  if (payload.type === "headline" && payload.item) {
    const { content: _content, ...rest } = payload.item;
    return containsUpstreamHost(rest);
  }
  return containsUpstreamHost(payload);
}

function sendJson(socket, payload) {
  if (socket.readyState !== 1) return;
  if (envelopeLeaksUpstream(payload)) return;
  socket.send(JSON.stringify(payload));
}

export function createHeadlinesHub({
  listenHost,
  listenPort = 0,
  path = HEADLINES_PATH,
  security = resolveRelaySecurityConfig(),
  ticketValidateUrl = process.env.TICKET_VALIDATE_URL || "http://127.0.0.1:8321/ws-ticket/validate",
  fetchImpl = fetch,
  connectUpstream = null,
  maxClients = MAX_CLIENTS,
  ringSize = RING_SIZE,
  maxFrameBytes = MAX_FRAME_BYTES,
  delayFn = reconnectDelayMs,
  loadHistory = null,
  store = null,
  log = (line) => process.stderr.write(`${line}\n`),
  recordHealth = null,
  failureThreshold = FAILURE_THRESHOLD,
  silenceMs = SILENCE_MS,
  healthTickMs = HEALTH_TICK_MS,
} = {}) {
  const host = resolveHubListenHost(listenHost, security.bindHost);
  const ring = [];
  const clients = new Set();
  let upstream = null;
  let closed = false;
  let healthTimer = null;
  let lastFrameAt = null;
  let startedAt = null;
  let consecutiveFailures = 0;
  let lastOkWriteAt = 0;

  function ingest(parsed) {
    if (frameByteLength(parsed) > maxFrameBytes) return false;
    const item = toHeadline(parsed);
    if (!item) return false;
    const { content: _content, ...meta } = item;
    if (containsUpstreamHost(meta)) return false;
    pushToRing(item);
    persist(item);
    for (const client of clients) sendJson(client, { type: "headline", item });
    return true;
  }

  function pushToRing(item) {
    const existing = ring.findIndex((row) => row.id === item.id);
    if (existing >= 0) ring.splice(existing, 1);
    ring.push(item);
    while (ring.length > ringSize) ring.shift();
  }

  function persist(item) {
    if (!store) return;
    Promise.resolve()
      .then(() => store.put(item))
      .catch((err) => {
        process.stderr.write(`[mktnews] ring persist failed: ${err?.message ?? err}\n`);
      });
  }

  async function restoreRing() {
    if (!store || closed) return;
    try {
      const items = await store.load();
      if (!Array.isArray(items)) return;
      for (const item of items) pushToRing(item);
    } catch (err) {
      process.stderr.write(`[mktnews] ring restore failed: ${err?.message ?? err}\n`);
    }
  }

  function ingestRaw(raw) {
    const bytes = Buffer.isBuffer(raw) ? raw.length : Buffer.byteLength(String(raw));
    if (bytes > maxFrameBytes) return false;
    return ingest(parseFrame(raw));
  }

  function broadcastStatus(state) {
    for (const client of clients) sendJson(client, { type: "status", state });
  }

  const httpServer = http.createServer((_req, res) => {
    res.writeHead(426, { "Content-Type": "text/plain" });
    res.end("WebSocket upgrade required");
  });
  const wss = new WebSocketServer({ noServer: true, maxPayload: maxFrameBytes });

  httpServer.on("upgrade", async (req, socket, head) => {
    const target = resolveUpgradeTarget(req);
    if (!isOriginFormPath(req.url, path) || target.pathname !== path) {
      socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
      socket.destroy();
      return;
    }
    if (clients.size >= maxClients) {
      socket.write("HTTP/1.1 503 Service Unavailable\r\n\r\n");
      socket.destroy();
      return;
    }

    const remoteAddr = socket.remoteAddress || "";
    const skipTicket = shouldSkipTicketValidation({
      clerkConfigured: security.clerkConfigured,
      allowUnauthenticatedDev: security.allowUnauthenticatedDev,
      remoteAddr,
      headers: req.headers,
    });
    if (!skipTicket) {
      const ticket = target.searchParams.get("ticket");
      if (!ticket) {
        socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
        socket.destroy();
        return;
      }
      try {
        const res = await fetchImpl(ticketValidateUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket }),
          signal: AbortSignal.timeout(TICKET_VALIDATION_TIMEOUT_MS),
        });
        if (!res.ok) {
          socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
          socket.destroy();
          return;
        }
      } catch {
        socket.write("HTTP/1.1 503 Service Unavailable\r\n\r\n");
        socket.destroy();
        return;
      }
    }

    wss.handleUpgrade(req, socket, head, (ws) => {
      clients.add(ws);
      sendJson(ws, { type: "snapshot", items: ring.slice() });
      ws.on("close", () => clients.delete(ws));
      ws.on("error", () => clients.delete(ws));
      ws.on("message", () => {
        // Client is receive-only. Drop inbound payloads.
      });
    });
  });

  function writeHealth(state, message) {
    if (!recordHealth) return;
    Promise.resolve()
      .then(() => recordHealth(state, message ? { error: { message } } : {}))
      .catch((err) => {
        log(`[mktnews] health write failed: ${err?.message ?? err}`);
      });
  }

  function healthTick() {
    if (closed) return;
    const now = Date.now();
    const sinceFrame = now - (lastFrameAt ?? startedAt);
    if (sinceFrame > silenceMs) {
      writeHealth("error", `no upstream frame for ${Math.round(sinceFrame / 1000)}s`);
      return;
    }
    if (consecutiveFailures >= failureThreshold) return;
    if (lastFrameAt != null && now - lastOkWriteAt >= silenceMs) {
      lastOkWriteAt = now;
      writeHealth("ok");
    }
  }

  function onUpstreamStatus(event) {
    if (event.event === "open") {
      consecutiveFailures = 0;
      log(`[mktnews] upstream open ${event.url}`);
      broadcastStatus("upstream-open");
    } else if (event.event === "close") {
      log(`[mktnews] close ${event.code} ${event.reason}`);
      broadcastStatus("upstream-down");
    } else if (event.event === "idle") {
      log(`[mktnews] idle: no upstream frame for ${event.idleMs}ms, terminating`);
    } else if (event.event === "reconnect") {
      consecutiveFailures = event.attempt;
      log(`[mktnews] reconnect attempt=${event.attempt} delayMs=${event.delayMs}`);
      if (event.attempt >= failureThreshold) {
        writeHealth("error", `upstream unreachable: ${event.attempt} consecutive failed attempts`);
      }
    } else if (event.event === "error") {
      log(`[mktnews] error ${event.error}`);
    }
  }

  function startUpstream() {
    if (!connectUpstream || closed) return;
    startedAt = Date.now();
    upstream = connectUpstream({
      delayFn,
      maxPayload: maxFrameBytes,
      onMessage: (msg) => {
        lastFrameAt = Date.now();
        ingest(msg);
      },
      onStatus: onUpstreamStatus,
    });
    if (recordHealth) {
      healthTimer = setInterval(healthTick, healthTickMs);
      healthTimer.unref?.();
    }
  }

  async function seedRing() {
    if (!loadHistory || closed) return;
    try {
      const items = await loadHistory();
      if (!Array.isArray(items)) return;
      for (const item of items) ingest(item);
    } catch {
      // Serve an empty cache rather than blocking the hub.
    }
  }

  async function listen() {
    await restoreRing();
    await seedRing();
    return new Promise((resolve) => {
      httpServer.listen(listenPort, host, () => {
        startUpstream();
        resolve();
      });
    });
  }

  async function stop() {
    closed = true;
    if (healthTimer != null) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
    upstream?.stop?.();
    for (const client of clients) {
      try {
        client.close();
      } catch {
        // already closing
      }
    }
    clients.clear();
    await new Promise((resolve) => httpServer.close(resolve));
  }

  return {
    listen,
    stop,
    ingest,
    ingestRaw,
    get listenHost() {
      return host;
    },
    get ring() {
      return ring.slice();
    },
    get clientCount() {
      return clients.size;
    },
    address() {
      const addr = httpServer.address();
      if (!addr || typeof addr === "string") return null;
      return `ws://127.0.0.1:${addr.port}${path}`;
    },
  };
}

export async function startHeadlinesHub(overrides = {}) {
  if (overrides.security?.requireClerk && !overrides.security.clerkConfigured) {
    throw new Error("Headlines hub requires CLERK_JWKS_URL and CLERK_ISSUER");
  }
  const security = overrides.security || resolveRelaySecurityConfig();
  if (security.requireClerk && !security.clerkConfigured) {
    throw new Error("Headlines hub requires CLERK_JWKS_URL and CLERK_ISSUER");
  }
  const hub = createHeadlinesHub({
    ...overrides,
    security,
    listenPort: overrides.listenPort ?? DEFAULT_LISTEN_PORT,
    connectUpstream: overrides.connectUpstream === undefined ? connectMktnews : overrides.connectUpstream,
    loadHistory: overrides.loadHistory === undefined ? fetchFlashHistory : overrides.loadHistory,
  });
  await hub.listen();
  return hub;
}
