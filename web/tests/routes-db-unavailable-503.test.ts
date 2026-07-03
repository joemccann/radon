/**
 * User-data routes must degrade to 503 (retryable, client keeps last-good
 * state) when Turso is unreachable — never a naked 500.
 *
 * 2026-07-02 incident: during the undici destroy-storm every DB read in the
 * process rejected TypeError('fetch failed'); /api/watchlist, /api/orders and
 * /api/profile turned that into 500s in the authed browser session. 500 reads
 * as "the app is broken"; 503 + Retry-After reads as "transient — retry", and
 * the client hooks already treat any !res.ok as keep-last-good.
 *
 * Also pins the dbExecute invariant (web/lib/db.ts:27-29): these routes were
 * the only Turso readers still calling bare getDb().execute, so a stale-pool
 * read could hang to undici's ~300s ceiling without healing the client.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

const resetDbMock = vi.fn();
const fetchFailed = () => {
  const err = new TypeError("fetch failed");
  (err as { cause?: unknown }).cause = new Error("The client is destroyed");
  return err;
};
const wedgedClient = {
  execute: vi.fn(async () => {
    throw fetchFailed();
  }),
  batch: vi.fn(),
};
vi.mock("@/lib/db", () => ({
  getDb: vi.fn(() => wedgedClient),
  resetDb: resetDbMock,
}));

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

beforeEach(() => {
  vi.resetModules();
  currentUserId = "user_test_1";
  wedgedClient.execute.mockClear();
  resetDbMock.mockClear();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("watchlist routes: Turso down → 503, not 500", () => {
  it("GET /api/watchlist returns 503 DB_UNAVAILABLE and self-heals the pool", async () => {
    const { GET } = await import("../app/api/watchlist/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
    expect(resetDbMock).toHaveBeenCalled();
  });

  it("POST /api/watchlist returns 503", async () => {
    const { POST } = await import("../app/api/watchlist/route");
    const res = await POST(
      new Request("http://localhost/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ symbol: "MU" }),
      }),
    );
    expect(res.status).toBe(503);
  });

  it("DELETE /api/watchlist/[symbol] returns 503", async () => {
    const { DELETE } = await import("../app/api/watchlist/[symbol]/route");
    const res = await DELETE(new Request("http://localhost/api/watchlist/MU", { method: "DELETE" }), {
      params: Promise.resolve({ symbol: "MU" }),
    });
    expect(res.status).toBe(503);
  });
});

describe("profile routes: Turso down → 503, not 500", () => {
  it("GET /api/profile returns 503 DB_UNAVAILABLE", async () => {
    const { GET } = await import("../app/api/profile/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
  });

  it("PUT /api/profile returns 503", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      new Request("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "joe" }),
      }),
    );
    expect(res.status).toBe(503);
  });
});

describe("bookmarks routes: Turso down → 503, not 500", () => {
  it("GET /api/bookmarks returns 503 DB_UNAVAILABLE", async () => {
    const { GET } = await import("../app/api/bookmarks/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
  });

  it("POST /api/bookmarks returns 503", async () => {
    const { POST } = await import("../app/api/bookmarks/route");
    const res = await POST(
      new Request("http://localhost/api/bookmarks", {
        method: "POST",
        body: JSON.stringify({ post_id: "p1" }),
      }),
    );
    expect(res.status).toBe(503);
  });

  it("DELETE /api/bookmarks/[post_id] returns 503", async () => {
    const { DELETE } = await import("../app/api/bookmarks/[post_id]/route");
    const res = await DELETE(new Request("http://localhost/api/bookmarks/p1", { method: "DELETE" }), {
      params: Promise.resolve({ post_id: "p1" }),
    });
    expect(res.status).toBe(503);
  });
});

describe("alerts routes: Turso down → 503, not 500", () => {
  it("GET /api/alerts returns 503 DB_UNAVAILABLE", async () => {
    const { GET } = await import("../app/api/alerts/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
  });

  it("POST /api/alerts returns 503", async () => {
    const { POST } = await import("../app/api/alerts/route");
    const res = await POST(
      new Request("http://localhost/api/alerts", {
        method: "POST",
        body: JSON.stringify({ ticker: "MU", metric: "score", op: ">", threshold: 50 }),
      }),
    );
    expect(res.status).toBe(503);
  });

  it("DELETE /api/alerts/[id] returns 503", async () => {
    const { DELETE } = await import("../app/api/alerts/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/alerts/a1", { method: "DELETE" }), {
      params: Promise.resolve({ id: "a1" }),
    });
    expect(res.status).toBe(503);
  });
});

describe("workflow routes: Turso down → 503, not 500", () => {
  it("GET /api/workflow returns 503 DB_UNAVAILABLE", async () => {
    const { GET } = await import("../app/api/workflow/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
  });
});

describe("orders route: Turso down → 503, not 500", () => {
  it("GET /api/orders returns 503 when the snapshot read fails", async () => {
    vi.doMock("@/lib/orders/readOrdersFromDb", () => ({
      readOrdersSnapshotFromDb: vi.fn(async () => {
        throw fetchFailed();
      }),
    }));
    const { GET } = await import("../app/api/orders/route");
    const res = await GET();
    expect(res.status).toBe(503);
  });
});
