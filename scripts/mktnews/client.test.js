import { createServer } from "node:http";

import { afterEach, describe, expect, it } from "vitest";
import { WebSocketServer } from "ws";

import { connectMktnews, fetchFlashHistory } from "./client.js";
import { DEFAULT_FLASH_URL } from "./protocol.js";

function listenServer() {
  const httpServer = createServer();
  const wss = new WebSocketServer({ server: httpServer });
  return new Promise((resolve) => {
    httpServer.listen(0, "127.0.0.1", () => {
      const { port } = httpServer.address();
      resolve({
        wss,
        httpServer,
        url: `ws://127.0.0.1:${port}`,
        close() {
          return new Promise((done) => {
            wss.close(() => httpServer.close(done));
          });
        },
      });
    });
  });
}

describe("connectMktnews", () => {
  const servers = [];

  afterEach(async () => {
    while (servers.length) {
      await servers.pop().close();
    }
  });

  it("emits parsed frames from a local websocket", async () => {
    const server = await listenServer();
    servers.push(server);
    server.wss.on("connection", (socket) => {
      socket.send(JSON.stringify({ type: "time", data: 99 }));
      socket.send(
        JSON.stringify({
          type: "flash",
          data: { id: "f1", data: { content: "hello flash" } },
        }),
      );
    });

    const received = [];
    const client = connectMktnews({
      url: server.url,
      reconnect: false,
      onMessage: (msg) => received.push(msg),
    });

    await viWait(() => received.some((m) => m.kind === "flash"));
    client.stop();

    const kinds = received.map((m) => m.kind);
    expect(kinds).toContain("time");
    expect(kinds).toContain("flash");
    expect(received.find((m) => m.kind === "flash").data.data.content).toBe(
      "hello flash",
    );
  });

  it("reconnects after the server drops the socket", async () => {
    const server = await listenServer();
    servers.push(server);
    let connections = 0;
    server.wss.on("connection", (socket) => {
      connections += 1;
      socket.send(JSON.stringify({ type: "flash", data: { id: String(connections) } }));
      if (connections === 1) socket.close();
    });

    const received = [];
    const client = connectMktnews({
      url: server.url,
      onMessage: (msg) => received.push(msg),
      delayFn: () => 20,
    });

    await viWait(() => received.filter((m) => m.kind === "flash").length >= 2);
    client.stop();
    expect(connections).toBeGreaterThanOrEqual(2);
  });

  it("sends Origin and stops on abort", async () => {
    const server = await listenServer();
    servers.push(server);
    const origins = [];
    server.wss.on("connection", (socket, req) => {
      origins.push(req.headers.origin);
    });
    const controller = new AbortController();
    const client = connectMktnews({
      url: server.url,
      reconnect: false,
      signal: controller.signal,
    });
    await viWait(() => origins.length >= 1);
    controller.abort();
    client.stop();
    expect(origins[0]).toBe("https://mktnews.net");
  });
});

describe("fetchFlashHistory", () => {
  it("parses REST flash rows oldest-first with the browser Origin", async () => {
    const seen = [];
    const fetchImpl = async (url, init) => {
      seen.push({ url, origin: init.headers.Origin });
      return {
        ok: true,
        json: async () => ({
          status: 200,
          data: [
            { id: "n1", type: 0, time: "2026-08-30T01:55:21.000Z", data: { content: "Newer." } },
            { id: "n0", type: 0, time: "2026-08-30T01:54:49.000Z", data: { content: "Older." } },
          ],
        }),
      };
    };
    const frames = await fetchFlashHistory({ fetchImpl });
    expect(seen[0].url).toBe(DEFAULT_FLASH_URL);
    expect(seen[0].origin).toBe("https://mktnews.net");
    expect(frames.map((row) => row.data.id)).toEqual(["n0", "n1"]);
    expect(frames[0].kind).toBe("flash");
  });

  it("returns an empty list when the history endpoint errors", async () => {
    const frames = await fetchFlashHistory({
      fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({}) }),
    });
    expect(frames).toEqual([]);
  });
});

function viWait(predicate, timeoutMs = 2000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      try {
        if (predicate()) {
          resolve();
          return;
        }
      } catch {
        // keep waiting until timeout
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error("timed out waiting for websocket condition"));
        return;
      }
      setTimeout(tick, 15);
    };
    tick();
  });
}
