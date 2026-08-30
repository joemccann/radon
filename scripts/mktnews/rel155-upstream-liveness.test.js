/**
 * REL-155 (R-435, R-459): a half-open upstream is detected, reconnected and
 * reported.
 *
 * `connectMktnews` used to react only to `open` / `error` / `close`, so an
 * upstream that dropped without a FIN (NAT idle timeout, host reboot) kept
 * `readyState` OPEN forever: no frame, no reconnect, and the hub's last
 * broadcast was `upstream-open`. The serve path also dropped the client's
 * `reconnect` / `error` events on the floor and wrote no service_health row,
 * so a revoked upstream (handshake 4xx) was redialled at the backoff cap with
 * no journal line and no page.
 */
import { createServer } from "node:http";

import { afterEach, describe, expect, it } from "vitest";
import { WebSocket, WebSocketServer } from "ws";

import { connectMktnews } from "./client.js";
import { createHeadlinesHub } from "./hub.js";

const AUTHLESS = {
  clerkConfigured: false,
  allowUnauthenticatedDev: true,
  bindHost: "127.0.0.1",
  requireClerk: false,
};

function listenUpstream({ onUpgradeStatus = null } = {}) {
  const httpServer = createServer();
  const wss = new WebSocketServer({ noServer: true });
  httpServer.on("upgrade", (req, socket, head) => {
    if (onUpgradeStatus) {
      socket.write(`HTTP/1.1 ${onUpgradeStatus} Forbidden\r\nConnection: close\r\n\r\n`);
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

describe("connectMktnews idle watchdog", () => {
  const servers = [];
  const clients = [];

  afterEach(async () => {
    while (clients.length) clients.pop().stop();
    while (servers.length) await servers.pop().close();
  });

  it("terminates and reconnects a socket that goes silent after open", async () => {
    const server = await listenUpstream();
    servers.push(server);
    let connections = 0;
    server.wss.on("connection", () => {
      connections += 1;
      // Say nothing: the peer is half-open from the client's point of view.
    });
    const events = [];
    const client = connectMktnews({
      url: server.url,
      idleTimeoutMs: 120,
      delayFn: () => 10,
      onStatus: (event) => events.push(event),
    });
    clients.push(client);

    await waitFor(() => connections >= 2, 1500);
    const kinds = events.map((e) => e.event);
    expect(kinds).toContain("idle");
    expect(kinds.indexOf("idle")).toBeLessThan(kinds.indexOf("reconnect"));
    const idle = events.find((e) => e.event === "idle");
    expect(idle.idleMs).toBe(120);
  });

  it("treats a time heartbeat as liveness and only trips once the frames stop", async () => {
    const server = await listenUpstream();
    servers.push(server);
    let connections = 0;
    let stoppedAt = null;
    server.wss.on("connection", (socket) => {
      connections += 1;
      if (connections > 1) return;
      let sent = 0;
      const timer = setInterval(() => {
        sent += 1;
        socket.send(JSON.stringify({ type: "time", data: Date.now() }));
        if (sent >= 6) {
          clearInterval(timer);
          stoppedAt = Date.now();
        }
      }, 40);
    });
    const events = [];
    const client = connectMktnews({
      url: server.url,
      idleTimeoutMs: 120,
      delayFn: () => 10,
      onStatus: (event) => events.push({ ...event, at: Date.now() }),
    });
    clients.push(client);

    await waitFor(() => stoppedAt != null, 1500);
    // Six 40 ms ticks span ~240 ms: longer than the 120 ms bound, so a clock
    // that the time frames did not reset would already have fired.
    expect(events.filter((e) => e.event === "idle")).toEqual([]);
    expect(connections).toBe(1);

    await waitFor(() => connections >= 2, 1500);
    const idle = events.find((e) => e.event === "idle");
    expect(idle.at).toBeGreaterThanOrEqual(stoppedAt + 100);
  });

  it("does not fire the idle clock after stop()", async () => {
    const server = await listenUpstream();
    servers.push(server);
    let connections = 0;
    server.wss.on("connection", () => {
      connections += 1;
    });
    const events = [];
    const client = connectMktnews({
      url: server.url,
      idleTimeoutMs: 60,
      delayFn: () => 10,
      onStatus: (event) => events.push(event),
    });
    await waitFor(() => connections === 1);
    client.stop();
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(events.filter((e) => e.event === "idle")).toEqual([]);
    expect(connections).toBe(1);
  });
});

describe("createHeadlinesHub upstream liveness", () => {
  const hubs = [];
  const servers = [];

  afterEach(async () => {
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

  it("broadcasts upstream-down to dashboards when the upstream goes silent", async () => {
    const server = await listenUpstream();
    servers.push(server);
    let connections = 0;
    server.wss.on("connection", () => {
      connections += 1;
    });
    const hub = await boot({
      connectUpstream: (opts) =>
        connectMktnews({ ...opts, url: server.url, idleTimeoutMs: 120, delayFn: () => 10 }),
      log: () => {},
    });
    const dashboard = new WebSocket(hub.address());
    const frames = collect(dashboard);
    await waitFor(() => frames.some((f) => f.type === "snapshot"));
    const states = () => frames.filter((f) => f.type === "status").map((f) => f.state);
    await waitFor(() => connections >= 2, 1500);
    // Silence -> terminate -> `close` -> upstream-down, then the redial's
    // `open` -> upstream-open, in that order.
    await waitFor(
      () => states().lastIndexOf("upstream-open") > states().indexOf("upstream-down") && states().includes("upstream-down"),
      1000,
    );
    dashboard.close();
    expect(states().filter((s) => s === "upstream-down")).toHaveLength(1);
  });

  it("serve mode journals every attempt against a 4xx upstream and writes a health row after K failures", async () => {
    const server = await listenUpstream({ onUpgradeStatus: 403 });
    servers.push(server);
    const lines = [];
    const health = [];
    await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 15,
      log: (line) => lines.push(line),
      recordHealth: async (state, extra) => {
        health.push({ state, ...extra, attempts: lines.filter((l) => /reconnect attempt=/.test(l)).length });
      },
      failureThreshold: 3,
    });
    await waitFor(() => lines.filter((l) => /reconnect attempt=/.test(l)).length >= 4, 3000);
    expect(lines.some((l) => /^\[mktnews\] error /.test(l))).toBe(true);
    expect(lines.filter((l) => /^\[mktnews\] reconnect attempt=\d+ delayMs=\d+/.test(l)).length).toBeGreaterThanOrEqual(4);
    expect(health.length).toBeGreaterThanOrEqual(1);
    expect(health[0].state).toBe("error");
    expect(health[0].attempts).toBe(3);
    expect(String(health[0].error?.message ?? health[0].error)).toMatch(/3 consecutive/);
  });

  it("writes an error row once no upstream frame has arrived within the silence bound, and ok once frames flow", async () => {
    const server = await listenUpstream();
    servers.push(server);
    server.wss.on("connection", (socket) => {
      socket.send(JSON.stringify({ type: "time", data: Date.now() }));
    });
    const health = [];
    const hub = await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url, idleTimeoutMs: 5000 }),
      log: () => {},
      recordHealth: async (state, extra) => health.push({ state, ...extra }),
      healthTickMs: 40,
      silenceMs: 150,
    });
    await waitFor(() => health.some((h) => h.state === "ok"), 1500);
    expect(health.filter((h) => h.state === "error")).toEqual([]);
    // The single time frame ages past the bound: the tick must report it.
    await waitFor(() => health.some((h) => h.state === "error"), 1500);
    const err = health.find((h) => h.state === "error");
    expect(String(err.error?.message ?? err.error)).toMatch(/no upstream frame/);
    await hub.stop();
  });

  it("logs upstream open and close in serve mode", async () => {
    const server = await listenUpstream();
    servers.push(server);
    server.wss.on("connection", (socket) => socket.close(1001, "bye"));
    const lines = [];
    await boot({
      connectUpstream: (opts) => connectMktnews({ ...opts, url: server.url }),
      delayFn: () => 10_000,
      log: (line) => lines.push(line),
    });
    await waitFor(() => lines.some((l) => /^\[mktnews\] close 1001/.test(l)));
    expect(lines.some((l) => /^\[mktnews\] upstream open ws:\/\/127\.0\.0\.1/.test(l))).toBe(true);
  });
});
