/** @vitest-environment node */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

const mocks = vi.hoisted(() => ({ access: vi.fn(), getDb: vi.fn() }));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.access }));
vi.mock("@/lib/db", () => ({ getDb: mocks.getDb, resetDb: vi.fn() }));
import { GET, dynamic, runtime, radonCapability } from "@/app/api/vix-model-spread/route";
import { __resetDbFirstReadMemoryForTests } from "@/lib/dbFirstRead";

let db: Client;
const request = () => new Request("http://localhost/api/vix-model-spread");
const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();
const entry = {
  date: "2026-10-16", vix: 19.99, vixeq: 41.71, cor1m: 10.07,
  model_vix: 13.235944124617633, spread: 6.754055875382365,
  mean_20: 4.327848928602837, std_20: 0.7021606855738204,
  upper_20: 5.732170299750478, lower_20: 2.9235275574551958, z_20: 3.4553443344621044,
  mean_252: null, std_252: null, upper_252: null, lower_252: null, z_252: null,
};
const missing = { missing: true, status: "missing", scan_time: null, as_of: null, data_date: null, count: 0, current: null, series: [] };
function payload(overrides: Record<string, unknown> = {}) {
  return {
    scan_time: hoursAgo(0), status: "ok", source: "cboe", as_of: entry.date,
    data_date: entry.date, expected_session: entry.date, count: 1,
    current: entry, series: [entry], ...overrides,
  };
}
async function insert(value: Record<string, unknown>, service = "vix-model-spread", timestamp?: string) {
  await db.execute({
    sql: "INSERT INTO scan_snapshots(service,scan_time,payload) VALUES(?,?,?)",
    args: [service, timestamp ?? String(value.scan_time), JSON.stringify(value)],
  });
}
async function body() { const response = await GET(request()); expect(response.status).toBe(200); return response.json(); }

beforeEach(async () => {
  vi.clearAllMocks();
  __resetDbFirstReadMemoryForTests();
  db = createClient({ url: ":memory:" });
  await db.execute("CREATE TABLE scan_snapshots(service TEXT NOT NULL,scan_time TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(service,scan_time))");
  mocks.getDb.mockReturnValue(db);
  mocks.access.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "owner" } });
});
afterEach(() => { db.close(); });

describe("personal VIX model source selection", () => {
  it("serves the latest service snapshot over an older row", async () => {
    const data = payload(); await insert(data); await insert(payload({scan_time: hoursAgo(2)}));
    expect(await body()).toEqual(data);
  });
  it("omits writer-only raw source history in the SQL read", async () => {
    const data=payload();await insert({...data,source_series:{VIX:[{date:entry.date,value:20}]}});
    const execute=vi.spyOn(db,"execute");
    expect(await body()).toEqual(data);
    expect(execute.mock.calls.some(([statement])=>typeof statement === "object" && statement.sql.includes("json_remove(payload, '$.source_series')"))).toBe(true);
  });
  it("serves the last good read from memory when storage throws", async () => {
    const data = payload(); await insert(data); await body();
    mocks.getDb.mockImplementationOnce(() => { throw new Error("DB unavailable"); });
    expect(await body()).toEqual(data);
  });
  it("only reads this service's snapshots", async () => { await insert(payload(), "vixcor"); expect(await body()).toEqual(missing); });
  it("keeps real older data over a newer missing heartbeat", async () => {
    const data = payload({ scan_time: hoursAgo(1) }); await insert(data); await body();
    await insert({ ...missing, scan_time: hoursAgo(0) });
    expect(await body()).toEqual(data);
  });
  it("keeps a source-lag status and both precomputed windows intact", async () => {
    const data = payload({ status: "stale_source", expected_session: "2026-10-19" }); await insert(data); expect(await body()).toEqual(data);
  });
});

