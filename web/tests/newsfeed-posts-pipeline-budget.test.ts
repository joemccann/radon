/**
 * PERF-006: GET /api/newsfeed/posts must cost ONE Turso execute when the
 * research source and feedback tables exist.
 *
 * The dashboard feed used to SELECT posts, probe sqlite_master, then SELECT
 * latest votes — three sequential HTTP pipelines per 30s cache miss.
 */
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
CREATE TABLE research_feedback (
  id TEXT PRIMARY KEY,
  target TEXT NOT NULL,
  post_id TEXT,
  work_key TEXT,
  file_id TEXT,
  vote TEXT NOT NULL,
  reasons TEXT NOT NULL DEFAULT '[]',
  comment TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL,
  snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
`;

const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
let db: Client;

beforeEach(async () => {
  guard.mockResolvedValue({ ok: true, principal: { kind: "operator" } });
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

describe("PERF-006 newsfeed posts pipeline budget", () => {
  it("loads posts, provenance, and latest feedback in one execute", async () => {
    const base = "/api/newsfeed/research/files/" + "c".repeat(64);
    const source = {
      kind: "dropbox",
      publisher: "Synthetic Bank",
      url: base + ".pdf",
      documentDate: "2026-09-07",
      folderDate: "2026-09-07",
      pages: [1],
      figures: [],
      fileId: "id:budget",
      revision: "r1",
      contentHash: "c".repeat(64),
    };
    const insertPost =
      "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at) VALUES (?, ?, ?, ?, '[]', '[]', '[]', '[]', '[]', ?, ?)";
    await db.execute({
      sql: insertPost,
      args: ["regular", "Public post", null, "2026-09-07T15:00:00Z", "2026-09-07T15:00:00Z", "2026-09-07T15:00:00Z"],
    });
    await db.execute({
      sql: insertPost,
      args: ["research-fb", "Private research", "Evidence", "2026-09-07T16:00:00Z", "2026-09-07T16:00:00Z", "2026-09-07T16:00:00Z"],
    });
    await db.execute({
      sql: "INSERT INTO research_post_sources VALUES (?, ?)",
      args: ["research-fb", JSON.stringify(source)],
    });
    await db.execute({
      sql: "INSERT INTO research_feedback (id, target, post_id, vote, reasons, comment, actor, snapshot_json, created_at) VALUES (?, 'post', ?, 'up', '[]', ?, 'operator', '{}', ?)",
      args: ["fb1", "regular", "private operator triage note", "2026-09-07T17:00:00Z"],
    });

    const execute = vi.spyOn(db, "execute");
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const posts = await response.json();
    const regular = posts.find((post: { id: string }) => post.id === "regular");
    expect(regular.feedback).toEqual({ vote: "up", reasons: [], comment: "private operator triage note" });
    expect(posts.find((post: { id: string }) => post.id === "research-fb").source).toEqual(source);
    expect(execute).toHaveBeenCalledTimes(1);
  });
});
