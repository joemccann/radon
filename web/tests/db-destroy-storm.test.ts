/**
 * @vitest-environment node
 *
 * Destroy-storm regression (2026-07-02 incident, second wave).
 *
 * 23b47212 made resetDb() destroy the ONE shared undici Agent. Every failure
 * path (dbExecute, dbFirstRead, dbKeepAlive, the selfHealing proxy, route
 * catches) calls resetDb() on ANY rejection — and Agent.destroy() aborts all
 * in-flight requests, which reject with TypeError('fetch failed'), whose
 * catches call resetDb() again, destroying the freshly rebuilt Agent under
 * the next wave of traffic. One slow query → process-wide livelock that only
 * a process restart cleared (prod 21:32–23:37 UTC).
 *
 * Pins the fix: Agent destruction is rate-limited by a cooldown inside
 * destroyPoolAgent(), so a burst of collateral failures tears the pool down
 * at most once per window; and pooledDbFetch resolves the dispatcher AFTER
 * body materialization so a reset landing in that microtask gap cannot
 * dispatch onto an already-destroyed Agent.
 */
import { describe, it, expect, vi, afterEach } from "vitest";

const { createClientMock, agents, FakeAgent, undiciFetchMock } = vi.hoisted(() => {
  class FakeAgent {
    opts: Record<string, unknown>;
    destroyed = false;
    destroy = vi.fn(async () => {
      this.destroyed = true;
    });
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
  return import("../lib/db");
}

function totalDestroys(): number {
  return agents.reduce((n, a) => n + a.destroy.mock.calls.length, 0);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete process.env.TURSO_DB_URL;
  delete process.env.TURSO_AUTH_TOKEN;
});

describe("isolated failures do not tear down the pool", () => {
  // 2026-07-03 diagnosis: the ~hourly triggers are genuine Turso tail-latency
  // events (external monitor: fresh-connection p95 582ms, max ~1s — the tail
  // exists off-pool), NOT socket rot. A single timed-out read used to destroy
  // the Agent and abort 5-18 concurrent siblings. A genuine wedge produces
  // CLUSTERED failures within seconds; an isolated one is just a slow query.
  it("one resetDb leaves the Agent alive (tail-latency timeout, not a wedge)", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);

    db.resetDb();
    expect(totalDestroys()).toBe(0);

    // Subsequent traffic keeps using the SAME healthy pool.
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);
    expect(agents[0].destroyed).toBe(false);
  });

  it("a second failure inside the cluster window destroys (genuine wedge detection)", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");

    db.resetDb();
    db.resetDb();
    expect(totalDestroys()).toBe(1);
  });

  it("two isolated failures far apart never destroy", async () => {
    const nowSpy = vi.spyOn(Date, "now");
    let t = 1_000_000;
    nowSpy.mockImplementation(() => t);

    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");

    db.resetDb();
    t += db.DB_POOL_FAILURE_CLUSTER_WINDOW_MS + 1;
    db.resetDb();
    expect(totalDestroys()).toBe(0);
  });
});

describe("destroy cooldown breaks the reset feedback loop", () => {
  it("a burst of resetDb calls destroys the Agent at most once per cooldown window", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);

    // Incident topology: clustered failures reset; the destroy aborts
    // in-flight siblings whose 'fetch failed' catches reset AGAIN
    // milliseconds later, destroying the fresh Agent under the next wave —
    // repeat forever. The fix: within the cooldown window only the FIRST
    // clustered reset tears the pool down.
    db.resetDb(); // failure 1 — isolated, arms the cluster window
    db.resetDb(); // failure 2 — clustered → destroy #1
    await boundedFetch("https://example.turso.io/v2/pipeline"); // rebuilds agent
    db.resetDb(); // collateral straggler — must NOT destroy the fresh agent
    await boundedFetch("https://example.turso.io/v2/pipeline");
    db.resetDb(); // more collateral
    expect(totalDestroys()).toBe(1);

    // The next request must get a LIVE agent, not a destroyed one.
    await boundedFetch("https://example.turso.io/v2/pipeline");
    const lastCall = undiciFetchMock.mock.calls.at(-1)! as [unknown, { dispatcher: InstanceType<typeof FakeAgent> }];
    expect(lastCall[1].dispatcher.destroyed).toBe(false);
  });

  it("after the cooldown elapses a genuinely new failure destroys again", async () => {
    const nowSpy = vi.spyOn(Date, "now");
    let t = 1_000_000;
    nowSpy.mockImplementation(() => t);

    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");

    db.resetDb();
    db.resetDb(); // clustered → destroy #1
    expect(totalDestroys()).toBe(1);

    await boundedFetch("https://example.turso.io/v2/pipeline");
    t += Math.max(db.DB_POOL_DESTROY_COOLDOWN_MS, db.DB_POOL_FAILURE_CLUSTER_WINDOW_MS) + 1;
    db.resetDb();
    db.resetDb(); // fresh cluster after cooldown → destroy #2
    expect(totalDestroys()).toBe(2);
  });

  it("cooldown outlives one full keep-alive heartbeat so the 3s metronome cannot sustain a storm", async () => {
    const db = await freshDbModule();
    expect(db.DB_POOL_DESTROY_COOLDOWN_MS).toBeGreaterThan(3_000);
  });

  it("__resetDbForTests bypasses the cooldown (test isolation stays deterministic)", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");

    db.resetDb();
    db.resetDb(); // clustered → destroy; cooldown + cluster state now armed
    db.__resetDbForTests(); // must fully rearm both gates
    await boundedFetch("https://example.turso.io/v2/pipeline");
    db.resetDb();
    db.resetDb();
    // Post-bypass, a fresh cluster must destroy the fresh agent immediately —
    // no leftover cooldown from before the bypass may swallow it.
    expect(agents.at(-1)!.destroyed).toBe(true);
  });
});

describe("pooledDbFetch dispatches on the CURRENT agent", () => {
  it("a reset landing during body materialization cannot route the request onto the destroyed agent", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls[0][0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(agents).toHaveLength(1);

    // hrana-style Request whose body read yields the microtask gap in which a
    // concurrent failure path fires resetDb().
    const request = {
      url: "https://example.turso.io/v2/pipeline",
      method: "POST",
      headers: new Headers(),
      arrayBuffer: async () => {
        db.__resetDbForTests();
        return new TextEncoder().encode('{"requests":[]}').buffer;
      },
    } as unknown as Request;

    await boundedFetch(request);

    const [, init] = undiciFetchMock.mock.calls.at(-1)! as [unknown, { dispatcher: InstanceType<typeof FakeAgent> }];
    expect(init.dispatcher.destroyed).toBe(false);
  });
});
