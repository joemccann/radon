import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createClient, type Client } from "@libsql/client";

// Hermetic tests for the Phase 1 dual-write path. Spin up an in-memory
// libSQL DB per test, apply the schema, inject it into the writer, and
// exercise the upsert + taxonomy helpers end-to-end.

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
  updated_at  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_taxonomy (
  tag         TEXT    PRIMARY KEY COLLATE NOCASE,
  created_at  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS service_health (
  service                  TEXT  PRIMARY KEY,
  state                    TEXT  NOT NULL,
  last_attempt_started_at  TEXT,
  last_attempt_finished_at TEXT,
  last_error               TEXT,
  updated_at               TEXT  NOT NULL
);
`;

let db: Client;

beforeEach(async () => {
  db = createClient({ url: ":memory:" });
  for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
  const writer = await import("../../scripts/db/writer.js");
  writer.__setDbForTests(db);
});

afterEach(async () => {
  const writer = await import("../../scripts/db/writer.js");
  writer.__resetDbForTests();
  db.close();
});

describe("upsertPosts", () => {
  it("inserts a new post with serialized JSON arrays", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPosts([
      {
        id: "abc",
        title: "Test post",
        content: "body",
        timestamp: "2026-05-01T12:00:00Z",
        images: ["https://media.radon.run/abc.png"],
        rawImages: ["https://themarketear.com/abc.png"],
        tags: ["BTC", "MACRO"],
        tags_text: ["BTC"],
        tags_vision: ["MACRO"],
      },
    ]);

    const result = await db.execute("SELECT * FROM posts WHERE id = 'abc'");
    expect(result.rows.length).toBe(1);
    const row = result.rows[0] as unknown as Record<string, string>;
    expect(row.title).toBe("Test post");
    expect(JSON.parse(row.images)).toEqual(["https://media.radon.run/abc.png"]);
    expect(JSON.parse(row.tags)).toEqual(["BTC", "MACRO"]);
    expect(JSON.parse(row.raw_images)).toEqual(["https://themarketear.com/abc.png"]);
  });

  it("absolutizes relative /media/ paths to https://media.radon.run/...", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPosts([
      {
        id: "rel",
        title: "Relative paths",
        timestamp: "2026-05-01T12:00:00Z",
        images: ["/media/foo.png", "https://other.cdn/bar.png", "/media/baz.png"],
      },
    ]);

    const result = await db.execute("SELECT images FROM posts WHERE id = 'rel'");
    const images = JSON.parse(
      (result.rows[0] as unknown as { images: string }).images,
    );
    expect(images).toEqual([
      "https://media.radon.run/foo.png",
      "https://other.cdn/bar.png",
      "https://media.radon.run/baz.png",
    ]);
  });

  it("updates an existing post on conflict", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPosts([
      { id: "x", title: "v1", timestamp: "2026-05-01T12:00:00Z" },
    ]);
    await writer.upsertPosts([
      { id: "x", title: "v2", timestamp: "2026-05-01T13:00:00Z", tags: ["VOL"] },
    ]);

    const result = await db.execute("SELECT * FROM posts WHERE id = 'x'");
    expect(result.rows.length).toBe(1);
    const row = result.rows[0] as unknown as Record<string, string>;
    expect(row.title).toBe("v2");
    expect(JSON.parse(row.tags)).toEqual(["VOL"]);
  });

  it("handles posts with missing optional fields", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.upsertPosts([
      { id: "minimal", title: "no extras", timestamp: "2026-05-01T12:00:00Z" },
    ]);

    const result = await db.execute("SELECT * FROM posts WHERE id = 'minimal'");
    expect(result.rows.length).toBe(1);
    const row = result.rows[0] as unknown as Record<string, string>;
    expect(JSON.parse(row.images)).toEqual([]);
    expect(JSON.parse(row.tags)).toEqual([]);
  });
});

describe("appendTaxonomy", () => {
  it("inserts new tags and reports the count", async () => {
    const writer = await import("../../scripts/db/writer.js");

    const added = await writer.appendTaxonomy(["BTC", "ETH", "GOLD"]);
    expect(added).toBe(3);

    const result = await db.execute("SELECT tag FROM tag_taxonomy ORDER BY tag");
    expect(result.rows.map((r) => (r as unknown as { tag: string }).tag)).toEqual([
      "BTC",
      "ETH",
      "GOLD",
    ]);
  });

  it("ignores duplicates (case-insensitive)", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.appendTaxonomy(["BTC"]);
    const added = await writer.appendTaxonomy(["btc", "BTC", "ETH"]);
    expect(added).toBe(1);

    const result = await db.execute("SELECT COUNT(*) as n FROM tag_taxonomy");
    expect((result.rows[0] as unknown as { n: number }).n).toBe(2);
  });

  it("returns 0 when given empty input", async () => {
    const writer = await import("../../scripts/db/writer.js");

    expect(await writer.appendTaxonomy([])).toBe(0);
    expect(await writer.appendTaxonomy(null)).toBe(0);
  });
});

describe("recordServiceHealth", () => {
  it("upserts a service health row", async () => {
    const writer = await import("../../scripts/db/writer.js");

    await writer.recordServiceHealth("newsfeed-scraper", "ok", {
      startedAt: "2026-05-01T12:00:00Z",
      finishedAt: "2026-05-01T12:00:01Z",
    });
    await writer.recordServiceHealth("newsfeed-scraper", "error", {
      startedAt: "2026-05-01T12:02:00Z",
      finishedAt: "2026-05-01T12:02:01Z",
      error: { message: "boom" },
    });

    const result = await db.execute(
      "SELECT state, last_error FROM service_health WHERE service = 'newsfeed-scraper'",
    );
    expect(result.rows.length).toBe(1);
    const row = result.rows[0] as unknown as { state: string; last_error: string };
    expect(row.state).toBe("error");
    expect(JSON.parse(row.last_error)).toEqual({ message: "boom" });
  });
});
