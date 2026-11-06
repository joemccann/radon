/**
 * cache_meta freshness on GET, derived from the snapshot's own content time.
 *
 * Routes under test:
 *   - app/api/flow-analysis/route.ts
 *   - app/api/scanner/route.ts
 *   - app/api/discover/route.ts
 *
 * Run with:
 *   web/node_modules/.bin/vitest run web/tests/route-cache-meta.test.ts
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const STALE_THRESHOLD = 3600; // seconds; matches hourly flow-refresh cadence

const mockExecute = vi.hoisted(() => vi.fn());
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

function rowAged(ageSeconds: number, payload: Record<string, unknown>) {
  return {
    rows: [{
      scan_time: new Date(Date.now() - ageSeconds * 1000).toISOString(),
      payload: JSON.stringify(payload),
    }],
  };
}

const ROUTES = [
  ["flow-analysis", () => import("../app/api/flow-analysis/route"), {
    positions_scanned: 5, supports: [], against: [], watch: [], neutral: [],
  }],
  ["scanner", () => import("../app/api/scanner/route"), {
    tickers_scanned: 10, signals_found: 2, top_signals: [],
  }],
  ["discover", () => import("../app/api/discover/route"), {
    alerts_analyzed: 150, candidates_found: 3, candidates: [],
  }],
] as const;

describe.each(ROUTES)("GET /api/%s — cache_meta", (_name, importRoute, payload) => {
  beforeEach(() => {
    vi.resetModules();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    mockExecute.mockReset();
  });

  it("returns age_seconds ≈ 30 and is_stale: false when the snapshot is 30s old", async () => {
    mockExecute.mockResolvedValue(rowAged(30, payload));
    const { GET } = await importRoute();
    const body = await (await GET()).json();

    expect(body.cache_meta).toBeDefined();
    expect(body.cache_meta.last_refresh).not.toBeNull();
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(28);
    expect(body.cache_meta.age_seconds).toBeLessThan(35);
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });

  it("returns is_stale: true when the snapshot is 3700s old", async () => {
    mockExecute.mockResolvedValue(rowAged(3700, payload));
    const { GET } = await importRoute();
    const body = await (await GET()).json();

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3695);
  });

  it("returns last_refresh: null and is_stale: true when nothing exists", async () => {
    mockExecute.mockResolvedValue({ rows: [] });
    const { GET } = await importRoute();
    const body = await (await GET()).json();

    expect(body.cache_meta.last_refresh).toBeNull();
    expect(body.cache_meta.age_seconds).toBeNull();
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });

  it("an unparseable row timestamp reads stale, never current", async () => {
    mockExecute.mockResolvedValue({ rows: [{ scan_time: "not-a-date", payload: JSON.stringify(payload) }] });
    const { GET } = await importRoute();
    const body = await (await GET()).json();

    expect(body.cache_meta.last_refresh).toBeNull();
    expect(body.cache_meta.is_stale).toBe(true);
  });
});
