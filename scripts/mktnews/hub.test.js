import crypto from "node:crypto";
import net from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";

import { createHeadlinesHub, startHeadlinesHub } from "./hub.js";
import { parseFrame } from "./protocol.js";
import { RING_SIZE } from "./normalize.js";

const FLASH = {
  type: "flash",
  data: {
    id: "h1",
    time: "2026-08-29T20:35:56.000Z",
    important: 1,
    data: { content: "Explosions heard in Kyiv." },
    impact: [{ symbol: "WTI", impact: "bearish" }],
  },
};

const AUTHLESS = {
  clerkConfigured: false,
  allowUnauthenticatedDev: true,
  bindHost: "127.0.0.1",
  requireClerk: false,
};

function waitOpen(ws) {
  return new Promise((resolve, reject) => {
    ws.once("open", resolve);
    ws.once("error", reject);
    ws.once("unexpected-response", (_req, res) => {
      reject(Object.assign(new Error(`upgrade ${res.statusCode}`), { statusCode: res.statusCode }));
    });
  });
}

function collect(ws, n, timeoutMs = 1500) {
  const messages = [];
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timed out collecting frames")), timeoutMs);
    ws.on("message", (data) => {
      messages.push(JSON.parse(data.toString()));
      if (messages.length >= n) {
        clearTimeout(timer);
        resolve(messages);
      }
    });
  });
}

function listenPort(hub) {
  return Number(new URL(hub.address()).port);
}

