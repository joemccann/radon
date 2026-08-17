import { afterEach, describe, expect, it, vi } from "vitest";
import { readFile } from "fs/promises";
import path from "path";

const ROOT = path.resolve(__dirname, "../..");

const countMatches = (source: string, pattern: RegExp): number => (source.match(pattern) ?? []).length;

/** Every upstream `fetch(` in a provider route, excluding the `radonFetch`/`.fetch(` wrappers. */
const countUpstreamFetchCallSites = (source: string): number => countMatches(source, /(?<![.\w])fetch\(/g);

const countDeadlinedFetches = (source: string): number => countMatches(source, /signal:\s*AbortSignal\.timeout\(/g);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("supplemental route remediations", () => {
  it("reliability cap selects newest rows and exposes truncation", async () => {
    const route = await readFile(path.join(ROOT, "web/app/api/admin/reliability/route.ts"), "utf8");
    expect(route).toContain("ORDER BY created_at DESC");
    expect(route).toContain("MAX_EVENT_ROWS + 1");
    expect(route).toContain("slice(-MAX_EVENT_ROWS)");
    expect(route).toContain("truncated");
  });

  it("GEX route delegates session-date selection to the session-aware helper", async () => {
    const source = await readFile(path.join(ROOT, "web/app/api/gex/route.ts"), "utf8");
    expect(source).toContain("isGexDataStale(cached");
    expect(source).toContain("undefined, currentMarketOpen");
    expect(source).not.toContain("function todayET");
  });

  it("share previews proxy remote generator storage and internals emits its own type", async () => {
    const gex = await readFile(path.join(ROOT, "web/app/api/gex/share/content/route.ts"), "utf8");
    const internals = await readFile(path.join(ROOT, "web/app/api/internals/share/content/route.ts"), "utf8");
    const server = await readFile(path.join(ROOT, "scripts/api/server.py"), "utf8");
    expect(gex).toContain("radonFetchText(`/share/content?type=gex");
    expect(internals).toContain("radonFetchText(`/share/content?type=internals");
    expect(server).toContain('"--card-type", "internals"');
    expect(server).toContain('@app.get("/share/content"');
  });

  it("served regime previews have no CSP-blocked inline controls", async () => {
    const source = await readFile(path.join(ROOT, "scripts/generate_regime_share.py"), "utf8");
    const start = source.indexOf("def build_preview");
    const end = source.indexOf("# ── Main");
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    const preview = source.slice(start, end);
    // A marker rename must not silently shrink the slice into a vacuous pass.
    expect(preview.length).toBeGreaterThan(200);
    expect(preview).toContain("<html");
    expect(preview).not.toContain("<script>");
    expect(preview).not.toContain("onclick=");
  });

  it("provider routes apply deadlines to EVERY upstream fetch, not to a counted subset", async () => {
    const info = await readFile(path.join(ROOT, "web/app/api/ticker/info/route.ts"), "utf8");
    const news = await readFile(path.join(ROOT, "web/app/api/ticker/news/route.ts"), "utf8");
    for (const [name, source] of [["ticker/info", info], ["ticker/news", news]] as const) {
      const callSites = countUpstreamFetchCallSites(source);
      expect(callSites, `${name} must still make upstream fetches`).toBeGreaterThan(0);
      expect(
        countDeadlinedFetches(source),
        `${name}: every upstream fetch must carry AbortSignal.timeout — ${callSites} call sites found`,
      ).toBe(callSites);
    }
  });

  it("websocket ticket fetch is inside a bounded deadline", async () => {
    const source = await readFile(path.join(ROOT, "web/app/api/previous-close/route.ts"), "utf8");
    const ticketFetch = source.slice(source.indexOf("async function buildWsUrl"), source.indexOf("async function fetchFromIB"));
    expect(ticketFetch).toContain("signal: AbortSignal.timeout(750)");
  });

  it("demo webhook replay ledger is migrated and claimed before provisioning", async () => {
    const route = await readFile(path.join(ROOT, "web/app/api/webhooks/clerk/route.ts"), "utf8");
    const migration = await readFile(path.join(ROOT, "scripts/db/demo_migrations/0003_demo_webhook_events.sql"), "utf8");
    expect(route.indexOf("claimDemoWebhookEvent")).toBeLessThan(route.indexOf("provisionDemoTrial(event.data"));
    expect(migration).toContain("event_id     TEXT PRIMARY KEY");
  });

  it("stale FRED observations return a labeled private fallback", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T12:00:00Z"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      "DATE,DFF\n2026-07-01,3.64\n",
      { status: 200 },
    )));
    const { GET } = await import("../app/api/risk-free-rate/route");
    const response = await GET();
    expect(await response.json()).toEqual({
      rate: 0,
      asOf: "2026-07-01",
      source: "fallback",
      stale: true,
    });
    expect(response.headers.get("cache-control")).toContain("no-store");
  });
});
