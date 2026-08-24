/**
 * REL-069 tranche B — R-193, R-194, R-195, R-196.
 *
 * Four ways a served payload or a rendered summary overstates what it knows:
 * a degraded heartbeat outranking a real series, a stale collapse that wipes
 * provenance, hardcoded cadence copy on an unknown-lag branch, and a count
 * denominated over 22 tabs when only 4 can be flagged.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const REPO = join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(join(REPO, rel), "utf-8");

// ---------------------------------------------------------------------------
// R-193 — a fresher degraded row must not outrank an older real one
// ---------------------------------------------------------------------------
const DEGRADE_GUARD_ROUTES = [
  "web/app/api/credit-spread/route.ts",
  "web/app/api/iei-hyg/route.ts",
  "web/app/api/trin/route.ts",
  "web/app/api/vol-cone/route.ts",
];

describe("R-193: the four delta routes carry the isDegraded guard", () => {
  it.each(DEGRADE_GUARD_ROUTES)("%s passes isDegraded to dbFirstRead", (rel) => {
    const src = read(rel);
    const call = src.split("dbFirstRead({")[1]?.split("});")[0] ?? "";
    expect(call).toContain("isDegraded");
  });

  it("pickFresherSource still prefers the healthy side", async () => {
    const { dbFirstRead, isMissingPayload } = await import("../lib/dbFirstRead");
    const result = await dbFirstRead({
      fromDb: async () => ({ data: { missing: true, scan_time: "2026-08-23" }, timestampMs: 2_000 }),
      fromDisk: async () => ({ data: { missing: false, scan_time: "2026-08-22" }, timestampMs: 1_000 }),
      maxAgeMs: 10_000_000_000_000,
      label: "test",
      isDegraded: isMissingPayload,
      now: () => 2_500,
    });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.source).toBe("disk");
  });
});

// ---------------------------------------------------------------------------
// R-194 — a stale collapse must not wipe provenance
// ---------------------------------------------------------------------------
describe("R-194: vixcor and ivrank keep the stale row's provenance", () => {
  it.each(["web/app/api/vixcor/route.ts", "web/app/api/ivrank/route.ts"])(
    "%s collapses through staleCollapse",
    (rel) => {
      const src = read(rel);
      expect(src).toContain("staleCollapse");
      expect(src).not.toMatch(/result\.fresh \? result\.data : MISSING_[A-Z]+\)/);
    },
  );

  it("the collapsed payload says WHEN the feed died", async () => {
    const { staleCollapse } = await import("../lib/dbFirstRead");
    const collapsed = staleCollapse(
      { missing: true, status: "missing", scan_time: null, as_of: null },
      {
        ok: true,
        source: "db",
        data: { scan_time: "2026-08-20T22:10:00Z" },
        timestampMs: 1,
        fresh: false,
      },
    );
    expect(collapsed.scan_time).toBe("2026-08-20T22:10:00Z");
    expect(collapsed.stale).toBe(true);
    expect(collapsed.status).toBe("missing");
  });

  it("a job that never ran is still a bare missing payload", async () => {
    const { staleCollapse } = await import("../lib/dbFirstRead");
    const missing = { missing: true, status: "missing", scan_time: null };
    expect(staleCollapse(missing, { ok: false })).toEqual(missing);
  });
});

// ---------------------------------------------------------------------------
// R-195 — no hardcoded cadence copy on an unknown-lag branch
// ---------------------------------------------------------------------------
describe("R-195: parentLagCopy distinguishes unknown from current", () => {
  it("says the lag is unknown when the job could not determine it", async () => {
    const { parentLagCopy } = await import("../lib/vixcor");
    const copy = parentLagCopy(null);
    expect(copy).not.toBe(parentLagCopy(0));
    expect(copy).toMatch(/UNKNOWN|UNAVAILABLE/i);
  });

  it("a current parent does not claim a hardcoded start month", async () => {
    const { parentLagCopy } = await import("../lib/vixcor");
    expect(parentLagCopy(0)).not.toMatch(/2006-01/);
  });

  it("still reports a known lag", async () => {
    const { parentLagCopy } = await import("../lib/vixcor");
    expect(parentLagCopy(1)).toBe("PARENT 1 SESSION BEHIND");
    expect(parentLagCopy(4)).toBe("PARENT 4 SESSIONS BEHIND");
  });

  it("carries no hardcoded cadence string at all", () => {
    const src = read("web/lib/vixcor.ts");
    const fn = src
      .split("export function parentLagCopy(")[1]
      .split("\n}")[0]
      .split("\n")
      .filter((line) => !line.trim().startsWith("//"))
      .join("\n");
    expect(fn).not.toMatch(/DAILY SERIES SINCE/);
  });
});

// ---------------------------------------------------------------------------
// R-196 — "N ELEVATED" must be denominated over what can be flagged
// ---------------------------------------------------------------------------
describe("R-196: the rail counts over the tabs it can actually flag", () => {
  it("exports the flaggable tab set", async () => {
    const { FLAGGABLE_REGIME_TABS, REGIME_TABS } = await import("../lib/regimeRail");
    expect(FLAGGABLE_REGIME_TABS.length).toBeGreaterThan(0);
    expect(FLAGGABLE_REGIME_TABS.length).toBeLessThan(REGIME_TABS.length);
    for (const tab of FLAGGABLE_REGIME_TABS) {
      expect(REGIME_TABS).toContain(tab);
    }
  });

  it("every flaggable tab is one buildRailStatuses can emit", async () => {
    const { FLAGGABLE_REGIME_TABS, buildRailStatuses } = await import("../lib/regimeRail");
    const emitted = Object.keys(
      buildRailStatuses({
        cri: { score: 90, level: "high" },
        cor1m: 0.95,
        vcg: null,
        gex: null,
      }),
    );
    for (const tab of emitted) {
      expect(FLAGGABLE_REGIME_TABS).toContain(tab);
    }
  });

  it("counts elevated over the flaggable set", async () => {
    const { elevatedCount, FLAGGABLE_REGIME_TABS } = await import("../lib/regimeRail");
    const src = read("web/lib/regimeRail.ts");
    const fn = src.split("export function elevatedCount(")[1].split("\n}")[0];
    expect(fn).toContain("FLAGGABLE_REGIME_TABS");
    expect(elevatedCount({})).toBe(0);
    expect(FLAGGABLE_REGIME_TABS.length).toBeGreaterThan(0);
  });

  it("the rail's denominator matches its numerator", () => {
    const src = read("web/components/RegimeRail.tsx");
    const foot = src.split('className="regime-rail__foot"')[1].split("</div>")[0];
    expect(foot).not.toMatch(/REGIME_TABS\.length\} INDICATORS/);
    expect(foot).toMatch(/FLAGGABLE_REGIME_TABS/);
  });
});
