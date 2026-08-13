import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

/**
 * User-scoped feature foundation: profile / bookmarks / watchlist API routes.
 *
 * These routes are AUTH-REQUIRED. We mock @clerk/nextjs/server's auth() to
 * control userId, and back getDb() with a real in-memory libsql client seeded
 * with the 0010 migration schema so the SQL executes for real.
 */

// ---------------------------------------------------------------------------
// Auth mock — controllable userId
// ---------------------------------------------------------------------------

let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

// ---------------------------------------------------------------------------
// DB mock — real in-memory libsql client
// ---------------------------------------------------------------------------

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: mockGetDb,
}));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE user_profiles (
    user_id TEXT PRIMARY KEY, username TEXT, avatar_url TEXT, updated_at TEXT NOT NULL)`);
  await client.execute(`CREATE TABLE bookmarks (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, post_id TEXT NOT NULL,
    snapshot TEXT, saved_at TEXT NOT NULL, UNIQUE(user_id, post_id))`);
  await client.execute(`CREATE TABLE user_watchlist (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, symbol TEXT NOT NULL,
    sector TEXT, added_at TEXT NOT NULL, UNIQUE(user_id, symbol))`);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Auth gate
// ---------------------------------------------------------------------------

describe("auth gate — 401 when no userId", () => {
  it("GET /api/profile returns 401", async () => {
    currentUserId = null;
    const { GET } = await import("../app/api/profile/route");
    const res = await GET();
    expect(res.status).toBe(401);
    expect((await jsonOf(res)).code).toBe("UNAUTHORIZED");
  });

  it("GET /api/bookmarks returns 401", async () => {
    currentUserId = null;
    const { GET } = await import("../app/api/bookmarks/route");
    const res = await GET();
    expect(res.status).toBe(401);
  });

  it("GET /api/watchlist returns 401", async () => {
    currentUserId = null;
    const { GET } = await import("../app/api/watchlist/route");
    const res = await GET();
    expect(res.status).toBe(401);
  });
});

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

describe("profile", () => {
  it("GET returns null fields when no row exists", async () => {
    const { GET } = await import("../app/api/profile/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await jsonOf(res);
    expect(body.username).toBeNull();
    expect(body.avatar_url).toBeNull();
  });

  it("PUT upserts then GET reflects it", async () => {
    const { PUT, GET } = await import("../app/api/profile/route");
    const putRes = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "Joe Trader", avatar_url: "https://media.radon.run/a.png" }),
      }),
    );
    expect(putRes.status).toBe(200);
    const saved = await jsonOf(putRes);
    expect(saved.username).toBe("Joe Trader");
    expect(saved.avatar_url).toBe("https://media.radon.run/a.png");

    const getRes = await GET();
    const fetched = await jsonOf(getRes);
    expect(fetched.username).toBe("Joe Trader");
  });

  it("PUT rejects oversized username", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "x".repeat(33) }),
      }),
    );
    expect(res.status).toBe(400);
    expect((await jsonOf(res)).code).toBe("VALIDATION_ERROR");
  });

  it("PUT rejects invalid username characters", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "bad<script>" }),
      }),
    );
    expect(res.status).toBe(400);
  });

  it("PUT rejects a non-data/https avatar_url", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ avatar_url: "javascript:alert(1)" }),
      }),
    );
    expect(res.status).toBe(400);
  });

  it("PUT accepts a data: URL avatar", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ avatar_url: "data:image/png;base64,iVBORw0KG" }),
      }),
    );
    expect(res.status).toBe(200);
    expect((await jsonOf(res)).avatar_url).toBe("data:image/png;base64,iVBORw0KG");
  });

  it("PUT is a partial update: saving username alone preserves the avatar (and vice versa)", async () => {
    const { PUT, GET } = await import("../app/api/profile/route");
    // Seed both fields.
    await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "Joe", avatar_url: "https://media.radon.run/a.png" }),
      }),
    );
    // Save username only -> avatar must survive.
    await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "Joe Renamed" }),
      }),
    );
    let body = await jsonOf(await GET());
    expect(body.username).toBe("Joe Renamed");
    expect(body.avatar_url).toBe("https://media.radon.run/a.png");

    // Save avatar only -> username must survive.
    await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ avatar_url: "https://media.radon.run/b.png" }),
      }),
    );
    body = await jsonOf(await GET());
    expect(body.username).toBe("Joe Renamed");
    expect(body.avatar_url).toBe("https://media.radon.run/b.png");
  });

  it("concurrent partial profile updates preserve disjoint fields", async () => {
    const { PUT, GET } = await import("../app/api/profile/route");
    await Promise.all([
      PUT(req("http://localhost/api/profile", { method: "PUT", body: JSON.stringify({ username: "Parallel" }) })),
      PUT(req("http://localhost/api/profile", { method: "PUT", body: JSON.stringify({ avatar_url: "https://media.radon.run/parallel.png" }) })),
    ]);
    expect(await jsonOf(await GET())).toMatchObject({
      username: "Parallel",
      avatar_url: "https://media.radon.run/parallel.png",
    });
  });
});

// ---------------------------------------------------------------------------
// Bookmarks
// ---------------------------------------------------------------------------

describe("bookmarks", () => {
  it("POST then GET returns it; DELETE removes it", async () => {
    const { POST, GET } = await import("../app/api/bookmarks/route");

    const postRes = await POST(
      req("http://localhost/api/bookmarks", {
        method: "POST",
        body: JSON.stringify({ post_id: "post_42", snapshot: { title: "Hi" } }),
      }),
    );
    expect(postRes.status).toBe(200);
    expect(await jsonOf(postRes)).toMatchObject({ ok: true, bookmarked: true });

    const getRes = await GET();
    const body = await jsonOf(getRes);
    const bookmarks = body.bookmarks as Array<Record<string, unknown>>;
    expect(bookmarks).toHaveLength(1);
    expect(bookmarks[0].post_id).toBe("post_42");
    expect(bookmarks[0].snapshot).toMatchObject({ title: "Hi" });

    const { DELETE } = await import("../app/api/bookmarks/[post_id]/route");
    const delRes = await DELETE(req("http://localhost/api/bookmarks/post_42", { method: "DELETE" }), {
      params: Promise.resolve({ post_id: "post_42" }),
    });
    expect(delRes.status).toBe(200);
    expect(await jsonOf(delRes)).toMatchObject({ ok: true, bookmarked: false });

    const afterRes = await GET();
    expect((await jsonOf(afterRes)).bookmarks).toHaveLength(0);
  });

  it("POST is idempotent on duplicate (UNIQUE user_id+post_id)", async () => {
    const { POST, GET } = await import("../app/api/bookmarks/route");
    const body = JSON.stringify({ post_id: "dup" });
    await POST(req("http://localhost/api/bookmarks", { method: "POST", body }));
    await POST(req("http://localhost/api/bookmarks", { method: "POST", body }));
    const getRes = await GET();
    expect((await jsonOf(getRes)).bookmarks).toHaveLength(1);
  });

  it("DELETE preserves literal percent sequences in bookmark ids", async () => {
    const { POST, GET } = await import("../app/api/bookmarks/route");
    await POST(req("http://localhost/api/bookmarks", {
      method: "POST",
      body: JSON.stringify({ post_id: "post%2Fone" }),
    }));
    const { DELETE } = await import("../app/api/bookmarks/[post_id]/route");
    const res = await DELETE(req("http://localhost/api/bookmarks/post%252Fone", { method: "DELETE" }), {
      params: Promise.resolve({ post_id: "post%2Fone" }),
    });
    expect(res.status).toBe(200);
    expect((await jsonOf(await GET())).bookmarks).toHaveLength(0);
  });

  it("POST rejects missing post_id", async () => {
    const { POST } = await import("../app/api/bookmarks/route");
    const res = await POST(
      req("http://localhost/api/bookmarks", { method: "POST", body: JSON.stringify({}) }),
    );
    expect(res.status).toBe(400);
  });

  it("bookmarks are user-scoped", async () => {
    const { POST, GET } = await import("../app/api/bookmarks/route");
    await POST(req("http://localhost/api/bookmarks", { method: "POST", body: JSON.stringify({ post_id: "mine" }) }));
    currentUserId = "other_user";
    const getRes = await GET();
    expect((await jsonOf(getRes)).bookmarks).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Watchlist
// ---------------------------------------------------------------------------

describe("watchlist", () => {
  it("POST uppercases symbol, GET returns it, DELETE removes it", async () => {
    const { POST, GET } = await import("../app/api/watchlist/route");

    const postRes = await POST(
      req("http://localhost/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ symbol: "nvda", sector: "Tech" }),
      }),
    );
    expect(postRes.status).toBe(200);
    expect(await jsonOf(postRes)).toMatchObject({ ok: true, watched: true });

    const getRes = await GET();
    const watchlist = (await jsonOf(getRes)).watchlist as Array<Record<string, unknown>>;
    expect(watchlist).toHaveLength(1);
    expect(watchlist[0].symbol).toBe("NVDA");
    expect(watchlist[0].sector).toBe("Tech");

    const { DELETE } = await import("../app/api/watchlist/[symbol]/route");
    const delRes = await DELETE(req("http://localhost/api/watchlist/nvda", { method: "DELETE" }), {
      params: Promise.resolve({ symbol: "nvda" }),
    });
    expect(delRes.status).toBe(200);
    expect(await jsonOf(delRes)).toMatchObject({ ok: true, watched: false });

    const afterRes = await GET();
    expect((await jsonOf(afterRes)).watchlist).toHaveLength(0);
  });

  it("POST is idempotent on duplicate symbol", async () => {
    const { POST, GET } = await import("../app/api/watchlist/route");
    await POST(req("http://localhost/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: "AMD" }) }));
    await POST(req("http://localhost/api/watchlist", { method: "POST", body: JSON.stringify({ symbol: "amd" }) }));
    const getRes = await GET();
    expect((await jsonOf(getRes)).watchlist).toHaveLength(1);
  });

  it("POST rejects missing symbol", async () => {
    const { POST } = await import("../app/api/watchlist/route");
    const res = await POST(
      req("http://localhost/api/watchlist", { method: "POST", body: JSON.stringify({}) }),
    );
    expect(res.status).toBe(400);
  });
});
