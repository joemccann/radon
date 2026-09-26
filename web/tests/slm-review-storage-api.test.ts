import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { readFileSync } from "node:fs";
import path from "node:path";

const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));

const RUN = "slm-review-cohort-2026";
const URL = `http://localhost/api/admin/slm-review?runId=${RUN}`;
const migration = readFileSync(path.join(__dirname, "../../scripts/db/migrations/0087_slm_review_decisions.sql"), "utf8");
let db: Client;

const decision = (id: string, reviewedAt = "2026-09-25T12:00:00.000Z") => ({
  id,
  humanTags: ["MACRO", "RATES", "FED"],
  acceptance: { "Candidate 1": true },
  reviewer: "operator-a",
  reviewedAt,
});
const body = (decisions: unknown[], index = 0) => ({
  schema: "radon.slm-review-decisions.v1",
  runId: RUN,
  reviewer: "operator-a",
  decisions,
  index,
});

async function put(payload: unknown) {
  const { PUT } = await import("../app/api/admin/slm-review/route");
  return PUT(new Request(URL, { method: "PUT", body: JSON.stringify(payload) }));
}
async function get(url = URL) {
  const { GET } = await import("../app/api/admin/slm-review/route");
  return GET(new Request(url));
}

beforeEach(async () => {
  guard.mockReset();
  guard.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "operator-a" } });
  db = createClient({ url: ":memory:" });
  await db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)");
  for (const statement of migration.split(";").map((sql) => sql.replace(/--.*$/gm, "").trim()).filter(Boolean)) {
    await db.execute(statement);
  }
  (await import("../lib/db")).__setDbForTests(db);
});

afterEach(async () => {
  (await import("../lib/db")).__resetDbForTests();
  db.close();
});

describe("operator SLM review storage", () => {
  it("returns empty decisions and cursor for a new reviewer/run", async () => {
    const response = await get();
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toMatchObject({ ...body([]), index: 0 });
    expect(guard).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true }));
  });

  it("persists only decisions and cursor, and restores them across requests", async () => {
    expect((await put(body([decision("item-1")], 7))).status).toBe(200);
    expect(await (await get()).json()).toMatchObject({ ...body([decision("item-1")], 7), updatedAt: expect.any(String) });
    const row = (await db.execute("SELECT * FROM slm_review_decisions")).rows[0];
    expect(Object.keys(row).sort()).toEqual(["cursor_index", "decisions_json", "reviewer", "revision", "run_id", "updated_at"]);
    expect(String(row.decisions_json)).not.toContain("imageUrls");
  });

  it("merges newer item edits and preserves decisions absent from stale saves", async () => {
    expect((await put(body([decision("item-1"), decision("item-2")]))).status).toBe(200);
    const stale = { ...decision("item-1", "2026-09-25T11:00:00.000Z"), acceptance: { "Candidate 1": false } };
    const response = await put(body([stale, decision("item-3")], 9));
    expect(response.status).toBe(200);
    const saved = await response.json();
    expect(saved.decisions).toHaveLength(3);
    expect(saved.decisions.find((item: { id: string }) => item.id === "item-1").acceptance["Candidate 1"]).toBe(true);
    expect(saved.index).toBe(9);
    const newer = { ...decision("item-1", "2026-09-25T13:00:00.000Z"), acceptance: { "Candidate 1": false } };
    expect((await put(body([newer]))).status).toBe(200);
    expect((await (await get()).json()).decisions.find((item: { id: string }) => item.id === "item-1").acceptance["Candidate 1"]).toBe(false);
  });

  it("isolates reviewers and enforces the authenticated reviewer identity", async () => {
    await put(body([decision("item-1")]));
    guard.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "operator-b" } });
    expect((await (await get()).json()).decisions).toEqual([]);
    expect((await put(body([decision("item-2")]))).status).toBe(400);
    expect((await db.execute("SELECT count(*) AS count FROM slm_review_decisions")).rows[0].count).toBe(1);
  });

  it("rejects source content, malformed decisions, duplicate IDs, and oversized indexes", async () => {
    const bad = [
      { ...body([]), text: "private source" },
      body([{ ...decision("item-1"), imageUrls: ["https://example.com/secret.png"] }]),
      body([decision("item-1"), decision("item-1")]),
      body([{ ...decision("item-1"), acceptance: { "Candidate 4": true } }]),
      body([], 200),
    ];
    for (const value of bad) expect((await put(value)).status).toBe(400);
    expect((await db.execute("SELECT count(*) AS count FROM slm_review_decisions")).rows[0].count).toBe(0);
  });

  it("returns denied access unchanged and never reads storage", async () => {
    guard.mockResolvedValue({ ok: false, response: new Response("forbidden", { status: 403 }) });
    expect((await get()).status).toBe(403);
    expect((await put(body([]))).status).toBe(403);
  });
});
