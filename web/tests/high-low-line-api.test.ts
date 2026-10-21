/** @vitest-environment node */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

const mocks = vi.hoisted(() => ({ getDb: vi.fn(), readFile: vi.fn(), access: vi.fn() }));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.access }));
vi.mock("@/lib/db", () => ({ getDb: mocks.getDb, resetDb: vi.fn() }));
vi.mock("fs/promises", async (original) => ({ ...await original<typeof import("fs/promises")>(), readFile: mocks.readFile }));
let db: Client;
const absent = { missing: true, degraded: false, scan_time: null, source_last_modified: null,
  data_date: null, expected_session_date: null, count: 0, weekly_count: 0, current: null,
  weekly_current: null, series: [], weekly_series: [], source_hash: null, source: null, baseline: null, settings: null };
// Synthetic observations only; licensed source history stays outside Git.
function payload(stamp = new Date().toISOString()) {
  const row = { date: "2026-10-20", period_start: "2026-10-20", period_end: "2026-10-20", period_complete: true,
    highs: 60, lows: 20, net: 40, cumulative: 250000.1256, rsi14: 45, stoch_rsi14: 0.1,
    sar: 250500, sar_direction: "falling", reset_to_zero: false, source_fractional: false };
  const weekly = { ...row, period_start: "2026-10-19", period_end: "2026-10-23", period_complete: false };
  return { missing: false, degraded: false, scan_time: stamp, source_last_modified: null,
    data_date: row.date, expected_session_date: row.date, count: 1, weekly_count: 1,
    current: { ...row, zone: "oversold" }, weekly_current: { ...weekly, zone: "oversold" },
    series: [row], weekly_series: [weekly], source_hash: "a".repeat(64),
    source: { name: "StockCharts DecisionPoint", url: "https://stockcharts.com", symbols: { highs: "!NEWHINYC", lows: "!NEWLONYC" }, history_start: "2003-01-02", history_end: row.date, fractional_policy: "preserved", fractional_observations: 275, excluded_non_sessions: [] },
    baseline: { date: "2003-01-02", value: 0, convention: "before_first_observation" },
    settings: { rsi_period: 14, stoch_period: 14, smoothing: "Wilder", sar_start: 1, sar_increment: 1, sar_max: 1, daily_ohlc: "close_only", weekly_ohlc: "daily_cumulative_range", default_interval: "weekly" } };
}
beforeEach(async () => {
  vi.resetModules(); vi.clearAllMocks(); db = createClient({ url: ":memory:" });
  await db.execute("CREATE TABLE scan_snapshots(service TEXT,scan_time TEXT,payload TEXT,PRIMARY KEY(service,scan_time))");
  mocks.getDb.mockReturnValue(db);
  mocks.readFile.mockRejectedValue(new Error("absent"));
  mocks.access.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "operator" } });
});
afterEach(() => { db.close(); vi.restoreAllMocks(); });
async function seed(data: ReturnType<typeof payload>, service = "high-low-line") {
  await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?,?,?)", args: [service, data.scan_time, JSON.stringify(data)] });
}
describe("private common-stock high-low GET", () => {
  it("is dynamic, read only and operator gated before all data access", async () => {
    mocks.access.mockResolvedValue({ ok: false, response: new Response("denied", { status: 403 }) });
    const route = await import("../app/api/high-low-line/route");
    expect(route.dynamic).toBe("force-dynamic"); expect(route.runtime).toBe("nodejs"); expect(route.radonCapability).toBe("read");
    expect(route).not.toHaveProperty("POST");
    expect((await route.GET(new Request("http://localhost/api/high-low-line"))).status).toBe(403);
    expect(mocks.getDb).not.toHaveBeenCalled(); expect(mocks.readFile).not.toHaveBeenCalled();
    expect(mocks.access).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true }));
  });
  it("returns the exact authenticated missing contract at HTTP200", async () => {
    const { GET } = await import("../app/api/high-low-line/route");
    const response = await GET(new Request("http://localhost/api/high-low-line"));
    expect(response.status).toBe(200); expect(await response.json()).toEqual(absent);
  });
  it("executes service-scoped SQL with fresh content timestamps", async () => {
    const good = payload(); await seed(good); await seed(payload(new Date(Date.now()+1000).toISOString()), "other-service");
    mocks.readFile.mockResolvedValue(JSON.stringify(payload(new Date(Date.now()-3600000).toISOString())));
    const { GET } = await import("../app/api/high-low-line/route");
    const response = await GET(new Request("http://localhost/api/high-low-line"));
    expect(await response.json()).toEqual(good); expect(response.headers.get("X-Request-Id")).toBeTruthy();
    expect(response.headers.get("Cache-Control")).toContain("private");
  });
  it("does not revive stale samples with a new SQL envelope timestamp", async () => {
    const old = payload(new Date(Date.now()-27*3600000).toISOString());
    await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?,?,?)", args: ["high-low-line", new Date().toISOString(), JSON.stringify(old)] });
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual({ ...absent, stale: true, scan_time: old.scan_time });
  });
  it("uses the valid disk fallback when Turso is unavailable", async () => {
    mocks.getDb.mockImplementation(() => { throw new Error("offline"); }); const good = payload();
    mocks.readFile.mockResolvedValue(JSON.stringify(good));
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(good);
  });
  it("rejects invalid nonfinite-equivalent or wrong-universe payloads", async () => {
    const bad = payload(); bad.series[0].stoch_rsi14 = 20; await seed(bad);
    const { GET } = await import("../app/api/high-low-line/route");
    expect((await (await GET(new Request("http://localhost/api/high-low-line"))).json()).missing).toBe(true);
  });
  it("uses the newer disk content clock even when the SQL envelope is newer", async () => {
    const old = payload(new Date(Date.now()-3_600_000).toISOString());
    await db.execute({ sql: "INSERT INTO scan_snapshots VALUES (?,?,?)", args: ["high-low-line", new Date().toISOString(), JSON.stringify(old)] });
    const good = payload(); mocks.readFile.mockResolvedValue(JSON.stringify(good));
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(good);
  });
  it("preserves degraded source lag and published fractional values", async () => {
    const good = payload(); good.degraded = true;
    good.series[0].highs = 60.0001; good.series[0].net = 40.0001; good.series[0].source_fractional = true;
    good.current = { ...good.series[0], zone: "oversold" };
    await seed(good);
    const { GET } = await import("../app/api/high-low-line/route");
    const response = await GET(new Request("http://localhost/api/high-low-line"));
    expect(await response.json()).toEqual(good);
    expect(response.headers.get("Cache-Control")).toBe("private, max-age=300, stale-while-revalidate=3600");
    expect(response.headers.get("X-Cache-Tags")).toBe("high-low-line");
  });
  it.each([
    ["broad NYSE universe", (p: ReturnType<typeof payload>) => { p.source.symbols.highs = "$NYHGH"; }],
    ["unmarked source lag", (p: ReturnType<typeof payload>) => { p.expected_session_date = "2026-10-21"; }],
    ["future source date", (p: ReturnType<typeof payload>) => { p.data_date = "2099-01-01"; }],
    ["wrong baseline", (p: ReturnType<typeof payload>) => { p.baseline.date = "2023-01-02"; }],
    ["null finite field", (p: ReturnType<typeof payload>) => { p.series[0].highs = null as unknown as number; }],
    ["impossible date", (p: ReturnType<typeof payload>) => { p.series[0].date = "2026-02-30"; }],
    ["wrong weekly period", (p: ReturnType<typeof payload>) => { p.weekly_series[0].period_start = "2026-10-20"; }],
    ["wrong current", (p: ReturnType<typeof payload>) => { p.current.cumulative += 1; }],
    ["wrong current zone", (p: ReturnType<typeof payload>) => { p.weekly_current.zone = "neutral"; }],
    ["wrong settings", (p: ReturnType<typeof payload>) => { p.settings.sar_start = 0.02; }],
    ["naive clock", (p: ReturnType<typeof payload>) => { p.scan_time = "2026-10-21T12:00:00"; }],
    ["malformed source URL", (p: ReturnType<typeof payload>) => { p.source.url = "not a URL"; }],
    ["unknown non-session", (p: ReturnType<typeof payload>) => { p.source.excluded_non_sessions = ["2026-10-19"]; }],
    ["untrusted source", (p: ReturnType<typeof payload>) => { p.source.url = "https://stockcharts.com.example.com"; }],
    ["wrong source end", (p: ReturnType<typeof payload>) => { p.source.history_end = "2026-10-19"; }],
  ])("fails closed for %s", async (_label, mutate) => {
    const bad = payload(); mutate(bad); await seed(bad);
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(absent);
  });
  it("keeps a valid older disk sample when newer DB content is malformed", async () => {
    await seed(payload());
    await db.execute("UPDATE scan_snapshots SET payload='not JSON'");
    const good = payload(new Date(Date.now()-60_000).toISOString()); mocks.readFile.mockResolvedValue(JSON.stringify(good));
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(good);
  });

  it.each([
    ["anonymous", null, {}, 401],
    ["unlisted authenticated", "reader", {}, 403],
    ["active demo", "demo", { NEXT_PUBLIC_RADON_DEMO: "1" }, 403],
  ] as const)("rejects %s through the real authorization guard before I/O", async (_label, userId, env, status) => {
    const actual = await vi.importActual<typeof import("@/lib/routeAccess")>("@/lib/routeAccess");
    mocks.access.mockImplementation((request, options) => actual.requireRouteAccess(request, options, {
      env: { ...env, ALLOWED_USER_IDS: "operator" },
      authFn: async () => ({ userId, publicMetadata: { demoRole: "trial", demoTrialExpiresAt: new Date(Date.now()+86_400_000).toISOString() } }),
    }));
    const { GET } = await import("../app/api/high-low-line/route");
    const response = await GET(new Request("http://localhost/api/high-low-line"));
    expect(response.status).toBe(status);
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(mocks.getDb).not.toHaveBeenCalled(); expect(mocks.readFile).not.toHaveBeenCalled();
  });

  it("bounds a hung DB request, serves disk, and retries DB on the next GET", async () => {
    vi.useFakeTimers();
    try {
      mocks.getDb.mockReturnValueOnce({ execute: () => new Promise(() => {}) });
      const good = payload(); mocks.readFile.mockResolvedValue(JSON.stringify(good));
      const { GET } = await import("../app/api/high-low-line/route");
      const pending = GET(new Request("http://localhost/api/high-low-line"));
      await vi.advanceTimersByTimeAsync(3_001);
      expect(await (await pending).json()).toEqual(good);
      expect(mocks.getDb).toHaveBeenCalledTimes(1);
      await seed(good); mocks.readFile.mockRejectedValue(new Error("absent"));
      expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(good);
      expect(mocks.getDb).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });

  it("accepts the official source metadata and paired known exchange closures", async () => {
    const good = payload(); good.source.name = "NYSE Common Stock Only New Highs and New Lows";
    good.source.url = "https://stockcharts.com/quotebrain/historyandquote/d";
    good.source.excluded_non_sessions = ["2012-10-29", "2012-10-30"];
    await seed(good);
    const { GET } = await import("../app/api/high-low-line/route");
    expect(await (await GET(new Request("http://localhost/api/high-low-line"))).json()).toEqual(good);
  });

});
