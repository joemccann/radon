import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { readFileSync } from "node:fs";
import path from "node:path";

const MIGRATION = readFileSync(path.join(__dirname, "../../scripts/db/migrations/0080_research_rule_proposals.sql"), "utf8");
const guard = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
let db: Client;

async function exec(sql: string) {
  for (const stmt of sql.replace(/--.*$/gm, "").split(";").map((s) => s.trim()).filter(Boolean)) await db.execute(stmt);
}

async function proposal(id: string, status = "proposed", decidedAt: string | null = null) {
  const [kind, key] = id.split(":");
  await db.execute({
    sql: "INSERT INTO research_rule_proposals (id,kind,key,downs,ups,evidence_json,status,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
    args: [id, kind, key, 4, 0, JSON.stringify(["s0", "s1", "s2", "h1"]), status, "2026-09-19T12:00:00Z", decidedAt],
  });
}

beforeEach(async () => {
  guard.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "user_operator" } });
  db = createClient({ url: ":memory:" });
  await exec("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)");
  await exec(MIGRATION);
  (await import("../lib/db")).__setDbForTests(db);
});

afterEach(async () => {
  vi.restoreAllMocks();
  (await import("../lib/db")).__resetDbForTests();
  db.close();
});

async function list() {
  const { GET } = await import("../app/api/newsfeed/research/rules/route");
  const response = await GET(new Request("http://localhost/api/newsfeed/research/rules"));
  return { status: response.status, body: await response.json() as { proposed: Array<Record<string, unknown>>; approved: Array<Record<string, unknown>>; rejected: Array<Record<string, unknown>> } };
}

async function decide(body: unknown) {
  const { POST } = await import("../app/api/newsfeed/research/rules/route");
  return POST(new Request("http://localhost/api/newsfeed/research/rules", { method: "POST", body: JSON.stringify(body) }));
}

describe("/api/newsfeed/research/rules", () => {
  it("is operator-only on both verbs", async () => {
    await list();
    await decide({ id: "series_deny:x", decision: "approve" });
    expect(guard).toHaveBeenCalledTimes(2);
    for (const call of guard.mock.calls) expect(call[1]).toEqual(expect.objectContaining({ operatorOnly: true }));
  });

  it("lists rejected under rejected, newest decision first", async () => {
    await proposal("series_deny:ubs cio fx view");
    await proposal("doc_type_drop:single_stock", "approved");
    await proposal("publisher_deny:Older Desk", "rejected", "2026-09-17T12:00:00Z");
    await proposal("publisher_deny:Maxim Group", "rejected", "2026-09-18T12:00:00Z");
    const { status, body } = await list();
    expect(status).toBe(200);
    expect(body.proposed).toEqual([{ id: "series_deny:ubs cio fx view", kind: "series_deny", key: "ubs cio fx view", downs: 4, ups: 0, evidence: 4 }]);
    expect(body.approved.map((r) => r.id)).toEqual(["doc_type_drop:single_stock"]);
    expect(body.rejected).toEqual([
      { id: "publisher_deny:Maxim Group", kind: "publisher_deny", key: "Maxim Group", downs: 4, ups: 0, evidence: 4 },
      { id: "publisher_deny:Older Desk", kind: "publisher_deny", key: "Older Desk", downs: 4, ups: 0, evidence: 4 },
    ]);
  });

  it("caps rejected at 50 so history never starves proposed", async () => {
    await proposal("series_deny:ubs cio fx view");
    for (let i = 0; i < 60; i += 1) {
      const stamp = String(i).padStart(2, "0");
      await proposal(`publisher_deny:desk-${stamp}`, "rejected", `2026-09-19T12:${stamp}:00Z`);
    }
    const { status, body } = await list();
    expect(status).toBe(200);
    expect(body.proposed.map((r) => r.id)).toEqual(["series_deny:ubs cio fx view"]);
    expect(body.rejected).toHaveLength(50);
    expect(body.rejected[0].id).toBe("publisher_deny:desk-59");
    expect(body.rejected[49].id).toBe("publisher_deny:desk-10");
  });

  it("approve, reject and revoke record who decided and when", async () => {
    await proposal("series_deny:ubs cio fx view");
    expect((await decide({ id: "series_deny:ubs cio fx view", decision: "approve" })).status).toBe(200);
    let row = (await db.execute("SELECT status, decided_by, decided_at FROM research_rule_proposals")).rows[0];
    expect(row.status).toBe("approved");
    expect(row.decided_by).toBe("user_operator");
    expect(String(row.decided_at)).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect((await decide({ id: "series_deny:ubs cio fx view", decision: "revoke" })).status).toBe(200);
    row = (await db.execute("SELECT status FROM research_rule_proposals")).rows[0];
    expect(row.status).toBe("rejected");
    expect((await decide({ id: "series_deny:ubs cio fx view", decision: "approve" })).status).toBe(200);
    row = (await db.execute("SELECT status FROM research_rule_proposals")).rows[0];
    expect(row.status).toBe("approved");
  });

  it.each([
    [{ id: "series_deny:missing", decision: "approve" }, 404],
    [{ id: "series_deny:x", decision: "maybe" }, 400],
    [{ id: 7, decision: "approve" }, 400],
    [{ id: "drop_everything:x", decision: "approve" }, 400],
  ])("rejects %j with %i", async (body, status) => {
    await proposal("series_deny:x");
    expect((await decide(body)).status).toBe(status);
    expect((await db.execute("SELECT status FROM research_rule_proposals")).rows[0].status).toBe("proposed");
  });

  it("serves empty lists before the table is migrated", async () => {
    await db.execute("DROP TABLE research_rule_proposals");
    const { status, body } = await list();
    expect(status).toBe(200);
    expect(body).toEqual({ proposed: [], approved: [], rejected: [] });
  });
});
