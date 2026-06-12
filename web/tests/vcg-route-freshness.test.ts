/**
 * /api/vcg freshness regression (DUR-01).
 *
 * Bug: the route preferred ANY non-null vcg_snapshots row, so when the
 * Turso mirror froze (dual-write gated off 2026-06-11) the route served
 * day-old data forever AND fired triggerBackgroundScan() on every
 * request — an infinite no-op rescan loop — even while data/vcg.json on
 * disk was fresh.
 *
 * Contract:
 *  - serve whichever source (DB row vs disk JSON) has the newer scan_time
 *  - trigger a background rescan ONLY when BOTH sources are stale
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockReadFile = vi.fn();
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  stat: vi.fn(),
}));

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mockExecute }),
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

// Thursday 2026-06-11, 11:00 ET (15:00 UTC) — market open, so staleness
// is governed by the 60s intraday scan_time TTL in vcgStaleness.ts.
const NOW_ISO = "2026-06-11T15:00:00Z";
const FRESH_SCAN = "2026-06-11T14:59:30+00:00"; // 30s old → fresh intraday
const FROZEN_SCAN = "2026-06-10T19:55:00+00:00"; // prior session → stale

function vcgPayload(scanTime: string): Record<string, unknown> {
  return {
    scan_time: scanTime,
    market_open: true,
    credit_proxy: "HYG",
    signal: { vcg: 1.23, vix: 18, vvix: 95 },
    history: [],
  };
}

function seedDb(scanTime: string | null): void {
  mockExecute.mockResolvedValue({
    rows:
      scanTime === null
        ? []
        : [{ scan_time: scanTime, payload: JSON.stringify(vcgPayload(scanTime)) }],
  });
}

function seedDisk(scanTime: string | null): void {
  if (scanTime === null) {
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
  } else {
    mockReadFile.mockResolvedValue(JSON.stringify(vcgPayload(scanTime)));
  }
}

let warn: ReturnType<typeof vi.spyOn>;

describe("GET /api/vcg — freshness-aware source selection + rescan gating", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));
    mockReadFile.mockReset();
    mockExecute.mockReset();
    mockRadonFetch.mockReset().mockResolvedValue({});
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    warn.mockRestore();
  });

  it("serves the fresher DISK snapshot when the DB mirror is frozen — and does NOT rescan", async () => {
    seedDb(FROZEN_SCAN);
    seedDisk(FRESH_SCAN);

    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.scan_time).toBe(FRESH_SCAN);
    // Regression: frozen DB scan_time used to drive isVcgDataStale → an
    // infinite background-rescan loop on every request.
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("serves the fresher DB snapshot when disk lags — and does NOT rescan", async () => {
    seedDb(FRESH_SCAN);
    seedDisk(FROZEN_SCAN);

    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    const body = await res.json();

    expect(body.scan_time).toBe(FRESH_SCAN);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("triggers exactly one background rescan when BOTH sources are stale", async () => {
    seedDb(FROZEN_SCAN);
    seedDisk(FROZEN_SCAN);

    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    const body = await res.json();

    expect(body.scan_time).toBe(FROZEN_SCAN);
    expect(mockRadonFetch).toHaveBeenCalledTimes(1);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/vcg/scan",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("triggers a rescan and returns the empty envelope when neither source exists", async () => {
    seedDb(null);
    seedDisk(null);

    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.scan_time).toBe("");
    expect(body.history).toEqual([]);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/vcg/scan",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
