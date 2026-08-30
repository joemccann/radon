/**
 * 2026-08-30 headlines incident: the production hub's upstream WS dial to
 * api.mktnews.net failed repeatedly (vendor edge is Cloudflare; the VPS IP
 * was refused while other networks connected fine), so every dashboard sat
 * on "HEADLINES FEED DOWN" for 16+ minutes even though the SAME data was
 * reachable over the vendor's flash REST endpoint — the one the hub already
 * calls at every boot to seed its ring (`loadHistory`/`fetchFlashHistory`).
 *
 * Two gaps pinned here:
 *  1. While the upstream WS is down, the hub polls flash history and keeps
 *     feeding the tape (new rows only — no re-broadcast, no ring reorder).
 *  2. A dashboard that connects DURING the outage is told `upstream-down`
 *     right after its snapshot; before, it rendered a stale tape as live.
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

function listenUpstream({ refuse = false, onConnection = null } = {}) {
  const httpServer = createServer();
  const wss = new WebSocketServer({ noServer: true });
  httpServer.on("upgrade", (req, socket, head) => {
    if (refuse) {
      socket.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });
  if (onConnection) wss.on("connection", onConnection);
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

function waitFor(predicate, timeoutMs = 3000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (predicate()) return resolve();
      if (Date.now() - start > timeoutMs) return reject(new Error("timed out waiting for condition"));
      setTimeout(tick, 10);
    };
    tick();
  });
}

function collect(ws) {
  const frames = [];
  ws.on("message", (data) => frames.push(JSON.parse(data.toString())));
  return frames;
}

describe("flash poll fallback while the upstream WS is down", () => {
  const hubs = [];
  const servers = [];
  const dashboards = [];

  afterEach(async () => {
    while (dashboards.length) dashboards.pop().terminate();
    while (hubs.length) await hubs.pop().stop();
    while (servers.length) await servers.pop().close();
  });

  async function boot(overrides = {}) {
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      log: () => {},
      ...overrides,
    });
    hubs.push(hub);
    await hub.listen();
    return hub;
  }

  function dashboard(hub) {
    const ws = new WebSocket(hub.address());
    dashboards.push(ws);
    return ws;
  }

  it("feeds the tape from flash history once the upstream dial fails", async () => {
    const server = await listenUpstream({ refuse: true });
    servers.push(server);
    let historyCalls = 0;
    const loadHistory = async () => {
      historyCalls += 1;
      // Boot seed sees an empty history; the outage polls see the two rows
      // the REST lane still serves.
      if (historyCalls === 1) return [];
      return [flashFrame("poll-1", "First polled print."), flashFrame("poll-2", "Second polled print.")];
    };
    const hub = await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10,
      loadHistory,
      flashPollMs: 30,
    });
    const ws = dashboard(hub);
    const frames = collect(ws);
    await waitFor(() =>
      frames.filter((f) => f.type === "headline").map((f) => f.item.id).includes("poll-2"),
    );
    expect(hub.ring.map((row) => row.id)).toEqual(["poll-1", "poll-2"]);
  });

  it("never re-broadcasts a row the ring already has", async () => {
    const server = await listenUpstream({ refuse: true });
    servers.push(server);
    let historyCalls = 0;
    const rows = [flashFrame("dup-1", "Same print every poll."), flashFrame("dup-2", "Second print.")];
    const loadHistory = async () => {
      historyCalls += 1;
      return historyCalls === 1 ? [] : rows;
    };
    const hub = await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10,
      loadHistory,
      flashPollMs: 20,
    });
    const ws = dashboard(hub);
    const frames = collect(ws);
    await waitFor(() => historyCalls >= 5);
    const perId = frames
      .filter((f) => f.type === "headline")
      .reduce((acc, f) => acc.set(f.item.id, (acc.get(f.item.id) ?? 0) + 1), new Map());
    expect(perId.get("dup-1")).toBe(1);
    expect(perId.get("dup-2")).toBe(1);
    // Order is stable: the repeated polls did not reorder the ring.
    expect(hub.ring.map((row) => row.id)).toEqual(["dup-1", "dup-2"]);
  });

  it("does not poll while upstream frames flow", async () => {
    const server = await listenUpstream({
      onConnection: (socket) => {
        const timer = setInterval(() => {
          socket.send(JSON.stringify({ type: "time", data: Date.now() }));
        }, 15);
        socket.on("close", () => clearInterval(timer));
      },
    });
    servers.push(server);
    let historyCalls = 0;
    const loadHistory = async () => {
      historyCalls += 1;
      return [];
    };
    await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10,
      loadHistory,
      flashPollMs: 25,
      silenceMs: 10_000,
    });
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(historyCalls).toBe(1); // the boot seed only
  });
});

describe("clients admitted during an outage are told the upstream is down", () => {
  const hubs = [];
  const servers = [];
  const dashboards = [];

  afterEach(async () => {
    while (dashboards.length) dashboards.pop().terminate();
    while (hubs.length) await hubs.pop().stop();
    while (servers.length) await servers.pop().close();
  });

  async function boot(overrides = {}) {
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      ...overrides,
    });
    hubs.push(hub);
    await hub.listen();
    return hub;
  }

  it("sends upstream-down after the snapshot when the upstream is down", async () => {
    const server = await listenUpstream({ refuse: true });
    servers.push(server);
    const lines = [];
    const hub = await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      // Production backoff reaches 120s between dials; a client admitted
      // inside that window gets no close-driven broadcast, so the admit
      // itself must carry the state. The long delay pins exactly that.
      delayFn: () => 60_000,
      log: (line) => lines.push(line),
    });
    await waitFor(() => lines.some((l) => /reconnect attempt=/.test(l)));
    const ws = new WebSocket(hub.address());
    dashboards.push(ws);
    const frames = collect(ws);
    await waitFor(() => frames.some((f) => f.type === "status" && f.state === "upstream-down"));
    expect(frames[0].type).toBe("snapshot");
  });

  it("does not send upstream-down to a client admitted while the upstream is open", async () => {
    const server = await listenUpstream();
    servers.push(server);
    const lines = [];
    const hub = await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10,
      log: (line) => lines.push(line),
    });
    await waitFor(() => lines.some((l) => /upstream open/.test(l)));
    const ws = new WebSocket(hub.address());
    dashboards.push(ws);
    const frames = collect(ws);
    await waitFor(() => frames.some((f) => f.type === "snapshot"));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(frames.filter((f) => f.type === "status" && f.state === "upstream-down")).toEqual([]);
  });
});
