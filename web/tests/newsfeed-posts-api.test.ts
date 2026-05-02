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
  tags        TEXT,
  tags_text   TEXT,
  tags_vision TEXT,
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL
);
`;

let db: Client;

beforeEach(async () => {
  db = createClient({ url: ":memory:" });
  for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
  const dbModule = await import("../lib/db");
  dbModule.__setDbForTests(db);
});

afterEach(async () => {
  const dbModule = await import("../lib/db");
  dbModule.__resetDbForTests();
  db.close();
});

describe("/api/newsfeed/posts", () => {
  it("returns posts ordered by timestamp DESC", async () => {
    await db.execute({
      sql: "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
      sql: "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

  it("safely handles malformed JSON in array columns", async () => {
    await db.execute({
      sql: "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