function rawUpgrade(port, requestTarget, extraHeaders = "") {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: "127.0.0.1", port }, () => {
      const key = crypto.randomBytes(16).toString("base64");
      socket.write(
        `GET ${requestTarget} HTTP/1.1\r\n` +
          `Host: 127.0.0.1:${port}\r\n` +
          "Upgrade: websocket\r\n" +
          "Connection: Upgrade\r\n" +
          `Sec-WebSocket-Key: ${key}\r\n` +
          "Sec-WebSocket-Version: 13\r\n" +
          extraHeaders +
          "\r\n",
      );
    });
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error(`upgrade timeout for ${requestTarget}`));
    }, 1500);
    socket.once("data", (buf) => {
      clearTimeout(timer);
      const status = Number(String(buf).split(" ")[1]);
      socket.destroy();
      resolve(status);
    });
    socket.once("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

describe("createHeadlinesHub", () => {
  const hubs = [];

  afterEach(async () => {
    while (hubs.length) await hubs.pop().stop();
  });

  async function boot(overrides = {}) {
    const hub = createHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      connectUpstream: null,
      ...overrides,
    });
    hubs.push(hub);
    await hub.listen();
    return hub;
  }

  it("seeds the in-memory ring from flash history before the first snapshot", async () => {
    const loadHistory = async () => [
      parseFrame(
        JSON.stringify({
          id: "old",
          type: 0,
          time: "2026-08-29T20:00:00.000Z",
          data: { content: "Older print." },
        }),
      ),
      parseFrame(
        JSON.stringify({
          id: "new",
          type: 0,
          time: "2026-08-29T21:00:00.000Z",
          data: { content: "Newer print." },
        }),
      ),
    ];
    const hub = await boot({ loadHistory });
    const ws = new WebSocket(hub.address());
    const [snap] = await collect(ws, 1);
    ws.close();
    expect(snap).toEqual({
      type: "snapshot",
      items: [
        {
          kind: "headline",
          id: "old",
          time: "2026-08-29T20:00:00.000Z",
          important: false,
          content: "Older print.",
          impact: [],
        },
        {
          kind: "headline",
          id: "new",
          time: "2026-08-29T21:00:00.000Z",
          important: false,
          content: "Newer print.",
          impact: [],
        },
      ],
    });
  });

  it("a later handshake receives seed plus live items", async () => {
    const hub = await boot({
      loadHistory: async () => [
        parseFrame(
          JSON.stringify({
            id: "seeded",
            type: 0,
            data: { content: "Cached on host." },
          }),
        ),
      ],
    });
    hub.ingest(
      parseFrame(
        JSON.stringify({
          type: "flash",
          data: { id: "live", data: { content: "Just printed." } },
        }),
      ),
    );
    const ws = new WebSocket(hub.address());
    const [snap] = await collect(ws, 1);
    ws.close();
    expect(snap.items.map((row) => row.id)).toEqual(["seeded", "live"]);
  });

  it("still boots when flash history seed fails", async () => {
    const hub = await boot({
      loadHistory: async () => {
        throw new Error("flash history down");
      },
    });
    const ws = new WebSocket(hub.address());
    const [snap] = await collect(ws, 1);
    ws.close();
    expect(snap).toEqual({ type: "snapshot", items: [] });
  });

  it("startHeadlinesHub seeds via loadHistory then fans out live prints", async () => {
    const hub = await startHeadlinesHub({
      security: AUTHLESS,
      listenHost: "127.0.0.1",
      listenPort: 0,
      connectUpstream: null,
      loadHistory: async () => [
        parseFrame(
          JSON.stringify({
            id: "seeded",
            type: 0,
            data: { content: "Cached on host." },
          }),
        ),
      ],
    });
    hubs.push(hub);
    const ws = new WebSocket(hub.address());
    const [snap] = await collect(ws, 1);
    hub.ingest(
      parseFrame(
        JSON.stringify({
          type: "flash",
          data: { id: "live", data: { content: "Just printed." } },
        }),
      ),
    );
    const [live] = await collect(ws, 1);
    ws.close();
    expect(snap.items.map((row) => row.id)).toEqual(["seeded"]);
    expect(live.item.id).toBe("live");
  });

  it("sends a snapshot then live headlines, never time ticks or upstream hosts", async () => {
    const hub = await boot();
    hub.ingest(parseFrame(JSON.stringify(FLASH)));
    hub.ingest(parseFrame('{"type":"time","data":99}'));
    const ws = new WebSocket(hub.address());
    const frames = await collect(ws, 1);
    hub.ingest(
      parseFrame(
        JSON.stringify({
          type: "flash",
          data: { id: "h2", data: { content: "Jobless claims 218k vs 225k." } },
        }),
      ),
    );
    const next = await collect(ws, 1);
    const live = [...frames, ...next];
    ws.close();
    expect(live[0]).toEqual({
      type: "snapshot",
      items: [
        {
          kind: "headline",
          id: "h1",
          time: "2026-08-29T20:35:56.000Z",
          important: true,
          content: "Explosions heard in Kyiv.",
          impact: [{ symbol: "WTI", impact: "bearish" }],
        },
      ],
    });
    expect(live[live.length - 1].type).toBe("headline");
    expect(JSON.stringify(live)).not.toMatch(/"type":"time"/);
  });

  it("still fans out a headline whose body mentions the upstream host", async () => {
    const hub = await boot();
    const ws = new WebSocket(hub.address());
    const pending = collect(ws, 2);
    await waitOpen(ws);
    hub.ingest(
      parseFrame(
        JSON.stringify({
          type: "flash",
          data: { id: "mention", data: { content: "Screenshot from mktnews.net is circulating." } },
        }),
      ),
    );
    const frames = await pending;
    ws.close();
    const live = frames.find((row) => row.type === "headline");
    expect(live.item.content).toContain("mktnews.net");
  });

  it("rejects the wrong path", async () => {
    const hub = await boot();
    const ws = new WebSocket(hub.address().replace("/ws-headlines", "/ws"));
    await expect(waitOpen(ws)).rejects.toMatchObject({ statusCode: 404 });
  });

  it("rejects a missing ticket when the handshake looks like a browser", async () => {
    const hub = await boot();
    const ws = new WebSocket(hub.address(), {
      headers: { Origin: "http://localhost:3000" },
    });
    await expect(waitOpen(ws)).rejects.toMatchObject({ statusCode: 401 });
  });

  it("rejects loopback upgrades that arrived through a proxy", async () => {
    const hub = await boot();
    const ws = new WebSocket(hub.address(), {
      headers: { "x-forwarded-for": "203.0.113.9" },
    });
    await expect(waitOpen(ws)).rejects.toMatchObject({ statusCode: 401 });
  });

  it("accepts a validated ticket from a browser handshake", async () => {
    const fetchImpl = async () => ({ ok: true });
    const hub = await boot({
      security: { ...AUTHLESS, allowUnauthenticatedDev: false, clerkConfigured: true },
      fetchImpl,
    });
    const ws = new WebSocket(`${hub.address()}?ticket=good`, {
      headers: { Origin: "http://localhost:3000" },
    });
    const pending = collect(ws, 1);
    await waitOpen(ws);
    const [snap] = await pending;
    ws.close();
    expect(snap.type).toBe("snapshot");
  });

  it("restores the ring from the store before the first snapshot, oldest-first", async () => {
    const stored = [
      { kind: "headline", id: "s1", time: null, important: false, content: "stored one", impact: [] },
      { kind: "headline", id: "s2", time: null, important: true, content: "stored two", impact: [] },
    ];
    const puts = [];
    const hub = await boot({
      store: { load: async () => stored, put: async (item) => puts.push(item.id) },
      loadHistory: async () => [
        parseFrame(JSON.stringify({ type: "flash", data: { id: "u1", data: { content: "upstream" } } })),
      ],
    });
    const ws = new WebSocket(hub.address());
    const [snapshot] = await collect(ws, 1);
    ws.close();
    expect(snapshot.items.map((row) => row.id)).toEqual(["s1", "s2", "u1"]);
    expect(puts).toEqual(["u1"]);
  });

  it("persists every ingested print through the store", async () => {
    const puts = [];
    const hub = await boot({ store: { load: async () => [], put: async (item) => puts.push(item) } });
    hub.ingest(parseFrame(JSON.stringify(FLASH)));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(puts).toEqual([
      {
        kind: "headline",
        id: "h1",
        time: "2026-08-29T20:35:56.000Z",
        important: true,
        content: "Explosions heard in Kyiv.",
        impact: [{ symbol: "WTI", impact: "bearish" }],
      },
    ]);
  });

  it("still boots and fans out when the store is down", async () => {
    // T-485: the restore/persist failure paths write to process.stderr from
    // async continuations. Under vitest that write becomes a pending
    // onUserConsoleLog rpc message which can still be in flight when a later
    // file's environment tears down (EnvironmentTeardownError with 0 failed
    // tests — gate logs 2026-09-02 and 2026-09-06). Capture stderr here so
    // the writes never reach the interceptor, and assert both failure lines
    // actually fired before the test returns, so nothing is left in flight.
    const stderrLines = [];
    const realWrite = process.stderr.write;
    process.stderr.write = (chunk, ...rest) => {
      stderrLines.push(String(chunk));
      return typeof rest[rest.length - 1] === "function" ? rest[rest.length - 1]() ?? true : true;
    };
    try {
      const hub = await boot({
        store: {
          load: async () => {
            throw new Error("turso unreachable");
          },
          put: async () => {
            throw new Error("turso unreachable");
          },
        },
      });
      expect(hub.ingest(parseFrame(JSON.stringify(FLASH)))).toBe(true);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(hub.ring.map((row) => row.id)).toEqual(["h1"]);
      expect(stderrLines).toContain("[mktnews] ring restore failed: turso unreachable\n");
      expect(stderrLines).toContain("[mktnews] ring persist failed: turso unreachable\n");
    } finally {
      process.stderr.write = realWrite;
    }
  });

  it("caps the ring", async () => {
    const hub = await boot({ ringSize: 3 });
    for (let i = 0; i < 6; i += 1) {
      hub.ingest(
        parseFrame(
          JSON.stringify({ type: "flash", data: { id: `id-${i}`, data: { content: `n${i}` } } }),
        ),
      );
    }
    expect(hub.ring.map((row) => row.id)).toEqual(["id-3", "id-4", "id-5"]);
  });

  it("drops oversized frames on ingest and ingestRaw", async () => {
    const hub = await boot({ maxFrameBytes: 64 });
    expect(hub.ingestRaw("x".repeat(200))).toBe(false);
    const huge = parseFrame(
      JSON.stringify({
        type: "flash",
        data: { id: "huge", data: { content: "K".repeat(200) } },
      }),
    );
    expect(hub.ingest(huge)).toBe(false);
    expect(hub.ring).toEqual([]);
  });

  it("binds loopback even if asked for a public host", async () => {
    const hub = await boot({
      listenHost: "0.0.0.0",
      security: { ...AUTHLESS, bindHost: "0.0.0.0" },
    });
    expect(hub.listenHost).toBe("127.0.0.1");
  });

  it("refuses a connect storm past MAX_CLIENTS", async () => {
    const hub = await boot({ maxClients: 1 });
    const first = new WebSocket(hub.address());
    await waitOpen(first);
    const second = new WebSocket(hub.address());
    await expect(waitOpen(second)).rejects.toMatchObject({ statusCode: 503 });
    first.close();
  });

  it("ignores Host-header steering of the upgrade path", async () => {
    const hub = await boot();
    const ws = new WebSocket(hub.address().replace("/ws-headlines", "/"), {
      headers: { Host: "api.mktnews.net" },
    });
    await expect(waitOpen(ws)).rejects.toMatchObject({ statusCode: 404 });
  });

  it("rejects request-targets that are not origin-form /ws-headlines", async () => {
    const hub = await boot();
    const port = listenPort(hub);
    const rejected = [
      "/./ws-headlines",
      "/x/../ws-headlines",
      "ws-headlines",
      "//api.mktnews.net/ws-headlines",
      "http://api.mktnews.net/ws-headlines",
      "/ws-headlinesX",
      "/ws-headlines/extra",
    ];
    for (const target of rejected) {
      expect(await rawUpgrade(port, target), target).not.toBe(101);
    }
    expect(await rawUpgrade(port, "/ws-headlines")).toBe(101);
    expect(await rawUpgrade(port, "/ws-headlines?ticket=dev")).toBe(101);
  });
});

describe("RING_SIZE", () => {
  it("stays bounded", () => {
    expect(RING_SIZE).toBe(50);
  });
});
