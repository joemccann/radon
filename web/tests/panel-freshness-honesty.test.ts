/**
 * REL-052 tranche E — R-124, R-125, R-135.
 *
 * Three ways the 2026-08-22 delta let a failure render as a healthy number:
 * panels that never read `error`, routes that compute freshness and then
 * discard it, and a cash-flow lozenge that short-circuits before its own
 * error branches.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const REPO = join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(join(REPO, rel), "utf-8");

// ---------------------------------------------------------------------------
// R-124 — a background refresh failure must be visible
// ---------------------------------------------------------------------------
const DELTA_PANELS = [
  "web/components/TrinPanel.tsx",
  "web/components/CreditSpreadPanel.tsx",
  "web/components/IeiHygPanel.tsx",
  "web/components/IvRankPanel.tsx",
];

describe("R-124: the new panels surface their hook's error", () => {
  it.each(DELTA_PANELS)("%s destructures error from its sync hook", (rel) => {
    const src = read(rel);
    const destructure = src.match(/const \{[^}]*\} = use[A-Za-z]+\(\);/)?.[0] ?? "";
    expect(destructure).toMatch(/\berror\b/);
  });

  it.each(DELTA_PANELS)("%s renders a refresh-failure affordance", (rel) => {
    const src = read(rel);
    // useSyncHook keeps the previous `data` on a failed fetch, so an
    // `error && !data` guard alone leaves a silent background failure
    // rendering last night's numbers as current.
    expect(src).toContain("PanelRefreshError");
  });
});

describe("R-124: the shared affordance exists and is honest", () => {
  it("renders nothing when there is no error", () => {
    expect(read("web/components/PanelRefreshError.tsx")).toMatch(/if \(!error\) return null/);
  });

  it("uses brand tokens, never raw hex", () => {
    const src = read("web/components/PanelRefreshError.tsx");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).toContain("var(--");
  });
});

// ---------------------------------------------------------------------------
// R-125 — a computed freshness verdict must gate the response
// ---------------------------------------------------------------------------
const FRESHNESS_ROUTES = [
  "web/app/api/trin/route.ts",
  "web/app/api/credit-spread/route.ts",
  "web/app/api/iei-hyg/route.ts",
  "web/app/api/vol-cone/route.ts",
];

describe("R-125: the routes serve on freshness, not merely on ok", () => {
  it.each(FRESHNESS_ROUTES)("%s gates on result.fresh", (rel) => {
    const src = read(rel);
    expect(src).not.toMatch(/NextResponse\.json\(result\.ok \? /);
    expect(src).toMatch(/result\.ok && result\.fresh/);
  });

  it.each(FRESHNESS_ROUTES)("%s keeps the stale row's provenance", (rel) => {
    // A constant wipes scan_time, so "the feed died 3 days ago" reads
    // identically to "this job has never run".
    expect(read(rel)).toContain("staleCollapse");
  });
});

describe("R-125: staleCollapse carries provenance onto the missing shape", () => {
  it("stamps the stale timestamp and marks the payload stale", async () => {
    const { staleCollapse } = await import("../lib/dbFirstRead");
    const missing = Object.freeze({ missing: true, scan_time: null, current: null });
    const collapsed = staleCollapse(missing, {
      ok: true,
      source: "db",
      data: { scan_time: "2026-08-20T21:00:00Z" },
      timestampMs: Date.parse("2026-08-20T21:00:00Z"),
      fresh: false,
    });
    expect(collapsed).toMatchObject({
      missing: true,
      stale: true,
      scan_time: "2026-08-20T21:00:00Z",
    });
    expect(collapsed.current).toBeNull();
  });

  it("returns the untouched constant when nothing was read at all", async () => {
    const { staleCollapse } = await import("../lib/dbFirstRead");
    const missing = Object.freeze({ missing: true, scan_time: null });
    expect(staleCollapse(missing, { ok: false })).toEqual(missing);
  });

  it("never mutates the frozen constant", async () => {
    const { staleCollapse } = await import("../lib/dbFirstRead");
    const missing = Object.freeze({ missing: true, scan_time: null });
    staleCollapse(missing, {
      ok: true,
      source: "db",
      data: { scan_time: "2026-08-20T21:00:00Z" },
      timestampMs: 1,
      fresh: false,
    });
    expect(missing.scan_time).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// R-135 — a first-ever sync failure is still a failure
// ---------------------------------------------------------------------------
describe("R-135: the cash-flow lozenge does not hide a never-succeeded sync", () => {
  const lozenge = () =>
    read("web/components/CashFlowsSection.tsx")
      .split("const lozengeLabel = (() => {")[1]
      .split("})();")[0];

  it("evaluates the throttled and errored branches before giving up", () => {
    const body = lozenge();
    const bail = body.indexOf("return null");
    const throttled = body.indexOf("isThrottled");
    expect(throttled).toBeGreaterThan(-1);
    expect(bail === -1 || throttled < bail).toBe(true);
  });

  it("reports a throttle with no prior success", () => {
    const body = lozenge();
    expect(body).toMatch(/Never synced|no prior sync/);
    // The bail-out may survive, but never as the FIRST statement — that was
    // the shape that keyed the whole lozenge off a prior success.
    const firstStatement = body.split("\n").map((l) => l.trim()).find(
      (l) => l.length > 0 && !l.startsWith("//"),
    );
    expect(firstStatement).not.toMatch(/if \(!lastSyncedRelative\) return null;/);
  });
});
