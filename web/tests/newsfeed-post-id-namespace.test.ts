// Scraped posts carry an id chosen by the upstream page markup, and
// upsertPosts writes ON CONFLICT(id) DO UPDATE into the same `posts` table
// the research publisher fills under its reserved `research-` namespace.
// A scraped item must never be able to overwrite a research post: the cycle
// drops it before merge and the writer refuses it at the sink.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createClient, type Client } from "@libsql/client";

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS posts (
  id          TEXT    PRIMARY KEY,
  title       TEXT    NOT NULL,
  content     TEXT,
  timestamp   TEXT    NOT NULL,
  images      TEXT,
  raw_images  TEXT,
  image_sources TEXT,
  tags        TEXT,
  tags_text   TEXT,
  tags_vision TEXT,
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL
);
`;

const research = {
  id: "research-" + "a".repeat(32),
  title: "Research finding",
  content: "operator-curated",
  timestamp: "2026-11-01T00:00:00Z",
};

describe("isReservedPostId", () => {
  it("reserves the research namespace and rejects unusable ids", async () => {
    const { isReservedPostId, isWritablePostId } = await import("../../scripts/db/post_id.js");
    expect(isReservedPostId(research.id)).toBe(true);
    expect(isReservedPostId("Research-abc")).toBe(true);
    expect(isReservedPostId("research")).toBe(false);
    expect(isReservedPostId("post-123")).toBe(false);
    expect(isWritablePostId("post-123")).toBe(true);
    expect(isWritablePostId(research.id)).toBe(false);
    expect(isWritablePostId("")).toBe(false);
    expect(isWritablePostId(42)).toBe(false);
    expect(isWritablePostId(undefined)).toBe(false);
  });
});

describe("upsertPosts namespace guard", () => {
  let db: Client;

  beforeEach(async () => {
    db = createClient({ url: ":memory:" });
    for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
      await db.execute(stmt);
    }
    const writer = await import("../../scripts/db/writer.js");
    writer.__setDbForTests(db);
    await db.execute({
      sql: "INSERT INTO posts (id, title, content, timestamp, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
      args: [research.id, research.title, research.content, research.timestamp, "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"],
    });
  });

  afterEach(async () => {
    const writer = await import("../../scripts/db/writer.js");
    writer.__resetDbForTests();
    db.close();
  });

  it("refuses a reserved id through the singular writer too", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPost({ id: research.id, title: "replaced", content: "x", timestamp: "2026-11-14T00:00:00Z" });

    const rows = await db.execute("SELECT title, content FROM posts WHERE id = ?", [research.id]);
    expect(rows.rows.map((r) => [r.title, r.content])).toEqual([[research.title, research.content]]);
  });

  it("never updates a reserved row and still writes the valid posts in the batch", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPosts([
      { id: research.id, title: "replaced by a scrape", content: "upstream markup", timestamp: "2026-11-14T00:00:00Z" },
      { id: "", title: "no id", timestamp: "2026-11-14T00:00:00Z" },
      { id: "scraped-1", title: "Scraped", content: "body", timestamp: "2026-11-14T00:00:00Z" },
    ]);

    const rows = await db.execute("SELECT id, title, content FROM posts ORDER BY id");
    expect(rows.rows.map((r) => [r.id, r.title, r.content])).toEqual([
      [research.id, research.title, research.content],
      ["scraped-1", "Scraped", "body"],
    ]);
  });
});

describe("runScrapeCycle namespace guard", () => {
  it("drops a scraped item carrying a reserved id before merge and write", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const merged: string[] = [];
    const written: string[] = [];

    const result = await runScrapeCycle({
      cycleStartIso: "2026-11-14T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({
        items: [
          { id: research.id, title: "Hijack", content: "body", timestamp: "2026-11-14T12:00:00Z", images: [] },
          { id: "scraped-1", title: "Fresh", content: "body", timestamp: "2026-11-14T12:00:00Z", images: [] },
        ],
      }),
      mergePosts: (_existing: unknown[], items: Array<{ id: string }>) => {
        merged.push(...items.map((i) => i.id));
        return { merged: items.map((item) => ({ ...item })), changed: true };
      },
      hydrateLocalImages: async () => false,
      persistPosts: async () => ({ archived: false }),
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async (posts: Array<{ id: string }>) => {
        written.push(...posts.map((p) => p.id));
      },
      hydrateTagsDual: async () => ({ updated: false }),
      recordServiceHealth: async () => {},
      buildTextTagger: () => null,
      buildVisionTagger: () => null,
      onNewTags: () => {},
      requestReauth: async () => {},
      paths: { postsFile: "/dev/null" },
    });

    expect(result).toBeTruthy();
    expect(merged).toEqual(["scraped-1"]);
    expect(written).toEqual(["scraped-1"]);
  });
});
