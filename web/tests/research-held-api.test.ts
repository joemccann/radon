import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

const migration = (name: string) => readFileSync(path.join(__dirname, "../../scripts/db/migrations", name), "utf8");
const BASE_SQL = `
CREATE TABLE posts (id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT, timestamp TEXT NOT NULL, images TEXT, raw_images TEXT,
  tags TEXT, tags_text TEXT, tags_vision TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, image_sources TEXT);
CREATE TABLE research_post_sources (post_id TEXT PRIMARY KEY, provenance_json TEXT NOT NULL);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`;

const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
let db: Client;
const NOW = new Date("2026-09-19T15:00:00Z");
const wk = (name: string) => createHash("sha256").update(name).digest("hex");

async function exec(sql: string) {
  for (const stmt of sql.replace(/--.*$/gm, "").split(";").map((s) => s.trim()).filter(Boolean)) await db.execute(stmt);
}

async function outcome(key: string, outcomeValue: string, codes: string[], folderDate = "2026-09-17", extra: Partial<Record<string, string>> = {}) {
  await db.execute({
    sql: `INSERT INTO research_outcomes (work_key,file_id,file_name,publisher,series,doc_type,folder_date,document_date,outcome,reason_codes,drafts_json,posts,pipeline,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    args: [wk(key), "id:" + key, extra.file_name ?? key + ".pdf", extra.publisher ?? "Goldman Sachs", "series " + key, "research", folderDate, folderDate,
      outcomeValue, JSON.stringify(codes), JSON.stringify([{ title: "Draft " + key, content: "Body", held: codes[0], detail: "$47bn" }]), outcomeValue === "published" ? 1 : 0, "v2", "2026-09-19T10:00:00Z"],
  });
}

beforeEach(async () => {
  vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
  guard.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "user_operator" } });
  db = createClient({ url: ":memory:" });
  await exec(BASE_SQL);
  await exec(migration("0078_research_feedback.sql"));
  await exec(migration("0079_research_outcomes.sql"));
  await exec(migration("0081_research_outcomes_context.sql"));
  (await import("../lib/db")).__setDbForTests(db);
});

afterEach(async () => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  (await import("../lib/db")).__resetDbForTests();
  db.close();
});

async function held() {
  const { GET } = await import("../app/api/newsfeed/research/held/route");
  const response = await GET(new Request("http://localhost/api/newsfeed/research/held"));
  return { status: response.status, body: await response.json() as { items: Array<Record<string, unknown>>; pending: number } };
}

async function vote(body: unknown) {
  const { POST } = await import("../app/api/newsfeed/research/feedback/route");
  return POST(new Request("http://localhost/api/newsfeed/research/feedback", { method: "POST", body: JSON.stringify(body) }));
}

describe("GET /api/newsfeed/research/held", () => {
  it("is operator-only", async () => {
    await held();
    expect(guard).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true }));
  });

  it("returns held and dropped documents with reason codes and rejected drafts, never published ones", async () => {
    await outcome("a", "held", ["NUMBER_NOT_ON_PAGE"]);
    await outcome("b", "dropped", ["DOC_TYPE_FX_PAIR_NOTE"]);
    await outcome("c", "published", []);
    const { status, body } = await held();
    expect(status).toBe(200);
    expect(body.items.map((i) => i.fileName).sort()).toEqual(["a.pdf", "b.pdf"]);
    const item = body.items.find((i) => i.fileName === "a.pdf")!;
    expect(item).toMatchObject({ workKey: wk("a"), publisher: "Goldman Sachs", outcome: "held", reasonCodes: ["NUMBER_NOT_ON_PAGE"], folderDate: "2026-09-17" });
    expect(item.drafts).toEqual([{ title: "Draft a", content: "Body", held: "NUMBER_NOT_ON_PAGE", detail: "$47bn" }]);
  });

  it("returns the document context so a hold with no drafts can still be judged", async () => {
    await outcome("w", "held", ["NO_CANDIDATES"]);
    const context = { pageCount: 9, figureCount: 2, dateSource: "text", excerpt: "The Daily Froth: A Brief History of the End of the World.",
      selectorReason: "Historical essay; no measured market finding.", sourceUrl: "/api/newsfeed/research/files/" + "c".repeat(64) + ".pdf" };
    await db.execute({ sql: "UPDATE research_outcomes SET context_json = ? WHERE work_key = ?", args: [JSON.stringify(context), wk("w")] });
    expect((await held()).body.items[0].context).toEqual(context);
  });

  it("never serves a source link that is not an authenticated research PDF", async () => {
    await outcome("x", "held", ["NO_CANDIDATES"]);
    await db.execute({ sql: "UPDATE research_outcomes SET context_json = ? WHERE work_key = ?", args: [JSON.stringify({ sourceUrl: "https://evil.example/x.pdf", excerpt: 7 }), wk("x")] });
    expect((await held()).body.items[0].context).toEqual({ pageCount: null, figureCount: 0, dateSource: "", excerpt: "", selectorReason: "", sourceUrl: "" });
  });

  it("samples at most 10 a day, stratified across reason codes, stable within the day", async () => {
    for (let i = 0; i < 30; i += 1) await outcome("n" + i, "held", ["NUMBER_NOT_ON_PAGE"]);
    for (let i = 0; i < 3; i += 1) await outcome("v" + i, "held", ["VERIFY_FAILED"]);
    await outcome("d0", "dropped", ["DOC_TYPE_CALENDAR"]);
    const first = await held();
    expect(first.body.items).toHaveLength(10);
    expect(first.body.pending).toBe(34);
    const codes = first.body.items.map((i) => (i.reasonCodes as string[])[0]);
    expect(codes.filter((c) => c === "VERIFY_FAILED").length).toBe(3);
    expect(codes).toContain("DOC_TYPE_CALENDAR");
    expect((await held()).body.items.map((i) => i.workKey)).toEqual(first.body.items.map((i) => i.workKey));
  });

  it("drops documents older than seven folder days and ones already voted on", async () => {
    await outcome("old", "held", ["NO_CANDIDATES"], "2026-09-01");
    await outcome("fresh", "held", ["NO_CANDIDATES"]);
    await outcome("voted", "held", ["NO_CANDIDATES"]);
    expect((await vote({ workKey: wk("voted"), vote: "down" })).status).toBe(200);
    expect((await held()).body.items.map((i) => i.fileName)).toEqual(["fresh.pdf"]);
  });

  it("serves an empty list when the outcome mirror is not migrated yet", async () => {
    await db.execute("DROP TABLE research_outcomes");
    const { status, body } = await held();
    expect(status).toBe(200);
    expect(body.items).toEqual([]);
  });
});

describe("POST feedback on a held document", () => {
  it("records a should-have-published vote against the work key with the outcome snapshot", async () => {
    await outcome("a", "held", ["NUMBER_NOT_ON_PAGE"]);
    const response = await vote({ workKey: wk("a"), vote: "up", comment: "this flow note matters, the number is in the table" });
    expect(response.status).toBe(200);
    const row = (await db.execute("SELECT * FROM research_feedback")).rows[0];
    expect(row).toMatchObject({ target: "held", work_key: wk("a"), post_id: null, file_id: "id:a", vote: "up", actor: "user_operator" });
    expect(JSON.parse(String(row.snapshot_json))).toMatchObject({ fileName: "a.pdf", publisher: "Goldman Sachs", outcome: "held", reasonCodes: ["NUMBER_NOT_ON_PAGE"] });
  });

  it.each([
    [{ workKey: "short", vote: "up" }, 400],
    [{ workKey: "f".repeat(64), vote: "up" }, 404],
    [{ workKey: wk("a"), postId: "research-" + "a".repeat(64), vote: "up" }, 400],
  ])("rejects %j with %i", async (body, status) => {
    await outcome("a", "held", ["NUMBER_NOT_ON_PAGE"]);
    expect((await vote(body)).status).toBe(status);
    expect((await db.execute("SELECT count(*) AS n FROM research_feedback")).rows[0].n).toBe(0);
  });
});
