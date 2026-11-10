import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Tests for the new sync-based hooks and API routes.
 *
 * - useSyncHook: factory behavior (GET/POST flow, interval, cleanup)
 * - Flow Analysis API: GET cache read, POST spawn
 * - Scanner API: GET cache read, POST spawn
 */

// ---------------------------------------------------------------------------
// Mock fs/promises and child_process for API route tests
// ---------------------------------------------------------------------------

const mockReadFile = vi.fn();
const mockWriteFile = vi.fn();
vi.mock("fs/promises", () => ({
  readFile: (...args: unknown[]) => mockReadFile(...args),
  writeFile: (...args: unknown[]) => mockWriteFile(...args),
}));

function dbRow(snapshot: Record<string, unknown>) {
  return {
    rows: [{
      payload: JSON.stringify(snapshot),
      scan_time: snapshot.scan_time ?? snapshot.discovery_time ?? snapshot.analysis_time,
      taken_at: snapshot.scan_time,
    }],
  };
}

const mockExecute = vi.fn().mockResolvedValue({ rows: [] });
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

const mockSpawn = vi.fn();
vi.mock("child_process", () => ({
  spawn: (...args: unknown[]) => mockSpawn(...args),
}));

// ---------------------------------------------------------------------------
// Flow Analysis API — GET
// ---------------------------------------------------------------------------

describe("Flow Analysis API GET", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
  });

  it("returns cached data when file exists", async () => {
    const cached = {
      analysis_time: "2026-03-06T12:00:00",
      positions_scanned: 5,
      supports: [{ ticker: "AAPL", position: "Long Calls", strength: 30 }],
      against: [],
      watch: [],
      neutral: [],
    };
    mockExecute.mockResolvedValueOnce(dbRow(cached));

    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.positions_scanned).toBe(5);
    expect(body.supports).toHaveLength(1);
  });

  it("returns empty structure when cache file is missing", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));

    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.positions_scanned).toBe(0);
    expect(body.supports).toEqual([]);
    expect(body.against).toEqual([]);
    expect(body.watch).toEqual([]);
    expect(body.neutral).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Scanner API — GET
// ---------------------------------------------------------------------------

describe("Scanner API GET", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
  });

  it("returns cached data when file exists", async () => {
    const cached = {
      scan_time: "2026-03-06T12:00:00",
      tickers_scanned: 20,
      signals_found: 5,
      top_signals: [{ ticker: "NVDA", score: 75, signal: "STRONG" }],
    };
    mockExecute.mockResolvedValueOnce(dbRow(cached));

    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.tickers_scanned).toBe(20);
    expect(body.top_signals).toHaveLength(1);
  });

  it("returns empty structure when cache file is missing", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));

    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.tickers_scanned).toBe(0);
    expect(body.signals_found).toBe(0);
    expect(body.top_signals).toEqual([]);
  });
});
