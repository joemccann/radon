/** @vitest-environment node */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

const mocks = vi.hoisted(() => ({ getDb: vi.fn(), readFile: vi.fn(), requireRouteAccess: vi.fn() }));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/db", () => ({ getDb: mocks.getDb, resetDb: vi.fn() }));
vi.mock("fs/promises", async (original) => ({
  ...await original<typeof import("fs/promises")>(), readFile: mocks.readFile,
}));

let db: Client;
const comparison = { available: false, label: "MSCI US forward 12-month EPS", reason: "Licensed EPS series not connected" };
const missing = { missing: true, scan_time: null, source_last_modified: null, data_date: null,
  count: 0, series: [], current: null, sources: null, comparison };
function payload(stamp = new Date().toISOString(), mean = 25) {
  return { missing: false, scan_time: stamp, source_last_modified: null, data_date: "2026-09-30",
    count: 1, current: { date: "2026-09", mean_yoy_pct: mean, ma3_yoy_pct: mean },
    series: [{ date: "2026-09", mean_yoy_pct: mean }], comparison, sources: {} };
}
async function seed(data: ReturnType<typeof payload>, service = "asia-export-lead") {
  await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?, ?, ?)", args: [service, data.scan_time, JSON.stringify(data)] });
}
beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await db.execute("CREATE TABLE scan_snapshots(service TEXT,scan_time TEXT,payload TEXT,PRIMARY KEY(service,scan_time))");
  mocks.getDb.mockReturnValue(db);
  mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "operator" } });
  mocks.readFile.mockRejectedValue(Object.assign(new Error("missing"), { code: "ENOENT" }));
});
afterEach(() => { db.close(); vi.restoreAllMocks(); });

