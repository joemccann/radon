/**
 * REL-163 (R-460, R-461, R-462): the headlines hub bounds its clients and its
 * boot.
 *
 * R-460: no ping/pong sweep and no bufferedAmount check, so a browser that
 * vanished behind NAT pinned a slot until the kernel gave up while every
 * headline accumulated in its send buffer.
 * R-461: `clients.size >= maxClients` was checked BEFORE the awaited ticket
 * validation and the socket added AFTER it, so N simultaneous upgrades all
 * passed the check in flight.
 * R-462: `seedRing` pushed every flash-history row through `ingest`, so a
 * restart launched one Turso batch per row although only `ringSize` survive.
 */
import crypto from "node:crypto";
import net from "node:net";

import { afterEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";

import { createHeadlinesHub } from "./hub.js";
import { parseFrame } from "./protocol.js";

const AUTHLESS = {
  clerkConfigured: false,
  allowUnauthenticatedDev: true,
  bindHost: "127.0.0.1",
  requireClerk: false,
};

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

function upgradeOutcome(ws) {
  return new Promise((resolve) => {
    ws.once("open", () => resolve(101));
    ws.once("error", () => resolve(0));
    ws.once("unexpected-response", (_req, res) => resolve(res.statusCode));
  });
}

/** A handshake-only peer that never reads: the kernel window closes and the
 * hub's send queue backs up in userspace. */
function stalledPeer(port) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: "127.0.0.1", port }, () => {
      const key = crypto.randomBytes(16).toString("base64");
      socket.write(
        "GET /ws-headlines HTTP/1.1\r\n" +
          `Host: 127.0.0.1:${port}\r\n` +
          "Upgrade: websocket\r\n" +
          "Connection: Upgrade\r\n" +
          `Sec-WebSocket-Key: ${key}\r\n` +
          "Sec-WebSocket-Version: 13\r\n\r\n",
      );
    });
    socket.once("data", () => {
      socket.pause();
      resolve(socket);
    });
    socket.once("error", reject);
  });
}

function flash(i) {
  return parseFrame(
    JSON.stringify({
      type: "flash",
      data: { id: `f${i}`, time: "2026-08-30T12:00:00.000Z", data: { content: `${i} ` + "K".repeat(1900) } },
    }),
  );
}

describe("createHeadlinesHub client bounds", () => {
  const hubs = [];
  const sockets = [];

  afterEach(async () => {
    while (sockets.length) {
      const s = sockets.pop();
      try {
        s.terminate?.() ?? s.destroy?.();
      } catch {
        // already gone
      }
    }
    while (hubs.length) await hubs.pop().stop();
  });

  async function boot(overrides = {}) {
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      connectUpstream: null,
      log: () => {},
      ...overrides,
    });
    hubs.push(hub);
    await hub.listen();
    return hub;
  }

  it("drops a client that never answers pings within the sweep, and keeps one that does", async () => {
    const hub = await boot({ pingIntervalMs: 50 });
    const mute = new WebSocket(hub.address(), { autoPong: false });
    const ponging = new WebSocket(hub.address());
    sockets.push(mute, ponging);
    await Promise.all([upgradeOutcome(mute), upgradeOutcome(ponging)]);
    expect(hub.clientCount).toBe(2);
    await waitFor(() => hub.clientCount === 1, 1000);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(hub.clientCount).toBe(1);
    expect(ponging.readyState).toBe(WebSocket.OPEN);
  });

  it("closes a client whose send buffer passes the cutoff instead of queueing forever", async () => {
    const hub = await boot({ maxBufferedBytes: 64 * 1024 });
    const port = Number(new URL(hub.address()).port);
    const peer = await stalledPeer(port);
    sockets.push(peer);
    await waitFor(() => hub.clientCount === 1);
    for (let i = 0; i < 20_000 && hub.clientCount > 0; i += 1) hub.ingest(flash(i));
    expect(hub.clientCount).toBe(0);
  });

  it("reserves the slot before the awaited ticket validation so a burst cannot exceed maxClients", async () => {
    const hub = await boot({
      security: { ...AUTHLESS, allowUnauthenticatedDev: false, clerkConfigured: true },
      fetchImpl: () => new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 200)),
    });
    const outcomes = await Promise.all(
      Array.from({ length: 40 }, () => {
        const ws = new WebSocket(`${hub.address()}?ticket=x`, { headers: { Origin: "http://localhost:3000" } });
        sockets.push(ws);
        return upgradeOutcome(ws);
      }),
    );
    expect(hub.clientCount).toBeLessThanOrEqual(32);
    expect(outcomes.filter((s) => s === 101)).toHaveLength(32);
    expect(outcomes.filter((s) => s === 503)).toHaveLength(8);
  });

  it("releases a reserved slot when the ticket is rejected", async () => {
    const hub = await boot({
      maxClients: 1,
      security: { ...AUTHLESS, allowUnauthenticatedDev: false, clerkConfigured: true },
      fetchImpl: async () => ({ ok: false }),
    });
    const bad = new WebSocket(`${hub.address()}?ticket=x`, { headers: { Origin: "http://localhost:3000" } });
    sockets.push(bad);
    expect(await upgradeOutcome(bad)).toBe(401);
    const again = new WebSocket(`${hub.address()}?ticket=x`, { headers: { Origin: "http://localhost:3000" } });
    sockets.push(again);
    // A leaked reservation would answer 503 here.
    expect(await upgradeOutcome(again)).toBe(401);
  });

  it("seeds at most ringSize rows from a long flash history and persists only the new ones", async () => {
    const puts = [];
    const history = Array.from({ length: 500 }, (_, i) => flash(i));
    const hub = await boot({
      ringSize: 50,
      loadHistory: async () => history,
      store: { load: async () => [], put: async (item) => puts.push(item.id) },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(hub.ring.map((row) => row.id)).toEqual(history.slice(-50).map((f) => f.data.id));
    expect(puts.length).toBeLessThanOrEqual(50);
    expect(new Set(puts)).toEqual(new Set(hub.ring.map((row) => row.id)));
  });

  it("does not re-persist a seed row the store already restored", async () => {
    const puts = [];
    const stored = { kind: "headline", id: "f1", time: null, important: false, content: "stored", impact: [] };
    await boot({
      loadHistory: async () => [flash(1), flash(2)],
      store: { load: async () => [stored], put: async (item) => puts.push(item.id) },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(puts).toEqual(["f2"]);
  });
});
