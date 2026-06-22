import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

/**
 * F6 — signal alert rules engine CRUD API.
 *
 * AUTH-REQUIRED, user-scoped (mirrors the bookmarks/watchlist pattern). We mock
 * @clerk/nextjs/server's auth() to control userId and back getDb() with a real
 * in-memory libsql client seeded with the 0020 alert_rules schema so the SQL
 * executes for real.
 */

let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  getDb: mockGetDb,
}));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE alert_rules (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, ticker TEXT NOT NULL,
    metric TEXT NOT NULL, op TEXT NOT NULL, threshold REAL NOT NULL,
    channel TEXT NOT NULL, created_at TEXT NOT NULL, last_fired_at TEXT)`);
}

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

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
  it("GET /api/alerts returns 401 when signed out", async () => {
    currentUserId = null;
    const { GET } = await import("../app/api/alerts/route");
    const res = await GET();
    expect(res.status).toBe(401);
    expect((await jsonOf(res)).code).toBe("UNAUTHORIZED");
  });

  it("POST /api/alerts returns 401 when signed out", async () => {
    currentUserId = null;
    const { POST } = await import("../app/api/alerts/route");
    const res = await POST(req("http://x/api/alerts", { method: "POST", body: "{}" }));
    expect(res.status).toBe(401);
  });
});

describe("CRUD", () => {
  async function createRule(body: Record<string, unknown>): Promise<Response> {
    const { POST } = await import("../app/api/alerts/route");
    return POST(
      req("http://x/api/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  }

  it("creates a rule and lists it", async () => {
    const created = await createRule({
      ticker: "aapl",
      metric: "flow_strength",
      op: ">",
      threshold: 70,
      channel: "pushover",
    });
    expect(created.status).toBe(200);
    const createdJson = await jsonOf(created);
    expect(createdJson.ok).toBe(true);

    const { GET } = await import("../app/api/alerts/route");
    const listed = await GET();
    const json = (await jsonOf(listed)) as { rules: Array<Record<string, unknown>> };
    expect(json.rules.length).toBe(1);
    expect(json.rules[0].ticker).toBe("AAPL"); // normalized upper-case
    expect(json.rules[0].metric).toBe("flow_strength");
    expect(json.rules[0].op).toBe(">");
    expect(json.rules[0].threshold).toBe(70);
  });

  it("rejects an invalid operator", async () => {
    const res = await createRule({
      ticker: "AAPL",
      metric: "flow_strength",
      op: "!!",
      threshold: 70,
      channel: "pushover",
    });
    expect(res.status).toBe(400);
    expect((await jsonOf(res)).code).toBe("VALIDATION_ERROR");
  });

  it("rejects a missing threshold", async () => {
    const res = await createRule({
      ticker: "AAPL",
      metric: "flow_strength",
      op: ">",
      channel: "pushover",
    });
    expect(res.status).toBe(400);
  });

  it("deletes a rule by id and scopes the delete to the owner", async () => {
    await createRule({ ticker: "AAPL", metric: "score", op: ">=", threshold: 80, channel: "pushover" });
    const { GET } = await import("../app/api/alerts/route");
    const listed = await GET();
    const { rules } = (await jsonOf(listed)) as { rules: Array<{ id: string }> };
    const ruleId = rules[0].id;

    const { DELETE } = await import("../app/api/alerts/[id]/route");
    const res = await DELETE(req(`http://x/api/alerts/${ruleId}`, { method: "DELETE" }), {
      params: Promise.resolve({ id: ruleId }),
    });
    expect(res.status).toBe(200);

    const after = await GET();
    expect(((await jsonOf(after)) as { rules: unknown[] }).rules.length).toBe(0);
  });

  it("does not list another user's rules", async () => {
    await createRule({ ticker: "AAPL", metric: "score", op: ">", threshold: 50, channel: "pushover" });
    currentUserId = "user_other";
    const { GET } = await import("../app/api/alerts/route");
    const listed = await GET();
    expect(((await jsonOf(listed)) as { rules: unknown[] }).rules.length).toBe(0);
  });
});
