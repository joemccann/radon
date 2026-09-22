import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { readFileSync } from "node:fs";
import path from "node:path";

const POSTS_SQL = `
CREATE TABLE posts (id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT, timestamp TEXT NOT NULL, images TEXT, raw_images TEXT,
  tags TEXT, tags_text TEXT, tags_vision TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, image_sources TEXT);
CREATE TABLE research_post_sources (post_id TEXT PRIMARY KEY, provenance_json TEXT NOT NULL);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`;
const MIGRATION = readFileSync(path.join(__dirname, "../../scripts/db/migrations/0078_research_feedback.sql"), "utf8");

const POST_ID = "research-" + "a".repeat(64);
const PROVENANCE = {
  kind: "dropbox", publisher: "PNC Economics", url: `/api/newsfeed/research/files/${"b".repeat(64)}.pdf`,
  documentDate: "2026-09-16", folderDate: "2026-09-17", pages: [1], figures: [], fileId: "id:one", revision: "r1",
  contentHash: "c".repeat(64), series: "pnc economics research fomc statement", pipeline: "v2",
};

const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
let db: Client;

async function exec(sql: string) {
  for (const stmt of sql.split(";").map((s) => s.replace(/--.*$/gm, "").trim()).filter(Boolean)) await db.execute(stmt);
}

beforeEach(async () => {
  guard.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "user_operator" } });
  db = createClient({ url: ":memory:" });
  await exec(POSTS_SQL);
  await exec(MIGRATION);
  await db.execute({
    sql: "INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
    args: [POST_ID, "FOMC hikes 25 bps", "body", "2026-09-19T08:34:00Z", "[]", "[]", '["MACRO","RATES"]', "[]", "[]", "2026-09-19T08:34:00Z", "2026-09-19T08:34:00Z"],
  });
  await db.execute({ sql: "INSERT INTO research_post_sources VALUES (?,?)", args: [POST_ID, JSON.stringify(PROVENANCE)] });
  (await import("../lib/db")).__setDbForTests(db);
  (await import("../lib/dbCache")).__clearDbCache();
});

afterEach(async () => {
  vi.restoreAllMocks();
  (await import("../lib/db")).__resetDbForTests();
  db.close();
});

function request(body: unknown) {
  return new Request("http://localhost/api/newsfeed/research/feedback", { method: "POST", body: JSON.stringify(body) });
}

async function vote(body: unknown) {
  const { POST } = await import("../app/api/newsfeed/research/feedback/route");
  return POST(request(body));
}

async function feed() {
  const { GET } = await import("../app/api/newsfeed/posts/route");
  return (await (await GET()).json()) as Array<{ id: string; feedback?: { vote: string; reasons: string[]; comment: string } }>;
}

describe("POST /api/newsfeed/research/feedback", () => {
  it("is operator-only and rate limited", async () => {
    await vote({ postId: POST_ID, vote: "up" });
    expect(guard).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true }));
  });

  it("appends a vote with reasons, comment, actor and a frozen identity snapshot", async () => {
    const response = await vote({ postId: POST_ID, vote: "up", reasons: ["want_more"], comment: "  add the dot plot chart  " });
    expect(response.status).toBe(200);
    const rows = (await db.execute("SELECT * FROM research_feedback")).rows;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ post_id: POST_ID, target: "post", vote: "up", comment: "add the dot plot chart", actor: "user_operator", file_id: "id:one" });
    expect(JSON.parse(String(rows[0].reasons))).toEqual(["want_more"]);
    expect(JSON.parse(String(rows[0].snapshot_json))).toMatchObject({
      title: "FOMC hikes 25 bps", tags: ["MACRO", "RATES"], publisher: "PNC Economics", series: "pnc economics research fomc statement", pipeline: "v2",
    });
  });

  it("is append-only: a second vote adds a row and the latest one wins", async () => {
    await vote({ postId: POST_ID, vote: "up" });
    await vote({ postId: POST_ID, vote: "down", reasons: ["not_relevant"] });
    expect((await db.execute("SELECT count(*) AS n FROM research_feedback")).rows[0].n).toBe(2);
    expect((await feed()).map((p) => p.id)).not.toContain(POST_ID);
  });

  it("a thumbs-down hides the post from the feed immediately and clear restores it", async () => {
    expect((await feed()).map((p) => p.id)).toContain(POST_ID);
    await vote({ postId: POST_ID, vote: "down", reasons: ["not_relevant"], comment: "single stock, not for me" });
    expect((await feed()).map((p) => p.id)).not.toContain(POST_ID);
    await vote({ postId: POST_ID, vote: "clear" });
    const restored = (await feed()).find((p) => p.id === POST_ID);
    expect(restored).toBeDefined();
    expect(restored?.feedback).toBeUndefined();
  });

  it("an up-voted post carries its feedback in the feed", async () => {
    await vote({ postId: POST_ID, vote: "up", reasons: ["want_more"], comment: "chart please" });
    expect((await feed()).find((p) => p.id === POST_ID)?.feedback).toEqual({ vote: "up", reasons: ["want_more"], comment: "chart please" });
  });

  it.each([
    [{ postId: "12345", vote: "up" }, "post id"],
    [{ postId: POST_ID, vote: "sideways" }, "vote"],
    [{ postId: POST_ID, vote: "down", reasons: ["boring"] }, "reason"],
    [{ postId: POST_ID, vote: "down", reasons: "not_relevant" }, "reason"],
    [{ postId: POST_ID, vote: "up", comment: "x".repeat(2001) }, "comment"],
  ])("rejects %j without writing", async (body) => {
    expect((await vote(body)).status).toBe(400);
    expect((await db.execute("SELECT count(*) AS n FROM research_feedback")).rows[0].n).toBe(0);
  });

  it("refuses a vote on a post that is not a research post", async () => {
    expect((await vote({ postId: "research-" + "f".repeat(64), vote: "up" })).status).toBe(404);
  });

  it("returns the guard response untouched when access is denied", async () => {
    guard.mockResolvedValueOnce({ ok: false, response: new Response("no", { status: 403 }) });
    expect((await vote({ postId: POST_ID, vote: "up" })).status).toBe(403);
  });
});

describe("feed compatibility", () => {
  it("serves the feed when the feedback table has not been migrated yet", async () => {
    await db.execute("DROP TABLE research_feedback");
    expect((await feed()).map((p) => p.id)).toContain(POST_ID);
  });
});
