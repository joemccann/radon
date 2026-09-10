/**
 * REL-182 (R-513, NF-3): a REST-fed headline never renders as a live feed,
 * and recovery is journalled promptly.
 *
 * The 838558bf flash-REST rows arrived as ordinary `headline` frames while
 * `upstreamState === "down"`, and the client flips to `live` on any headline
 * frame — so the banner REL-155 added cleared on the first fed print. And
 * `lastOkWriteAt` was only aged by SILENCE_MS, so the recovery `ok` row
 * lagged a full silence window after the upstream reopened.
 */
import { createServer } from "node:http";

import { afterEach, describe, expect, it } from "vitest";
import { WebSocket, WebSocketServer } from "ws";

import { connectMktnews } from "./client.js";
import { createHeadlinesHub } from "./hub.js";
import { parseFrame } from "./protocol.js";

const AUTHLESS = {
  clerkConfigured: false,
  allowUnauthenticatedDev: true,
  bindHost: "127.0.0.1",
  requireClerk: false,
};

function listenUpstream({ refuse = false } = {}) {
  const httpServer = createServer();
  const wss = new WebSocketServer({ noServer: true });
  httpServer.on("upgrade", (req, socket, head) => {
    if (refuse) {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });
  return new Promise((resolve) => {
    httpServer.listen(0, "127.0.0.1", () => {
      const { port } = httpServer.address();
      resolve({
        wss,
        url: `ws://127.0.0.1:${port}`,
        close() {
          for (const client of wss.clients) client.terminate();
          return new Promise((done) => wss.close(() => httpServer.close(done)));
        },
      });
    });
  });
}

function waitFor(predicate, timeoutMs = 4000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (predicate()) return resolve();
      if (Date.now() - start > timeoutMs) return reject(new Error("timed out"));
      setTimeout(tick, 10);
    };
    tick();
  });
}

function collect(ws) {
  const frames = [];
  ws.on("message", (raw) => {
    try {
      frames.push(JSON.parse(String(raw)));
    } catch {}
  });
  return frames;
}

function flashFrame(id, content) {
  return parseFrame(
    JSON.stringify({
      id,
      type: 0,
      time: "2026-08-30T17:56:10.000Z",
      data: { content },
    }),
  );
}

const FLASH_ROW = flashFrame("flash-1", "CPI prints hot");

describe("REL-182 — flash-REST rows are marked degraded", () => {
  const hubs = [];
  const servers = [];
  const dashboards = [];

  afterEach(async () => {
    while (dashboards.length) dashboards.pop().terminate();
    while (hubs.length) await hubs.pop().stop();
    while (servers.length) await servers.pop().close();
  });

  it("a headline fed while the upstream is down carries degraded:true", async () => {
    const server = await listenUpstream({ refuse: true });
    servers.push(server);
    const lines = [];
    // The poll dedupes on id (onlyNew), so hold the flash row back until the
    // dashboard is attached — otherwise it is fed pre-connect and only the
    // snapshot carries it.
    let releaseRow = false;
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 60_000,
      loadHistory: async () => (releaseRow ? [FLASH_ROW] : []),
      flashPollMs: 25,
      silenceMs: 10_000,
      log: (line) => lines.push(line),
    });
    hubs.push(hub);
    await hub.listen();
    await waitFor(() => lines.some((l) => /reconnect attempt=/.test(l)));

    const ws = new WebSocket(hub.address());
    dashboards.push(ws);
    const frames = collect(ws);
    await waitFor(() => frames.some((f) => f.type === "snapshot"));
    releaseRow = true;
    await waitFor(() => frames.some((f) => f.type === "headline"));
    const headline = frames.find((f) => f.type === "headline");
    expect(headline.degraded).toBe(true);
  });
});

describe("REL-182 — recovery ok row lands promptly", () => {
  const hubs = [];
  const servers = [];

  afterEach(async () => {
    while (hubs.length) await hubs.pop().stop();
    while (servers.length) await servers.pop().close();
  });

  it("a reopen after an outage writes ok within two ticks, not a silence window later", async () => {
    const health = [];
    const server = await listenUpstream();
    servers.push(server);
    let seq = 0;
    server.wss.on("connection", (ws) => {
      seq += 1;
      ws.send(JSON.stringify({ data: { ...FLASH_ROW, id: `flash-${seq}` } }));
    });
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10,
      silenceMs: 5_000,
      healthTickMs: 100,
      failureThreshold: 1,
      recordHealth: (state, extra) => health.push([state, Date.now()]),
    });
    hubs.push(hub);
    await hub.listen();
    // First ok lands (fresh hub: lastOkWriteAt starts at 0).
    await waitFor(() => health.some(([state]) => state === "ok"), 2_000);
    const firstOkCount = health.filter(([state]) => state === "ok").length;

    // Outage: drop the upstream connection, then let it reconnect and frame.
    // The budget is counted in HEALTH TICKS, not elapsed ms (T-418): an
    // event-loop stall coalesces missed interval firings for the hub's health
    // timer and for this counter alike, so a loaded machine cannot false-red a
    // margin that is really "two 100ms ticks".
    for (const client of server.wss.clients) client.terminate();
    let ticksSinceReopen = 0;
    const tickCounter = setInterval(() => {
      ticksSinceReopen += 1;
    }, 100);
    tickCounter.unref?.();
    let ticksToOk = null;
    try {
      await waitFor(() => {
        if (health.filter(([state]) => state === "ok").length <= firstOkCount) return false;
        ticksToOk ??= ticksSinceReopen;
        return true;
      }, 2_000);
    } finally {
      clearInterval(tickCounter);
    }
    // Pre-fix the second ok waits a full silenceMs (5s = 50 health ticks)
    // after the first; the recovery reset makes it land within ~two ticks.
    expect(ticksToOk).toBeLessThanOrEqual(3);
  });
});