describe("official export lead GET", () => {
  it("classifies read-only dynamic Node data", async () => {
    const route = await import("../app/api/asia-export-lead/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
    expect(route.radonCapability).toBe("read");
    expect(route).not.toHaveProperty("POST");
  });
  it("executes service-scoped SQL and serves Turso over older disk", async () => {
    await seed(payload());
    await seed(payload(new Date(Date.now()+1000).toISOString(), 999), "other-service");
    mocks.readFile.mockResolvedValue(JSON.stringify(payload(new Date(Date.now()-3600000).toISOString(), -30)));
    const { GET } = await import("../app/api/asia-export-lead/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect((await response.json()).current.mean_yoy_pct).toBe(25);
    expect(response.headers.get("X-Request-Id")).toBeTruthy();
    expect(response.headers.get("Cache-Control")).toContain("max-age=300");
    expect(response.headers.get("Cache-Control")).toContain("stale-while-revalidate=3600");
  });
  it("uses the newer content timestamp rather than blindly preferring database", async () => {
    await seed(payload(new Date(Date.now()-3600000).toISOString(), -30));
    mocks.readFile.mockResolvedValue(JSON.stringify(payload(new Date().toISOString(), 40)));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect((await (await GET()).json()).current.mean_yoy_pct).toBe(40);
  });
  it("returns the exact missing measurement contract at HTTP200", async () => {
    const { GET } = await import("../app/api/asia-export-lead/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(missing);
  });
  it("falls back to a valid disk snapshot when the database is unavailable", async () => {
    mocks.getDb.mockImplementation(() => { throw new Error("offline"); });
    mocks.readFile.mockResolvedValue(JSON.stringify(payload()));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect((await (await GET()).json()).missing).toBe(false);
  });
  it("collapses stale writer snapshots while retaining their diagnostic timestamp", async () => {
    const stamp = new Date(Date.now()-27*3600000).toISOString();
    await seed(payload(stamp));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual({ ...missing, stale: true, scan_time: stamp });
  });
});


describe("export snapshot provenance and invalid sources", () => {
  it("selects the latest snapshot within the export service", async () => {
    await seed(payload(new Date(Date.now() - 3600000).toISOString(), -30));
    const newest = payload();
    await seed(newest);
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(newest);
  });

  it("never returns another service when the export snapshot is absent", async () => {
    await seed(payload(), "other-service");
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(missing);
  });

  it("compares payload sample clocks rather than a fresher SQL envelope", async () => {
    const old = payload(new Date(Date.now() - 27 * 3600000).toISOString(), -10);
    await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?, ?, ?)",
      args: ["asia-export-lead", new Date().toISOString(), JSON.stringify(old)] });
    const disk = payload(new Date(Date.now() - 3600000).toISOString(), 40);
    mocks.readFile.mockResolvedValue(JSON.stringify(disk));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(disk);
  });

  it.each(["db", "disk"])("rejects a future %s sample without displacing the healthy source", async (source) => {
    const fresh = payload(new Date(Date.now() - 3600000).toISOString(), 25);
    const future = payload(new Date(Date.now() + 3600000).toISOString(), 999);
    await seed(source === "db" ? future : fresh);
    mocks.readFile.mockResolvedValue(JSON.stringify(source === "disk" ? future : fresh));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(fresh);
  });

  it("rejects an only future sample", async () => {
    await seed(payload(new Date(Date.now() + 3600000).toISOString()));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(missing);
  });

  it("preserves complete source and comparison metadata without mutating the stored payload", async () => {
    const data = { ...payload(), source_last_modified: "Mon, 19 Oct 2026 08:00:00 GMT",
      sources: { taiwan: { name: "Taiwan MOF", latest_month: "2026-09", last_modified: null },
        korea: { name: "Korea Customs", latest_month: "2026-09", last_modified: null } },
      revision_note: "Latest-revised history" };
    const serialized = JSON.stringify(data);
    await seed(data);
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(data);
    const stored = await db.execute("SELECT payload FROM scan_snapshots");
    expect(stored.rows[0].payload).toBe(serialized);
  });

  it.each([null, [], 5, "not-json"])("uses the healthy DB when disk is malformed: %s", async (value) => {
    const data = payload();
    await seed(data);
    mocks.readFile.mockResolvedValue(value === "not-json" ? value : JSON.stringify(value));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(data);
  });

  it("falls back after a malformed database payload", async () => {
    await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?, ?, ?)", args: ["asia-export-lead", new Date().toISOString(), "not-json"] });
    const data = payload();
    mocks.readFile.mockResolvedValue(JSON.stringify(data));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(data);
  });

  it("does not let a newer missing marker replace valid observations", async () => {
    await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?, ?, ?)",
      args: ["asia-export-lead", new Date().toISOString(), JSON.stringify({ ...missing, scan_time: new Date().toISOString() })] });
    const data = payload(new Date(Date.now() - 3600000).toISOString());
    mocks.readFile.mockResolvedValue(JSON.stringify(data));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(data);
  });

  it.each([26 * 3600000, 26 * 3600000 + 1])("uses the shared 26h inclusive boundary at age %i", async (age) => {
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now);
    const data = payload(new Date(now - age).toISOString());
    await seed(data);
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(age === 26 * 3600000
      ? data : { ...missing, stale: true, scan_time: data.scan_time });
  });

  it("retains an invalid timestamp as outage provenance while withholding observations", async () => {
    await seed(payload("invalid"));
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual({ ...missing, stale: true, scan_time: "invalid" });
  });
});


describe("export snapshots across demo mirror windows", () => {
  it("serves a mirrored 60h sample to a demo principal without altering source clocks", async () => {
    const data = payload(new Date(Date.now() - 60 * 3600000).toISOString());
    await seed(data);
    mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { kind: "demo", userId: "demo" } });
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual(data);
    expect(mocks.requireRouteAccess).toHaveBeenCalledWith();
  });

  it("collapses demo snapshots older than the mirror budget with their original sample clock", async () => {
    const data = payload(new Date(Date.now() - 97 * 3600000).toISOString());
    await seed(data);
    mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { kind: "demo", userId: "demo" } });
    const { GET } = await import("../app/api/asia-export-lead/route");
    expect(await (await GET()).json()).toEqual({ ...missing, stale: true, scan_time: data.scan_time });
  });
});
