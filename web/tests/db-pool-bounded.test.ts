/**
 * @vitest-environment node
 *
 * Bounded undici connection pool for the libsql HTTP client (2026-07-02
 * incident). The long-lived Next.js process accumulated 59 established
 * sockets to Turso; Turso then stopped servicing NEW requests from that IP,
 * so every DB-backed route hit its 3s timeout until a process restart
 * dropped the sockets.
 *
 * Pins two invariants:
 *   1. getDb()/getDemoDb() hand @libsql/client a custom fetch that dispatches
 *      through ONE shared undici Agent with a small per-origin connection
 *      cap — the pool can never grow unbounded.
 *   2. resetDb() (and the client's own self-heal) DESTROYS that Agent, so a
 *      wedged pool's sockets are actually dropped, not just orphaned behind
 *      a fresh client.
 */
import { describe, it, expect, vi, afterEach } from "vitest";

const { createClientMock, agents, FakeAgent, undiciFetchMock } = vi.hoisted(() => {
  class FakeAgent {
    opts: Record<string, unknown>;
    destroy = vi.fn(async () => {});
    close = vi.fn(async () => {});
    constructor(opts: Record<string, unknown>) {
      this.opts = opts;
      agents.push(this);
    }
  }
  const agents: InstanceType<typeof FakeAgent>[] = [];
  return {
    createClientMock: vi.fn((config: Record<string, unknown>) => ({
      config,
      execute: vi.fn(async () => ({ rows: [] })),
      batch: vi.fn(),
    })),
    agents,
    FakeAgent,
    undiciFetchMock: vi.fn(async () => ({ ok: true, status: 200 })),
  };
});

vi.mock("@libsql/client", () => ({ createClient: createClientMock }));
vi.mock("undici", () => ({ Agent: FakeAgent, fetch: undiciFetchMock }));

type DbModule = typeof import("../lib/db");

async function freshDbModule(): Promise<DbModule> {
  vi.resetModules();
  createClientMock.mockClear();
  undiciFetchMock.mockClear();
  agents.length = 0;
  process.env.TURSO_DB_URL = "libsql://example.turso.io";
  process.env.TURSO_AUTH_TOKEN = "token";
  process.env.TURSO_DEMO_DB_URL = "libsql://demo.turso.io";
  process.env.TURSO_DEMO_AUTH_TOKEN = "demo-token";
  return import("../lib/db");
}

function clientConfig(call = 0): Record<string, unknown> {
  return createClientMock.mock.calls[call][0] as Record<string, unknown>;
}

afterEach(() => {
  delete process.env.TURSO_DB_URL;
  delete process.env.TURSO_AUTH_TOKEN;
  delete process.env.TURSO_DEMO_DB_URL;
  delete process.env.TURSO_DEMO_AUTH_TOKEN;
});

describe("bounded pool wiring", () => {
  it("getDb passes a custom fetch to createClient", async () => {
    const db = await freshDbModule();
    db.getDb();
    expect(typeof clientConfig().fetch).toBe("function");
  });

  it("getDemoDb passes the same bounded fetch", async () => {
    const db = await freshDbModule();
    db.getDb();
    db.getDemoDb();
    expect(typeof clientConfig(0).fetch).toBe("function");
    expect(clientConfig(1).fetch).toBe(clientConfig(0).fetch);
  });

  it("the custom fetch dispatches through a capped, idle-reaping undici Agent", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = clientConfig().fetch as typeof db.pooledDbFetch;

    await boundedFetch("https://example.turso.io/v2/pipeline", { method: "POST" });

    expect(agents).toHaveLength(1);
    expect(agents[0].opts.connections).toBe(db.DB_POOL_MAX_CONNECTIONS);
    expect(db.DB_POOL_MAX_CONNECTIONS).toBeLessThanOrEqual(16);
    // Idle sockets must be reaped (no unbounded accumulation), but not before
    // the 3s dbKeepAlive heartbeat can keep the warm socket alive.
    expect(agents[0].opts.keepAliveTimeout).toBe(db.DB_POOL_KEEP_ALIVE_TIMEOUT_MS);
    expect(db.DB_POOL_KEEP_ALIVE_TIMEOUT_MS).toBeGreaterThan(3_000);

    expect(undiciFetchMock).toHaveBeenCalledWith(
      "https://example.turso.io/v2/pipeline",
      expect.objectContaining({ method: "POST", dispatcher: agents[0] }),
    );
  });

  it("translates hrana-style Request inputs so undici's fetch accepts them", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = clientConfig().fetch as typeof db.pooledDbFetch;

    // The hrana HTTP client calls fetch(new Request(url, {method, body})).
    const request = new Request("https://example.turso.io/v2/pipeline", {
      method: "POST",
      body: JSON.stringify({ requests: [] }),
    });
    await boundedFetch(request);

    const [input, init] = undiciFetchMock.mock.calls.at(-1)! as [unknown, Record<string, unknown>];
    expect(input).toBe("https://example.turso.io/v2/pipeline");
    expect(init.method).toBe("POST");
    expect(init.dispatcher).toBe(agents[0]);
    expect(Buffer.from(init.body as Uint8Array).toString()).toBe(
      JSON.stringify({ requests: [] }),
    );
  });

  it("reuses ONE agent across requests — a pool, not per-request agents", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = clientConfig().fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);
  });
});

describe("pool teardown on reset", () => {
  it("resetDb destroys the pooled agent so wedged sockets are dropped", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = clientConfig().fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);

    db.resetDb();
    expect(agents[0].destroy).toHaveBeenCalledTimes(1);

    // The next request builds a FRESH agent, not the destroyed one.
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(2);
  });

  it("a failed execute self-heals the agent too, not just the client cache", async () => {
    createClientMock.mockImplementationOnce((config: Record<string, unknown>) => ({
      config,
      execute: vi.fn().mockRejectedValue(new Error("connection wedged")),
      batch: vi.fn(),
    }));
    const db = await freshDbModule();
    const client = db.getDb();
    const boundedFetch = clientConfig().fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);

    await expect(client.execute("SELECT 1")).rejects.toThrow("connection wedged");
    await Promise.resolve();
    await Promise.resolve();

    expect(agents[0].destroy).toHaveBeenCalledTimes(1);
  });
});