describe("VIX model missing and timestamp contract", () => {
  it("returns missing HTTP 200 when nothing exists", async () => { expect(await body()).toEqual(missing); });
  it("collapses snapshots over the shared 48h budget and preserves scan provenance", async () => {
    const old = hoursAgo(49); await insert(payload({scan_time: old})); expect(await body()).toEqual({...missing, stale:true, scan_time:old});
  });
  it("serves a 47h snapshot even in the tighter open-hours watchdog state", async () => {
    const data = payload({scan_time:hoursAgo(47)}); await insert(data); expect(await body()).toEqual(data);
  });
  it("uses payload content age rather than a newer database row key", async () => {
    const old = hoursAgo(49); await insert(payload({scan_time:old}), "vix-model-spread", hoursAgo(0));
    expect(await body()).toEqual({...missing,stale:true,scan_time:old});
  });
  it("rejects a future-dated snapshot", async () => { await insert(payload({scan_time:hoursAgo(-2)})); expect(await body()).toEqual(missing); });
  it.each(["null", "[]", "{}", "{broken", '"unexpected"'])("ignores malformed DB JSON %s and retains the last good read", async (raw) => {
    const data=payload({scan_time:hoursAgo(1)}); await insert(data); await body();
    await db.execute({sql:"INSERT INTO scan_snapshots VALUES(?,?,?)",args:["vix-model-spread",hoursAgo(0),raw]});
    expect(await body()).toEqual(data);
  });
  it.each([
    { series: [null] }, { current: [] }, { source: "unexpected" }, { count: 99 },
    { series: [{...entry,spread:null}] }, { series: [{...entry,z_20:"3.4"}] },
  ])("rejects invalid typed snapshot fields %j", async (invalid) => {
    await insert(payload(invalid)); expect(await body()).toEqual(missing);
  });
  it("collapses a fresh missing heartbeat to the stable missing shape", async () => {
    const stamp=hoursAgo(0);await insert({...missing,scan_time:stamp});expect(await body()).toEqual({...missing,stale:true,scan_time:stamp});
  });
});

describe("personal-use access and HTTP contract", () => {
  it("pins live GET capability and exposes no mutation", async () => {
    expect(dynamic).toBe("force-dynamic"); expect(runtime).toBe("nodejs"); expect(radonCapability).toBe("read");
    expect(await import("@/app/api/vix-model-spread/route")).not.toHaveProperty("POST");
  });
  it.each([401,403])("returns denied %s before any DB read", async (status) => {
    mocks.access.mockResolvedValue({ok:false,response:new Response("Forbidden",{status})});
    expect((await GET(request())).status).toBe(status); expect(mocks.getDb).not.toHaveBeenCalled();
    expect(mocks.access).toHaveBeenCalledWith(expect.any(Request),{operatorOnly:true});
  });
  it("rejects a real active demo principal before storage", async () => {
    const actual = await vi.importActual<typeof import("@/lib/routeAccess")>("@/lib/routeAccess");
    mocks.access.mockImplementationOnce((req, options) => actual.requireRouteAccess(req, options, {
      env: { NEXT_PUBLIC_RADON_DEMO: "1" },
      authFn: async () => ({ userId: "demo", sessionClaims: { metadata: {
        demoRole: "trial", demoTrialExpiresAt: new Date(Date.now()+86_400_000).toISOString(),
      } } }),
    }));
    expect((await GET(request())).status).toBe(403);
    expect(mocks.getDb).not.toHaveBeenCalled();
  });
  it("sets private cache metadata and the service cache tag", async () => {
    const response=await GET(request());expect(response.headers.get("Cache-Control")).toBe("private, max-age=300, stale-while-revalidate=3600");
    expect(response.headers.get("X-Cache-Tags")).toBe("vix-model-spread");
  });
});

describe("VIX model provisional current-session row", () => {
  const liveRow = { ...entry, date: "2026-10-19", vix: 18, vixeq: 40, cor1m: 12, model_vix: 13.856406460551018, spread: 4.143593539448982 };
  function live(overrides: Record<string, unknown> = {}) {
    return { scan_time: hoursAgo(0), row: liveRow, source: "cboe_delayed", quote_time: "2026-10-19T14:45:00", ...overrides };
  }
  it("appends a newer live row from the side snapshot as provisional current", async () => {
    await insert(payload({ scan_time: hoursAgo(3) })); await insert(live(), "vix-model-spread:live");
    const served = await body();
    expect(served.current).toEqual(liveRow);
    expect(served.series).toEqual([entry, liveRow]);
    expect(served.count).toBe(2);
    expect(served.data_date).toBe("2026-10-19");
    expect(served.provisional).toEqual({ source: "cboe_delayed", quote_time: "2026-10-19T14:45:00", scan_time: expect.any(String) });
  });
  it("lifts a source-lag status when the live row covers the expected session", async () => {
    await insert(payload({ status: "stale_source", expected_session: "2026-10-19" }));
    await insert(live(), "vix-model-spread:live");
    expect((await body()).status).toBe("ok");
  });
  it("ignores a live row not newer than the close, an empty row, or a malformed row", async () => {
    const data = payload(); await insert(data);
    await insert(live({ row: entry, scan_time: hoursAgo(0.3) }), "vix-model-spread:live");
    expect(await body()).toEqual(data);
    await insert(live({ row: null, source: null, scan_time: hoursAgo(0.2) }), "vix-model-spread:live");
    expect(await body()).toEqual(data);
    await insert(live({ row: { ...liveRow, spread: "x" }, scan_time: hoursAgo(0.1) }), "vix-model-spread:live");
    expect(await body()).toEqual(data);
  });
});
