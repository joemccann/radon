import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS posts (
  id          TEXT    PRIMARY KEY,
  title       TEXT    NOT NULL,
  content     TEXT,
  timestamp   TEXT    NOT NULL,
  images      TEXT,
  raw_images  TEXT,
  tags        TEXT,
  tags_text   TEXT,
  tags_vision TEXT,
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  image_sources TEXT
 );
CREATE TABLE research_post_sources (post_id TEXT PRIMARY KEY, provenance_json TEXT NOT NULL);
`;

const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
let db: Client;

beforeEach(async () => {
  guard.mockResolvedValue({ok:true, principal:{kind:"operator"}});
  db = createClient({ url: ":memory:" });
  for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
  const dbModule = await import("../lib/db");
  dbModule.__setDbForTests(db);
});

afterEach(async () => {
  vi.restoreAllMocks();
  const dbModule = await import("../lib/db");
  dbModule.__resetDbForTests();
  db.close();
});

describe("/api/newsfeed/posts", () => {
  it("returns posts ordered by timestamp DESC", async () => {
    await db.execute({
      sql: "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      args: [
        "older",
        "Older post",
        null,
        "2026-04-30T12:00:00Z",
        "[]",
        "[]",
        "[]",
        "[]",
        "[]",
        "2026-04-30T12:00:00Z",
        "2026-04-30T12:00:00Z",
      ],
    });
    await db.execute({
      sql: "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      args: [
        "newer",
        "Newer post",
        "body",
        "2026-05-01T12:00:00Z",
        '["https://media.radon.run/x.png"]',
        '["https://themarketear.com/x.png"]',
        '["BTC","MACRO"]',
        '["BTC"]',
        '["MACRO"]',
        "2026-05-01T12:00:00Z",
        "2026-05-01T12:00:00Z",
      ],
    });

    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const data = await response.json();

    expect(Array.isArray(data)).toBe(true);
    expect(data.length).toBe(2);
    expect(data[0].id).toBe("newer");
    expect(data[0].title).toBe("Newer post");
    expect(data[0].images).toEqual(["https://media.radon.run/x.png"]);
    expect(data[0].rawImages).toEqual(["https://themarketear.com/x.png"]);
    expect(data[0].tags).toEqual(["BTC", "MACRO"]);
    expect(data[0].tags_text).toEqual(["BTC"]);
    expect(data[0].tags_vision).toEqual(["MACRO"]);
    expect(data[0].createdAt).toBe("2026-05-01T12:00:00Z");
    expect(data[0].updatedAt).toBe("2026-05-01T12:00:00Z");
  });

  it("returns empty array when no posts", async () => {
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data).toEqual([]);
  });

  it("returns 503 when the DB read fails (never a blank 200)", async () => {
    const failing = {
      execute: async () => {
        throw new Error("turso unreachable");
      },
    } as unknown as Client;
    const dbModule = await import("../lib/db");
    dbModule.__setDbForTests(failing);

    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();

    expect(response.status).toBe(503);
    const data = await response.json();
    expect(data.error).toBe("Newsfeed temporarily unavailable");
  });

  it("safely handles malformed JSON in array columns", async () => {
    await db.execute({
      sql: "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      args: [
        "bad",
        "Bad JSON",
        null,
        "2026-05-01T12:00:00Z",
        "not-json",
        null,
        "[broken",
        null,
        null,
        "2026-05-01T12:00:00Z",
        "2026-05-01T12:00:00Z",
      ],
    });

    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data[0].images).toEqual([]);
    expect(data[0].tags).toEqual([]);
  });
});

describe("research post visibility", () => {
 it("joins provenance for operators but excludes research from demo responses", async () => {
  const base = "/api/newsfeed/research/files/" + "a".repeat(64);
  const source = {kind:"dropbox",publisher:"Synthetic Bank",url:base+".pdf",documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2],figures:[],fileId:"id:fixture",revision:"r1",contentHash:"a".repeat(64)};
  await db.execute({sql:"INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",args:["research-fixture","Private research","Evidence","2026-09-07T16:00:00Z","[]","[]","[]","[]","[]","2026-09-07T16:00:00Z","2026-09-07T16:00:00Z"]});
  await db.execute({sql:"INSERT INTO research_post_sources VALUES (?, ?)",args:["research-fixture",JSON.stringify(source)]});
  const {GET} = await import("../app/api/newsfeed/posts/route");
  const response = await GET();
  expect((await response.json())[0].source).toEqual(source);
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  guard.mockResolvedValue({ok:true,principal:{kind:"demo"}});
  expect(await (await GET()).json()).toEqual([]);
 });
});


describe("newsfeed during independent research schema rollout", () => {
  it.each(["operator", "demo"])("serves only the newest 500 legacy rows without the research table for %s", async (kind) => {
    guard.mockResolvedValue({ ok: true, principal: { kind } });
    await db.execute("DROP TABLE research_post_sources");
    const insert = (id: string, timestamp: string) => ({
      sql: "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      args: [id, id, "body", timestamp, "[]", "[]", "[]", "[]", "[]", timestamp, timestamp],
    });
    await db.batch([
      ...Array.from({ length: 501 }, (_, index) => insert(
        `legacy-${index}`, new Date(Date.UTC(2026, 8, 7, 0, 0, index)).toISOString(),
      )),
      insert("research-private", "2026-09-08T00:00:00Z"),
    ], "write");

    // Reconnecting to a real database preserves its rows. Keep the in-memory
    // fixture available when dbExecute resets the cached client after SQL errors.
    const dbModule = await import("../lib/db");
    const getDb = vi.spyOn(dbModule, "getDb").mockReturnValue(db);
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(getDb).toHaveBeenCalledTimes(2);
    const posts = await response.json();
    expect(posts).toHaveLength(500);
    expect(posts[0].id).toBe("legacy-500");
    expect(posts.at(-1).id).toBe("legacy-1");
    expect(posts.every((post: { id: string; source?: unknown }) => !post.id.startsWith("research-") && !post.source)).toBe(true);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it.each([
    ["NETWORK_ERROR", "fetch failed"],
    ["SQLITE_AUTH", "not authorized"],
    ["SQLITE_ERROR", "SQLITE_ERROR: no such table: posts"],
    ["SQLITE_ERROR", "SQLITE_ERROR: no such table: research_post_sources_backup"],
    ["SQLITE_ERROR", "SQLITE_ERROR: no such column: r.provenance_json"],
    ["NETWORK_ERROR", "SQLITE_ERROR: no such table: research_post_sources"],
  ])("does not retry the legacy query for %s: %s", async (code, message) => {
    const execute = vi.fn().mockRejectedValue(Object.assign(new Error(message), { code }));
    const dbModule = await import("../lib/db");
    dbModule.__setDbForTests({ execute } as unknown as Client);
    const { GET } = await import("../app/api/newsfeed/posts/route");
    expect((await GET()).status).toBe(503);
    expect(execute).toHaveBeenCalledTimes(1);
  });
});


describe("Market Ear image source attribution", () => {
  const image = "https://media.radon.run/images/adoption.png";
  const second = "https://media.radon.run/images/other.png";

  async function readSources(raw: string | null) {
    const stamp = "2026-09-10T12:00:00Z";
    await db.execute({
      sql: "INSERT INTO posts (id, title, timestamp, images, created_at, updated_at, image_sources) VALUES (?, ?, ?, ?, ?, ?, ?)",
      args: ["adoption", "Adoption slows", stamp, JSON.stringify([image, second]), stamp, stamp, raw],
    });
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    return (await response.json())[0];
  }

  it("returns image providers keyed by the served image URL", async () => {
    const post = await readSources(JSON.stringify({ [image]: "Ramp", [second]: "Goldman Sachs" }));
    expect(post.imageSources).toEqual({ [image]: "Ramp", [second]: "Goldman Sachs" });
    expect(post.source).toBeUndefined();
    expect(post.images).toEqual([image, second]);
  });

  it.each([null, "not-json", "null", "[]", '["Ramp"]', '"Ramp"', "42"])(
    "ignores missing or malformed image source metadata: %s",
    async (raw) => {
      const post = await readSources(raw);
      expect(post.imageSources).toEqual({});
      expect(post.images).toEqual([image, second]);
    },
  );

  it("keeps only nonempty string providers for this post's served images", async () => {
    const post = await readSources(JSON.stringify({
      [image]: "  Ramp  ",
      [second]: { publisher: "Do not stringify" },
      "https://themarketear.com/raw.png": "Raw URL must not leak",
      "https://media.radon.run/images/unrelated.png": "Unrelated chart",
    }));
    expect(post.imageSources).toEqual({ [image]: "Ramp" });
  });

  it.each(["", "   ", null, 3, true, ["Ramp"]].map(provider => ({ provider })))("ignores invalid provider values: $provider", async ({ provider }) => {
    const post = await readSources(JSON.stringify({ [image]: provider }));
    expect(post.imageSources).toEqual({});
  });
});
