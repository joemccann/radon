import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

/**
 * F14 — workflow graph CRUD API. AUTH-REQUIRED, user-scoped (mirrors the F6
 * alerts route). We mock @clerk/nextjs/server's auth() and back getDb() with a
 * real in-memory libsql client seeded with the 0019 workflow_graphs schema so
 * the SQL executes for real.
 */

let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE workflow_graphs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, graph TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_run_at TEXT, last_run_ok INTEGER, last_run_summary TEXT)`);
}

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

const GRAPH = {
  nodes: [
    { id: "n1", type: "data-source", params: { source: "scanner" } },
    { id: "n2", type: "notify", params: { channel: "service_health" } },
  ],
  edges: [{ from: "n1", to: "n2" }],
};

beforeEach(async () => {
  vi.resetModules();
  currentUserId = "user_test_1";
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
});

afterEach(() => {
  db.close();
  vi.clearAllMocks();
});

describe("auth gate", () => {
  it("GET returns 401 when signed out", async () => {
    currentUserId = null;
    const { GET } = await import("../app/api/workflow/route");
    const res = await GET();
    expect(res.status).toBe(401);
  });

  it("POST returns 401 when signed out", async () => {
    currentUserId = null;
    const { POST } = await import("../app/api/workflow/route");
    const res = await POST(req("http://x/api/workflow", { method: "POST", body: "{}" }));
    expect(res.status).toBe(401);
  });
});

describe("CRUD", () => {
  async function save(body: Record<string, unknown>): Promise<Response> {
    const { POST } = await import("../app/api/workflow/route");
    return POST(
      req("http://x/api/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  }

  it("rejects a graph without nodes/edges arrays", async () => {
    const res = await save({ name: "bad", graph: { nodes: "nope" } });
    expect(res.status).toBe(400);
  });

  it("saves then lists the graph for the owner", async () => {
    const saveRes = await save({ name: "Accumulation alerts", graph: GRAPH });
    expect(saveRes.status).toBe(200);

    const { GET } = await import("../app/api/workflow/route");
    const listRes = await GET();
    const body = await jsonOf(listRes);
    const graphs = body.graphs as Array<Record<string, unknown>>;
    expect(graphs).toHaveLength(1);
    expect(graphs[0].name).toBe("Accumulation alerts");
    expect((graphs[0].graph as { nodes: unknown[] }).nodes).toHaveLength(2);
  });

  it("updates in place when the same id is re-saved", async () => {
    const first = await jsonOf(await save({ name: "v1", graph: GRAPH }));
    await save({ id: first.id, name: "v2", graph: { nodes: [], edges: [] } });

    const rows = await db.execute("SELECT COUNT(*) AS c FROM workflow_graphs");
    expect(Number(rows.rows[0].c)).toBe(1);

    const { GET } = await import("../app/api/workflow/route");
    const graphs = (await jsonOf(await GET())).graphs as Array<Record<string, unknown>>;
    expect(graphs[0].name).toBe("v2");
  });

  it("does not leak another user's graphs", async () => {
    await save({ name: "mine", graph: GRAPH });
    currentUserId = "user_test_2";
    const { GET } = await import("../app/api/workflow/route");
    const graphs = (await jsonOf(await GET())).graphs as unknown[];
    expect(graphs).toHaveLength(0);
  });
});
