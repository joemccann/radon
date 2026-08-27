/**
 * @vitest-environment node
 *
 * R-257 / T-213 — a corrupt stored snapshot must not page as a Turso outage.
 *
 * `readPortfolioFromDb` raises `PortfolioSnapshotCorruptError` for a payload
 * that will not parse, with the stated purpose "so the route can report
 * corruption rather than DB_UNAVAILABLE". Nothing mapped it: every throw
 * funnelled into `unavailablePortfolioResponse` (503 / DB_UNAVAILABLE) on GET
 * and into UPSTREAM_ERROR (502) on the POST sync-failure fallback. Both point
 * the operator at the database or at IB; the actual repair is re-running the
 * portfolio sync, and no amount of retrying re-parses the same bad row.
 *
 * The mutation the source-grep assertion in p2-operability-remainder.test.ts
 * cannot see: keep the throw, drop the route mapping. The grep stays green.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mockExecute, batch: vi.fn() }),
  resetDb: () => {},
}));

const TRUNCATED_PAYLOAD = '{"bankroll": 100000, "positions": [{"ticker": "SP';
const TAKEN_AT = "2026-03-05T10:00:00Z";

/** portfolio_snapshots yields one unparseable row; every other read is empty. */
function mockCorruptSnapshotRow(): void {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+portfolio_snapshots/i.test(sql)) {
      return { rows: [{ taken_at: TAKEN_AT, payload: TRUNCATED_PAYLOAD }] };
    }
    return { rows: [] };
  });
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mockExecute.mockReset();
  mockCorruptSnapshotRow();
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the snapshot reader distinguishes corruption from a transport failure", () => {
  it("raises PortfolioSnapshotCorruptError with code SNAPSHOT_CORRUPT", async () => {
    const { readPortfolioFromDb, PortfolioSnapshotCorruptError } = await import(
      "../lib/portfolio/readPortfolioSnapshot.server"
    );
    await expect(readPortfolioFromDb()).rejects.toBeInstanceOf(
      PortfolioSnapshotCorruptError,
    );
    await expect(readPortfolioFromDb()).rejects.toMatchObject({
      code: "SNAPSHOT_CORRUPT",
    });
  });

  it("names the offending row so the operator can find it", async () => {
    const { readPortfolioFromDb } = await import(
      "../lib/portfolio/readPortfolioSnapshot.server"
    );
    await expect(readPortfolioFromDb()).rejects.toThrow(
      new RegExp(`taken_at=${TAKEN_AT}`),
    );
  });
});

describe("GET /api/portfolio reports corruption, not DB_UNAVAILABLE", () => {
  it("returns code SNAPSHOT_CORRUPT for an unparseable stored payload", async () => {
    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    const body = await jsonOf(res);

    expect(body.code).toBe("SNAPSHOT_CORRUPT");
    expect(body.code).not.toBe("DB_UNAVAILABLE");
  });

  it("does not present corruption as a retryable 503 outage", async () => {
    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();

    expect(res.status).not.toBe(503);
    expect(res.status).toBe(500);
  });

  it("logs the corrupt row instead of blaming the database", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { GET } = await import("../app/api/portfolio/route");
    await GET();

    const logged = errorSpy.mock.calls.map((args) => args.join(" ")).join("\n");
    expect(logged).toContain("corrupt");
    expect(logged).not.toContain("Turso portfolio read failed");
  });

  it("still reports a genuine transport failure as DB_UNAVAILABLE", async () => {
    mockExecute.mockImplementation(async () => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    const body = await jsonOf(res);

    expect(res.status).toBe(503);
    expect(body.code).toBe("DB_UNAVAILABLE");
  });
});

describe("POST /api/portfolio does not blame the upstream for a corrupt row", () => {
  it("returns SNAPSHOT_CORRUPT when the sync fails and the snapshot is corrupt", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    const { POST } = await import("../app/api/portfolio/route");
    const res = await POST();
    const body = await jsonOf(res);

    expect(body.code).toBe("SNAPSHOT_CORRUPT");
    expect(body.code).not.toBe("UPSTREAM_ERROR");
  });

  it("still reports a genuine transport failure as UPSTREAM_ERROR", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockExecute.mockImplementation(async () => {
      throw new TypeError("fetch failed");
    });
    const { POST } = await import("../app/api/portfolio/route");
    const res = await POST();
    const body = await jsonOf(res);

    expect(res.status).toBe(502);
    expect(body.code).toBe("UPSTREAM_ERROR");
  });
});

describe("the RSC seed surfaces corruption before degrading to the client GET", () => {
  it("logs the corrupt snapshot and returns no seed", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { readPortfolioSnapshotSeed } = await import(
      "../lib/portfolio/readPortfolioSnapshot.server"
    );
    await expect(readPortfolioSnapshotSeed()).resolves.toBeUndefined();

    const logged = errorSpy.mock.calls.map((args) => args.join(" ")).join("\n");
    expect(logged).toContain("stored snapshot is corrupt");
  });
});
